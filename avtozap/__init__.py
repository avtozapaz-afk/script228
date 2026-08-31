"""AVTOZAP — сквозной пайплайн подбора запчастей.

RAW REQUEST
  → Layer 0 (сегментатор)
  → OEM / part-number resolver
  → Photo evidence layer
  → Retriever V2
  → Arbiter V3 (ЗАМОРОЖЕН)
  → Validator
  → final part_id / UNKNOWN

Все слои, кроме Arbiter V3 и Photo, детерминированы и не требуют сети.
"""

from .types import (
    ArbiterDecision,
    Candidate,
    ItemRecord,
    Layer0Item,
    Layer0Result,
    OemEvidence,
    PhotoEvidence,
    RetrieverResult,
    ValidatorResult,
)

__all__ = [
    "ArbiterDecision",
    "Candidate",
    "ItemRecord",
    "Layer0Item",
    "Layer0Result",
    "OemEvidence",
    "PhotoEvidence",
    "RetrieverResult",
    "ValidatorResult",
]
