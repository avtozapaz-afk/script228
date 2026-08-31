"""Arbiter V3: заморозка настоящего промпта и строгий разбор ответа.

Ни один тест здесь не ходит в сеть.
"""

import hashlib
import json

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


def test_prompt_is_the_supplied_v3_verbatim():
    """Это именно поставленный V3, а не его пересказ."""
    text = load_prompt()
    assert text.startswith("# AVTOZAP ARBITER V3")
    for marker in ("STRICT HEAD-NOUN GUARDS", "MULTI-PART GUARD",
                   "ABSENT-CANDIDATE GUARD", "HARD VALIDATION",
                   "lupalı fara", "difuzer = fan shroud"):
        assert marker in text


def test_prompt_defines_the_output_contract_we_parse():
    text = load_prompt()
    assert '"decision":"select|unknown|clarify"' in text
    assert '"confidence":"high|medium|low"' in text
    assert "external_code" in text


# ── сборка входа ────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def shortlist() -> RetrieverResult:
    part = RetrieverV2().dict.get("EY-002")
    return RetrieverResult(status="OK", candidates=[Candidate(
        external_code="EY-002", name_ru=part.name_ru, name_az=part.name_az,
        category=part.category, score=0.999, reason="exact:eylec diski",
        synonyms=list(part.synonyms[:5]))])


def test_user_message_is_json_with_every_required_field(shortlist):
    message = build_user_message(
        original_text="1K0615301AA əyləc diski", item_raw="əyləc diski",
        oem=OemEvidence(status="UNRESOLVED", numbers=["1K0615301AA"],
                        reason="каталог не подключён"),
        photo=PhotoEvidence(status="NO_IMAGE"), retriever=shortlist,
        vehicle_context="W210", translation="тормозной диск",
        search_phrases=["əyləc diski"])
    data = json.loads(message)
    assert data["original_text"] and data["item_raw"] == "əyləc diski"
    assert data["translation"] and data["vehicle_context"] == "W210"
    assert data["oem"]["status"] == "UNRESOLVED"
    assert data["photo"]["status"] == "NO_IMAGE"
    assert [c["external_code"] for c in data["candidates"]] == ["EY-002"]


def test_candidates_carry_names_and_synonyms_the_prompt_asks_for(shortlist):
    candidate = json.loads(build_user_message(
        "x", "x", OemEvidence(), PhotoEvidence(), shortlist))["candidates"][0]
    assert candidate["name_az"] and candidate["name_ru"]
    assert candidate["synonyms"]


# ── разбор ответа ───────────────────────────────────────────────────────────
def test_valid_select_is_parsed():
    data, error = parse_response(
        '{"decision":"select","external_code":"EY-002","confidence":"high",'
        '"reason":"тормозной диск","clarification_text":null}', {"EY-002"})
    assert error is None
    assert data["external_code"] == "EY-002"


@pytest.mark.parametrize("payload", [
    '{"decision":"unknown","external_code":null,"confidence":"low"}',
    '{"decision":"clarify","external_code":null,"confidence":"medium",'
    '"clarification_text":"диск или колодка?"}',
])
def test_refusals_are_valid_answers(payload):
    _, error = parse_response(payload, {"EY-002"})
    assert error is None


@pytest.mark.parametrize("payload,fragment", [
    ("не json", "JSON"),
    ('{"decision":"MAYBE"}', "недопустимое решение"),
    ('{"decision":"select","external_code":null}', "select без external_code"),
    ('{"decision":"select","external_code":"ZZ-999"}', "отсутствует среди кандидатов"),
    ('{"decision":"unknown","external_code":"EY-002"}', "не должно содержать"),
    ('{"decision":"select","external_code":"EY-002","confidence":0.9}', "вне набора"),
])
def test_malformed_responses_are_reported_not_repaired(payload, fragment):
    """Плохой ответ — факт для отчёта, а не повод «починить» решение."""
    _, error = parse_response(payload, {"EY-002"})
    assert error and fragment in error


def test_decision_case_is_normalised():
    data, error = parse_response(
        '{"decision":"SELECT","external_code":"EY-002","confidence":"HIGH"}',
        {"EY-002"})
    assert error is None and data["decision"].lower() == "select"


# ── поведение без кандидатов ────────────────────────────────────────────────
def test_no_candidates_means_unknown_without_calling_the_api():
    decision = ArbiterV3(client=None).decide(
        original_text="qwerty", item_raw="qwerty", oem=OemEvidence(),
        photo=PhotoEvidence(), retriever=RetrieverResult(status="EMPTY"))
    assert decision.decision == ARB_UNKNOWN
    assert decision.external_code is None
