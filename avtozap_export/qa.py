"""Ответы на вопросы обычными словами.

Порядок работы: модель разбирает вопрос в план → программа сама берёт данные
и считает → модель складывает из посчитанного человеческую фразу.

Ни одно число в ответе не приходит от модели: всё считается здесь.
Данные наружу не уходят — в OpenAI отправляются только названия разделов
и колонок, а в конце небольшой набор уже посчитанных значений.
"""

from __future__ import annotations

import csv
import datetime as dt
import difflib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from . import config
from .ai import AiClient, AiError
from .api import AdminClient, Stopped
from .core import (
    day_phrase,
    filter_rows_by_date,
    find_date_field,
    parse_datetime,
    plural,
    write_csv,
)
from .matching import display_name, filter_rows, loose, resolve_column, similar_names

MANIFEST_NAME = "выгрузка.json"

# Сколько минут держать скачанное в памяти, чтобы не дёргать админку
# на каждый вопрос — и при этом не отвечать по устаревшим данным.
CACHE_MINUTES = 10

# Поля, по которым видно последнюю активность.
ACTIVITY_FIELDS = (
    "last_active_at", "last_activity_at", "last_seen_at", "last_seen", "last_login_at",
    "last_login", "active_at", "updated_at", "created_at",
)

PERIOD_WORDS = {
    "сегодня": "за сегодня",
    "вчера": "за вчера",
    "неделя": "за неделю",
    "месяц": "за месяц",
}


@dataclass
class Answer:
    """Готовый ответ пользователю."""

    text: str
    ok: bool = True
    facts: dict = field(default_factory=dict)
    table_path: Path | None = None
    table_rows: int = 0


class Unclear(Exception):
    """Вопрос понят не до конца — надо переспросить у человека."""


# ── откуда берём данные ──────────────────────────────────────────────────────

def read_csv_rows(path: Path) -> list[dict]:
    """Прочитать csv, который программа сама же и записала."""
    with open(path, encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle, delimiter=";")]


def save_manifest(out_dir: Path, entries: list[dict]) -> None:
    """Запомнить, что и за какой период уже выгружено в эту папку."""
    payload = {"дата": dt.date.today().isoformat(), "разделы": entries}
    try:
        (out_dir / MANIFEST_NAME).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        pass


def load_manifest(out_dir: Path) -> list[dict]:
    """Что лежит в сегодняшней папке выгрузки."""
    path = out_dir / MANIFEST_NAME
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []
    if not isinstance(payload, dict) or payload.get("дата") != dt.date.today().isoformat():
        return []  # вчерашняя выгрузка уже не свежая
    entries = payload.get("разделы")
    return entries if isinstance(entries, list) else []


def _covers(entry: dict, date_from: dt.date | None, date_to: dt.date | None) -> bool:
    """Покрывает ли уже выгруженный файл нужный период целиком."""
    if entry.get("фильтр_цены", "all") != "all":
        return False  # файл урезан фильтром — считать по нему нельзя
    covered_from = entry.get("с")
    covered_to = entry.get("по")
    if covered_from and (date_from is None or dt.date.fromisoformat(covered_from) > date_from):
        return False
    if covered_to and (date_to is None or dt.date.fromisoformat(covered_to) < date_to):
        return False
    return True


class DataStore:
    """Данные разделов: из свежей выгрузки, иначе из админки. С запоминанием."""

    def __init__(
        self,
        client: AdminClient,
        sections: Sequence[dict],
        *,
        out_dir: Path | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.client = client
        self.sections = {section["key"]: section for section in sections}
        self.out_dir = out_dir or (config.DATA_DIR / dt.date.today().isoformat())
        self.log = logger or logging.getLogger("avtozap")
        # ключ раздела -> (записи, начало покрытого периода, конец, когда скачано)
        self._cache: dict[str, tuple[list[dict], dt.date | None, dt.date | None, dt.datetime]] = {}

    def attach(self, client: AdminClient, out_dir: Path | None = None) -> None:
        """Продолжить работу с новым подключением, не теряя скачанное."""
        self.client = client
        if out_dir is not None:
            self.out_dir = out_dir

    def section(self, key: str) -> dict | None:
        return self.sections.get(key)

    def rows(self, key: str, date_from: dt.date | None, date_to: dt.date | None) -> list[dict]:
        """Записи раздела за период."""
        section = self.sections.get(key)
        if section is None:
            raise Unclear(f"Не нашёл раздел «{key}»")

        cached = self._cache.get(key)
        if cached and (dt.datetime.now() - cached[3]).total_seconds() > CACHE_MINUTES * 60:
            cached = None  # полежало достаточно, перечитаем
        if cached and _covers(
            {"с": cached[1].isoformat() if cached[1] else None,
             "по": cached[2].isoformat() if cached[2] else None},
            date_from, date_to,
        ):
            return filter_rows_by_date(cached[0], date_from, date_to)

        rows = self._from_files(key, date_from, date_to)
        covered_from, covered_to = date_from, date_to
        if rows is None:
            rows = self._from_admin(section, date_from, date_to)
            if not (section.get("date_params") and (date_from or date_to)):
                covered_from = covered_to = None  # скачали раздел целиком
        self._cache[key] = (rows, covered_from, covered_to, dt.datetime.now())
        return filter_rows_by_date(rows, date_from, date_to)

    def _from_files(self, key: str, date_from, date_to) -> list[dict] | None:
        """Взять из сегодняшней выгрузки, если она покрывает нужный период."""
        for entry in load_manifest(self.out_dir):
            if entry.get("ключ") != key or not _covers(entry, date_from, date_to):
                continue
            path = self.out_dir / str(entry.get("файл") or f"{key}.csv")
            if not path.exists():
                continue
            try:
                rows = read_csv_rows(path)
            except (OSError, ValueError):
                return None
            self.log.info("Раздел %s взят из сегодняшней выгрузки", key)
            return rows
        return None

    def _from_admin(self, section: dict, date_from, date_to) -> list[dict]:
        """Скачать раздел из админки."""
        params: dict[str, str] = {}
        date_params = section.get("date_params")
        if date_params and len(date_params) == 2 and (date_from or date_to):
            if date_from:
                params[date_params[0]] = date_from.isoformat()
            if date_to:
                params[date_params[1]] = date_to.isoformat()
        self.log.info("Раздел %s читается из админки", section["key"])
        return self.client.fetch_all(section["path"], params=params)

    def columns(self, key: str) -> list[str]:
        """Названия колонок раздела — нужны только для разбора вопроса."""
        section = self.sections.get(key, {})
        stored = section.get("columns")
        if stored:
            return list(stored)
        status, payload = self.client.probe(section.get("path", ""), {"page": 1, "per_page": 1})
        columns: list[str] = []
        if status == 200:
            from .core import extract_rows

            rows = extract_rows(payload)
            if rows:
                columns = [str(name) for name in rows[0]]
        section["columns"] = columns
        return columns


def build_catalog(store: DataStore) -> str:
    """Список разделов и колонок для модели. Самих данных здесь нет."""
    lines = []
    for key, section in store.sections.items():
        columns = store.columns(key)[:40]
        lines.append(f"- {key} ({section.get('label', key)}): {', '.join(columns) or 'нет данных'}")
    return "\n".join(lines)


# ── период ───────────────────────────────────────────────────────────────────

def period_from_plan(plan: dict, today: dt.date | None = None) -> tuple[dt.date | None, dt.date | None, str]:
    """Границы периода. Даты считает программа, а не модель."""
    today = today or dt.date.today()
    period = plan.get("период") or {}
    kind = str(period.get("вид") or "всё").strip().lower()

    if kind == "сегодня":
        return today, today, f"за сегодня, {day_phrase(today)}"
    if kind == "вчера":
        day = today - dt.timedelta(days=1)
        return day, day, f"за вчера, {day_phrase(day)}"
    if kind == "неделя":
        start = today - dt.timedelta(days=6)
        return start, today, "за неделю"
    if kind == "месяц":
        start = today - dt.timedelta(days=29)
        return start, today, "за месяц"
    if kind == "диапазон":
        start = _as_date(period.get("с"))
        end = _as_date(period.get("по"))
        if start is None and end is None:
            raise Unclear("Не понял, за какой период — уточните, пожалуйста")
        if start and end and start > end:
            start, end = end, start
        pieces = []
        if start:
            pieces.append(f"с {day_phrase(start)}")
        if end:
            pieces.append(f"по {day_phrase(end)}")
        return start, end, " ".join(pieces)
    return None, None, "за всё время"


def _as_date(value) -> dt.date | None:
    parsed = parse_datetime(value)
    return parsed.date() if parsed else None


# ── операции: считает программа ──────────────────────────────────────────────

def _search_terms(plan: dict) -> list[str]:
    """Что ищем: и то, как написал человек, и варианты от модели."""
    search = plan.get("поиск") or {}
    terms = []
    text = search.get("текст")
    if text:
        terms.append(str(text))
    for variant in search.get("варианты") or []:
        if variant and str(variant) not in terms:
            terms.append(str(variant))
    return terms


@dataclass
class Selection:
    """Что нашлось по вопросу и где предлагать похожее, если не нашлось."""

    rows: list[dict]           # все записи раздела за период
    found: list[dict]          # из них подходящие под поиск
    suggest_from: list[dict]   # среди чего искать похожие названия
    known: bool = True         # само название нашлось (пусть записей по нему и нет)


def _id_column(store: DataStore, key: str) -> str | None:
    return resolve_column(store.columns(key), "id", hints=("uuid", f"{key}_id"))


def _link_sections(store: DataStore, key: str) -> list[tuple[str, str]]:
    """Разделы, на которые ссылается этот: («колонка», «ключ раздела»).

    Из колонки ``store_id`` получается раздел ``stores`` — так «отклики»
    находят «магазины», в которых лежит название.
    """
    links = []
    for column in store.columns(key):
        name = str(column)
        if not name.lower().endswith("_id"):
            continue
        stem = name[:-3].lower()
        for candidate in (stem + "s", stem, stem + "es"):
            if candidate in store.sections and candidate != key:
                links.append((name, candidate))
                break
    return links


def _rows_by_link(
    store: DataStore, rows: list[dict], link_column: str, lookup_key: str, terms: Sequence[str]
) -> tuple[list[dict], list[dict]]:
    """Найти записи по названию из связанного раздела (магазина, марки и т.п.)."""
    lookup_rows = store.rows(lookup_key, None, None)
    matched = filter_rows(lookup_rows, terms)
    if not matched:
        return [], lookup_rows
    id_column = _id_column(store, lookup_key)
    ids = {str(row.get(id_column)) for row in matched if row.get(id_column) not in (None, "")}
    if not ids:
        return [], lookup_rows
    found = []
    for row in rows:
        value = row.get(link_column)
        if isinstance(value, dict):
            value = value.get("id")
        if value not in (None, "") and str(value) in ids:
            found.append(row)
    return found, lookup_rows


def _pick_rows(store: DataStore, key: str, plan: dict, date_from, date_to) -> Selection:
    """Записи раздела за период и они же после поиска по названию."""
    rows = store.rows(key, date_from, date_to)
    terms = _search_terms(plan)
    if not terms:
        return Selection(rows, rows, rows)

    search = plan.get("поиск") or {}
    columns = store.columns(key)

    # 1. Модель прямо указала, в каком разделе искать название.
    lookup_key = search.get("раздел")
    if lookup_key and lookup_key in store.sections and lookup_key != key:
        link_column = resolve_column(columns, search.get("ключ"), hints=(f"{lookup_key[:-1]}_id",))
        if link_column:
            found, lookup_rows = _rows_by_link(store, rows, link_column, lookup_key, terms)
            if found:
                return Selection(rows, found, lookup_rows)
            if not filter_rows(lookup_rows, terms):
                # Название искали там, где ему и место, но такого там нет —
                # похожее предлагаем из того же раздела.
                return Selection(rows, [], lookup_rows, known=False)

    # 2. Обычный поиск по тексту самих записей.
    column = resolve_column(columns, search.get("колонка"))
    found = filter_rows(rows, terms, column)
    if found:
        return Selection(rows, found, rows)

    # 3. Не нашли — заглянем в связанные разделы: имя магазина лежит там.
    looked: list[list[dict]] = []
    for link_column, candidate in _link_sections(store, key):
        found, lookup_rows = _rows_by_link(store, rows, link_column, candidate, terms)
        if found:
            return Selection(rows, found, lookup_rows)
        if filter_rows(lookup_rows, terms):
            # Название нашлось, а записей по нему нет — это честный ноль, а не опечатка.
            return Selection(rows, [], lookup_rows, known=True)
        looked.append(lookup_rows)

    # Похожие предлагаем оттуда, где нашлось самое близкое название.
    query = str(search.get("текст") or "")
    best = max(
        [rows_set for rows_set in looked if rows_set] or [rows],
        key=lambda candidate_rows: _best_similarity(query, candidate_rows),
    )
    return Selection(rows, [], best, known=False)


def _best_similarity(query: str, rows: Sequence[dict]) -> float:
    """Насколько близко самое похожее название в этих записях."""
    target = loose(query)
    if not target:
        return 0.0
    return max(
        (difflib.SequenceMatcher(None, target, loose(display_name(row))).ratio() for row in rows),
        default=0.0,
    )


def _no_match(rows: Sequence[dict], plan: dict, period: str = "") -> Answer:
    """Ничего не нашли — честно скажем и предложим похожее."""
    query = (plan.get("поиск") or {}).get("текст") or ""
    similar = similar_names(str(query), rows)
    if similar:
        return Answer(
            f"Не нашёл «{query}». Вот похожие: " + ", ".join(similar) + ".",
            ok=False,
        )
    return Answer(f"Не нашёл «{query}» — проверьте написание.", ok=False)


def _latest_date(rows: Sequence[dict]) -> tuple[dt.datetime | None, str | None]:
    """Самая свежая дата среди записей и поле, по которому её нашли."""
    lowered = {}
    for row in rows:
        for key in row:
            lowered.setdefault(str(key).lower(), str(key))
    for name in ACTIVITY_FIELDS:
        column = lowered.get(name)
        if not column:
            continue
        dates = [parse_datetime(row.get(column)) for row in rows]
        dates = [value for value in dates if value]
        if dates:
            return max(dates), column
    column = find_date_field(rows)
    if column:
        dates = [parse_datetime(row.get(column)) for row in rows]
        dates = [value for value in dates if value]
        if dates:
            return max(dates), column
    return None, None


def _group_counts(rows: Sequence[dict], column: str, label_column: str | None) -> list[dict]:
    """Посчитать записи по группам и назвать каждую по-человечески."""
    counts: dict[str, int] = {}
    names: dict[str, str] = {}
    for row in rows:
        value = row.get(column)
        if isinstance(value, dict):
            key = str(display_name(value))
        else:
            key = "" if value is None else str(value)
        if not key:
            continue
        counts[key] = counts.get(key, 0) + 1
        if key not in names:
            names[key] = display_name(row, (label_column,) if label_column else ())
    ordered = sorted(counts.items(), key=lambda item: (-item[1], names.get(item[0], "")))
    return [{"название": names.get(key, key), "сколько": count} for key, count in ordered]


def _keys_in(rows: Sequence[dict], column: str) -> set[str]:
    values = set()
    for row in rows:
        value = row.get(column)
        if isinstance(value, dict):
            value = value.get("id")
        if value not in (None, ""):
            values.add(str(value))
    return values


# ── сборка ответа ────────────────────────────────────────────────────────────

def local_phrase(facts: dict) -> str:
    """Запасная фраза, если OpenAI не ответил: та же правда, попроще словами."""
    kind = facts.get("что")
    period = facts.get("период", "")
    if kind == "количество":
        return f"{facts.get('о_чём', 'Записей')} {period}: {facts.get('количество', 0)}."
    if kind == "последняя_дата":
        if facts.get("нет_данных"):
            return str(facts["нет_данных"])
        return (
            f"Последняя запись — {facts.get('дата')}"
            + (f" ({facts['дней_назад']} дн. назад)." if facts.get("дней_назад") is not None else ".")
        )
    if kind == "список":
        return f"{facts.get('о_чём', 'Записей')} {period}: {facts.get('количество', 0)}."
    if kind == "топ":
        lines = [f"{facts.get('о_чём', 'Топ')} {period}:"]
        for number, item in enumerate(facts.get("строки", []), start=1):
            lines.append(f"{number}. {item['название']} — {item['сколько']}")
        return "\n".join(lines)
    if kind == "без_пары":
        return f"{facts.get('о_чём', 'Записей без пары')} {period}: {facts.get('количество', 0)}."
    return str(facts.get("текст", "Готово."))


def _write_table(out_dir: Path, name: str, rows: Sequence[dict]) -> Path:
    """Сохранить таблицу ответа в csv рядом с остальными выгрузками."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{name}.csv"
    number = 2
    while path.exists():
        path = out_dir / f"{name}-{number}.csv"
        number += 1
    write_csv(path, list(rows))
    return path


def answer_question(
    question: str,
    store: DataStore,
    ai: AiClient,
    *,
    today: dt.date | None = None,
    logger: logging.Logger | None = None,
) -> Answer:
    """Разобрать вопрос, посчитать по настоящим данным и ответить по-русски."""
    log = logger or logging.getLogger("avtozap")
    today = today or dt.date.today()

    plan = ai.plan(question, build_catalog(store), day_phrase(today) + f" {today.year} года")
    log.info("Разобран вопрос, операция: %s", plan.get("операция"))

    if not plan.get("понятно", True):
        text = str(plan.get("уточнение") or "").strip()
        return Answer(text or "Не понял вопрос — попробуйте сказать иначе.", ok=False)

    try:
        return _run_plan(question, plan, store, ai, today, log)
    except Unclear as error:
        return Answer(str(error), ok=False)


def _run_plan(question, plan, store: DataStore, ai: AiClient, today, log) -> Answer:
    key = _resolve_section(store, plan.get("раздел"))
    section = store.section(key)
    label = section.get("label", key)
    date_from, date_to, period_text = period_from_plan(plan, today)
    operation = str(plan.get("операция") or "посчитать").strip().lower()

    facts: dict = {"период": period_text, "о_чём": label}
    table: list[dict] = []
    table_name = key

    if operation == "последняя_дата":
        chosen = _pick_rows(store, key, plan, None, None)
        if _search_terms(plan) and not chosen.found and not chosen.known:
            return _no_match(chosen.suggest_from, plan)
        latest, _column = _latest_date(chosen.found)
        who = (plan.get("поиск") or {}).get("текст")
        facts["что"] = "последняя_дата"
        facts["кого_искали"] = who or label
        if latest is None:
            facts["нет_данных"] = (
                f"В админке нет данных о последней активности: {who or label}."
                if who else f"В админке нет дат в разделе «{label}»."
            )
        else:
            facts["дата"] = f"{day_phrase(latest.date())} {latest.year} года"
            facts["дней_назад"] = (today - latest.date()).days

    elif operation == "топ":
        chosen = _pick_rows(store, key, plan, date_from, date_to)
        column = resolve_column(store.columns(key), plan.get("группировка"))
        if not column:
            raise Unclear(
                f"Не понял, по чему считать топ в разделе «{label}» — уточните, пожалуйста."
            )
        label_column = resolve_column(store.columns(key), plan.get("подпись"))
        limit = _as_limit(plan.get("предел"), default=10)
        counted = _group_counts(chosen.found, column, label_column)
        facts["что"] = "топ"
        facts["строки"] = counted[:limit]
        facts["всего_участников"] = len(counted)
        table = [{"Название": item["название"], "Сколько": item["сколько"]} for item in counted]
        table_name = f"топ-{key}"

    elif operation == "без_пары":
        key_2 = _resolve_section(store, plan.get("раздел_2"))
        label_2 = store.section(key_2).get("label", key_2)
        link_column = resolve_column(
            store.columns(key_2), plan.get("ключ"), hints=(f"{key}_id", f"{key[:-1]}_id")
        )
        own_column = resolve_column(store.columns(key), plan.get("ключ_2"), hints=("id", "uuid"))
        if not link_column or not own_column:
            raise Unclear(
                f"Не понял, как связаны «{label}» и «{label_2}» — попробуйте спросить иначе."
            )
        applies_to_own = bool(plan.get("период_к_разделу"))
        own_rows = store.rows(key, date_from if applies_to_own else None, date_to if applies_to_own else None)
        own_rows = filter_rows(own_rows, _search_terms(plan))
        linked = _keys_in(store.rows(key_2, date_from, date_to), link_column)
        found = [
            row for row in own_rows
            if str(row.get(own_column, "")) and str(row.get(own_column)) not in linked
        ]
        facts["что"] = "без_пары"
        facts["о_чём"] = label
        facts["количество"] = len(found)
        facts["примеры"] = [display_name(row) for row in found[:5]]
        table = found
        table_name = f"{key}-без-{key_2}"

    elif operation == "список":
        chosen = _pick_rows(store, key, plan, date_from, date_to)
        if _search_terms(plan) and not chosen.found and not chosen.known:
            return _no_match(chosen.suggest_from, plan)
        found = chosen.found
        limit = _as_limit(plan.get("предел"), default=0)
        if limit:
            found = found[:limit]
        facts["что"] = "список"
        facts["количество"] = len(found)
        facts["примеры"] = [display_name(row) for row in found[:5]]
        table = found

    else:  # посчитать
        chosen = _pick_rows(store, key, plan, date_from, date_to)
        if _search_terms(plan) and not chosen.found and not chosen.known:
            return _no_match(chosen.suggest_from, plan)
        facts["что"] = "количество"
        facts["количество"] = len(chosen.found)
        if _search_terms(plan):
            facts["искали"] = (plan.get("поиск") or {}).get("текст")

    text = _phrase(question, facts, ai, log)

    answer = Answer(text=text, facts=facts)
    if table and (plan.get("в_файл") or operation in ("список", "без_пары", "топ")):
        path = _write_table(store.out_dir, table_name, table)
        answer.table_path = path
        answer.table_rows = len(table)
        word = plural(len(table), "строка", "строки", "строк")
        answer.text += f"\n\nСохранил в файл ({len(table)} {word}):\n{path}"
    return answer


def _phrase(question: str, facts: dict, ai: AiClient, log) -> str:
    """Фраза от модели, а если не вышло — своя, из тех же самых цифр."""
    try:
        text = ai.phrase(question, facts)
        if text:
            return text
    except AiError as error:
        log.info("Фразу сложил сам: %s", error)
    except Stopped:
        raise
    return local_phrase(facts)


def _resolve_section(store: DataStore, wanted) -> str:
    """Найти раздел, названный моделью, среди настоящих."""
    if wanted:
        name = str(wanted)
        if name in store.sections:
            return name
        for key, section in store.sections.items():
            if name.lower() in (key.lower(), str(section.get("label", "")).lower()):
                return key
        column = resolve_column(list(store.sections), name)
        if column:
            return column
    available = ", ".join(section.get("label", key) for key, section in store.sections.items())
    raise Unclear(f"Не понял, где искать. Могу посмотреть здесь: {available}.")


def _as_limit(value, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default
