"""Arbiter V3: заморозка промпта и строгий разбор ответа.

Ни один тест здесь не ходит в сеть.
"""

import pytest

from avtozap.arbiter import (
    ArbiterV3,
    build_user_message,
    frozen_sha256,
    load_prompt,
    parse_response,
    prompt_sha256,
)
from avtozap.retriever import RetrieverV2
from avtozap.types import (
    ARB_CLARIFY,
    ARB_SELECT,
    ARB_UNKNOWN,
    Candidate,
    OemEvidence,
    PhotoEvidence,
    RetrieverResult,
)


# ── заморозка ───────────────────────────────────────────────────────────────
def test_prompt_is_frozen():
    """Промпт Arbiter V3 не должен меняться незаметно.

    Если тест упал — промпт правили. Это допустимо только осознанно: обновите
    avtozap/prompts/arbiter_v3.sha256 тем же коммитом, что и сам промпт.
    """
    assert prompt_sha256() == frozen_sha256(), (
        "промпт Arbiter V3 изменился, а контрольная сумма — нет")


def test_prompt_states_the_hard_rules():
    text = load_prompt()
    assert "FROZEN" in text
    assert "CANDIDATES" in text
    assert "UNKNOWN" in text


# ── сборка входа ────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def sample():
    retriever = RetrieverV2.from_file()
    part = retriever.dict.parts["EY-002"]
    candidates = RetrieverResult(candidates=[Candidate(
        part_id="EY-002", name_ru=part.name_ru, name_az=part.name_az,
        category=part.category, subcategory=part.subcategory,
        leaf_code=part.leaf_code, score=1.0, reason="exact_name")], status="OK")
    return candidates


def test_user_message_carries_every_required_field(sample):
    message = build_user_message(
        original_text="1K0615301AA əyləc diski",
        item_raw="əyləc diski",
        oem=OemEvidence(status="UNRESOLVED", numbers=["1K0615301AA"], reason="нет каталога"),
        photo=PhotoEvidence(status="NO_IMAGE"),
        retriever=sample,
        vehicle_context="Mercedes W211",
        translation="тормозной диск")
    for expected in ("ORIGINAL_MESSAGE", "ATOMIC_ITEM", "TRANSLATION",
                     "OEM_STATUS", "PHOTO_STATUS", "CANDIDATES", "EY-002"):
        assert expected in message


def test_user_message_lists_only_real_candidates(sample):
    message = build_user_message("x", "x", OemEvidence(), PhotoEvidence(), sample)
    assert "EY-002" in message
    assert "(none)" not in message


# ── разбор ответа ───────────────────────────────────────────────────────────
def test_valid_select_is_parsed():
    data, error = parse_response(
        '{"decision":"SELECT","part_id":"EY-002","confidence":0.9,"reason":"ok"}',
        {"EY-002"})
    assert error is None and data["part_id"] == "EY-002"


@pytest.mark.parametrize("payload,fragment", [
    ("не json", "JSON"),
    ('{"decision":"MAYBE"}', "недопустимое решение"),
    ('{"decision":"SELECT","part_id":null}', "SELECT без part_id"),
    ('{"decision":"SELECT","part_id":"ZZ-999"}', "отсутствует среди кандидатов"),
    ('{"decision":"UNKNOWN","part_id":"EY-002"}', "не должно содержать part_id"),
    ('{"decision":"SELECT","part_id":"EY-002","confidence":7}', "вне диапазона"),
])
def test_malformed_responses_are_reported_not_repaired(payload, fragment):
    """Плохой ответ — это факт для отчёта, а не повод «починить» решение."""
    _, error = parse_response(payload, {"EY-002"})
    assert error and fragment in error


# ── поведение без кандидатов ────────────────────────────────────────────────
def test_no_candidates_means_unknown_without_calling_the_api():
    arbiter = ArbiterV3(client=None)          # клиента нет — вызвать нечего
    decision = arbiter.decide(
        original_text="qwerty", item_raw="qwerty",
        oem=OemEvidence(), photo=PhotoEvidence(),
        retriever=RetrieverResult(status="EMPTY"))
    assert decision.decision == ARB_UNKNOWN
    assert decision.part_id is None
