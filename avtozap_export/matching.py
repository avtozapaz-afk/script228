"""Сопоставление текста: русское и английское написание, неточные названия.

Пользователь пишет «тойота королла», а в данных лежит «Toyota Corolla»;
название магазина может быть набрано с опечаткой или наполовину. Всё это
решается здесь, без обращения к языковой модели.
"""

from __future__ import annotations

import difflib
import json
import re
from typing import Any, Iterable, Sequence

# Кириллица в латиницу — грубо, зато одинаково для обеих сторон сравнения.
TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "i", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sh", "ъ": "",
    "ы": "i", "ь": "", "э": "e", "ю": "iu", "я": "ia",
}

# Буквы, которые в разных написаниях звучат одинаково: Corolla / Королла.
EQUIVALENT = {"c": "k", "q": "k", "w": "v", "y": "i", "j": "i"}


def loose(text: Any) -> str:
    """Привести текст к виду, в котором «Corolla» и «королла» совпадают."""
    if text is None:
        return ""
    lowered = str(text).lower()
    letters = []
    for char in lowered:
        letters.append(TRANSLIT.get(char, char))
    joined = "".join(letters)
    joined = joined.replace("ck", "k").replace("ph", "f").replace("sch", "sh")
    # Латинская «c» перед e, i, y читается как «с» (Mercedes), иначе как «к» (Corolla).
    joined = re.sub(r"c(?=[eiy])", "s", joined)
    joined = "".join(EQUIVALENT.get(char, char) for char in joined)
    joined = re.sub(r"[^a-z0-9]+", "", joined)
    return re.sub(r"(.)\1+", r"\1", joined)  # «королла» и «корола» — одно и то же


def loose_words(text: Any) -> list[str]:
    """Разбить запрос на слова и привести каждое к общему виду."""
    parts = re.split(r"[\s,;/]+", str(text or "").strip())
    return [word for word in (loose(part) for part in parts) if word]


VOWELS = set("aeiou")


def skeleton(text: Any) -> str:
    """Согласный «скелет» слова: «Хендай» и «Hyundai» дают одно и то же.

    Нужен только как запасной вариант, когда побуквенное сравнение не сошлось,
    поэтому применяется лишь к достаточно длинным словам.
    """
    letters = loose(text)
    if not letters:
        return ""
    return letters[0] + "".join(char for char in letters[1:] if char not in VOWELS)


def skeleton_text(text: Any) -> str:
    """«Скелеты» всех слов строки: по ним ищем, когда точное сравнение не сошлось."""
    return " ".join(skeleton(word) for word in re.split(r"[\s,;/]+", str(text or "")) if word)


def flatten(value: Any) -> str:
    """Значение ячейки в виде текста — для поиска по всей записи."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "да" if value else "нет"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def row_text(row: dict) -> str:
    """Вся запись одной строкой — чтобы искать, не зная нужной колонки."""
    return " ".join(flatten(value) for value in row.values())


def row_matches(row: dict, terms: Sequence[str], column: str | None = None, fuzzy: bool = True) -> bool:
    """Подходит ли запись под искомые слова.

    Совпадением считается любой из вариантов написания; если вариант состоит
    из нескольких слов, все они должны найтись в записи.
    """
    source = flatten(row.get(column)) if column else row_text(row)
    haystack = loose(source)
    if not haystack:
        return False
    for term in terms:
        words = loose_words(term)
        if words and all(word in haystack for word in words):
            return True
    if not fuzzy:
        return False
    # Побуквенно не сошлось — сравниваем «скелеты» длинных слов.
    bones = f" {skeleton_text(source)} "
    for term in terms:
        words = [word for word in loose_words(term) if len(word) >= 5]
        if words and all(f" {skeleton(word)} " in bones for word in words):
            return True
    return False


def filter_rows(
    rows: Iterable[dict], terms: Sequence[str], column: str | None = None, fuzzy: bool = True
) -> list[dict]:
    """Оставить записи, подходящие под искомые слова.

    Сначала строгий проход; если он не дал ничего, пробуем сравнение по
    «скелету» — так «Хендай» находит «Hyundai».
    """
    rows = list(rows)
    terms = [term for term in terms if str(term).strip()]
    if not terms:
        return rows
    strict = [row for row in rows if row_matches(row, terms, column, fuzzy=False)]
    if strict or not fuzzy:
        return strict
    return [row for row in rows if row_matches(row, terms, column, fuzzy=True)]


# ── имена и колонки ──────────────────────────────────────────────────────────

NAME_FIELDS = (
    "name", "title", "название", "имя", "store_name", "shop_name", "full_name",
    "company", "company_name", "login", "username", "email", "phone",
)


def display_name(row: dict, extra_fields: Sequence[str] = ()) -> str:
    """Как назвать запись в ответе человеку (не показывая служебных полей)."""
    lowered = {str(key).lower(): value for key, value in row.items()}
    for field in list(extra_fields) + list(NAME_FIELDS):
        if not field:
            continue
        value = lowered.get(str(field).lower())
        if value not in (None, "", [], {}):
            if isinstance(value, dict):
                inner = display_name(value)
                if inner:
                    return inner
                continue
            return flatten(value)
    for key in ("id", "uuid", "код"):
        if lowered.get(key) not in (None, ""):
            return f"№ {flatten(lowered[key])}"
    return "без названия"


def resolve_column(available: Sequence[str], wanted: str | None, hints: Sequence[str] = ()) -> str | None:
    """Найти настоящую колонку по названию, которое предложила модель.

    Точное совпадение, потом без учёта регистра, потом похожее по написанию,
    потом подсказки. Ничего не нашли — ``None`` (и мы честно об этом скажем).
    """
    if not available:
        return None
    candidates = [str(name) for name in available]
    for wish in [wanted, *hints]:
        if not wish:
            continue
        wish = str(wish)
        if wish in candidates:
            return wish
        lowered = {name.lower(): name for name in candidates}
        if wish.lower() in lowered:
            return lowered[wish.lower()]
        loosened = {loose(name): name for name in candidates}
        if loose(wish) in loosened:
            return loosened[loose(wish)]
        # «created» — это явно «created_at», если такая колонка одна.
        inside = [name for name in candidates if wish.lower() in name.lower()]
        if len(inside) == 1:
            return inside[0]
        close = difflib.get_close_matches(wish.lower(), list(lowered), n=1, cutoff=0.85)
        if close:
            return lowered[close[0]]
    return None


def similar_names(query: str, rows: Iterable[dict], limit: int = 5) -> list[str]:
    """Похожие названия — чтобы предложить их, когда точного совпадения нет."""
    names = []
    seen = set()
    for row in rows:
        name = display_name(row)
        if name and name != "без названия" and name not in seen:
            seen.add(name)
            names.append(name)
    if not names:
        return []
    target = loose(query)
    scored = sorted(
        names,
        key=lambda name: difflib.SequenceMatcher(None, target, loose(name)).ratio(),
        reverse=True,
    )
    good = [
        name
        for name in scored
        if difflib.SequenceMatcher(None, target, loose(name)).ratio() >= 0.45
        or target and target[:4] in loose(name)
    ]
    return (good or scored)[:limit]
