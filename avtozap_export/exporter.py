"""Сама выгрузка: обойти выбранные разделы и сложить csv в папку.

Правила простые: один упавший раздел не мешает остальным, «Стоп»
срабатывает сразу, а уже скачанное всегда сохраняется на диск.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

from . import config
from .api import AdminClient, ApiError, NetworkError, Stopped
from .core import (
    build_summary,
    count_stores,
    filter_rows_by_date,
    has_price,
    period_phrase,
    write_csv,
)

Progress = Callable[[str, float | None], None]


@dataclass
class ExportOptions:
    """Что именно выгружаем."""

    sections: list[dict]
    period_kind: str = "today"  # today / yesterday / week / custom / all
    date_from: dt.date | None = None
    date_to: dt.date | None = None
    price_filter: str = "all"  # all / with / without


@dataclass
class SectionResult:
    key: str
    label: str
    count: int
    file: Path | None = None
    extra: dict = field(default_factory=dict)


@dataclass
class ExportResult:
    out_dir: Path
    sections: list[SectionResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    stopped: bool = False
    summary: str = ""


def period_bounds(kind: str, custom_from: dt.date | None = None, custom_to: dt.date | None = None):
    """Границы периода по выбранной кнопке."""
    today = dt.date.today()
    if kind == "today":
        return today, today
    if kind == "yesterday":
        yesterday = today - dt.timedelta(days=1)
        return yesterday, yesterday
    if kind == "week":
        return today - dt.timedelta(days=6), today
    if kind == "custom":
        return custom_from, custom_to
    return None, None


def _server_params(section: dict, options: ExportOptions) -> dict:
    """Параметры запроса: даты и цена, если раздел их понимает."""
    params: dict[str, str] = {}
    date_params = section.get("date_params")
    if date_params and len(date_params) == 2:
        if options.date_from:
            params[date_params[0]] = options.date_from.isoformat()
        if options.date_to:
            params[date_params[1]] = options.date_to.isoformat()
    if (
        section.get("key") == "offers"
        and options.price_filter == "with"
        and section.get("has_price_filter", True)
    ):
        params["has_price"] = "true"
    return params


def _friendly_error(error: Exception, label: str) -> str:
    """Ошибка человеческим языком."""
    if isinstance(error, NetworkError):
        return f"Раздел «{label}»: {error}"
    if isinstance(error, ApiError):
        return f"Раздел «{label}»: {error}"
    return f"Раздел «{label}» не отвечает"


def run_export(
    client: AdminClient,
    options: ExportOptions,
    *,
    progress: Progress | None = None,
    out_root: Path | None = None,
    logger: logging.Logger | None = None,
) -> ExportResult:
    """Выгрузить выбранные разделы в csv и вернуть результат со сводкой."""
    log = logger or logging.getLogger("avtozap")
    out_root = out_root or config.DATA_DIR
    out_dir = out_root / dt.date.today().isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)

    result = ExportResult(out_dir=out_dir)
    total_sections = max(1, len(options.sections))

    def say(text: str, fraction: float | None) -> None:
        if progress:
            progress(text, fraction)

    for index, section in enumerate(options.sections):
        label = section.get("label") or section.get("key") or "раздел"
        key = section.get("key") or "section"
        path = section["path"]
        base = index / total_sections
        step = 1 / total_sections
        rows: list[dict] = []
        stopped_here = False
        failed = False

        # Значения раздела фиксируем в аргументах: так замыкание не зависит
        # от того, на каком шаге цикла его позовут.
        def on_page(page: int, pages: int | None, downloaded: int, label=label, base=base, step=step) -> None:
            if pages:
                text = f"Качаю {label.lower()}, страница {page} из {pages}"
                inside = min(1.0, page / max(pages, 1))
            else:
                text = f"Качаю {label.lower()}, страница {page}"
                inside = page / (page + 2)
            say(text, (base + step * inside) * 100)

        say(f"Начинаю раздел «{label}»", base * 100)
        try:
            for chunk in client.iter_pages(
                path,
                params=_server_params(section, options),
                per_page=config.PAGE_SIZE,
                on_page=on_page,
            ):
                rows.extend(chunk)
        except Stopped:
            stopped_here = True
            result.stopped = True
            log.info("Остановка по кнопке «Стоп» на разделе %s", key)
        except Exception as error:  # раздел упал — остальные продолжаем
            failed = True
            result.errors.append(_friendly_error(error, label))
            log.warning("Ошибка раздела %s: %s", key, error)

        rows = filter_rows_by_date(rows, options.date_from, options.date_to)

        extra: dict = {}
        if key == "offers":
            if options.price_filter == "with":
                rows = [row for row in rows if has_price(row)]
            elif options.price_filter == "without":
                rows = [row for row in rows if not has_price(row)]
            with_price = sum(1 for row in rows if has_price(row))
            extra = {
                "with_price": with_price,
                "without_price": len(rows) - with_price,
                "stores": count_stores(rows),
            }

        # Пустой файл пишем только если раздел отработал без ошибки:
        # иначе пустой csv выглядел бы так, будто данных и правда нет.
        csv_path: Path | None = None
        if rows or not failed:
            csv_path = out_dir / f"{key}.csv"
            try:
                write_csv(csv_path, rows)
            except OSError as error:
                result.errors.append(f"Не удалось сохранить файл раздела «{label}»")
                log.warning("Не записал %s: %s", csv_path, error)
                csv_path = None

        result.sections.append(
            SectionResult(key=key, label=label, count=len(rows), file=csv_path, extra=extra)
        )
        say(f"Готов раздел «{label}»", (base + step) * 100)

        if stopped_here:
            break

    title = period_phrase(options.period_kind, options.date_from, options.date_to)
    if options.price_filter == "with":
        title += " (только отклики с ценой)"
    elif options.price_filter == "without":
        title += " (только отклики без цены)"

    result.summary = build_summary(
        title,
        [
            {"key": item.key, "label": item.label, "count": item.count, "extra": item.extra}
            for item in result.sections
        ],
        result.errors,
    )
    if result.stopped:
        result.summary += "\n\nВыгрузка остановлена вами. Всё, что успели скачать, сохранено."

    try:
        (out_dir / "сводка.txt").write_text(result.summary + "\n", encoding="utf-8-sig")
    except OSError:
        pass

    say("Готово", 100.0)
    return result


def sections_by_keys(sections: Sequence[dict], keys: Sequence[str]) -> list[dict]:
    """Отобрать разделы по их ключам, сохранив порядок списка."""
    chosen = set(keys)
    return [section for section in sections if section.get("key") in chosen]
