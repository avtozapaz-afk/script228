"""Два правила, согласованных с проектом: головное слово и вязкость масла.

Сети не требуют.
"""

import json
import os

import pytest

import avtozap.head_ranking as head_ranking
from avtozap.head_ranking import head_token
from avtozap.layer0 import Layer0
from avtozap.lubricants import (
    HINT_BRAKE_FLUID,
    HINT_ENGINE_OIL,
    HINT_GEAR_OIL,
    hint_for,
    viscosity_in,
)
from avtozap.retriever import RetrieverV2


@pytest.fixture(scope="module")
def retriever() -> RetrieverV2:
    return RetrieverV2()


# ── масло по вязкости, а не по марке ────────────────────────────────────────
@pytest.mark.parametrize("text", [
    "Meguin 0/20",
    "Prista ultra plus 5w-40 neciyedi qiymeti",
    "Castrol Edge 5W30",
    "10w40 yag",
])
def test_engine_oil_is_recognised_by_viscosity(text):
    """Марку в синонимы заводить нельзя — список брендов бесконечен."""
    assert hint_for(text) == HINT_ENGINE_OIL


@pytest.mark.parametrize("text", ["Karopka yağı 75w-90", "ATF 75W90"])
def test_gear_oil_is_told_apart_from_engine_oil(text):
    assert hint_for(text) == HINT_GEAR_OIL


def test_engine_viscosity_next_to_a_gearbox_word_means_gear_oil():
    """«Karopka yağı 5w-40» — трансмиссионное, хотя вязкость моторная."""
    assert hint_for("Karopka yağı 5w-40") == HINT_GEAR_OIL


@pytest.mark.parametrize("text", ["DOT-4 tormoz mayesi", "dot 4", "DOT5.1"])
def test_brake_fluid_is_recognised_by_its_specification(text):
    assert hint_for(text) == HINT_BRAKE_FLUID


@pytest.mark.parametrize("text", [
    "Qabaq bufer",
    "W210 e230 termostat",
    "Naklatka və tormuz disk",
    "205/55R16 şin",          # размер шины — не вязкость
    "1K0 615 301 AA",         # номер детали
    "BMW E90 2010",
    "",
])
def test_an_ordinary_request_carries_no_lubricant_marker(text):
    assert hint_for(text) is None


def test_the_viscosity_itself_is_reported():
    assert viscosity_in("Prista ultra plus 5w-40") == "5w-40"


@pytest.mark.parametrize("text", ["Meguin 0/20",
                                  "Prista ultra plus 5w-40 neciyedi qiymeti"])
def test_oil_brands_now_reach_the_right_part(retriever, text):
    """Обе заявки были в промахах эталона и закрываются этим правилом."""
    assert retriever.retrieve(text).codes[0] == "SR-001"


def test_the_rule_never_invents_a_code(retriever):
    """Подсказка расширяет поиск, но код всё равно приходит из словаря."""
    for candidate in retriever.retrieve("Meguin 0/20").candidates:
        assert candidate.external_code in retriever.dict


# ── ранжирование по головному слову ─────────────────────────────────────────
def test_the_head_is_the_last_word_the_dictionary_knows():
    """Хвостовая марка головой не считается."""
    known = {"cashka", "caska", "çaşka", "bufer", "qapaq"}.__contains__
    assert head_token(["qabaq", "çaşka", "lenforderle"], known) == "çaşka"


def test_no_head_when_the_dictionary_knows_nothing():
    assert head_token(["qwerty", "zxcvbn"], lambda _: False) is None


def test_short_words_are_not_taken_as_the_head():
    assert head_token(["bufer", "ab"], lambda _: True) == "bufer"


@pytest.mark.parametrize("text,expected", [
    ("yanacaq çəninin qapağı", "YA-005"),      # крышка, а не бак
    ("Kompressorun daçiki", "EL-020"),         # датчик, а не компрессор
    ("Kondisaner kompresorun təziq daciki", "EL-020"),
])
def test_the_requested_part_beats_its_owner(retriever, text, expected):
    assert retriever.retrieve(text).codes[0] == expected


@pytest.mark.parametrize("text,expected", [
    ("Qabaq bufer", "KZ-001"),
    ("termostat", "SO-004"),
    ("naklatka", "EY-001"),
    ("əyləc diski", "EY-002"),
])
def test_plain_requests_are_untouched(retriever, text, expected):
    assert retriever.retrieve(text).codes[0] == expected


def test_the_boost_stays_small_enough_not_to_beat_an_exact_phrase():
    """0.02 давало +6, 0.06 уже −4: надбавка обязана быть маленькой."""
    assert head_ranking.HEAD_BOOST <= 0.02


def test_the_rule_can_be_switched_off_for_measurement():
    assert RetrieverV2(head_ranking=False).head_ranking is False


def test_the_rule_does_not_change_which_codes_are_offered():
    """Правило меняет порядок, но не состав: потолок цепочки оно не двигает."""
    with_rule = RetrieverV2(head_ranking=True)
    without = RetrieverV2(head_ranking=False)
    for text in ["yanacaq çəninin qapağı", "Kompressorun daçiki",
                 "Qabaq bufer", "termostat", "naklatka"]:
        assert set(with_rule.retrieve(text).codes) == set(without.retrieve(text).codes)


# ── замер, ради которого правило и делалось ─────────────────────────────────
def test_head_ranking_improves_rank_one_on_the_accuracy_set():
    """Зафиксированный результат замера: 323 → 334 заявки с кодом на 1 месте.

    Порог намеренно ниже достигнутого: тест ловит регрессию правила, а не
    закрепляет конкретное число, которое сдвинется при следующем словаре.
    """
    path = os.path.join("data", "etalon_469.jsonl")
    with open(path, encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh if line.strip()]
    rows = [r for r in rows if r["expected_external_code"] != "UNKNOWN"]

    def rank_one(enabled: bool) -> int:
        retriever = RetrieverV2(head_ranking=enabled)
        layer0 = Layer0(retriever)
        hits = 0
        for row in rows:
            want = row["expected_external_code"]
            for item in layer0.segment(row["original_text"]).items:
                codes = retriever.retrieve(item.item_raw).codes
                if codes and codes[0] == want:
                    hits += 1
                    break
        return hits

    assert rank_one(True) > rank_one(False)
