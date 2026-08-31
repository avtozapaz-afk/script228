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
    result = layer0.segment("mühərrik və sürətlər qutusunun yastıqları")
    items = texts(result)
    assert "yastiq" in items[0].lower() or "yastıq" in items[0].lower()
    assert "sürətlər qutusunun yastıqları" in items[1]
    assert "ellipsis_expanded" in result.items[0].reason


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
    assert result.items[0].position_hint == "on,arxa"


def test_left_and_right_of_one_part_stay_one_item(layer0):
    result = layer0.segment("sol və sağ güzgü")
    assert len(result.items) == 1
    assert result.items[0].side_hint == "sol,sag"


def test_repeated_head_with_positions_merges(layer0):
    result = layer0.segment("ön bufer və arxa bufer")
    assert len(result.items) == 1
    assert result.items[0].position_hint == "on,arxa"


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


# ── дефекты, найденные на реальном fresh-300 ────────────────────────────────
def test_list_numbering_is_not_part_of_the_item(layer0):
    """«1. Qabaq arxa apornu» — «1.» это разметка перечня, а не деталь."""
    items = texts(layer0.segment("1. Qabaq arxa apornu\n2. Babin"))
    assert all(not i.strip().startswith(("1.", "2.")) for i in items)


def test_ellipsis_does_not_fuse_parts_from_different_lines(layer0):
    """Через перевод строки слово не опускают — это разные заявки.

    Регрессия с живых данных: «Qabaq abirsofka» и «...parkradari» склеивались
    в несуществующий предмет «abirsofka parkradari».
    """
    items = texts(layer0.segment(
        "Qabaq bufer\nQabaq abirsofka\nQabaq sol terefin 2 parkradari"))
    assert not any("abirsofka" in i and "parkradari" in i for i in items)


def test_commentary_is_flagged_but_never_dropped(layer0):
    """Хвост «Hər birinin firma adı» помечается, но предмет не исчезает."""
    result = layer0.segment(
        "Qabaq sağ stupitsa podşipniki\nHər birinin firma adı")
    assert len(result.items) == 2
    flags = [i.is_part_request for i in result.items]
    assert flags[0] is True and flags[1] is False


def test_a_misspelled_real_part_is_not_flagged_as_commentary(layer0):
    """«abirsofka» — опечатка реальной детали, а не примечание."""
    result = layer0.segment("Qabaq bufer\nQabaq abirsofka")
    assert all(i.is_part_request for i in result.items)


def test_numeral_ten_is_not_mistaken_for_front(layer0):
    """После снятия диакритики «ön» и «on» (10) совпадают — «on ədəd» это «10 шт»."""
    result = layer0.segment("naklatka on ədəd")
    assert result.items[0].position_hint is None
