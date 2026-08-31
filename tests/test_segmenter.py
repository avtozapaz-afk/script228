"""Layer 0 — LLM-сегментер проекта и его детерминированный запасной вариант.

Сети здесь нет: транспорт подставной.
"""

import pytest

from avtozap.layer0 import Layer0
from avtozap.retriever import RetrieverV2
from avtozap.segmenter import (
    SOURCE_FALLBACK,
    SOURCE_LLM,
    Segmenter,
    frozen_sha256,
    load_prompt,
    parse_response,
    prompt_sha256,
)


@pytest.fixture(scope="module")
def fallback() -> Layer0:
    return Layer0(RetrieverV2())


def segmenter_with(*script, fallback=None) -> Segmenter:
    from tests.test_llm import StubBackend, _Response
    from avtozap.llm import LlmClient
    responses = [_Response(s) if isinstance(s, str) else s for s in script]
    client = LlmClient("ключ-не-используется", backend=StubBackend(*responses))
    return Segmenter(client, model="stub", fallback=fallback)


# ── заморозка промпта ───────────────────────────────────────────────────────
def test_prompt_is_frozen():
    assert prompt_sha256() == frozen_sha256()


def test_prompt_is_the_supplied_segmenter_verbatim():
    text = load_prompt()
    assert text.startswith("# AVTOZAP SEGMENTER v1")
    assert "You do not choose part_id, external_code" in text
    assert "search_phrases are retrieval hints, not answers" in text


# ── разбор ответа ───────────────────────────────────────────────────────────
def test_valid_response_is_parsed():
    data, error = parse_response(
        '{"items":[{"raw":"əyləc diski","search_phrases":["диск"],'
        '"oem_code":null,"is_part_request":true}],"vehicle_context":"W210"}')
    assert error is None
    assert data["items"][0]["raw"] == "əyləc diski"
    assert data["vehicle_context"] == "W210"


@pytest.mark.parametrize("payload,fragment", [
    ("не json", "JSON"),
    ('{"items":[]}', "ни одного предмета"),
    ('{"vehicle_context":"W210"}', "ни одного предмета"),
    ('{"items":[{"raw":"  "}]}', "пустое поле raw"),
])
def test_malformed_responses_are_reported(payload, fragment):
    _, error = parse_response(payload)
    assert error and fragment in error


def test_search_phrases_are_capped_at_three():
    data, error = parse_response(
        '{"items":[{"raw":"x","search_phrases":["a","b","c","d","e"]}]}')
    assert error is None
    assert len(data["items"][0]["search_phrases"]) == 3


# ── работа сегментера ───────────────────────────────────────────────────────
def test_llm_items_become_layer0_items(fallback):
    seg = segmenter_with(
        '{"items":[{"raw":"naklatka","search_phrases":["əyləc bəndi"],'
        '"oem_code":null,"is_part_request":true},'
        '{"raw":"tormuz disk","search_phrases":[],"oem_code":null,'
        '"is_part_request":true}],"vehicle_context":"W210"}', fallback=fallback)
    result = seg.segment("naklatka və tormuz disk")
    assert result.source == SOURCE_LLM
    assert [i.item_raw for i in result.items] == ["naklatka", "tormuz disk"]
    assert result.items[0].search_phrases == ["əyləc bəndi"]
    assert result.vehicle_context == "W210"


def test_service_request_is_marked_not_a_part(fallback):
    seg = segmenter_with(
        '{"items":[{"raw":"Lyuk təmiri","search_phrases":[],"oem_code":null,'
        '"is_part_request":false}],"vehicle_context":null}', fallback=fallback)
    result = seg.segment("Lyuk təmiri")
    assert result.items[0].is_part_request is False


def test_oem_only_request_keeps_the_number(fallback):
    seg = segmenter_with(
        '{"items":[{"raw":"06A115561B","search_phrases":[],'
        '"oem_code":"06A115561B","is_part_request":true}],"vehicle_context":null}',
        fallback=fallback)
    result = seg.segment("06A115561B")
    assert result.items[0].oem_code == "06A115561B"


# ── запасной вариант ────────────────────────────────────────────────────────
def test_broken_llm_answer_falls_back_instead_of_losing_the_request(fallback):
    """Сбой сегментера не должен стоить нам заявки."""
    seg = segmenter_with("не json", fallback=fallback)
    result = seg.segment("naklatka və tormuz disk")
    assert result.source == SOURCE_FALLBACK
    assert len(result.items) == 2


def test_without_a_client_the_deterministic_segmenter_is_used(fallback):
    seg = Segmenter(None, model="stub", fallback=fallback)
    result = seg.segment("naklatka və tormuz disk")
    assert result.source == SOURCE_FALLBACK
    assert result.items


def test_api_failure_falls_back(fallback):
    from tests.test_llm import RateLimitError
    seg = segmenter_with(*[RateLimitError()] * 6, fallback=fallback)
    seg.client.max_retries = 2
    result = seg.segment("naklatka")
    assert result.source == SOURCE_FALLBACK
    assert result.items


def test_every_fallback_item_says_who_segmented_it(fallback):
    seg = segmenter_with("не json", fallback=fallback)
    for item in seg.segment("naklatka və tormuz disk").items:
        assert item.reason.startswith(SOURCE_FALLBACK)
