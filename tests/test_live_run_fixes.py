"""Регрессии, найденные на живом прогоне 300 заявок (31.08).

Каждый тест здесь закрывает конкретный сбой, который стоил тому прогону
результата. Сети не требуют.
"""

import json

import pytest

from avtozap.arbiter import build_user_message
from avtozap.config import RunConfig
from avtozap.encoding_guard import check, looks_mojibake, repair
from avtozap.llm import LlmClient, TruncatedResponse
from avtozap.pipeline import Pipeline
from avtozap.report import build_summary, render_markdown
from avtozap.types import Candidate, OemEvidence, PhotoEvidence, RetrieverResult

# Порча из живого лога: UTF-8 прочитан как latin-1.
BROKEN = "Matorun padu" + chr(0xC5) + chr(0x9F) + "kas" + chr(0xC4) + chr(0xB1)
CLEAN = "Matorun paduşkası"


# ── 1. Обрыв длинного ответа ────────────────────────────────────────────────
class _Message:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content, finish_reason="stop"):
        self.message = _Message(content)
        self.finish_reason = finish_reason


class _Response:
    def __init__(self, content, finish_reason="stop", model="stub"):
        self.choices = [_Choice(content, finish_reason)]
        self.model = model


class _Completions:
    def __init__(self, script):
        self.script = list(script)
        self.calls = 0
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        self.calls += 1
        item = self.script.pop(0) if self.script else _Response("{}")
        if isinstance(item, Exception):
            raise item
        return item


class Backend:
    def __init__(self, *script):
        self.chat = type("Chat", (), {})()
        self.chat.completions = _Completions(script)


def client_for(*script) -> LlmClient:
    return LlmClient("ключ-не-используется", sleep=lambda _: None,
                     backend=Backend(*script))


def test_a_truncated_answer_is_named_as_such_not_as_broken_json():
    """В живом прогоне обрыв выглядел как «ошибка разбора JSON» и уводил не туда."""
    client = client_for(_Response('{"items": [{"raw": "Kap', finish_reason="length"))
    with pytest.raises(TruncatedResponse) as exc:
        client.complete("m", [{"role": "user", "content": "x"}], max_tokens=10)
    assert "оборван" in str(exc.value)


def test_a_truncated_answer_is_not_retried():
    """Повтор с теми же параметрами оборвётся так же — попытки только тратят время."""
    client = client_for(*[_Response("{", finish_reason="length")] * 5)
    with pytest.raises(TruncatedResponse):
        client.complete("m", [{"role": "user", "content": "x"}])
    assert client._client.chat.completions.calls == 1


def test_the_segmenter_gets_room_for_a_ten_part_request():
    """Три заявки упали именно на длинном JSON со списком деталей."""
    from avtozap.config import SEGMENTER_MAX_TOKENS
    assert SEGMENTER_MAX_TOKENS >= 3000


def test_a_truncated_segmenter_answer_falls_back_instead_of_losing_the_request():
    from avtozap.layer0 import Layer0
    from avtozap.retriever import RetrieverV2
    from avtozap.segmenter import SOURCE_FALLBACK, Segmenter

    retriever = RetrieverV2()
    seg = Segmenter(client_for(_Response('{"items": [{"raw": "Kap',
                                         finish_reason="length")),
                    model="stub", fallback=Layer0(retriever))
    result = seg.segment("Naklatka və tormuz disk")
    assert result.source == SOURCE_FALLBACK
    assert len(result.items) == 2


# ── 2. Ложные отказы «две разные детали» ────────────────────────────────────
def _shortlist() -> RetrieverResult:
    return RetrieverResult(status="OK", candidates=[Candidate(
        external_code="EY-002", name_ru="Тормозной диск", name_az="Apornu",
        category="Диски/барабаны", score=0.999, reason="exact")])


def test_item_raw_comes_before_original_text_in_the_payload():
    """Промпт V3 судит item_raw; original_text не должен идти первым.

    В живом прогоне 11 из 12 отказов «в запросе две разные детали» пришлись на
    предметы, которые Layer 0 уже разрезал верно.
    """
    payload = json.loads(build_user_message(
        "Naklatka və tormuz disk", "tormuz disk",
        OemEvidence(), PhotoEvidence(), _shortlist()))
    assert list(payload)[0] == "item_raw"


def test_sibling_items_are_passed_as_data():
    """Соседние предметы сообщения обрабатываются своими запусками — это факт."""
    payload = json.loads(build_user_message(
        "Naklatka və tormuz disk", "tormuz disk",
        OemEvidence(), PhotoEvidence(), _shortlist(),
        other_items=["Naklatka"]))
    assert payload["other_items_in_same_message"] == ["Naklatka"]


def test_no_sibling_field_when_the_message_holds_one_part():
    payload = json.loads(build_user_message(
        "tormuz disk", "tormuz disk", OemEvidence(), PhotoEvidence(), _shortlist()))
    assert "other_items_in_same_message" not in payload


# ── 3. Дорезка предмета с двумя разными деталями ────────────────────────────
@pytest.fixture(scope="module")
def pipeline() -> Pipeline:
    return Pipeline(RunConfig(input_path="-", mock=True))


def items_for(pipeline, text: str) -> list[str]:
    return [r.item_raw for r in
            pipeline.process_request({"rfq_id": "t", "original_text": text}, 0)]


@pytest.mark.parametrize("text,count", [
    ("Qabaq arxa apornu ve naklatka", 2),      # диск + колодка
    ("Turbo katrici və turbo zbor", 2),
    ("Naklatka və tormuz disk", 2),
])
def test_two_different_parts_are_split_before_the_arbiter(pipeline, text, count):
    assert len(items_for(pipeline, text)) == count


@pytest.mark.parametrize("text", [
    "ön bufer arxa bufer",       # одна деталь в двух положениях
    "sol sağ güzgü",
    "termostat",
])
def test_one_part_in_several_positions_is_never_split(pipeline, text):
    """Признак обязан остаться при своей детали.

    Иначе «arxa» отрывается от бампера и утаскивает ответ в задний фонарь.
    """
    assert len(items_for(pipeline, text)) == 1


def test_positions_stay_attached_to_their_part(pipeline):
    records = pipeline.process_request(
        {"rfq_id": "t", "original_text": "ön bufer arxa bufer"}, 0)
    assert len(records) == 1
    assert "bufer" in records[0].item_raw.lower()
    assert records[0].item_raw.count("bufer") == 2


# ── 4. Порча кодировки не должна проходить молча ────────────────────────────
def test_mojibake_from_the_live_run_is_detected():
    assert looks_mojibake(BROKEN)


def test_the_original_text_is_reconstructible():
    assert repair(BROKEN) == CLEAN


@pytest.mark.parametrize("text", [
    "Əyləc diski, mühərrik yastığı, GÜZGÜ",
    "Тормозной диск, подушка двигателя",
    "Naklatka ve tormuz disk",
    "1K0 615 301 AA",
    "",
])
def test_clean_text_is_never_flagged(text):
    assert not looks_mojibake(text)


def test_the_warning_names_the_cause_and_the_probable_original():
    message = check(BROKEN, "ответ сегментера")
    assert "latin-1" in message and CLEAN in message


def test_a_corrupted_run_is_marked_unusable_in_the_summary():
    """Прогон с битой кодировкой нельзя подавать как измерение качества."""
    record = {
        "rfq_id": "a", "item_index": 0, "final_status": "UNKNOWN",
        "final_external_code": None, "encoding_warning": "битая кодировка",
        "layer0_status": "OK", "failure_layer": "-",
        "oem": {"status": "NONE"}, "photo": {"status": "NO_IMAGE"},
        "retriever": {"status": "OK", "candidates": []},
        "arbiter": {"decision": "unknown"}, "validator": {"status": "PASS"},
    }
    summary = build_summary([record], 1)
    assert summary["encoding_corrupted_items"] == 1
    assert "НЕПРИГОДЕН" in render_markdown(summary)


def test_a_clean_run_carries_no_such_warning():
    record = {
        "rfq_id": "a", "item_index": 0, "final_status": "SELECT",
        "final_external_code": "EY-002", "encoding_warning": None,
        "layer0_status": "OK", "failure_layer": "-",
        "oem": {"status": "NONE"}, "photo": {"status": "NO_IMAGE"},
        "retriever": {"status": "OK", "candidates": []},
        "arbiter": {"decision": "select"}, "validator": {"status": "PASS"},
    }
    assert "НЕПРИГОДЕН" not in render_markdown(build_summary([record], 1))


# ── 5. Транспорт на Python не теряет азербайджанские буквы ──────────────────
def test_the_python_transport_round_trips_azerbaijani():
    """Клиент openai кодирует тело явно в UTF-8 — порчи из живого лога тут быть не может."""
    import httpx

    text = "Matorun paduşkası, əyləc diski, GÜZGÜ"
    body = httpx.Request("POST", "https://x/", json={"m": text}).content
    assert json.loads(body.decode("utf-8"))["m"] == text

    response = httpx.Response(
        200, content=json.dumps({"m": text}, ensure_ascii=False).encode("utf-8"),
        headers={"content-type": "application/json; charset=utf-8"})
    assert json.loads(response.text)["m"] == text


# ── 6. Дорезка именно на пути LLM-сегментера (как в живом прогоне) ──────────
def _segmenter_says(raw: str) -> str:
    return json.dumps({"items": [{"raw": raw, "search_phrases": [],
                                  "oem_code": None, "is_part_request": True}],
                       "vehicle_context": None}, ensure_ascii=False)


def _pipeline_with(*script) -> Pipeline:
    return Pipeline(RunConfig(input_path="-", mock=False),
                    client=LlmClient("ключ", sleep=lambda _: None,
                                     backend=Backend(*script)))


def test_an_under_split_segmenter_item_is_cut_before_the_arbiter():
    """Ровно случай живого прогона: сегментер отдал две детали одним предметом.

    Раньше арбитр отвечал clarify по правилу MULTI-PART GUARD, и обе детали
    терялись. Теперь предмет дорезается ДО арбитра.
    """
    pipeline = _pipeline_with(
        _Response(_segmenter_says("Qabaq arxa apornu ve naklatka")),
        *[_Response('{"decision":"select","external_code":"EY-002",'
                    '"confidence":"high","reason":"диск"}')] * 4)
    records = pipeline.process_request(
        {"rfq_id": "t", "original_text": "Qabaq arxa apornu ve naklatka"}, 0)
    assert len(records) == 2
    assert all("resplit_multi_part" in r.layer0_reason for r in records)


def test_a_single_part_from_the_segmenter_is_left_alone():
    pipeline = _pipeline_with(
        _Response(_segmenter_says("ön bufer arxa bufer")),
        _Response('{"decision":"select","external_code":"KZ-001",'
                  '"confidence":"high","reason":"бампер"}'))
    records = pipeline.process_request(
        {"rfq_id": "t", "original_text": "Ön və arxa bufer"}, 0)
    assert len(records) == 1
    assert "resplit_multi_part" not in records[0].layer0_reason


def test_corrupted_text_from_the_segmenter_is_flagged_on_the_record():
    pipeline = _pipeline_with(
        _Response(_segmenter_says(BROKEN)),
        _Response('{"decision":"unknown","external_code":null,"confidence":"low"}'))
    record = pipeline.process_request(
        {"rfq_id": "t", "original_text": CLEAN}, 0)[0]
    assert record.encoding_warning
    assert CLEAN in record.encoding_warning
