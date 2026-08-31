"""Retriever V2 — кандидаты ТОЛЬКО из словаря, ничего не выдумывается.

Каждый кандидат — реальный ``part_id`` из ``SLOVAR_FINAL.txt``; ретривер лишь
находит уже существующие записи и никогда не порождает новые.

Как ищем
--------
Запрос раскладывается на несколько «ключей»: полная нормализованная фраза и она
же без служебных слов (сторона/позиция/количество/вежливость/марка авто). Для
каждого ключа токены мягко сопоставляются со словарной лексикой (см.
``_match_token``): точное совпадение, опечатка, согласный скелет
(``tormuz`` ≡ ``tormoz``) или общий префикс (``qutusunun`` ≈ ``qutusu``).
Кириллические записи предварительно транслитерируются, поэтому «tormuz disk»
достаёт и «тормозной диск».

Причины (``reason``) кандидата, по убыванию доверия::

    exact_name           1.00   фраза == вариант имени детали
    exact_synonym        0.95   фраза == синоним листа → все детали листа
    name_containment     0.70+  все токены имени покрыты запросом (или наоборот)
    synonym_containment  0.66+  то же для синонима листа
    fuzzy_name/synonym   =sim   опечатка в целой фразе
    token_overlap        0.40+  частичное покрытие значимых токенов

Полнота здесь важнее краткости: пропущенный ретривером правильный кандидат
арбитр уже не спасёт, поэтому список намеренно шире одного «лучшего» ответа.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from slovar_matcher.normalize import normalize, similarity, tokens
from slovar_matcher.parser import Dictionary, name_variants, parse_file

from .config import (
    CONJUNCTIONS,
    NOISE_WORDS,
    POSITION_WORDS,
    QUANTITY_WORDS,
    RETRIEVER_FUZZY_THRESHOLD,
    RETRIEVER_MIN_SCORE,
    RETRIEVER_TOP_K,
    SIDE_WORDS,
    SLOVAR_PATH,
    VEHICLE_BRANDS,
)
from .translit import common_prefix_len, fold, skeleton
from .types import Candidate, RetrieverResult

STOPWORDS = SIDE_WORDS | POSITION_WORDS | QUANTITY_WORDS | NOISE_WORDS | CONJUNCTIONS

_EXACT_NAME = 1.00
_EXACT_SYNONYM = 0.95
_EXACT_LEAF = 0.90

#: Базовый вес ключа по его происхождению (имя детали > синоним листа > имя листа).
_BASE = {"name": 0.70, "synonym": 0.66, "leaf": 0.62}

#: Порог мягкого совпадения ДВУХ ТОКЕНОВ (не целых фраз).
TOKEN_MATCH_THRESHOLD = 0.75
_SKELETON_SCORE = 0.85
_PREFIX_SCORE = 0.78
_MIN_PREFIX = 4
_MIN_SKELETON = 3

_PARENS_RE = re.compile(r"\([^)]*\)")

#: Кривая для частичного покрытия токенов. Наклон намеренно крутой: одно слабое
#: совпадение по модификатору не должно протаскивать в список весь лист словаря
#: (синонимы групповые — один хит расходится на все детали листа).
_OVERLAP_FLOOR = 0.25
_OVERLAP_SPAN = 0.60

#: Вес последнего («головного») токена фразы относительно остальных.
_HEAD_WEIGHT = 2.0


def _weighted_cover(items, score_of) -> float:
    """Средняя сила совпадения по токенам с двойным весом последнего."""
    total = 0.0
    weight_sum = 0.0
    last = len(items) - 1
    for i, item in enumerate(items):
        weight = _HEAD_WEIGHT if i == last else 1.0
        total += weight * score_of(item)
        weight_sum += weight
    return total / weight_sum if weight_sum else 0.0


def strip_stopwords(text: str, drop_brands: bool = True) -> str:
    """Нормализованная фраза без атрибутов/шума (и, опционально, марок авто)."""
    out = []
    for tok in tokens(text):
        if tok in STOPWORDS:
            continue
        if drop_brands and tok in VEHICLE_BRANDS:
            continue
        if tok.isdigit():
            continue
        out.append(tok)
    return " ".join(out)


def content_tokens(text: str) -> list[str]:
    """Значимые токены запроса — то, что реально описывает предмет."""
    return [t for t in tokens(text)
            if t not in STOPWORDS and t not in VEHICLE_BRANDS and not t.isdigit()]


def match_token(a: str, b: str) -> float:
    """Мягкое совпадение двух токенов в [0, 1]; 0 — не совпали.

    Порядок сигналов: точное равенство → свёрнутое равенство (диакритика и
    кириллица) → опечатка → согласный скелет → общий префикс (агглютинативные
    окончания азербайджанского: ``yastıqları`` ≈ ``yastığı``).
    """
    if a == b:
        return 1.0
    fa, fb = fold(a), fold(b)
    if fa == fb:
        return 1.0
    sim = similarity(fa, fb)
    best = sim if sim >= TOKEN_MATCH_THRESHOLD else 0.0
    sa, sb = skeleton(a), skeleton(b)
    if len(sa) >= _MIN_SKELETON and sa == sb:
        best = max(best, _SKELETON_SCORE)
    shorter = min(len(fa), len(fb))
    if shorter >= _MIN_PREFIX and common_prefix_len(a, b) >= min(_MIN_PREFIX, shorter):
        best = max(best, _PREFIX_SCORE)
    return best if best >= TOKEN_MATCH_THRESHOLD else 0.0


def _leaf_variants(leaf_name: str) -> set[str]:
    """Варианты имени листа для индекса.

    Берём варианты по общему правилу словаря плюс — в отличие от имён деталей —
    «очищенный» ствол без скобок: ``Əyləc altlıqları (nakladkalar)`` →
    ``əyləc altlıqları``. Для имени детали такое расширение запрещено (оно
    превратило бы групповую двусмысленность в единственное совпадение), но имя
    листа и так групповой ключ, поэтому здесь оно только добавляет полноты.
    """
    variants = set(name_variants(leaf_name))
    stem = _PARENS_RE.sub(" ", leaf_name)
    for chunk in stem.split("/"):
        chunk = chunk.strip()
        if chunk:
            variants.add(chunk)
    return {v for v in variants if v.strip()}


@dataclass
class _Hit:
    score: float
    reason: str


class RetrieverV2:
    def __init__(self, dictionary: Dictionary,
                 top_k: int = RETRIEVER_TOP_K,
                 min_score: float = RETRIEVER_MIN_SCORE,
                 fuzzy_threshold: float = RETRIEVER_FUZZY_THRESHOLD):
        self.dict = dictionary
        self.top_k = top_k
        self.min_score = min_score
        self.fuzzy_threshold = fuzzy_threshold
        self._build_index()
        self._expand_cache: dict[str, dict[str, float]] = {}

    @classmethod
    def from_file(cls, path: str = SLOVAR_PATH, **kw) -> "RetrieverV2":
        if not os.path.exists(path):
            raise FileNotFoundError(f"словарь не найден: {path}")
        return cls(parse_file(path), **kw)

    def _build_index(self) -> None:
        """Единый поисковый индекс: имена деталей + синонимы + ИМЕНА ЛИСТЬЕВ.

        Имена листьев (подкатегорий) — такая же реальная структура словаря, как
        имена деталей, и покупатели пишут именно их: «əyləc altlığı» — это лист
        ``Əyləc altlıqları (nakladkalar)``. Без них целый класс запросов
        пролетал мимо правильной группы, поэтому лист индексируется как
        групповой ключ (как синоним), но с чуть меньшим базовым весом.
        """
        # index_key -> {kind: [part_id, ...]}
        self._refs: dict[str, dict[str, list[str]]] = {}
        self._key_tokens: dict[str, tuple[str, ...]] = {}
        self._postings: dict[str, list[str]] = {}
        self._vocab: list[str] = []
        seen_vocab: set[str] = set()

        def register(index_key: str, kind: str, part_ids: list[str]) -> None:
            toks = tuple(index_key.split())
            if not toks or not part_ids:
                return
            self._key_tokens[index_key] = toks
            bucket = self._refs.setdefault(index_key, {}).setdefault(kind, [])
            for pid in part_ids:
                if pid not in bucket:
                    bucket.append(pid)
            for tok in toks:
                if tok not in seen_vocab:
                    seen_vocab.add(tok)
                    self._vocab.append(tok)
                posting = self._postings.setdefault(tok, [])
                if index_key not in posting:
                    posting.append(index_key)

        for index_key, pids in self.dict.name_index.items():
            register(index_key, "name", list(pids))
        for index_key, codes in self.dict.synonym_index.items():
            register(index_key, "synonym", self._group_ids(list(codes)))
        for code, group in self.dict.groups.items():
            for variant in sorted(_leaf_variants(group.subcategory)):
                register(normalize(variant), "leaf", list(group.part_ids))

    # ── публичный API ───────────────────────────────────────────────────────
    def retrieve(self, item_raw: str, restrict_category: str | None = None,
                 extra_hints: list[str] | None = None) -> RetrieverResult:
        keys = self._query_keys(item_raw, extra_hints)
        if not keys:
            return RetrieverResult(status="EMPTY",
                                   reason="пустой запрос после нормализации",
                                   query_keys=[])

        best: dict[str, _Hit] = {}
        for key in keys:
            for pid, hit in self._hits_for_key(key).items():
                cur = best.get(pid)
                if cur is None or hit.score > cur.score:
                    best[pid] = hit

        candidates: list[Candidate] = []
        for pid, hit in best.items():
            if hit.score < self.min_score:
                continue
            part = self.dict.parts[pid]
            if restrict_category and part.category != restrict_category:
                continue
            candidates.append(Candidate(
                part_id=pid,
                name_ru=part.name_ru,
                name_az=part.name_az,
                category=part.category,
                subcategory=part.subcategory,
                leaf_code=part.leaf_code,
                score=round(hit.score, 4),
                reason=hit.reason,
            ))

        # Детерминированный порядок: score ↓, затем part_id ↑.
        candidates.sort(key=lambda c: (-c.score, c.part_id))
        candidates = candidates[: self.top_k]

        if not candidates:
            return RetrieverResult(status="EMPTY",
                                   reason="в словаре нет кандидатов выше min_score",
                                   query_keys=keys)
        return RetrieverResult(candidates=candidates, status="OK",
                               reason=f"{len(candidates)} кандидат(ов)",
                               query_keys=keys)

    def head_part_ids(self, item_raw: str) -> list[str]:
        """Точная «голова» предмета: только exact name/synonym, без догадок.

        Нужна Layer 0 (слияние одинаковых предметов) и OEM-резолверу (сравнение
        текстового объекта с объектом по номеру) — ещё ДО работы ретривера.
        """
        for key in self._query_keys(item_raw, None):
            pids = self.dict.name_index.get(key)
            if pids:
                return list(pids)
            codes = self.dict.synonym_index.get(key)
            if codes:
                return self._group_ids(codes)
        return []

    def is_exact_key(self, phrase: str) -> bool:
        """Есть ли такая точная запись (имя или синоним) в словаре."""
        key = normalize(phrase)
        return bool(key) and (key in self.dict.name_index or key in self.dict.synonym_index)

    def lexical_support(self, item_raw: str, part_id: str) -> bool:
        """Есть ли у выбранной детали текстовая опора в запросе.

        True, если хотя бы один значимый токен запроса мягко совпадает с
        каким-либо токеном имён детали, её подкатегории или синонимов её листа.
        Валидатор использует это против «выбрал родителя/соседа вместо детали».
        """
        part = self.dict.parts.get(part_id)
        if part is None:
            return False
        vocabulary: set[str] = set()
        for name in (part.name_ru, part.name_az, part.subcategory):
            vocabulary |= set(normalize(name).split())
        for syn in self.dict.groups[part.leaf_code].synonyms:
            vocabulary |= set(syn.split())

        for tok in content_tokens(item_raw):
            for word in vocabulary:
                if match_token(tok, word) > 0:
                    return True
        return False

    # ── внутреннее ──────────────────────────────────────────────────────────
    def _query_keys(self, item_raw: str, extra_hints: list[str] | None) -> list[str]:
        keys: list[str] = []

        def add(k: str) -> None:
            if k and k not in keys:
                keys.append(k)

        add(normalize(item_raw))
        add(strip_stopwords(item_raw, drop_brands=True))
        add(strip_stopwords(item_raw, drop_brands=False))
        for hint in extra_hints or []:
            add(normalize(hint))
        return keys

    def _group_ids(self, leaf_codes: list[str]) -> list[str]:
        ids: list[str] = []
        for code in leaf_codes:
            for pid in self.dict.groups[code].part_ids:
                if pid not in ids:
                    ids.append(pid)
        return ids

    def _expand(self, token: str) -> dict[str, float]:
        """Словарные токены, мягко совпадающие с ``token`` → сила совпадения."""
        cached = self._expand_cache.get(token)
        if cached is not None:
            return cached
        matches: dict[str, float] = {}
        for word in self._vocab:
            score = match_token(token, word)
            if score > 0:
                matches[word] = score
        self._expand_cache[token] = matches
        return matches

    def _hits_for_key(self, key: str) -> dict[str, _Hit]:
        hits: dict[str, _Hit] = {}

        def put(pid: str, score: float, reason: str) -> None:
            cur = hits.get(pid)
            if cur is None or score > cur.score:
                hits[pid] = _Hit(score, reason)

        query_tokens = [t for t in key.split() if t]
        if not query_tokens:
            return hits

        # 1) точное совпадение фразы целиком — максимальное доверие.
        exact = self._refs.get(key, {})
        for pid in exact.get("name", ()):
            put(pid, _EXACT_NAME, "exact_name")
        for pid in exact.get("synonym", ()):
            put(pid, _EXACT_SYNONYM, "exact_synonym")
        for pid in exact.get("leaf", ()):
            put(pid, _EXACT_LEAF, "exact_leaf")

        # 2) отбираем только словарные ключи, где есть хоть один общий токен.
        expansions = {t: self._expand(t) for t in query_tokens}
        candidate_keys: set[str] = set()
        for matches in expansions.values():
            for word in matches:
                candidate_keys.update(self._postings.get(word, ()))

        for index_key in candidate_keys:
            if index_key == key:
                continue                      # уже учтён как точное совпадение
            index_tokens = self._key_tokens[index_key]

            # Покрытие в обе стороны, с весом на «голове» фразы: и азербайджанский,
            # и русский здесь head-final («sürətlər qutusunun YASTIQLARI»,
            # «подушка КОРОБКИ» — последнее слово несёт тип детали), поэтому
            # совпадение по последнему токену весит вдвое против модификаторов.
            index_cover = _weighted_cover(
                index_tokens,
                lambda itok: max((expansions[q].get(itok, 0.0) for q in query_tokens),
                                 default=0.0))
            query_cover = _weighted_cover(
                query_tokens,
                lambda q: max((expansions[q].get(itok, 0.0) for itok in index_tokens),
                              default=0.0))
            if index_cover <= 0.0 or query_cover <= 0.0:
                continue

            full_sim = similarity(key, index_key)
            for kind, part_ids in self._refs[index_key].items():
                base = _BASE[kind]
                if index_cover >= 0.999 or query_cover >= 0.999:
                    other = query_cover if index_cover >= 0.999 else index_cover
                    score = base + 0.25 * other
                    reason = f"{kind}_containment"
                elif full_sim >= self.fuzzy_threshold:
                    score = full_sim
                    reason = f"fuzzy_{kind}"
                else:
                    # Гармоническое среднее: наказывает односторонние совпадения,
                    # когда половина запроса осталась непокрытой.
                    denom = index_cover + query_cover
                    harmonic = (2 * index_cover * query_cover / denom) if denom else 0.0
                    score = _OVERLAP_FLOOR + _OVERLAP_SPAN * harmonic
                    reason = "token_overlap"

                if score >= self.min_score - 1e-9:
                    for pid in part_ids:
                        put(pid, score, reason)

        return hits
