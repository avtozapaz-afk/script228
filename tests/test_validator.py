"""Валидатор: жёсткие проверки после Arbiter V3."""

import pytest

from avtozap.layer0 import Layer0
from avtozap.retriever import RetrieverV2
from avtozap.types import (
    ARB_CLARIFY,
    ARB_ERROR,
    ARB_SELECT,
    ARB_UNKNOWN,
    OEM_CONFLICT,
    OEM_NONE,
    VAL_DOWNGRADE,
    VAL_PASS,
    VAL_REJECT,
    ArbiterDecision,
    Candidate,
    Layer0Item,
    OemEvidence,
    RetrieverResult,
)
from avtozap.validator import Validator


@pytest.fixture(scope="module")
def retriever() -> RetrieverV2:
    return RetrieverV2.from_file()


@pytest.fixture(scope="module")
def validator(retriever) -> Validator:
    return Validator(retriever, layer0=Layer0(retriever))


def candidates_for(retriever, *part_ids) -> RetrieverResult:
    out = []
    for pid in part_ids:
        part = retriever.dict.parts[pid]
        out.append(Candidate(part_id=pid, name_ru=part.name_ru, name_az=part.name_az,
                             category=part.category, subcategory=part.subcategory,
                             leaf_code=part.leaf_code, score=0.9, reason="test"))
    return RetrieverResult(candidates=out, status="OK")


def item(text: str) -> Layer0Item:
    return Layer0Item(item_index=0, item_raw=text)


def select(part_id: str, confidence: float = 0.9) -> ArbiterDecision:
    return ArbiterDecision(decision=ARB_SELECT, part_id=part_id,
                           confidence=confidence, reason="тест")


NO_OEM = OemEvidence(status=OEM_NONE)


def test_valid_selection_passes(validator, retriever):
    result = validator.validate(item("əyləc diski"), select("EY-002"),
                                candidates_for(retriever, "EY-002"), NO_OEM)
    assert result.status == VAL_PASS


# ── V1 / V2: код должен существовать и быть разрешённым ────────────────────
def test_v1_part_id_absent_from_dictionary(validator, retriever):
    result = validator.validate(item("əyləc diski"), select("ZZ-999"),
                                candidates_for(retriever, "EY-002"), NO_OEM)
    assert (result.status, result.code) == (VAL_REJECT, "V1")


def test_v2_part_id_not_among_candidates(validator, retriever):
    """Существующий, но не предложенный ретривером код — тоже отказ."""
    result = validator.validate(item("əyləc diski"), select("KZ-020"),
                                candidates_for(retriever, "EY-002"), NO_OEM)
    assert (result.status, result.code) == (VAL_REJECT, "V2")


# ── V3: родитель/сосед вместо запрошенной детали ───────────────────────────
def test_v3_neighbour_part_without_textual_support_is_downgraded(validator, retriever):
    result = validator.validate(item("əyləc diski"), select("KZ-020"),
                                candidates_for(retriever, "KZ-020"), NO_OEM)
    assert (result.status, result.code) == (VAL_DOWNGRADE, "V3")


# ── V4: конфликт OEM ────────────────────────────────────────────────────────
def test_v4_unresolved_oem_conflict_is_downgraded(validator, retriever):
    oem = OemEvidence(status=OEM_CONFLICT, numbers=["06A115561B"],
                      resolved_part_id="MU-025", resolved_name="Катушка зажигания")
    result = validator.validate(item("əyləc diski"), select("EY-002"),
                                candidates_for(retriever, "EY-002"), oem)
    assert (result.status, result.code) == (VAL_DOWNGRADE, "V4")


def test_v4_conflict_resolved_in_favour_of_the_number_passes(validator, retriever):
    oem = OemEvidence(status=OEM_CONFLICT, resolved_part_id="EY-002")
    result = validator.validate(item("əyləc diski"), select("EY-002"),
                                candidates_for(retriever, "EY-002"), oem)
    assert result.status == VAL_PASS


# ── V5: Layer 0 не доделил предмет ─────────────────────────────────────────
def test_v5_item_still_contains_two_part_types(validator, retriever):
    result = validator.validate(item("naklatka və tormuz disk"), select("EY-001"),
                                candidates_for(retriever, "EY-001"), NO_OEM)
    assert (result.status, result.code) == (VAL_REJECT, "V5")


# ── V6: внутренняя несогласованность ───────────────────────────────────────
def test_v6_arbiter_error(validator, retriever):
    decision = ArbiterDecision(decision=ARB_ERROR, reason="таймаут", error="таймаут")
    result = validator.validate(item("əyləc diski"), decision,
                                candidates_for(retriever, "EY-002"), NO_OEM)
    assert (result.status, result.code) == (VAL_REJECT, "V6")


def test_v6_unknown_must_not_carry_a_part_id(validator, retriever):
    decision = ArbiterDecision(decision=ARB_UNKNOWN, part_id="EY-002", confidence=0.9)
    result = validator.validate(item("əyləc diski"), decision,
                                candidates_for(retriever, "EY-002"), NO_OEM)
    assert (result.status, result.code) == (VAL_REJECT, "V6")


# ── V7: точность важнее навязанной полноты ─────────────────────────────────
def test_v7_low_confidence_is_downgraded_not_returned(validator, retriever):
    result = validator.validate(item("əyləc diski"), select("EY-002", confidence=0.2),
                                candidates_for(retriever, "EY-002"), NO_OEM)
    assert (result.status, result.code) == (VAL_DOWNGRADE, "V7")


def test_v7_missing_confidence_is_downgraded(validator, retriever):
    decision = ArbiterDecision(decision=ARB_SELECT, part_id="EY-002", confidence=None)
    result = validator.validate(item("əyləc diski"), decision,
                                candidates_for(retriever, "EY-002"), NO_OEM)
    assert (result.status, result.code) == (VAL_DOWNGRADE, "V7")


# ── отказы арбитра проходят валидатор как есть ─────────────────────────────
@pytest.mark.parametrize("decision", [ARB_UNKNOWN, ARB_CLARIFY])
def test_arbiter_refusal_passes_through(validator, retriever, decision):
    result = validator.validate(item("əyləc diski"),
                                ArbiterDecision(decision=decision, confidence=0.1),
                                candidates_for(retriever, "EY-002"), NO_OEM)
    assert result.status == VAL_PASS
