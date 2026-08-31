"""OEM / part-number резолвер: извлечение номеров и разрешение конфликтов."""

import pytest

from avtozap.oem import OemResolver, classify_number, extract_numbers
from avtozap.retriever import RetrieverV2
from avtozap.types import OEM_CONFLICT, OEM_MATCH, OEM_NONE, OEM_UNRESOLVED


@pytest.fixture(scope="module")
def retriever() -> RetrieverV2:
    return RetrieverV2.from_file()


# ── извлечение ──────────────────────────────────────────────────────────────
def test_solid_oem_number_is_extracted():
    assert extract_numbers("06A115561B lazımdır")[0] == ["06A115561B"]


@pytest.mark.parametrize("text,expected", [
    ("1K0 615 301 AA əyləc diski", "1K0615301AA"),      # формат VW
    ("82 00 123 456 nömrəli detal", "8200123456"),      # формат Renault
    ("BMW E90 üçün 34 11 6 794 300 tormuz disk", "34116794300"),
])
def test_number_written_in_groups_is_joined(text, expected):
    assert extract_numbers(text)[0] == [expected]


@pytest.mark.parametrize("text", [
    "Mercedes W211 2005 ön bufer",     # код кузова + год
    "VIN WDB2110561A123456 üçün",      # VIN
    "205/55R16 şin",                   # типоразмер шины
    "telefon 050 123 45 67",           # телефон
    "naklatka 2 ədəd 3 dənə",          # количество
    "2005 2006 model",                 # перечисление годов
])
def test_vehicle_data_is_not_mistaken_for_a_part_number(text):
    assert extract_numbers(text)[0] == []


@pytest.mark.parametrize("token,reason", [
    ("2005", "год выпуска"),
    ("W211", "код кузова/модели"),
    ("205/55R16", "типоразмер шины"),
    ("+994501234567", "телефон"),
])
def test_rejection_reasons_are_explicit(token, reason):
    assert classify_number(token) == reason


# ── разрешение ──────────────────────────────────────────────────────────────
def test_no_number_gives_none(retriever):
    assert OemResolver(retriever).resolve("əyləc diski", "əyləc diski").status == OEM_NONE


def test_without_a_catalog_a_number_is_never_invented(retriever):
    """Каталога нет — значит номер честно не разрешён, а не «подобран»."""
    evidence = OemResolver(retriever).resolve("əyləc diski", "1K0615301AA əyləc diski")
    assert evidence.status == OEM_UNRESOLVED
    assert evidence.resolved_part_id is None


def test_number_agreeing_with_the_text_is_a_match(retriever):
    catalog = {"1K0615301AA": {"part_id": "EY-002"}}
    evidence = OemResolver(retriever, catalog).resolve(
        "əyləc diski", "1K0615301AA əyləc diski")
    assert evidence.status == OEM_MATCH
    assert evidence.resolved_part_id == "EY-002"


def test_number_pointing_elsewhere_is_a_conflict_not_an_override(retriever):
    """Номер не перебивает текст молча — конфликт доезжает до валидатора."""
    catalog = {"06A115561B": {"part_id": "MU-025"}}
    evidence = OemResolver(retriever, catalog).resolve(
        "əyləc diski", "06A115561B əyləc diski")
    assert evidence.status == OEM_CONFLICT
    assert evidence.resolved_part_id == "MU-025"
    assert "EY-002" in evidence.text_head_part_ids


def test_number_in_the_same_leaf_agrees(retriever):
    """Соседняя деталь того же листа — не конфликт, а уточнение внутри группы."""
    catalog = {"1K0615301AA": {"part_id": "EY-005"}}       # барабан, тот же лист
    assert OemResolver(retriever, catalog).resolve(
        "əyləc diski", "1K0615301AA əyləc diski").status == OEM_MATCH


def test_catalog_pointing_outside_the_dictionary_is_unresolved(retriever):
    catalog = {"1K0615301AA": {"part_id": "ZZ-999"}}
    evidence = OemResolver(retriever, catalog).resolve(
        "əyləc diski", "1K0615301AA əyləc diski")
    assert evidence.status == OEM_UNRESOLVED
    assert "ZZ-999" in evidence.reason
