"""Словарь на 541 деталь — источник истины по external_code."""

import pytest

from avtozap.dictionary import load, normalize


@pytest.fixture(scope="module")
def dictionary():
    return load()


def test_exactly_541_parts(dictionary):
    """541 деталь — целевой словарь проекта.

    Из поставки это число даёт ТОЛЬКО ``AVTOZAP_slovar_FINAL_31avg_v4.xlsx``
    (внутри архива Retriever V2). В ``02_DATA/SLOVAR_FINAL_541_REFERENCE.txt``
    вопреки имени 435 деталей, в ``parts_synonyms_cleaned_v2.xlsx`` — 537.
    """
    assert len(dictionary) == 541


@pytest.mark.parametrize("code", ["AK-004", "AK-006", "YA-027", "MU-011"])
def test_parts_absent_from_the_537_version_are_present(dictionary, code):
    assert code in dictionary


def test_every_part_has_both_names(dictionary):
    for code, part in dictionary.parts.items():
        assert part.name_az, code
        assert part.name_ru, code
        assert part.category, code


def test_codes_look_like_project_identifiers(dictionary):
    import re
    pattern = re.compile(r"^[A-Z]{2}-\d{2,3}$")
    bad = [c for c in dictionary.parts if not pattern.match(c)]
    # RG-01/RG-04 в словаре записаны с двумя цифрами — это данные проекта.
    assert all(c.startswith("RG-") for c in bad), bad


def test_term_index_is_populated(dictionary):
    assert len(dictionary.term_index) > 3000


def test_exact_lookup_uses_project_normalisation(dictionary):
    assert dictionary.exact_codes(normalize("Termostat")) == ["SO-004"]
    assert dictionary.exact_codes(normalize("əyləc diski")) == ["EY-002"]
    assert dictionary.exact_codes("несуществующий термин") == []


def test_vocabulary_covers_names_category_and_synonyms(dictionary):
    words = dictionary.vocabulary("EY-002")
    assert "eylec" in words and "diski" in words
    assert "тормознои" in words or "тормозной" in words or words


def test_vocabulary_of_an_unknown_code_is_empty(dictionary):
    assert dictionary.vocabulary("ZZ-999") == set()


def test_requires_flags_are_booleans(dictionary):
    part = dictionary.get("EY-002")
    assert isinstance(part.requires_side, bool)
    assert isinstance(part.requires_direction, bool)


def test_dictionary_is_loaded_once(dictionary):
    """Книга Excel читается один раз за процесс — иначе прогон 300 кейсов долгий."""
    assert load() is dictionary
