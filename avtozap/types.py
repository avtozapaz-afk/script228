"""Структуры данных пайплайна.

Один экземпляр ``ItemRecord`` = один атомарный запрошенный предмет после Layer 0.
Он же — одна строка в результирующих JSONL/CSV.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# ── статусы (единый словарь значений на весь пайплайн) ───────────────────────
# Layer 0
L0_OK = "OK"
L0_EMPTY = "EMPTY"
L0_FALLBACK = "FALLBACK_WHOLE_MESSAGE"

# OEM resolver
OEM_MATCH = "MATCH"
OEM_CONFLICT = "CONFLICT"
OEM_UNRESOLVED = "UNRESOLVED"
OEM_NONE = "NONE"

# Photo evidence
PHOTO_NONE = "NO_IMAGE"
PHOTO_UNAVAILABLE = "IMAGE_UNAVAILABLE"
PHOTO_OK = "OK"
PHOTO_ERROR = "ERROR"

# Arbiter V3 — значения ровно как в замороженном промпте (нижний регистр).
ARB_SELECT = "select"
ARB_UNKNOWN = "unknown"
ARB_CLARIFY = "clarify"
ARB_ERROR = "error"

#: Уверенность в промпте V3 — слово, а не число.
CONFIDENCE_LEVELS = ("high", "medium", "low")

# Validator
VAL_PASS = "PASS"
VAL_DOWNGRADE = "DOWNGRADE"
VAL_REJECT = "REJECT"

# Финальный статус
FINAL_SELECT = "SELECT"
FINAL_UNKNOWN = "UNKNOWN"
FINAL_REVIEW = "REVIEW"
FINAL_ERROR = "ERROR"

# Слой, признанный слабым звеном для конкретного провала
LAYER_L0 = "LAYER0_SEGMENTATION"
LAYER_NORM = "NORMALIZATION_DICTIONARY"
LAYER_OEM = "OEM_RESOLVER"
LAYER_PHOTO = "PHOTO_EVIDENCE"
LAYER_RETRIEVER = "RETRIEVER_V2_MISS"
LAYER_ARBITER = "ARBITER_V3_SEMANTIC"
LAYER_VALIDATOR = "VALIDATOR"
LAYER_ABSENT = "TRUE_DICTIONARY_ABSENCE"
LAYER_PIPELINE_ERROR = "PIPELINE_ERROR"
LAYER_NONE = "-"


@dataclass
class Layer0Item:
    """Один атомарный запрошенный тип детали."""

    item_index: int
    item_raw: str
    status: str = L0_OK
    reason: str = ""
    # Подсказки сегментера для поиска — не ответы, а расширение запроса.
    search_phrases: list[str] = field(default_factory=list)
    oem_code: str | None = None
    # Просьба о ремонте/услуге, а не о детали (правило 6 промпта сегментера).
    is_part_request: bool = True
    # Заполнено, если текст предмета пришёл с испорченной кодировкой.
    encoding_warning: str | None = None
    # Позиционные/сторонние атрибуты, собранные при слиянии фрагментов
    side_hint: str | None = None
    position_hint: str | None = None
    # Куски исходного текста, из которых собран item (для аудита)
    source_fragments: list[str] = field(default_factory=list)


@dataclass
class Layer0Result:
    items: list[Layer0Item] = field(default_factory=list)
    status: str = L0_OK
    reason: str = ""
    # Кто разобрал сообщение: сегментер LLM или детерминированный запасной.
    source: str = ""
    vehicle_context: str = ""
    # Сырые фрагменты до слияния — видно, что и почему склеилось
    raw_fragments: list[str] = field(default_factory=list)


@dataclass
class OemEvidence:
    status: str = OEM_NONE
    numbers: list[str] = field(default_factory=list)
    rejected: list[dict[str, str]] = field(default_factory=list)
    resolved_external_code: str | None = None
    resolved_name: str | None = None
    text_head_codes: list[str] = field(default_factory=list)
    reason: str = "no number found"


@dataclass
class PhotoEvidence:
    status: str = PHOTO_NONE
    summary: str = ""
    hints: list[str] = field(default_factory=list)
    reason: str = ""


@dataclass
class Candidate:
    """Реальная деталь словаря, предложенная ретривером.

    ``external_code`` — идентификатор проекта; именно его возвращает Arbiter V3
    и именно его проверяет валидатор.
    """

    external_code: str
    name_ru: str
    name_az: str
    category: str
    score: float
    reason: str
    synonyms: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RetrieverResult:
    candidates: list[Candidate] = field(default_factory=list)
    status: str = "OK"
    reason: str = ""
    # Ключи, по которым реально шёл поиск (полная фраза + очищенная)
    query_keys: list[str] = field(default_factory=list)

    @property
    def codes(self) -> list[str]:
        return [c.external_code for c in self.candidates]


@dataclass
class ArbiterDecision:
    """Ответ Arbiter V3 ровно в том виде, в каком его описывает промпт.

    Промпт заморожен, поэтому поля здесь повторяют его контракт буквально:
    решение в нижнем регистре, ``external_code``, уверенность словом
    (``high|medium|low``), текст уточнения.
    """

    decision: str = ARB_UNKNOWN
    external_code: str | None = None
    confidence: str | None = None            # high | medium | low
    reason: str = ""
    clarification_text: str | None = None
    model: str = ""
    latency_ms: int = 0
    attempts: int = 0
    error: str | None = None
    raw_response: str = ""


@dataclass
class ValidatorResult:
    status: str = VAL_PASS
    code: str = ""
    reason: str = ""


@dataclass
class ItemRecord:
    """Полная запись по одному атомарному предмету — строка выходного файла."""

    source_index: int
    rfq_id: str
    original_text: str
    item_index: int
    item_raw: str
    layer0_status: str
    layer0_reason: str
    layer0_item_count: int
    layer0_source: str
    vehicle_context: str
    oem: OemEvidence
    photo: PhotoEvidence
    retriever: RetrieverResult
    arbiter: ArbiterDecision
    validator: ValidatorResult
    final_external_code: str | None
    final_status: str
    # Что делать по правилу заказчика: ответить, переспросить, попросить фото
    # или отдать заявку магазинам сырым текстом.
    action: str = ""
    action_reason: str = ""
    buyer_question: str | None = None
    #: Сработавшее правило неоднозначности словаря: голый термин, по которому
    #: проект запретил выбирать код без уточнения. Пусто — правило не сработало.
    ambiguous_term: str | None = None
    #: Кто дал итоговый код: ``arbiter`` или ``dictionary_exact``. Нужно, чтобы
    #: в отчёте всегда было видно, сколько ответов дала модель, а сколько —
    #: точный термин словаря поверх её отказа.
    answered_by: str = ""

    search_phrases: list[str] = field(default_factory=list)
    is_part_request: bool = True
    encoding_warning: str | None = None
    expected_external_code: str | None = None
    failure_layer: str = LAYER_NONE
    latency_ms: int = 0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def key(self) -> str:
        """Ключ идемпотентности для resume."""
        return f"{self.rfq_id}#{self.item_index}"
