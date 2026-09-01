"""Неоднозначные термины словаря v7: спросить, а не угадать."""

from __future__ import annotations

import json
import os

import pytest

from avtozap.ambiguity import AmbiguityRules, load, load_rules
from avtozap.config import RunConfig
from avtozap.pipeline import Pipeline
from avtozap.retriever import RetrieverV2, content_tokens
from avtozap.types import FINAL_REVIEW

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def rules() -> AmbiguityRules:
    return load()


@pytest.fixture(scope="module")
def retriever() -> RetrieverV2:
    return RetrieverV2()


@pytest.fixture(scope="module")
def pipeline() -> Pipeline:
    return Pipeline(RunConfig(input_path="—", mock=True))


# ── файл правил ─────────────────────────────────────────────────────────────
def test_rules_are_extracted_from_the_dictionary_not_typed_by_hand():
    """Файл правил обязан совпадать с листом словаря до буквы.

    Если он разойдётся, значит его правили руками — а править надо словарь и
    пересобирать скриптом.
    """
    import sys
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    from extract_ambiguous_rules import extract

    from_sheet = extract(os.path.join(ROOT, "data",
                                      "AVTOZAP_slovar_FINAL_571.xlsx"))
    assert load_rules() == from_sheet


def test_every_rule_of_the_dictionary_is_loaded(rules):
    """Правил столько, сколько строк AMBIGUOUS в листе словаря: v7 — 4, v8 — 5."""
    assert len(load_rules()) == 5
    for rule in load_rules():
        assert rule["question"], "правило без вопроса покупателю бесполезно"
        assert len(rule["codes"]) == 2, "неоднозначность — это ровно два кода"


def test_every_code_in_the_rules_exists_in_the_dictionary(retriever):
    """Ни один код из правил не выдуман: все они есть в словаре 571."""
    for rule in load_rules():
        for code in rule["codes"]:
            assert retriever.dict.get(code) is not None, code


# ── что считается голым термином ────────────────────────────────────────────
@pytest.mark.parametrize("text,expected_codes", [
    ("Arxa şveller", ["KZ-030", "KZ-043"]),
    ("çaşka", ["AS-029", "AS-034"]),
    ("şveller lazımdır", ["KZ-030", "KZ-043"]),
    ("park radari", ["EL-014", "AK-028"]),
    ("parkradari", ["EL-014", "AK-028"]),
    ("teker sensoru", ["EY-011", "TK-005"]),
    ("təkər sensoru sol", ["EY-011", "TK-005"]),
    ("baqaj jaluzu", ["TY-016", "KZ-070"]),
])
def test_bare_ambiguous_term_is_caught(rules, retriever, text, expected_codes):
    """Слова положения и вежливости голым термин быть не мешают."""
    hit = rules.check(text, content_tokens, retriever.head_codes)
    assert hit is not None, text
    assert hit.codes == expected_codes


@pytest.mark.parametrize("text", [
    "porog sveleri",            # уточнено словом porog -> KZ-030
    "bamper şvelleri",          # уточнено словом bamper
    "park radari bloku",        # уточнено словом blok -> EL-014
    "baqaj perdesi",            # другой термин, правило к нему не относится
    "qabaq fara",
    "",
])
def test_a_clarified_request_is_not_touched(rules, retriever, text):
    """Дописал значащее слово — правило молчит, отвечает словарь."""
    assert rules.check(text, content_tokens, retriever.head_codes) is None


@pytest.mark.parametrize("text,code", [
    ("alt çaşka", "AS-029"),
    ("aşağı çaşka", "AS-029"),
    ("üst çaşka", "AS-034"),
])
def test_a_position_word_that_the_dictionary_uses_as_context_is_respected(
        rules, retriever, text, code):
    """Разводит не список слов, а сам словарь.

    Правило ``çaşka`` прямо говорит: ``alt`` → AS-029, ``üst`` → AS-034 — и
    словарь обе формы знает точными терминами. Слов положения в общем случае
    мы не считаем содержанием запроса, поэтому наивная проверка «остался один
    термин» переспросила бы там, где ответ уже есть. Спрашиваем словарь: знает
    фразу целиком и ведёт ею внутрь пары — вопрос не нужен.
    """
    assert rules.check(text, content_tokens, retriever.head_codes) is None
    assert retriever.retrieve(text).codes[0] == code


# ── зачем слой вообще нужен ─────────────────────────────────────────────────
@pytest.mark.parametrize("text", ["park radari", "teker sensoru", "baqaj jaluzu"])
def test_the_engine_alone_would_still_answer_confidently(retriever, text):
    """Снятия алиасов недостаточно — это и есть причина существования слоя.

    Проект убрал неоднозначные термины из уверенных алиасов, но движок на
    голом запросе всё равно отдаёт кандидата с максимальным score: срабатывают
    основы, нечёткое сравнение и контекстные правила. Если этот тест однажды
    упадёт, значит словарь научился молчать сам и слой можно упрощать.
    """
    candidates = retriever.retrieve(text).candidates
    assert candidates and candidates[0].score >= 0.99


# ── поведение всей цепочки ──────────────────────────────────────────────────
@pytest.mark.parametrize("text,fragment", [
    ("Arxa şveller", "порог"),
    ("park radari", "парктроника"),
    ("teker sensoru", "ABS"),
    ("baqaj jaluzu", "багажник"),
])
def test_pipeline_asks_the_dictionary_question_instead_of_answering(
        pipeline, text, fragment):
    records = pipeline.process_request(
        {"rfq_id": "amb", "original_text": text}, 0)
    assert len(records) == 1
    record = records[0]
    assert record.final_status == FINAL_REVIEW
    assert record.final_external_code is None
    assert record.action == "ASK_BUYER"
    assert record.ambiguous_term
    assert fragment.lower() in (record.buyer_question or "").lower()


def test_pipeline_still_answers_a_clarified_request(pipeline):
    """Правило не должно отбирать ответ у того, кто написал понятно."""
    records = pipeline.process_request(
        {"rfq_id": "clear", "original_text": "porog sveleri"}, 0)
    assert records[0].ambiguous_term is None
    assert records[0].final_external_code == "KZ-030"


def test_the_question_is_the_customers_own_wording(pipeline):
    """Текст вопроса берётся из словаря, а не сочиняется здесь."""
    questions = {r["question"] for r in load_rules()}
    record = pipeline.process_request(
        {"rfq_id": "amb", "original_text": "teker sensoru"}, 0)[0]
    assert record.buyer_question in questions


def test_rules_file_is_valid_json_with_a_note():
    with open(os.path.join(ROOT, "data", "ambiguous_rules.json"),
              encoding="utf-8") as fh:
        payload = json.load(fh)
    assert "_note" in payload and "rules" in payload


# ── дубли и деактивированные коды ───────────────────────────────────────────
def test_every_deactivated_code_has_a_canonical_replacement():
    """Деактивированный код обязан иметь замену в duplicate_codes.json.

    Иначе конвейер молча потеряет деталь: код из словаря исчез, а заменить его
    нечем. v8 деактивировал EL-013 и MU-012 — файл дублей должен был поехать
    вместе со словарём, и этот тест следит, чтобы так было и дальше.
    """
    import openpyxl

    from avtozap.dictionary import load_duplicates

    workbook = openpyxl.load_workbook(
        os.path.join(ROOT, "data", "AVTOZAP_slovar_FINAL_571.xlsx"),
        read_only=True, data_only=True)
    rows = list(workbook["parts_synonyms"].iter_rows(values_only=True))
    header = list(rows[0])
    code_at, active_at = header.index("external_code"), header.index("is_active")
    deactivated = {str(row[code_at]) for row in rows[1:]
                   if row[code_at]
                   and str(row[active_at]).strip().lower() not in
                   ("true", "1", "да", "yes")}

    duplicates = load_duplicates()
    assert deactivated <= set(duplicates), (
        "деактивированы без канонической замены: "
        f"{sorted(deactivated - set(duplicates))}")
    # И замена обязана вести на живой код, а не на другой деактивированный.
    assert not (set(duplicates.values()) & deactivated)


def test_a_deactivated_code_never_reaches_the_shortlist(retriever):
    """Проверка на живых запросах, а не только на данных."""
    for text in ("hava xortumu", "termostat korpusu zbor", "park radari",
                 "qalofka praklatkasi"):
        codes = retriever.retrieve(text).codes
        assert not ({"RG-04", "SO-025", "EL-013", "MU-012"} & set(codes)), text
