"""Эталоны: правило подсчёта, дубли кодов и политика порога.

Сети не требуют.
"""

import json
import os

import pytest

from avtozap.config import RunConfig
from avtozap.dictionary import load, load_duplicates
from avtozap.pipeline import Pipeline
from avtozap.policy import (
    ACTION_ANSWER,
    ACTION_ASK_BUYER,
    ACTION_ASK_PHOTO,
    ACTION_ERROR,
    ACTION_PASS_TO_SHOP,
    buyer_says_they_do_not_know_the_name,
    decide_action,
    question_for,
)
from avtozap.retriever import RetrieverV2
from avtozap.types import (
    ARB_CLARIFY,
    ARB_ERROR,
    ARB_SELECT,
    ARB_UNKNOWN,
    FINAL_ERROR,
    FINAL_REVIEW,
    FINAL_SELECT,
    FINAL_UNKNOWN,
    VAL_DOWNGRADE,
    VAL_PASS,
    VAL_REJECT,
    ArbiterDecision,
    ValidatorResult,
)

ETALON = os.path.join("data", "etalon_200.jsonl")


@pytest.fixture(scope="module")
def etalon() -> list[dict]:
    with open(ETALON, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


# ── сам эталон ──────────────────────────────────────────────────────────────
def test_the_etalon_holds_200_requests(etalon):
    assert len(etalon) == 200


def test_the_production_baseline_is_128(etalon):
    """Число, рядом с которым надо ставить наш результат."""
    baseline = sum(1 for r in etalon
                   if r["reference_production_correct"].lower() == "да")
    assert baseline == 128


def test_27_requests_expect_no_code_at_all(etalon):
    """Пустой правильный код — это ответ, а не пропуск разметки."""
    assert sum(1 for r in etalon
               if r["expected_external_code"] == "UNKNOWN") == 27


def test_the_input_carries_raw_text_only(etalon):
    """Разметка и ответы боевой системы на вход конвейера не подаются."""
    for row in etalon:
        assert row["original_text"].strip()
        assert "деталь_из_заявки" not in row
        assert "item_raw" not in row and "candidates" not in row


def test_expected_codes_exist_in_the_dictionary(etalon):
    dictionary = load()
    unknown = [r["expected_external_code"] for r in etalon
               if r["expected_external_code"] != "UNKNOWN"
               and r["expected_external_code"] not in dictionary]
    assert unknown == [], f"кодов нет в словаре 541: {sorted(set(unknown))}"


def test_no_expected_code_is_a_known_duplicate(etalon):
    """Разметка должна ссылаться на канонические коды, а не на исчезающие."""
    duplicates = load_duplicates()
    bad = [r["expected_external_code"] for r in etalon
           if r["expected_external_code"] in duplicates]
    assert bad == []


# ── дубли кодов ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("duplicate,canonical", [
    ("RG-04", "MU-089"),        # воздушный патрубок
    ("MU-012", "MU-078"),       # прокладка ГБЦ
    ("SO-025", "SO-005"),       # корпус термостата
])
def test_duplicates_map_to_their_canonical_code(duplicate, canonical):
    assert load().canonical(duplicate) == canonical


def test_a_normal_code_is_left_alone():
    assert load().canonical("EY-002") == "EY-002"


@pytest.mark.parametrize("query", ["hava borusu", "termostat korpusu",
                                   "qalofka praklatkasi"])
def test_the_shortlist_never_offers_a_dying_code(query):
    """Предлагать арбитру код, который исчезнет, нельзя."""
    retriever = RetrieverV2()
    codes = retriever.retrieve(query).codes
    assert not (set(codes) & set(retriever.dict.duplicates))


def test_the_canonical_code_replaces_the_duplicate_in_the_shortlist():
    retriever = RetrieverV2()
    assert "MU-089" in retriever.retrieve("hava borusu").codes


# ── «покупатель не знает названия» ──────────────────────────────────────────
@pytest.mark.parametrize("text", [
    "Kia k5 2022 gt line keçə lazimdir. Basqa adi bilmirem nedir",
    "не знаю как называется, деталь под капотом",
    "şəkil göndərdim, bu detal lazımdır",
])
def test_a_buyer_admitting_they_do_not_know_the_name_is_detected(text):
    assert buyer_says_they_do_not_know_the_name(text)


@pytest.mark.parametrize("text", [
    "Qabaq bufer", "Naklatka və tormuz disk", "W210 e230 termostat",
    "Sol arxa top və karobkada şəklini yerləşdirdiyim ehtiyyat hissəsi",
])
def test_an_ordinary_request_is_not_mistaken_for_that(text):
    assert not buyer_says_they_do_not_know_the_name(text)


# ── политика порога ─────────────────────────────────────────────────────────
def _select(confidence="high") -> ArbiterDecision:
    return ArbiterDecision(decision=ARB_SELECT, external_code="EY-002",
                           confidence=confidence)


PASS = ValidatorResult(VAL_PASS, "", "ок")


def test_a_confident_answer_is_answered():
    action, _ = decide_action(FINAL_SELECT, _select(), PASS, "əyləc diski")
    assert action == ACTION_ANSWER


def test_low_confidence_asks_the_buyer_instead_of_guessing():
    """Правило заказчика: не угадывать, а попросить описать иначе."""
    action, _ = decide_action(
        FINAL_REVIEW, _select("low"),
        ValidatorResult(VAL_DOWNGRADE, "V7", "уверенность low"), "nese detal")
    assert action == ACTION_ASK_BUYER


def test_the_second_failed_attempt_goes_to_the_shops():
    """Держать покупателя в переписке дольше одного уточнения не нужно."""
    action, reason = decide_action(
        FINAL_UNKNOWN, ArbiterDecision(decision=ARB_UNKNOWN, confidence="low"),
        PASS, "nese detal", attempt=2)
    assert action == ACTION_PASS_TO_SHOP
    assert "магазин" in reason


def test_a_buyer_without_the_name_is_asked_for_a_photo():
    action, _ = decide_action(
        FINAL_UNKNOWN, ArbiterDecision(decision=ARB_UNKNOWN, confidence="low"),
        PASS, "Basqa adi bilmirem nedir")
    assert action == ACTION_ASK_PHOTO


def test_a_photo_already_supplied_is_not_asked_for_again():
    action, _ = decide_action(
        FINAL_UNKNOWN, ArbiterDecision(decision=ARB_UNKNOWN, confidence="low"),
        PASS, "Basqa adi bilmirem nedir", has_photo=True)
    assert action != ACTION_ASK_PHOTO


def test_the_photo_request_wins_over_the_second_attempt_rule():
    """Если покупатель не знает названия, фото полезнее, чем сырой текст."""
    action, _ = decide_action(
        FINAL_UNKNOWN, ArbiterDecision(decision=ARB_UNKNOWN, confidence="low"),
        PASS, "adını bilmirəm", attempt=2)
    assert action == ACTION_ASK_PHOTO


def test_a_pipeline_failure_is_not_dressed_up_as_a_question():
    action, _ = decide_action(
        FINAL_ERROR, ArbiterDecision(decision=ARB_ERROR, reason="таймаут"),
        ValidatorResult(VAL_REJECT, "V6", "сбой"), "əyləc diski")
    assert action == ACTION_ERROR


def test_clarify_carries_the_arbiters_own_question():
    decision = ArbiterDecision(decision=ARB_CLARIFY, confidence="low",
                               clarification_text="диск или колодка?")
    action, reason = decide_action(FINAL_REVIEW, decision, PASS, "apornu")
    assert action == ACTION_ASK_BUYER
    assert reason == "диск или колодка?"


@pytest.mark.parametrize("action,expects_text", [
    (ACTION_ASK_BUYER, True),
    (ACTION_ASK_PHOTO, True),
    (ACTION_PASS_TO_SHOP, False),   # покупателю ничего не пишем
    (ACTION_ANSWER, False),
])
def test_only_the_asking_actions_produce_a_message(action, expects_text):
    assert bool(question_for(action, "причина")) is expects_text


# ── правило подсчёта ────────────────────────────────────────────────────────
def _scored(expected: str, final_status: str, code: str | None) -> int:
    from scripts.score_etalon import score_run
    etalon = [{"rfq_id": "x", "original_text": "t",
               "expected_external_code": expected,
               "reference_production_correct": "нет"}]
    results = {"x": [{"rfq_id": "x", "final_status": final_status,
                      "final_external_code": code, "arbiter": {}}]}
    return score_run(etalon, results)["correct"]


def test_refusing_when_no_code_is_expected_counts_as_a_hit():
    """У 27 заявок правильный ответ — отказ. Отказались — попали."""
    assert _scored("UNKNOWN", "UNKNOWN", None) == 1
    assert _scored("UNKNOWN", "REVIEW", None) == 1


def test_answering_when_no_code_is_expected_counts_as_a_miss():
    assert _scored("UNKNOWN", "SELECT", "EY-002") == 0


def test_the_right_code_counts_as_a_hit():
    assert _scored("EY-002", "SELECT", "EY-002") == 1


def test_the_wrong_code_counts_as_a_miss():
    assert _scored("EY-002", "SELECT", "KZ-020") == 0


def test_refusing_when_a_code_was_expected_counts_as_a_miss():
    assert _scored("EY-002", "UNKNOWN", None) == 0


# ── сквозной прогон по эталону ──────────────────────────────────────────────
def test_the_pipeline_records_an_action_for_every_item():
    pipeline = Pipeline(RunConfig(input_path=ETALON, mock=True))
    for text in ["W210 e230 termostat", "Basqa adi bilmirem nedir", "qwerty"]:
        for record in pipeline.process_request(
                {"rfq_id": "t", "original_text": text}, 0):
            assert record.action
            assert record.action_reason


def test_a_final_code_is_always_canonical():
    pipeline = Pipeline(RunConfig(input_path=ETALON, mock=True))
    duplicates = pipeline.retriever.dict.duplicates
    for text in ["hava borusu", "termostat korpusu", "qalofka praklatkasi"]:
        for record in pipeline.process_request(
                {"rfq_id": "t", "original_text": text}, 0):
            assert record.final_external_code not in duplicates


# ── основной набор точности: 469 заявок ─────────────────────────────────────
ETALON_469 = os.path.join("data", "etalon_469.jsonl")


@pytest.fixture(scope="module")
def etalon469() -> list[dict]:
    with open(ETALON_469, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def test_the_accuracy_set_holds_469_requests(etalon469):
    assert len(etalon469) == 469


def test_every_request_expects_a_code(etalon469):
    """В наборе точности заявок с ответом «кода быть не должно» нет.

    Поэтому правило «переспроси / попроси фото» им не проверить — для этого
    остаётся набор поведения на 200 заявок.
    """
    assert all(r["expected_external_code"] != "UNKNOWN" for r in etalon469)


def test_the_accuracy_set_carries_raw_text_only(etalon469):
    """Перевод и названия деталей — справка, а не вход конвейера.

    Перевод во время работы взять неоткуда, а подсказать конвейеру название —
    значит мерить не его.
    """
    for row in etalon469:
        assert row["original_text"].strip()
        assert "translation" not in row
        assert "name_ru" not in row and "name_az" not in row


def test_duplicate_codes_are_canonicalised_on_import(etalon469):
    duplicates = load_duplicates()
    assert not [r for r in etalon469
                if r["expected_external_code"] in duplicates]


def test_every_row_is_scorable_against_the_september_dictionary(etalon469):
    """KZ-094 и KZ-095 приехали со словарём 571 — все 469 строк считаются.

    Механизм ``scorable`` остаётся: если разметка снова обгонит словарь, такие
    строки не будут ни засчитаны, ни провалены.
    """
    dictionary = load()
    for row in etalon469:
        expected = row["expected_external_code"]
        assert row["scorable"] == (expected in dictionary), expected
    assert sum(1 for r in etalon469 if not r["scorable"]) == 0


def test_scorable_rows_all_resolve_to_real_parts(etalon469):
    dictionary = load()
    for row in etalon469:
        if row["scorable"]:
            assert dictionary.get(row["expected_external_code"]) is not None


def test_unscorable_rows_are_skipped_by_the_scorer():
    from scripts.score_etalon import score_run

    etalon = [
        {"rfq_id": "a", "original_text": "t", "expected_external_code": "KZ-095",
         "scorable": False},
        {"rfq_id": "b", "original_text": "t", "expected_external_code": "EY-002",
         "scorable": True},
    ]
    results = {"b": [{"rfq_id": "b", "final_status": "SELECT",
                      "final_external_code": "EY-002", "arbiter": {}}]}
    scored = score_run(etalon, results)
    assert scored["scored"] == 1 and scored["correct"] == 1
    assert scored["unscorable"] == 1


def test_the_behaviour_set_is_still_available(etalon):
    """Набор на 200 заявок нельзя терять: только он меряет отказы и переспрос."""
    assert len(etalon) == 200
    assert sum(1 for r in etalon
               if r["expected_external_code"] == "UNKNOWN") == 27
    assert sum(1 for r in etalon
               if r["reference_production_correct"].lower() == "да") == 128


# ── запускаемый файл для прогона ────────────────────────────────────────────
def test_the_runnable_script_exists_and_compiles():
    """Один файл, который заказчик запускает со своим ключом."""
    import py_compile
    py_compile.compile("PROGON_ETALON_469.py", doraise=True)


def test_the_scorer_survives_a_run_killed_mid_write(tmp_path):
    """Прогон возобновляемый, значит в файле может остаться обрывок строки.

    Харнесс его переживает — подсчёт обязан тоже, иначе прерванный прогон
    нельзя было бы досчитать.
    """
    from scripts.score_etalon import load_results

    path = tmp_path / "results.jsonl"
    path.write_text(
        '{"rfq_id": "a", "final_external_code": "EY-002"}\n'
        '{"rfq_id": "оборв\n'                       # процесс убили здесь
        '{"rfq_id": "b", "final_external_code": "KZ-001"}\n',
        encoding="utf-8")
    loaded = load_results(str(path))
    assert sorted(loaded) == ["a", "b"]


# ── сравнение с боевой системой на неполном прогоне ─────────────────────────
def test_an_unfinished_run_does_not_credit_itself_with_unscored_rows():
    """Непосчитанная заявка не может быть записана нам в актив.

    Раньше сравнение считало правыми всех, кого нет в списке ошибок, — включая
    тех, до кого прогон не дошёл. На оборванном прогоне картина получалась тем
    красивее, чем меньше успели посчитать.
    """
    import sys
    import os as _os

    sys.path.insert(0, _os.path.join(
        _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "scripts"))
    from score_etalon import compare_with_production, score_run

    etalon = [
        {"rfq_id": "a", "expected_external_code": "KZ-001",
         "original_text": "bufer", "reference_production_correct": "да"},
        {"rfq_id": "b", "expected_external_code": "KZ-002",
         "original_text": "kapot", "reference_production_correct": "нет"},
        {"rfq_id": "c", "expected_external_code": "KZ-003",
         "original_text": "fara", "reference_production_correct": "да"},
    ]
    # Прогон дошёл только до первой заявки, и та закрыта верно.
    results = {"a": [{"final_external_code": "KZ-001", "final_status": "SELECT"}]}

    scored = score_run(etalon, results)
    assert scored["correct"] == 1
    assert scored["missing_from_results"] == 2

    comparison = compare_with_production(etalon, scored)
    assert comparison["considered"] == 1
    assert comparison["ours_correct"] == 1, "только посчитанная заявка"
    assert comparison["both_correct"] == 1
    assert comparison["only_ours"] == []
    assert comparison["only_production"] == [], "c не посчитана — её тут быть не должно"
