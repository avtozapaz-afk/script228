"""Layer 0 — сегментация сырого сообщения на атомарные предметы."""

import pytest

from avtozap.layer0 import Layer0
from avtozap.retriever import RetrieverV2
from avtozap.types import L0_EMPTY


@pytest.fixture(scope="module")
def layer0() -> Layer0:
    return Layer0(RetrieverV2.from_file())


def texts(result) -> list[str]:
    return [i.item_raw for i in result.items]


# ── обязательное дробление: разные типы деталей ─────────────────────────────
@pytest.mark.parametrize("message", [
    "Naklatka və tormuz disk",
    "babin və sveça",
    "mühərrik və sürətlər qutusunun yastıqları",
])
def test_two_different_part_types_split(layer0, message):
    assert len(layer0.segment(message).items) == 2


def test_engine_and_gearbox_mounts_are_two_mounts(layer0):
    """Эллипсис: «mühərrik və …» — это опора двигателя, а не двигатель."""
    items = texts(layer0.segment("mühərrik və sürətlər qutusunun yastıqları"))
    assert items[0] == "mühərrik yastıqları"
    assert "sürətlər qutusunun yastıqları" in items[1]


def test_ellipsis_expands_only_to_a_real_dictionary_entry(layer0):
    """Расширение принимается лишь тогда, когда такая запись в словаре есть."""
    items = texts(layer0.segment("naklatka və tormuz disk"))
    assert items[0].lower() == "naklatka"        # «naklatka disk» словарю неизвестно


def test_comma_separated_list_splits(layer0):
    assert len(layer0.segment("amortizator, naklatka, hava filtri").items) == 3


# ── обязательное НЕ-дробление: одна деталь в разных позициях ────────────────
def test_front_and_rear_of_one_part_stay_one_item(layer0):
    result = layer0.segment("ön və arxa bufer")
    assert len(result.items) == 1
    assert result.items[0].position_hint == "ön,arxa"


def test_left_and_right_of_one_part_stay_one_item(layer0):
    result = layer0.segment("sol və sağ güzgü")
    assert len(result.items) == 1
    assert result.items[0].side_hint == "sol,sağ"


def test_repeated_head_with_positions_merges(layer0):
    result = layer0.segment("ön bufer və arxa bufer")
    assert len(result.items) == 1
    assert result.items[0].position_hint == "ön,arxa"


def test_different_parts_with_positions_do_not_merge(layer0):
    """«ön fara» и «arxa fanar» — разные детали, а не одна в двух позициях."""
    assert len(layer0.segment("ön fara və arxa fanar").items) == 2


# ── контекст автомобиля и шум ───────────────────────────────────────────────
def test_vehicle_context_is_separated_from_the_part(layer0):
    result = layer0.segment("Mercedes W211 2005 üçün ön bufer lazımdır")
    assert result.items[0].item_raw == "ön bufer"
    assert "Mercedes" in result.vehicle_context and "2005" in result.vehicle_context


def test_greeting_is_trimmed(layer0):
    assert texts(layer0.segment("Salam, radiator var?")) == ["radiator"]


def test_dictionary_word_is_never_mistaken_for_a_brand(layer0):
    """Слово из словаря не вырезается, как бы ни выглядело."""
    result = layer0.segment("disk")
    assert result.items[0].item_raw == "disk"
    assert result.vehicle_context == ""


# ── край ────────────────────────────────────────────────────────────────────
def test_empty_message(layer0):
    assert layer0.segment("   ").status == L0_EMPTY


def test_message_without_any_part_word_still_yields_one_item(layer0):
    result = layer0.segment("BMW 2010")
    assert len(result.items) == 1        # разбор не выдумывается, текст едет целиком
