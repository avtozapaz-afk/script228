"""Словарь AVTOZAP на 581 деталь — единственный источник истины по кодам.

Источник: ``data/AVTOZAP_slovar_FINAL_571.xlsx`` — тот самый файл, который
загружает поставленный движок Retriever V2. Загружается он один раз, здесь же,
и переиспользуется всеми слоями, чтобы книга Excel не читалась дважды.

Версия review27 от 01.09.2026: 581 деталь. К исходным 571 добавлены 10
отдельных физических позиций, подтверждённых ручной перепроверкой etalon-200.
Правки перечислены в data/review27_changes.json.
"""

from __future__ import annotations

import os
import sys
import json
from dataclasses import dataclass, field
from functools import lru_cache

DUPLICATE_CODES_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "duplicate_codes.json")

VENDOR_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "vendor", "retriever_v2")


#: Согласованные контекстные правила, ещё не вошедшие в поставку словаря.
LOCAL_CONTEXT_RULES_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "context_rules_local.json")

_local_rules_applied = False


def _ensure_vendor_on_path() -> None:
    if VENDOR_DIR not in sys.path:
        sys.path.insert(0, VENDOR_DIR)
    _apply_local_context_rules()


def _apply_local_context_rules() -> int:
    """Подать согласованные правила в ``CONTEXT_RULES`` проекта.

    Почему так, а не правкой вендорного файла. ``CONTEXT_RULES`` — это данные
    проекта, и добавить правило можно, ничего не переписывая: список
    расширяется до того, как ``AmbiguityLayer`` построит из него свой индекс, а
    файл проекта остаётся байт в байт. Тест целостности вендора продолжает
    работать и продолжает что-то значить.

    Момент вызова важен: движок создаёт ``AmbiguityLayer`` на импорте модуля,
    поэтому расширять список надо ДО первого импорта движка — то есть здесь,
    сразу после добавления пути.

    Возвращает число добавленных правил; повторные вызовы ничего не делают.
    """
    global _local_rules_applied
    if _local_rules_applied:
        return 0
    _local_rules_applied = True
    if not os.path.exists(LOCAL_CONTEXT_RULES_PATH):
        return 0
    with open(LOCAL_CONTEXT_RULES_PATH, encoding="utf-8") as fh:
        rules = json.load(fh).get("rules", [])
    if not rules:
        return 0
    import avtozap_ambiguity_layer as vendor_layer
    added = 0
    for rule in rules:
        entry = (rule["stem"], list(rule["qualifiers"]), rule["code"])
        if entry not in vendor_layer.CONTEXT_RULES:
            vendor_layer.CONTEXT_RULES.append(entry)
            added += 1
    return added


@dataclass(frozen=True)
class Part:
    external_code: str
    name_az: str
    name_ru: str
    category: str            # имя листа, напр. «Помпа/термостат»
    category_slug: str
    synonyms: tuple[str, ...] = ()
    requires_side: bool = False
    requires_direction: bool = False
    requires_location: bool = False

    @property
    def display(self) -> str:
        return f"{self.external_code} | {self.name_ru} | {self.name_az}"


@dataclass
class Dictionary:
    """Детали по коду плюс нормализованный индекс терминов из словаря."""

    parts: dict[str, Part] = field(default_factory=dict)
    # нормализованный термин -> коды деталей (терминов ~5690)
    term_index: dict[str, set[str]] = field(default_factory=dict)
    # код-дубль -> канонический код, на который переезжают карточки
    duplicates: dict[str, str] = field(default_factory=dict)

    def canonical(self, code: str | None) -> str | None:
        """Канонический код детали.

        В словаре есть подтверждённые дубли (RG-04 и MU-089 — один и тот же
        воздушный патрубок). Наружу конвейер обязан отдавать только тот код,
        который останется, иначе ответ протухнет вместе с дублем.
        """
        if not code:
            return code
        return self.duplicates.get(code, code)

    def __contains__(self, code: object) -> bool:
        return code in self.parts

    def __len__(self) -> int:
        return len(self.parts)

    def get(self, code: str) -> Part | None:
        return self.parts.get(code)

    def exact_codes(self, normalized_phrase: str) -> list[str]:
        """Коды, у которых есть ровно такой нормализованный термин."""
        return sorted(self.term_index.get(normalized_phrase, ()))

    def vocabulary(self, code: str) -> set[str]:
        """Все слова, которыми словарь описывает деталь.

        Нужна валидатору, чтобы отличить запрошенный объект от родителя/соседа.
        """
        part = self.parts.get(code)
        if part is None:
            return set()
        words: set[str] = set()
        for text in (part.name_az, part.name_ru, part.category, *part.synonyms):
            words |= set(normalize(text).split())
        return words


def normalize(text: str) -> str:
    """Нормализация ровно та же, что у поставленного Retriever V2 (``na``)."""
    _ensure_vendor_on_path()
    from avtozap_ambiguity_layer import na
    return na(text)


@lru_cache(maxsize=1)
def load() -> Dictionary:
    """Загрузить словарь один раз за процесс."""
    _ensure_vendor_on_path()
    import avtozap_matcher_engine as legacy

    def flag(value: object) -> bool:
        return str(value).strip().lower() in {"yes", "true", "1"}

    def synonyms_of(row: dict) -> tuple[str, ...]:
        raw = row.get("synonyms") or ""
        return tuple(s.strip() for s in str(raw).split(";") if s.strip())

    parts: dict[str, Part] = {}
    for code, row in legacy.PARTS.items():
        parts[code] = Part(
            external_code=code,
            name_az=str(row.get("name_az") or "").strip(),
            name_ru=str(row.get("name_ru") or "").strip(),
            category=str(row.get("category") or "").strip(),
            category_slug=str(row.get("category_slug") or "").strip(),
            synonyms=synonyms_of(row),
            requires_side=flag(row.get("requires_side")),
            requires_direction=flag(row.get("requires_direction")),
            requires_location=flag(row.get("requires_location")),
        )
    term_index = {term: set(codes) for term, codes in legacy.IDX.items()}
    return Dictionary(parts=parts, term_index=term_index,
                      duplicates=load_duplicates())


def load_duplicates(path: str = DUPLICATE_CODES_PATH) -> dict[str, str]:
    """Карта «код-дубль → канонический код»."""
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return dict(json.load(fh).get("codes") or {})
