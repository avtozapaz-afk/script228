"""Сквозной прогон конвейера в mock-режиме — без сети."""

import json
import os

import pytest

import run_test
from avtozap.config import RunConfig
from avtozap.io_utils import read_results
from avtozap.pipeline import Pipeline, attribute_failures, expected_codes
from avtozap.types import (
    FINAL_ERROR,
    FINAL_REVIEW,
    FINAL_SELECT,
    FINAL_UNKNOWN,
    LAYER_ARBITER,
    LAYER_RETRIEVER,
)

SAMPLE = os.path.join("data", "requests_300.jsonl")
VALID_STATUSES = {FINAL_SELECT, FINAL_UNKNOWN, FINAL_REVIEW, FINAL_ERROR}


@pytest.fixture(scope="module")
def pipeline() -> Pipeline:
    return Pipeline(RunConfig(input_path=SAMPLE, mock=True))


def test_a_multi_part_request_yields_one_record_per_part(pipeline):
    records = pipeline.process_request(
        {"rfq_id": "t", "original_text": "Naklatka və tormuz disk"}, 0)
    assert len(records) == 2
    assert [r.item_index for r in records] == [0, 1]


def test_every_record_has_a_valid_final_status(pipeline):
    for text in ["bufer", "qwerty zxcvbn", "sol güzgü", ""]:
        for record in pipeline.process_request({"rfq_id": "t", "original_text": text}, 0):
            assert record.final_status in VALID_STATUSES


def test_a_selected_part_always_exists_in_the_dictionary(pipeline):
    for text in ["bufer", "naklatka", "radiator", "əyləc diski", "fara"]:
        for record in pipeline.process_request({"rfq_id": "t", "original_text": text}, 0):
            if record.final_external_code:
                assert record.final_external_code in pipeline.retriever.dict


def test_a_selected_part_was_always_offered_by_the_retriever(pipeline):
    for text in ["bufer", "naklatka", "sveça", "stupitsa podsipniki"]:
        for record in pipeline.process_request({"rfq_id": "t", "original_text": text}, 0):
            if record.final_external_code:
                assert record.final_external_code in record.retriever.codes


def test_nonsense_never_produces_a_code_outside_the_shortlist(pipeline):
    """На бессмыслицу ретривер всё равно что-то находит по нечёткому сходству.

    Отказаться — работа арбитра (в промпте V3 это ABSENT-CANDIDATE GUARD), а не
    ретривера. Здесь проверяем инвариант, который обязан держаться всегда:
    что бы ни вышло, это реальный код словаря из выданного shortlist.
    """
    for record in pipeline.process_request(
            {"rfq_id": "t", "original_text": "qwerty zxcvbn"}, 0):
        if record.final_external_code:
            assert record.final_external_code in pipeline.retriever.dict
            assert record.final_external_code in record.retriever.codes


def test_an_empty_message_does_not_crash(pipeline):
    records = pipeline.process_request({"rfq_id": "t", "original_text": ""}, 0)
    assert records[0].final_status == FINAL_UNKNOWN


def test_a_layer_raising_does_not_kill_the_run(pipeline, monkeypatch):
    """Одно сломанное обращение не должно ронять прогон из 300 кейсов."""
    def boom(*args, **kwargs):
        raise RuntimeError("слой упал")
    monkeypatch.setattr(pipeline.retriever, "retrieve", boom)
    records = pipeline.process_request({"rfq_id": "t", "original_text": "bufer"}, 0)
    assert records[0].final_status == FINAL_ERROR
    assert "слой упал" in (records[0].error or "")


def test_record_serializes_to_json(pipeline):
    record = pipeline.process_request({"rfq_id": "t", "original_text": "bufer"}, 0)[0]
    assert json.loads(json.dumps(record.to_dict(), ensure_ascii=False))["rfq_id"] == "t"


# ── эталон и разбор слабого звена ──────────────────────────────────────────
@pytest.mark.parametrize("row,expected", [
    ({"expected_external_code": "EY-001"}, ["EY-001"]),
    ({"expected_external_code": "EY-001, EY-002"}, ["EY-001", "EY-002"]),
    ({"expected_part_ids": ["EY-001", "EY-002"]}, ["EY-001", "EY-002"]),
    ({}, []),
    ({"expected_external_code": ""}, []),
])
def test_expected_ids_are_parsed(row, expected):
    assert expected_codes(row) == expected


def test_a_miss_the_retriever_caused_is_not_blamed_on_the_arbiter(pipeline):
    """Двигателя нет среди кандидатов по слову «bufer» — виноват не арбитр."""
    records = pipeline.process_request(
        {"rfq_id": "t", "original_text": "bufer", "expected_external_code": "MU-011"}, 0)
    assert "MU-011" not in records[0].retriever.codes
    assert records[0].failure_layer == LAYER_RETRIEVER


def test_the_arbiter_is_blamed_only_when_it_held_the_right_candidate(pipeline):
    records = pipeline.process_request(
        {"rfq_id": "t", "original_text": "bufer", "expected_external_code": "KZ-002"}, 0)
    assert "KZ-002" in records[0].retriever.codes
    assert records[0].failure_layer == LAYER_ARBITER


def test_a_correct_answer_blames_nobody(pipeline):
    records = pipeline.process_request(
        {"rfq_id": "t", "original_text": "əyləc bəndi",
         "expected_external_code": "EY-001"}, 0)
    if records[0].final_external_code == "EY-001":
        assert records[0].failure_layer == "-"


# ── прогон целиком ─────────────────────────────────────────────────────────
def test_full_mock_run_produces_every_output(tmp_path):
    out = str(tmp_path)
    assert run_test.main(["--input", SAMPLE, "--out-dir", out, "--mock",
                          "--max-requests", "6"]) == 0
    for name in ("results.jsonl", "results.csv", "summary.json", "summary.md",
                 "failure_analysis.md"):
        assert os.path.exists(os.path.join(out, name)), name
    records = read_results(os.path.join(out, "results.jsonl"))
    assert len(records) >= 5


def test_rerunning_skips_finished_items(tmp_path):
    out = str(tmp_path)
    args = ["--input", SAMPLE, "--out-dir", out, "--mock", "--max-requests", "6"]
    run_test.main(args)
    before = len(read_results(os.path.join(out, "results.jsonl")))
    run_test.main(args)
    assert len(read_results(os.path.join(out, "results.jsonl"))) == before


def test_no_resume_starts_over(tmp_path):
    out = str(tmp_path)
    args = ["--input", SAMPLE, "--out-dir", out, "--mock", "--max-requests", "6"]
    run_test.main(args)
    before = len(read_results(os.path.join(out, "results.jsonl")))
    run_test.main(args + ["--no-resume"])
    assert len(read_results(os.path.join(out, "results.jsonl"))) == before


def test_out_dir_is_created_automatically(tmp_path):
    out = os.path.join(str(tmp_path), "новая", "папка")
    assert run_test.main(["--input", SAMPLE, "--out-dir", out, "--mock",
                          "--max-requests", "2"]) == 0
    assert os.path.isdir(out)


def test_the_api_key_never_reaches_the_output(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-секрет-не-должен-утечь")
    out = str(tmp_path)
    run_test.main(["--input", SAMPLE, "--out-dir", out, "--mock",
                   "--max-requests", "4"])
    for name in os.listdir(out):
        with open(os.path.join(out, name), encoding="utf-8-sig") as fh:
            assert "sk-секрет" not in fh.read()


# ── реальный путь Arbiter V3 (транспорт подставной, логика настоящая) ───────
def test_the_real_arbiter_path_runs_end_to_end():
    """Тот же код, что пойдёт в бой, но без сети: mock=False, живой ArbiterV3."""
    from tests.test_llm import StubBackend, _Response
    from avtozap.llm import LlmClient

    # Первый ответ получает сегментер (LLM PASS A), второй — арбитр (PASS B).
    backend = StubBackend(
        _Response('{"items":[{"raw":"əyləc diski","search_phrases":["əyləc diski"],'
                  '"oem_code":null,"is_part_request":true}],"vehicle_context":null}'),
        _Response('{"decision":"select","external_code":"EY-002","confidence":"high",'
                  '"reason":"покупатель просит тормозной диск"}'))
    client = LlmClient("ключ-не-используется", backend=backend)
    pipeline = Pipeline(RunConfig(input_path=SAMPLE, mock=False), client=client)

    record = pipeline.process_request(
        {"rfq_id": "t", "original_text": "əyləc diski"}, 0)[0]
    assert record.layer0_source == "llm_segmenter_v1"
    assert record.arbiter.decision == "select"
    assert record.validator.status == "PASS"
    assert (record.final_external_code, record.final_status) == ("EY-002", FINAL_SELECT)


def test_an_arbiter_answer_outside_the_candidates_never_becomes_a_result():
    """Главная гарантия: выдуманный код не может доехать до итога."""
    from tests.test_llm import StubBackend, _Response
    from avtozap.llm import LlmClient

    backend = StubBackend(
        _Response('{"items":[{"raw":"əyləc diski","search_phrases":[],'
                  '"oem_code":null,"is_part_request":true}],"vehicle_context":null}'),
        *[_Response('{"decision":"select","external_code":"ZZ-999",'
                    '"confidence":"high","reason":"x"}')] * 5)
    pipeline = Pipeline(RunConfig(input_path=SAMPLE, mock=False),
                        client=LlmClient("ключ", backend=backend))
    record = pipeline.process_request(
        {"rfq_id": "t", "original_text": "əyləc diski"}, 0)[0]
    assert record.final_external_code is None
    assert record.final_status in (FINAL_ERROR, FINAL_UNKNOWN)


def test_the_segmenter_falls_back_instead_of_losing_the_request():
    """Сломанный ответ сегментера не должен стоить нам запроса."""
    from tests.test_llm import StubBackend, _Response
    from avtozap.llm import LlmClient

    backend = StubBackend(
        _Response("не json"),
        _Response('{"decision":"unknown","external_code":null,"confidence":"low"}'))
    pipeline = Pipeline(RunConfig(input_path=SAMPLE, mock=False),
                        client=LlmClient("ключ", backend=backend))
    records = pipeline.process_request(
        {"rfq_id": "t", "original_text": "əyləc diski"}, 0)
    assert records
    assert records[0].layer0_source == "deterministic_fallback"
    assert records[0].item_raw
