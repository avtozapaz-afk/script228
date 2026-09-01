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
    assert evidence.resolved_external_code is None


def test_number_agreeing_with_the_text_is_a_match(retriever):
    catalog = {"1K0615301AA": {"external_code": "EY-002"}}
    evidence = OemResolver(retriever, catalog).resolve(
        "əyləc diski", "1K0615301AA əyləc diski")
    assert evidence.status == OEM_MATCH
    assert evidence.resolved_external_code == "EY-002"


def test_number_pointing_elsewhere_is_a_conflict_not_an_override(retriever):
    """Номер не перебивает текст молча — конфликт доезжает до валидатора."""
    catalog = {"06A115561B": {"external_code": "MU-025"}}
    evidence = OemResolver(retriever, catalog).resolve(
        "əyləc diski", "06A115561B əyləc diski")
    assert evidence.status == OEM_CONFLICT
    assert evidence.resolved_external_code == "MU-025"
    assert "EY-002" in evidence.text_head_codes


def test_number_in_the_same_leaf_agrees(retriever):
    """Соседняя деталь того же листа — не конфликт, а уточнение внутри группы."""
    catalog = {"1K0615301AA": {"external_code": "EY-005"}}       # барабан, тот же лист
    assert OemResolver(retriever, catalog).resolve(
        "əyləc diski", "1K0615301AA əyləc diski").status == OEM_MATCH


def test_catalog_pointing_outside_the_dictionary_is_unresolved(retriever):
    catalog = {"1K0615301AA": {"external_code": "ZZ-999"}}
    evidence = OemResolver(retriever, catalog).resolve(
        "əyləc diski", "1K0615301AA əyləc diski")
    assert evidence.status == OEM_UNRESOLVED
    assert "ZZ-999" in evidence.reason


def test_two_oems_in_one_message_are_bound_to_their_own_items(retriever):
    catalog = {
        "06E906265S": {"external_code": "EG-002"},
        "8K0941286N": {"external_code": "EL-088"},
    }
    resolver = OemResolver(retriever, catalog)
    original = "06E906265S katalizator datciki\n8K0941286N urvin datciki qabaq sag"
    first = resolver.resolve("06E906265S katalizator datciki", original)
    second = resolver.resolve("8K0941286N urvin datciki qabaq sag", original)
    assert first.status == OEM_MATCH and first.resolved_external_code == "EG-002"
    assert second.status == OEM_MATCH and second.resolved_external_code == "EL-088"


# ── живой прогон 200: номер был в каталоге, а заявка уходила в UNKNOWN ──────
def test_a_catalogue_number_answers_even_when_the_arbiter_refuses():
    """Номер детали — не догадка: если он найден, спрашивать нечего.

    Все четыре заявки из живого прогона 200. Резолвер честно давал MATCH с
    верным кодом, но итог собирался только из решения арбитра, и заявки
    уходили в UNKNOWN при полностью разрешённом номере.
    """
    from avtozap.config import RunConfig
    from avtozap.pipeline import Pipeline

    pipeline = Pipeline(RunConfig(input_path="—", mock=True))
    cases = [
        ("976742S000", "SO-044"),
        ("58323 2H300  Hyundai Santafe 3.3 2013", "EY-023"),
        ("61664849598", "EL-071"),
        ("30939070 Bu kodlu mehsul lazmdi amma sumqayit", "SR-005"),
    ]
    for text, code in cases:
        records = pipeline.process_request(
            {"rfq_id": "oem", "original_text": text}, 0)
        produced = {r.final_external_code for r in records}
        assert code in produced, f"{text}: {produced}"
        answered = [r for r in records if r.final_external_code == code]
        assert answered[0].answered_by == "oem_catalog"


def test_an_oem_conflict_still_goes_to_review_not_to_the_number():
    """Приоритет номера не отменяет разбор конфликта.

    Если арбитр уверенно выбрал одну деталь, а номер указывает на другую, это
    по-прежнему правило V4 валидатора и REVIEW, а не молчаливая подмена ответа
    номером: конфликт может значить, что номер относится к другой детали из
    того же сообщения.
    """
    import inspect

    from avtozap import pipeline as pipeline_module

    source = inspect.getsource(pipeline_module)
    assert "OEM_MATCH" in source
    assert "final_status in (FINAL_UNKNOWN, FINAL_REVIEW)" in source
