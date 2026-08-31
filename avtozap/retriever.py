"""Retriever V2 — адаптер над поставленной реализацией проекта.

Алгоритм поиска здесь НЕ переписан: за него отвечает
``vendor/retriever_v2/avtozap_retriever_v2.py`` — код проекта, перенесённый в
репозиторий как есть (правились только пути к файлам). Этот модуль:

* приводит его вывод к типам конвейера (``Candidate`` / ``RetrieverResult``),
  добавляя из словаря 541 имена, категорию и синонимы;
* даёт слоям выше три справки, которые им нужны от словаря:
  ``head_codes`` (точная «голова» предмета), ``is_exact_key`` и
  ``lexical_support`` (есть ли у выбранной детали опора в тексте запроса).

Дополнительный проход по именам листьев словаря здесь был и был удалён: на
реальном fresh-300 он не изменил ни полноту, ни ранги (см. README_TEST.md,
раздел о согласовании с поставленным V2). Держать код, который ничего не даёт,
дороже, чем удалить его.

Кандидатом может стать только реальный код словаря: список кодов приходит от
поставленного ретривера, а метаданные — из словаря 541.
"""

from __future__ import annotations

from .config import RETRIEVER_LIMIT as DEFAULT_LIMIT
from .dictionary import Dictionary, Part, _ensure_vendor_on_path, load, normalize
from .types import Candidate, RetrieverResult


def _vendor_retriever():
    _ensure_vendor_on_path()
    from avtozap_retriever_v2 import RetrieverV2 as _Vendor
    return _Vendor


class RetrieverV2:
    def __init__(self, limit: int = DEFAULT_LIMIT,
                 dictionary: Dictionary | None = None):
        self.dict = dictionary or load()
        self.limit = limit
        self._vendor = _vendor_retriever()()
        self._known_words = {tok for term in self.dict.term_index
                             for tok in term.split() if len(tok) >= 2}

    # Совместимость с прежним конструктором конвейера.
    @classmethod
    def from_file(cls, path: str | None = None, limit: int = DEFAULT_LIMIT,
                  **_ignored) -> "RetrieverV2":
        return cls(limit=limit)

    # ── публичный API ───────────────────────────────────────────────────────
    def retrieve(self, item_raw: str, search_phrases: list[str] | None = None,
                 extra_hints: list[str] | None = None) -> RetrieverResult:
        """Кандидаты по атомарному предмету.

        ``search_phrases`` — подсказки сегментера (Layer 0), ``extra_hints`` —
        слова со слоя фото. И то и другое лишь расширяет поиск: итоговые коды
        всё равно приходят только от ретривера и только из словаря.
        """
        queries = [item_raw]
        for extra in list(search_phrases or []) + list(extra_hints or []):
            if extra and extra not in queries:
                queries.append(extra)

        best: dict[str, tuple[float, str]] = {}
        used: list[str] = []
        for query in queries:
            if not normalize(query):
                continue
            used.append(query)
            for hit in self._vendor.retrieve_codes(query, limit=self.limit):
                current = best.get(hit.code)
                if current is None or hit.score > current[0]:
                    best[hit.code] = (hit.score, hit.reason)

        if not used:
            return RetrieverResult(status="EMPTY", query_keys=[],
                                   reason="пустой запрос после нормализации")

        candidates: list[Candidate] = []
        for code, (score, reason) in best.items():
            part = self.dict.get(code)
            if part is None:
                # Ретривер не может выдать код вне словаря, но если это
                # случится — молча пропускаем, а не выдумываем деталь.
                continue
            candidates.append(_candidate(part, score, reason))

        candidates.sort(key=lambda c: (-c.score, c.external_code))
        candidates = candidates[: self.limit]
        if not candidates:
            return RetrieverResult(status="EMPTY", query_keys=used,
                                   reason="ретривер не нашёл кандидатов в словаре")
        return RetrieverResult(candidates=candidates, status="OK", query_keys=used,
                               reason=f"{len(candidates)} кандидат(ов)")

    def head_codes(self, item_raw: str) -> list[str]:
        """Точная «голова» предмета: только полное совпадение термина словаря.

        Используется Layer 0 (слияние одной детали в разных позициях) и
        OEM-резолвером (сравнение объекта по тексту с объектом по номеру) —
        ещё до работы ретривера. Никаких догадок: либо термин есть, либо нет.
        """
        for key in _query_keys(item_raw):
            codes = self.dict.exact_codes(key)
            if codes:
                return codes
        return []

    def is_known_word(self, word: str) -> bool:
        """Знает ли словарь такое слово хотя бы в одном термине.

        Layer 0 спрашивает это перед тем, как вырезать токен как марку авто:
        слово из словаря запчастей не вырезается никогда.
        """
        key = normalize(word)
        return bool(key) and key in self._known_words

    def is_exact_key(self, phrase: str) -> bool:
        """Есть ли такой точный термин в словаре."""
        key = normalize(phrase)
        return bool(key) and bool(self.dict.exact_codes(key))

    def lexical_support(self, item_raw: str, code: str) -> bool:
        """Есть ли у выбранной детали опора в словах запроса.

        True, если хотя бы одно значимое слово запроса встречается среди имён,
        категории или синонимов детали — точно, с опечаткой или с поправкой на
        азербайджанские окончания. Валидатор использует это против «выбрал
        родителя или соседа вместо запрошенной детали».
        """
        vocabulary = self.dict.vocabulary(code)
        if not vocabulary:
            return False
        for token in content_tokens(item_raw):
            for word in vocabulary:
                if _tokens_match(token, word):
                    return True
        return False


def _candidate(part: Part, score: float, reason: str) -> Candidate:
    return Candidate(
        external_code=part.external_code,
        name_ru=part.name_ru,
        name_az=part.name_az,
        category=part.category,
        synonyms=list(part.synonyms[:12]),
        score=round(float(score), 4),
        reason=reason[:300],
    )


# ── работа со словами запроса ───────────────────────────────────────────────
#: Слова, которые описывают не деталь, а её положение, количество или вежливость.
MODIFIER_WORDS = {
    "sol", "sag", "qabaq", "on", "arxa", "alt", "ust", "ic", "col", "daxili",
    "xarici", "zbor", "komplekt", "tam", "original", "orjinal", "arginal",
    "lazim", "lazimdi", "lazimdir", "eded", "tere", "teref", "terefi", "ucun",
    "ve", "ile", "birlikde", "bir", "yerde", "surucu", "sernisin", "terefden",
    "salam", "xahis", "edirem", "zehmet", "olmasa", "var", "varmi", "olar",
    "olarmi", "qiymet", "qiymeti", "necedir", "nece", "please",
    "здравствуйте", "привет", "нужен", "нужна", "нужно", "нужны", "есть",
    "для", "пожалуйста", "спасибо", "цена", "сколько", "стоит", "и",
    "левый", "правый", "передний", "задний", "перед", "зад", "лево", "право",
}


def content_tokens(text: str) -> list[str]:
    """Значимые слова запроса — то, что описывает саму деталь."""
    return [t for t in normalize(text).split()
            if t not in MODIFIER_WORDS and not t.isdigit() and len(t) >= 2]


def strip_modifiers(text: str) -> str:
    return " ".join(content_tokens(text))


def _query_keys(item_raw: str) -> list[str]:
    keys: list[str] = []
    for key in (normalize(item_raw), strip_modifiers(item_raw)):
        if key and key not in keys:
            keys.append(key)
    return keys


def _tokens_match(a: str, b: str) -> bool:
    """Совпадают ли два слова с поправкой на окончания и опечатки.

    Морфологию берём у поставленного ретривера (та же функция ``stem_token``),
    чтобы слои конвейера судили о словах одинаково.
    """
    if a == b:
        return True
    _ensure_vendor_on_path()
    from avtozap_retriever_v2 import stem_token
    if len(a) >= 4 and len(b) >= 4 and stem_token(a) == stem_token(b):
        return True
    from rapidfuzz import fuzz
    shorter = min(len(a), len(b))
    if shorter < 4:
        return False
    return fuzz.ratio(a, b) >= 88
