"""Arbiter V4: версия рядом с V3, сравнение версий и очередь пилота."""

from __future__ import annotations

import json
import os

import pytest

from avtozap.arbiter import (ARBITERS, ArbiterV3, ArbiterV4, build_arbiter,
                             frozen_sha256, load_prompt, prompt_sha256)
from avtozap.config import (ARBITER_PROMPT_PATH, ARBITER_V4_PROMPT_PATH,
                            ARBITER_V4_PROMPT_SHA_PATH, RunConfig)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── V3 остаётся нетронутым ──────────────────────────────────────────────────
def test_v3_prompt_is_still_frozen_after_v4_appeared():
    """Появление V4 не должно ничего менять в V3 — иначе сравнивать не с чем."""
    assert prompt_sha256(ARBITER_PROMPT_PATH) == frozen_sha256()


def test_v4_is_a_separate_prompt_not_an_edit_of_v3():
    assert load_prompt(ARBITER_V4_PROMPT_PATH) != load_prompt(ARBITER_PROMPT_PATH)


def test_v4_prompt_checksum_is_tracked():
    """V4 не заморожен, но его правка обязана быть видимой в отчёте."""
    assert prompt_sha256(ARBITER_V4_PROMPT_PATH) == frozen_sha256(
        ARBITER_V4_PROMPT_SHA_PATH)


# ── сборка ──────────────────────────────────────────────────────────────────
def test_both_versions_are_selectable():
    assert isinstance(build_arbiter("v3", None), ArbiterV3)
    assert isinstance(build_arbiter("v4", None), ArbiterV4)
    assert sorted(ARBITERS) == ["v3", "v4"]


def test_an_unknown_version_is_an_error_not_a_silent_default():
    with pytest.raises(ValueError):
        build_arbiter("v5", None)


def test_the_run_config_defaults_to_the_frozen_version():
    assert RunConfig(input_path="—").arbiter_version == "v3"


def test_v4_reuses_the_v3_answer_contract():
    """Контракт ответа не менялся — валидатор и конвейер работают как были."""
    v4 = build_arbiter("v4", None)
    assert v4.decide.__func__ is ArbiterV3.decide


# ── что именно V4 говорит модели ────────────────────────────────────────────
def test_v4_answers_the_measured_failure_classes():
    """Каждое правило V4 отвечает классу ошибок, а не отдельной заявке.

    Классы взяты из живого прогона 200: модель судила по всему сообщению
    вместо своего предмета, уходила от первого кандидата к «более типичной»
    детали и уверенно отвечала там, где деталь вообще не названа.
    """
    prompt = load_prompt(ARBITER_V4_PROMPT_PATH)
    assert "You judge `item_raw` and nothing else" in prompt
    assert "NEVER answer `clarify` because the MESSAGE contains several parts" in prompt
    assert "Candidate #1 is the default answer" in prompt
    assert "oem.resolved_external_code" in prompt
    assert "`exact:`" in prompt and "`context:`" in prompt
    assert "do not know the part's name" in prompt


def test_v4_keeps_the_output_contract_verbatim():
    prompt = load_prompt(ARBITER_V4_PROMPT_PATH)
    assert '"decision":"select|unknown|clarify"' in prompt
    assert "external_code MUST exactly equal one supplied candidates" in prompt


def test_v4_contains_no_rule_keyed_to_a_test_row():
    """Запрет заказчика: никаких правил под конкретные строки эталона."""
    prompt = load_prompt(ARBITER_V4_PROMPT_PATH)
    assert "etalon" not in prompt.lower()
    for forbidden in ("469", "200", "rfq"):
        assert forbidden not in prompt.lower(), forbidden


# ── сравнение версий ────────────────────────────────────────────────────────
def _grade(etalon_rows, results_rows):
    import sys
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    from compare_arbiters import grade
    etalon = {r["rfq_id"]: r for r in etalon_rows}
    results: dict = {}
    for r in results_rows:
        results.setdefault(r["rfq_id"], []).append(r)
    return grade(etalon, results)


def test_comparison_separates_a_dangerous_answer_from_an_honest_question():
    """Главное различие: ответить вместо вопроса хуже, чем спросить лишний раз."""
    etalon = [
        {"rfq_id": "a", "expected_external_code": "UNKNOWN", "original_text": "x"},
        {"rfq_id": "b", "expected_external_code": "UNKNOWN", "original_text": "y"},
        {"rfq_id": "c", "expected_external_code": "KZ-001", "original_text": "z"},
        {"rfq_id": "d", "expected_external_code": "KZ-002", "original_text": "w"},
    ]
    results = [
        # Ответила там, где нужен был отказ — опасно.
        {"rfq_id": "a", "final_external_code": "KZ-009", "final_status": "SELECT"},
        # Отказалась там, где отказ и нужен — верно.
        {"rfq_id": "b", "final_external_code": None, "final_status": "REVIEW"},
        # Ответила не тем кодом.
        {"rfq_id": "c", "final_external_code": "KZ-050", "final_status": "SELECT"},
        # Переспросила там, где могла ответить.
        {"rfq_id": "d", "final_external_code": None, "final_status": "REVIEW"},
    ]
    graded = _grade(etalon, results)
    assert graded["dangerous"] == ["a"]
    assert graded["asked_rightly"] == ["b"]
    assert graded["wrong_code"] == ["c"]
    assert graded["asked_in_vain"] == ["d"]
    assert graded["correct"] == ["b"]


def test_a_multi_part_row_needs_every_code():
    etalon = [{"rfq_id": "m", "expected_external_code": "SR-002,SR-003",
               "original_text": "yağ hava filtiri"}]
    half = [{"rfq_id": "m", "final_external_code": "SR-002", "final_status": "SELECT"}]
    both = half + [{"rfq_id": "m", "final_external_code": "SR-003",
                    "final_status": "SELECT"}]
    assert _grade(etalon, half)["correct"] == []
    assert _grade(etalon, both)["correct"] == ["m"]


@pytest.mark.parametrize("correct_delta,danger_delta,expect", [
    (5, 0, "ПРИНИМАТЬ"),
    (5, 2, "РЕШАТЬ ЗАКАЗЧИКУ"),
    (0, -1, "ПРИНИМАТЬ"),
    (0, 0, "НЕ ПРИНИМАТЬ"),
])
def test_the_verdict_follows_the_customers_rule(correct_delta, danger_delta,
                                                expect):
    """Правило: принимаем, если семантика лучше и опасных ответов не больше."""
    import sys
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    from compare_arbiters import verdict

    a = {"correct": ["x"] * 10, "dangerous": ["y"] * 3}
    b = {"correct": ["x"] * (10 + correct_delta),
         "dangerous": ["y"] * (3 + danger_delta)}
    assert verdict(a, b).startswith(expect)


# ── очередь пилота ──────────────────────────────────────────────────────────
def test_the_pilot_queue_collects_only_what_needs_human_eyes():
    from avtozap.report import build_pilot_queue

    records = [
        # Закрыта верно и без вопросов — смотреть незачем.
        {"rfq_id": "ok", "action": "ANSWER", "final_external_code": "KZ-001",
         "expected_external_code": "KZ-001", "final_status": "SELECT"},
        # Переспросила — в очередь.
        {"rfq_id": "ask", "action": "ASK_BUYER", "final_external_code": None,
         "expected_external_code": "KZ-002", "final_status": "REVIEW"},
        # Ответила неверно — в очередь.
        {"rfq_id": "bad", "action": "ANSWER", "final_external_code": "KZ-009",
         "expected_external_code": "KZ-003", "final_status": "SELECT"},
        # Попросила фото — в очередь.
        {"rfq_id": "photo", "action": "ASK_PHOTO", "final_external_code": None,
         "expected_external_code": "UNKNOWN", "final_status": "UNKNOWN"},
    ]
    queue = build_pilot_queue(records)
    assert [q["rfq_id"] for q in queue] == ["ask", "bad", "photo"]
    assert queue[0]["why_here"] == "переспросили"
    assert queue[1]["why_here"] == "ответили неверно"
    # Поле под ответ человека есть у каждой записи и пустое.
    assert all(q["correct_external_code"] == "" for q in queue)


def test_the_pilot_queue_works_without_a_known_answer():
    """На живом потоке ожидаемого кода нет — очередь всё равно должна собираться."""
    from avtozap.report import build_pilot_queue

    queue = build_pilot_queue([
        {"rfq_id": "live", "action": "ASK_BUYER", "final_external_code": None,
         "final_status": "REVIEW"},
        {"rfq_id": "fine", "action": "ANSWER", "final_external_code": "KZ-001",
         "final_status": "SELECT"},
    ])
    assert [q["rfq_id"] for q in queue] == ["live"]
