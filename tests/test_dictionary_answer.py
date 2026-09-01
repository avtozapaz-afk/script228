"""Точный термин словаря сильнее отказа арбитра — правило по итогам прогона 469."""

from __future__ import annotations

import pytest

from avtozap.config import RunConfig
from avtozap.dictionary import load
from avtozap.dictionary_answer import (SOURCE_ARBITER, SOURCE_DICTIONARY,
                                       coverage, exact_phrase, resolve)
from avtozap.pipeline import Pipeline
from avtozap.retriever import RetrieverV2
from avtozap.types import Candidate


@pytest.fixture(scope="module")
def retriever() -> RetrieverV2:
    return RetrieverV2()


def _candidates(retriever, text):
    return retriever.retrieve(text).candidates


# ── разбор причины ──────────────────────────────────────────────────────────
def test_exact_phrase_is_read_from_the_retriever_reason():
    assert exact_phrase("exact:almaciqi | stem:rula") == "almaciqi"
    assert exact_phrase("token:fara | stem:fara") is None
    assert exact_phrase("") is None


def test_coverage_counts_meaningful_words_only():
    """Слова положения и вежливости покрытие не разбавляют.

    Иначе правило молчало бы на «sol arxa stop lazımdır», где значимое слово
    ровно одно и словарь его знает точно.
    """
    assert coverage("stop", "Stop") == pytest.approx(1.0)
    assert coverage("stop", "sol arxa stop lazımdır") == pytest.approx(1.0)
    # А вот настоящее второе слово покрытие уже делит.
    assert coverage("stop", "stop knopkasi") == pytest.approx(1 / 2)


def test_the_common_misspelling_of_original_is_a_stop_word():
    """«orginal» встречается в живых заявках чаще правильного написания.

    103 раза против 80 у «original» в 6188 заявках. Пока оно считалось
    значимым словом, оно вдвое разбавляло покрытие и правило молчало на
    «avtopasos orginal» — реальном отказе арбитра с верным ответом рядом.
    """
    from avtozap.retriever import content_tokens

    for text in ("avtopasos orginal", "avtopasos orijinal",
                 "avtopasos original"):
        assert content_tokens(text) == ["avtopasos"], text


# ── случаи из живого прогона ────────────────────────────────────────────────
@pytest.mark.parametrize("text,code", [
    ("Vintilyatorun datçiki", "SO-016"),
    ("On alin susesi (patpres)", "KZ-011"),
    ("avtopasos orginal", "MU-088"),
    ("Stop", "KZ-007"),
    ("Sol arxa top", "AS-010"),
])
def test_the_dictionary_answers_where_the_arbiter_refused(retriever, text, code):
    """Все пять — реальные отказы арбитра из прогона 469 с верным ответом рядом."""
    assert resolve(text, _candidates(retriever, text),
                   retriever.dict.canonical) == code


def test_a_fragment_of_a_long_request_is_not_enough(retriever):
    """Главная защита правила: совпадение по обрывку не считается.

    В этой заявке словарь находит термин, ведущий в «Проводка/разъёмы», хотя
    покупатель просит кнопку Start/Stop, которой в словаре нет. Порог покрытия
    ровно для таких случаев.
    """
    text = "Start Stop düyməsinin oboyması. Kod BT4Z-11584-BA"
    assert resolve(text, _candidates(retriever, text),
                   retriever.dict.canonical) is None


def test_no_exact_term_means_no_answer(retriever):
    text = "sernisin terefin qapaq"
    candidates = _candidates(retriever, text)
    if candidates and exact_phrase(candidates[0].reason):
        pytest.skip("на этой заявке словарь даёт точный термин")
    assert resolve(text, candidates, retriever.dict.canonical) is None


def test_an_empty_shortlist_yields_nothing(retriever):
    assert resolve("fara", [], retriever.dict.canonical) is None


def test_the_threshold_is_configurable(retriever):
    """Порог — настройка, а не константа: после прогона 200 его можно двигать."""
    text = "Start Stop düyməsinin oboyması. Kod BT4Z-11584-BA"
    candidates = _candidates(retriever, text)
    assert resolve(text, candidates, retriever.dict.canonical,
                   min_coverage=0.0) is not None
    assert resolve(text, candidates, retriever.dict.canonical,
                   min_coverage=0.6) is None


# ── поведение всей цепочки ──────────────────────────────────────────────────
@pytest.fixture(scope="module")
def pipeline() -> Pipeline:
    return Pipeline(RunConfig(input_path="—", mock=True))


class _RefusingArbiter:
    """Арбитр, который всегда отказывается — как в 20 заявках прогона 469."""

    def decide(self, *args, **kwargs):
        from avtozap.types import ARB_UNKNOWN, ArbiterDecision
        return ArbiterDecision(decision=ARB_UNKNOWN, confidence="high",
                               reason="не уверен", model="stub")


def test_the_dictionary_answers_when_the_arbiter_refuses(pipeline, monkeypatch):
    """Ровно тот случай, ради которого правило и сделано."""
    monkeypatch.setattr(pipeline, "arbiter", _RefusingArbiter())
    record = pipeline.process_request(
        {"rfq_id": "d", "original_text": "Vintilyatorun datçiki"}, 0)[0]
    assert record.final_external_code == "SO-016"
    assert record.answered_by == SOURCE_DICTIONARY
    assert record.action == "ANSWER"


def test_the_report_always_says_who_answered(pipeline):
    """Ответ модели обязан быть отличим от ответа словаря."""
    answered = pipeline.process_request(
        {"rfq_id": "a", "original_text": "qabaq fara"}, 0)[0]
    assert answered.final_external_code
    assert answered.answered_by == SOURCE_ARBITER


def test_the_rule_never_overrides_an_ambiguity_question(pipeline):
    """Переспрос по правилу словаря остаётся переспросом."""
    record = pipeline.process_request(
        {"rfq_id": "amb", "original_text": "park radari"}, 0)[0]
    assert record.ambiguous_term
    assert record.final_external_code is None
    assert record.answered_by == ""


def test_the_rule_stays_out_of_multi_part_requests(pipeline):
    """Многодетальная заявка — не место для доответа за арбитра.

    На наборе 200 больше половины заявок, где верный ответ «кода быть не
    должно», именно многодетальные.
    """
    records = pipeline.process_request(
        {"rfq_id": "multi", "original_text": "fara ve bufer lazimdir"}, 0)
    assert len(records) > 1
    assert all(r.answered_by != SOURCE_DICTIONARY for r in records)
