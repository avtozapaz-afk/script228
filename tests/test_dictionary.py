"""Словарь AVTOZAP — источник истины по external_code."""

import pytest

from avtozap.dictionary import load, normalize


@pytest.fixture(scope="module")
def dictionary():
    return load()


def test_exactly_581_parts(dictionary):
    """581 деталь после подтверждённого ручного review27 от 01.09.2026."""
    assert len(dictionary) == 581


@pytest.mark.parametrize("code,name", [
    ("KZ-094", "Молдинг двери"),
    ("KZ-095", "Кант лобового стекла"),
])
def test_parts_added_in_the_september_dictionary(dictionary, code, name):
    """Из-за их отсутствия три строки эталона раньше нельзя было засчитать."""
    part = dictionary.get(code)
    assert part is not None and name in part.name_ru


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
    assert len(dictionary.term_index) > 4000


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
