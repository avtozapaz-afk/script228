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


def candidates_for(retriever, *codes) -> RetrieverResult:
    out = []
    for code in codes:
        part = retriever.dict.get(code)
        out.append(Candidate(external_code=code, name_ru=part.name_ru,
                             name_az=part.name_az, category=part.category,
                             score=0.9, reason="test"))
    return RetrieverResult(candidates=out, status="OK")


def item(text: str) -> Layer0Item:
    return Layer0Item(item_index=0, item_raw=text)


def select(code: str, confidence: str = "high") -> ArbiterDecision:
    return ArbiterDecision(decision=ARB_SELECT, external_code=code,
                           confidence=confidence, reason="тест")


NO_OEM = OemEvidence(status=OEM_NONE)


def test_valid_selection_passes(validator, retriever):
    result = validator.validate(item("əyləc diski"), select("EY-002"),
                                candidates_for(retriever, "EY-002"), NO_OEM)
    assert result.status == VAL_PASS


# ── V1 / V2: код должен существовать и быть разрешённым ────────────────────
def test_v1_code_absent_from_dictionary(validator, retriever):
    result = validator.validate(item("əyləc diski"), select("ZZ-999"),
                                candidates_for(retriever, "EY-002"), NO_OEM)
    assert (result.status, result.code) == (VAL_REJECT, "V1")


def test_v2_code_not_among_candidates(validator, retriever):
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
                      resolved_external_code="MU-025", resolved_name="Катушка зажигания")
    result = validator.validate(item("əyləc diski"), select("EY-002"),
                                candidates_for(retriever, "EY-002"), oem)
    assert (result.status, result.code) == (VAL_DOWNGRADE, "V4")


def test_v4_conflict_resolved_in_favour_of_the_number_passes(validator, retriever):
    oem = OemEvidence(status=OEM_CONFLICT, resolved_external_code="EY-002")
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
    decision = ArbiterDecision(decision=ARB_UNKNOWN, external_code="EY-002",
                               confidence="high")
    result = validator.validate(item("əyləc diski"), decision,
                                candidates_for(retriever, "EY-002"), NO_OEM)
    assert (result.status, result.code) == (VAL_REJECT, "V6")


# ── V7: точность важнее навязанной полноты ─────────────────────────────────
def test_v7_low_confidence_is_downgraded_not_returned(validator, retriever):
    """Промпт V3 отдаёт уверенность словом: «low» не годится для готового ответа."""
    result = validator.validate(item("əyləc diski"), select("EY-002", confidence="low"),
                                candidates_for(retriever, "EY-002"), NO_OEM)
    assert (result.status, result.code) == (VAL_DOWNGRADE, "V7")


def test_v7_medium_confidence_is_accepted(validator, retriever):
    result = validator.validate(item("əyləc diski"),
                                select("EY-002", confidence="medium"),
                                candidates_for(retriever, "EY-002"), NO_OEM)
    assert result.status == VAL_PASS


def test_v7_missing_confidence_is_downgraded(validator, retriever):
    decision = ArbiterDecision(decision=ARB_SELECT, external_code="EY-002",
                               confidence=None)
    result = validator.validate(item("əyləc diski"), decision,
                                candidates_for(retriever, "EY-002"), NO_OEM)
    assert (result.status, result.code) == (VAL_DOWNGRADE, "V7")


# ── отказы арбитра проходят валидатор как есть ─────────────────────────────
@pytest.mark.parametrize("decision", [ARB_UNKNOWN, ARB_CLARIFY])
def test_arbiter_refusal_passes_through(validator, retriever, decision):
    result = validator.validate(item("əyləc diski"),
                                ArbiterDecision(decision=decision, confidence="low"),
                                candidates_for(retriever, "EY-002"), NO_OEM)
    assert result.status == VAL_PASS


# ── живой прогон 469: V5 отвергал верные ответы на заявках с номером ────────
def test_v5_does_not_fire_on_a_part_named_twice(validator, retriever):
    """Номер детали и её английское имя — это ОДНА деталь, а не три.

    Обе заявки из живого прогона 469, где валидатор зарубил верный ответ
    арбитра и отправил заявку в UNKNOWN. Сегментатор резал их на куски
    (`32700-3K070` / `PEDAL ASSY` / `ACCELERATOR`), и правило V5 считало
    каждый кусок отдельной деталью.
    """
    for text, code in (("ABS bloku 58500 - N6000", "EY-010"),
                       ("32700-3K070 — PEDAL ASSY - ACCELERATOR", "EL-026")):
        result = validator.validate(item(text), select(code),
                                    candidates_for(retriever, code), NO_OEM)
        assert result.code != "V5", f"{text}: {result.reason}"
        assert result.status != VAL_REJECT, f"{text}: {result.reason}"


def test_v5_still_fires_when_the_pieces_are_really_different_parts(validator,
                                                                  retriever):
    """Починка не должна отключить правило там, где оно нужно."""
    result = validator.validate(item("fara ve bufer"), select("KZ-005"),
                                candidates_for(retriever, "KZ-005"), NO_OEM)
    assert (result.status, result.code) == (VAL_REJECT, "V5")


def test_distinct_part_groups_ignores_a_catalogue_number():
    """Кусок, который ни на что не указывает, деталью не считается."""
    from avtozap.validator import distinct_part_groups

    from avtozap.retriever import RetrieverV2
    retriever = RetrieverV2()
    assert distinct_part_groups(["32700-3K070"], retriever) == set()
    assert len(distinct_part_groups(["fara", "bufer"], retriever)) == 2
