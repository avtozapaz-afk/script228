"""Чистая логика без сети и без окон: разбор ответов, даты, csv, сводка.

Здесь нет ничего, что ходит в интернет, поэтому эту часть легко проверять
тестами.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import re
from typing import Any, Iterable, Sequence

# ---------------------------------------------------------------- разбор ответа

# Ключи, под которыми сервер обычно кладёт список записей.
LIST_KEYS = (
    "data",
    "items",
    "results",
    "result",
    "rows",
    "list",
    "records",
    "content",
    "objects",
    "entries",
)

# Ключи с общим количеством записей.
TOTAL_KEYS = ("total", "total_count", "totalCount", "count", "total_items")

# Ключи с количеством страниц.
PAGES_KEYS = ("pages", "total_pages", "totalPages", "last_page", "page_count")


def extract_rows(payload: Any) -> list[dict]:
    """Достать список записей из ответа сервера.

    Понимает и голый список, и обёртки вида ``{"data": [...]}``,
    в том числе вложенные (``{"data": {"items": [...]}}``).
    """
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []

    for key in LIST_KEYS:
        if key in payload:
            value = payload[key]
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
            if isinstance(value, dict):
                nested = extract_rows(value)
                if nested:
                    return nested

    # Ничего знакомого — берём первый попавшийся список словарей.
    for value in payload.values():
        if isinstance(value, list) and value and all(isinstance(v, dict) for v in value):
            return list(value)
    return []


def looks_like_list_response(payload: Any) -> bool:
    """Похож ли ответ на список записей (пусть даже пустой)."""
    if isinstance(payload, list):
        return True
    if not isinstance(payload, dict):
        return False
    for key in LIST_KEYS:
        value = payload.get(key)
        if isinstance(value, list):
            return True
        if isinstance(value, dict) and looks_like_list_response(value):
            return True
    if any(key in payload for key in TOTAL_KEYS) and any(
        isinstance(value, list) for value in payload.values()
    ):
        return True
    return False


def _meta(payload: Any) -> dict:
    if not isinstance(payload, dict):
        return {}
    meta = {}
    for key in ("meta", "pagination", "pageable", "_meta"):
        inner = payload.get(key)
        if isinstance(inner, dict):
            meta.update(inner)
    merged = dict(payload)
    merged.update(meta)
    return merged


def total_count(payload: Any) -> int | None:
    """Общее количество записей, если сервер его сообщает."""
    merged = _meta(payload)
    for key in TOTAL_KEYS:
        value = merged.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int) and value >= 0:
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return None


def total_pages(payload: Any) -> int | None:
    """Количество страниц, если сервер его сообщает."""
    merged = _meta(payload)
    for key in PAGES_KEYS:
        value = merged.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int) and value >= 0:
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return None


def pages_estimate(payload: Any, per_page: int) -> int | None:
    """Сколько всего страниц придётся скачать (для полосы прогресса)."""
    pages = total_pages(payload)
    if pages:
        return pages
    total = total_count(payload)
    if total is not None and per_page > 0:
        return max(1, -(-total // per_page))
    return None


# ------------------------------------------------------------------- поля строки

DATE_FIELDS = (
    "created_at",
    "createdAt",
    "created",
    "created_date",
    "date_created",
    "creation_date",
    "registered_at",
    "date",
    "added_at",
    "insert_date",
    "timestamp",
)

PRICE_FIELDS = ("price", "offer_price", "price_amount", "cost", "amount", "sum", "total_price")

STORE_FIELDS = (
    "store_id",
    "storeId",
    "shop_id",
    "seller_id",
    "supplier_id",
    "merchant_id",
    "company_id",
)

STORE_OBJECT_FIELDS = ("store", "shop", "seller", "supplier", "merchant", "company")


def get_field(row: dict, names: Sequence[str]) -> Any:
    """Первое непустое значение из перечисленных полей (регистр не важен)."""
    lowered = {str(key).lower(): value for key, value in row.items()}
    for name in names:
        value = lowered.get(name.lower())
        if value not in (None, "", [], {}):
            return value
    return None


def find_date_field(rows: Iterable[dict]) -> str | None:
    """Найти в записях поле с датой создания."""
    for row in rows:
        lowered = {str(key).lower(): key for key in row}
        for name in DATE_FIELDS:
            key = lowered.get(name)
            if key is not None and parse_datetime(row[key]) is not None:
                return key
    return None


_DATE_PATTERNS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d",
    "%d.%m.%Y %H:%M:%S",
    "%d.%m.%Y %H:%M",
    "%d.%m.%Y",
    "%Y/%m/%d",
)


def parse_datetime(value: Any) -> dt.datetime | None:
    """Разобрать дату в любом из привычных видов. Не вышло — ``None``."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, dt.datetime):
        return value
    if isinstance(value, dt.date):
        return dt.datetime(value.year, value.month, value.day)
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 1e11:  # миллисекунды
            seconds /= 1000.0
        if seconds < 10_000_000:  # слишком мало для даты — это просто число
            return None
        try:
            return dt.datetime.fromtimestamp(seconds)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str):
        return None

    text = value.strip()
    if not text:
        return None
    if re.fullmatch(r"\d{10,13}", text):
        return parse_datetime(int(text))

    cleaned = text.replace("Z", "+00:00")
    cleaned = re.sub(r"(\.\d{3})\d+", r"\1", cleaned)
    try:
        parsed = dt.datetime.fromisoformat(cleaned)
    except ValueError:
        parsed = None
    if parsed is not None:
        return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed

    head = re.sub(r"[+-]\d{2}:?\d{2}$", "", text).strip()
    for pattern in _DATE_PATTERNS:
        try:
            return dt.datetime.strptime(head, pattern)
        except ValueError:
            continue
    return None


def row_in_range(row: dict, field: str | None, date_from: dt.date | None, date_to: dt.date | None) -> bool:
    """Попадает ли запись в выбранный период."""
    if date_from is None and date_to is None:
        return True
    if not field:
        return True
    parsed = parse_datetime(row.get(field))
    if parsed is None:
        return False
    day = parsed.date()
    if date_from is not None and day < date_from:
        return False
    if date_to is not None and day > date_to:
        return False
    return True


def filter_rows_by_date(
    rows: Sequence[dict], date_from: dt.date | None, date_to: dt.date | None
) -> list[dict]:
    """Отобрать записи за период. Нет поля с датой — отдаём всё как есть."""
    if date_from is None and date_to is None:
        return list(rows)
    field = find_date_field(rows)
    if not field:
        return list(rows)
    return [row for row in rows if row_in_range(row, field, date_from, date_to)]


def has_price(row: dict) -> bool:
    """Есть ли в отклике цена."""
    value = get_field(row, PRICE_FIELDS)
    if value is None:
        return False
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return value > 0
    text = str(value).strip().replace(",", ".").replace(" ", "")
    if not text:
        return False
    try:
        return float(re.sub(r"[^\d.\-]", "", text) or "0") > 0
    except ValueError:
        return True


def store_key(row: dict) -> str | None:
    """Кто именно ответил — идентификатор магазина в отклике."""
    value = get_field(row, STORE_FIELDS)
    if value is not None:
        return str(value)
    for name in STORE_OBJECT_FIELDS:
        obj = row.get(name)
        if isinstance(obj, dict):
            inner = get_field(obj, ("id", "uuid", "store_id", "name", "title"))
            if inner is not None:
                return f"{name}:{inner}"
        elif obj not in (None, "", [], {}):
            return f"{name}:{obj}"
    return None


def count_stores(rows: Iterable[dict]) -> int:
    """Сколько разных магазинов ответило."""
    return len({key for key in (store_key(row) for row in rows) if key})


# ------------------------------------------------------------------------- csv

def flatten_value(value: Any) -> str:
    """Превратить значение в текст для ячейки csv."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "да" if value else "нет"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def collect_columns(rows: Sequence[dict]) -> list[str]:
    """Названия колонок — как пришли от сервера, в порядке появления."""
    columns: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            name = str(key)
            if name not in seen:
                seen.add(name)
                columns.append(name)
    return columns


def write_csv(path, rows: Sequence[dict]) -> int:
    """Записать записи в csv (UTF-8 с BOM — чтобы открывался в Excel)."""
    columns = collect_columns(rows) or ["нет данных"]
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=";", quoting=csv.QUOTE_MINIMAL)
        writer.writerow(columns)
        for row in rows:
            writer.writerow([flatten_value(row.get(column)) for column in columns])
    return len(rows)


# --------------------------------------------------------------- даты по-русски

MONTHS_GENITIVE = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)


def day_phrase(day: dt.date) -> str:
    """«6 сентября»."""
    return f"{day.day} {MONTHS_GENITIVE[day.month - 1]}"


def period_phrase(kind: str, date_from: dt.date | None, date_to: dt.date | None) -> str:
    """Заголовок сводки: «За сегодня, 6 сентября»."""
    if date_from is None and date_to is None:
        return "За всё время"
    if kind == "today" and date_from:
        return f"За сегодня, {day_phrase(date_from)}"
    if kind == "yesterday" and date_from:
        return f"За вчера, {day_phrase(date_from)}"
    if date_from and date_to and date_from == date_to:
        return f"За {day_phrase(date_from)}"
    if date_from and date_to:
        return f"За период с {day_phrase(date_from)} по {day_phrase(date_to)}"
    if date_from:
        return f"Начиная с {day_phrase(date_from)}"
    return f"По {day_phrase(date_to)}" if date_to else "За всё время"


def plural(number: int, one: str, few: str, many: str) -> str:
    """«1 магазин», «2 магазина», «5 магазинов»."""
    tail_100 = abs(number) % 100
    tail_10 = abs(number) % 10
    if 11 <= tail_100 <= 14:
        return many
    if tail_10 == 1:
        return one
    if 2 <= tail_10 <= 4:
        return few
    return many


# ---------------------------------------------------------------------- сводка

def build_summary(period_title: str, sections: Sequence[dict], errors: Sequence[str] = ()) -> str:
    """Короткая сводка простым текстом.

    ``sections`` — список словарей ``{"key", "label", "count", "extra"}``,
    где ``extra`` для откликов содержит цены и число магазинов.
    """
    lines = [f"{period_title}:"]
    by_key = {section["key"]: section for section in sections}
    used: set[str] = set()

    users = by_key.get("users")
    if users:
        used.add("users")
        lines.append(f"Новых пользователей — {users['count']}")

    rfq = by_key.get("rfq")
    if rfq:
        used.add("rfq")
        lines.append(f"Создано заявок — {rfq['count']}")

    offers = by_key.get("offers")
    if offers:
        used.add("offers")
        extra = offers.get("extra") or {}
        line = f"Откликов от магазинов — {offers['count']}"
        if "with_price" in extra:
            line += f", из них с ценой {extra['with_price']}, без цены {extra['without_price']}"
        lines.append(line)
        if "stores" in extra:
            count = extra["stores"]
            word = plural(count, "магазин", "магазина", "магазинов")
            answered = plural(count, "Ответил", "Ответили", "Ответили")
            lines.append(f"{answered} {count} {word}")

    rest = [section for section in sections if section["key"] not in used]
    if rest:
        lines.append("")
        for section in rest:
            lines.append(f"{section['label']} — {section['count']}")

    if errors:
        lines.append("")
        lines.append("Не удалось выгрузить:")
        for message in errors:
            lines.append(f"— {message}")

    return "\n".join(lines)
