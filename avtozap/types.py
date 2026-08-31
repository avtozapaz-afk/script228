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

# Arbiter V3
ARB_SELECT = "SELECT"
ARB_UNKNOWN = "UNKNOWN"
ARB_CLARIFY = "CLARIFY"
ARB_ERROR = "ERROR"

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
    vehicle_context: str = ""
    # Сырые фрагменты до слияния — видно, что и почему склеилось
    raw_fragments: list[str] = field(default_factory=list)


@dataclass
class OemEvidence:
    status: str = OEM_NONE
    numbers: list[str] = field(default_factory=list)
    rejected: list[dict[str, str]] = field(default_factory=list)
    resolved_part_id: str | None = None
    resolved_name: str | None = None
    text_head_part_ids: list[str] = field(default_factory=list)
    reason: str = "no number found"


@dataclass
class PhotoEvidence:
    status: str = PHOTO_NONE
    summary: str = ""
    hints: list[str] = field(default_factory=list)
    reason: str = ""


@dataclass
class Candidate:
    part_id: str
    name_ru: str
    name_az: str
    category: str
    subcategory: str
    leaf_code: str
    score: float
    reason: str

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
    def part_ids(self) -> list[str]:
        return [c.part_id for c in self.candidates]


@dataclass
class ArbiterDecision:
    decision: str = ARB_UNKNOWN
    part_id: str | None = None
    confidence: float | None = None
    reason: str = ""
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
    vehicle_context: str
    oem: OemEvidence
    photo: PhotoEvidence
    retriever: RetrieverResult
    arbiter: ArbiterDecision
    validator: ValidatorResult
    final_part_id: str | None
    final_status: str
    expected_part_id: str | None = None
    failure_layer: str = LAYER_NONE
    latency_ms: int = 0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def key(self) -> str:
        """Ключ идемпотентности для resume."""
        return f"{self.rfq_id}#{self.item_index}"
