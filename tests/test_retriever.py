"""Retriever V2: адаптер над поставленной реализацией проекта.

Главное, что здесь проверяется, — что репозиторий действительно запускает
поставленный ретривер и ничего не искажает. Эталон для сравнения берётся из
реального fresh-300 devset, где shortlist посчитан самим проектом.
"""

import json
import os

import pytest

from avtozap.dictionary import load, normalize
from avtozap.retriever import RetrieverV2, content_tokens, strip_modifiers

DEVSET = os.path.join("data", "reference", "AVTOZAP_FRESH_300_V4_DEVSET.json")


@pytest.fixture(scope="module")
def retriever() -> RetrieverV2:
    return RetrieverV2()


@pytest.fixture(scope="module")
def devset() -> list[dict]:
    with open(DEVSET, encoding="utf-8") as fh:
        return json.load(fh)


# ── словарь ─────────────────────────────────────────────────────────────────
def test_dictionary_has_all_581_parts(retriever):
    assert len(retriever.dict) == 581


@pytest.mark.parametrize("code", ["AK-004", "AK-006", "YA-027", "MU-011"])
def test_the_four_parts_missing_from_the_537_version_are_present(retriever, code):
    """Именно этих кодов не было в версии на 537 деталей."""
    assert retriever.dict.get(code) is not None


# ── ничего не выдумывается ──────────────────────────────────────────────────
def test_every_candidate_is_a_real_dictionary_part(retriever):
    for query in ["tormuz disk", "naklatka", "qwerty", "sol güzgü", "радиатор", ""]:
        for candidate in retriever.retrieve(query).candidates:
            part = retriever.dict.get(candidate.external_code)
            assert part is not None
            assert candidate.name_ru == part.name_ru
            assert candidate.name_az == part.name_az


def test_candidate_order_is_deterministic(retriever):
    first = retriever.retrieve("tormuz disk").codes
    assert first == RetrieverV2().retrieve("tormuz disk").codes


def test_limit_is_respected():
    assert len(RetrieverV2(limit=3).retrieve("fara").candidates) <= 3


# ── верность поставленной реализации ────────────────────────────────────────
def test_the_project_control_run_reproduces_exactly(retriever):
    """704-control stays stable except one explicit review27 correction.

    Raw legacy labels now give 500 / 69 / 135 because the old control labels
    ``AFS OFF sensoru ön`` as EL-029 «Модуль круиз-контроля». Manual review27
    confirmed that this is an AFS/headlight-level sensor, now EL-088.  The
    correction is explicit in ``data/review27_etalon_corrections.json``.

    With that one documented correction the control result is 501 / 68 / 135:
    no previously-correct row is lost, and one old no-match becomes correct.
    """
    import json
    import os

    from avtozap.dictionary import _ensure_vendor_on_path
    _ensure_vendor_on_path()
    from avtozap_matcher_engine import PARTS, match

    path = os.path.join("data", "reference", "zayavki", "zayavki_6188.json")
    with open(path, encoding="utf-8") as fh:
        rows = json.load(fh)
    with open(os.path.join("data", "review27_etalon_corrections.json"), encoding="utf-8") as fh:
        corrections = {x["text"]: x["new_expected"] for x in json.load(fh)["control704"]}

    by_name = {}
    for code, part in PARTS.items():
        by_name.setdefault((part["name_ru"] or "").strip().lower(), code)
    gold = [(text, by_name[(name or "").strip().lower()])
            for _, text, name, _ in rows
            if (name or "").strip().lower() in by_name]

    raw_ok = raw_wrong = corrected_ok = corrected_wrong = 0
    for text, legacy_expected in gold:
        got, _ = match(text)
        if got == legacy_expected:
            raw_ok += 1
        elif got:
            raw_wrong += 1

        expected = corrections.get(text, legacy_expected)
        if got == expected:
            corrected_ok += 1
        elif got:
            corrected_wrong += 1

    raw_none = len(gold) - raw_ok - raw_wrong
    corrected_none = len(gold) - corrected_ok - corrected_wrong
    assert (raw_ok, raw_wrong, raw_none) == (500, 69, 135)
    assert (corrected_ok, corrected_wrong, corrected_none) == (501, 68, 135)


def test_recall_of_the_old_production_code_stays_at_the_measured_level(retriever, devset):
    """Полнота по old_external_code — не точность, а «ретривер не потерял код».

    Проект измерял ~90% на боевом экспорте; на fresh-300 получается ~93%.
    Порог 90% ловит регрессию интеграции, а не качество арбитра.
    """
    canonical = retriever.dict.canonical
    total = hits = 0
    for case in devset:
        old = case.get("old_external_code")
        if not old:
            continue
        total += 1
        codes = {canonical(code) for code in retriever.retrieve(case["item_raw"]).codes}
        if canonical(old) in codes:
            hits += 1
    assert total >= 200
    assert hits / total >= 0.90, f"полнота упала до {hits / total:.1%}"


def test_shortlist_is_almost_never_empty(retriever, devset):
    empty = sum(1 for case in devset
                if not retriever.retrieve(case["item_raw"]).codes)
    assert empty <= 3


# ── проверенные вручную случаи из KNOWN_ISSUES ──────────────────────────────
@pytest.mark.parametrize("query,expected", [
    ("tormuz disk", "EY-002"),
    ("əyləc diski", "EY-002"),
    ("naklatka", "EY-001"),
    ("termostat", "SO-004"),
    ("babin", "MU-025"),
    ("mühərrik yastıqları", "MU-017"),
    ("sürətlər qutusunun yastıqları", "MU-018"),
])
def test_expected_part_is_in_the_shortlist(retriever, query, expected):
    assert expected in retriever.retrieve(query).codes


def test_search_phrases_widen_the_search_but_not_the_dictionary(retriever):
    """Подсказки сегментера расширяют запрос, но кодов вне словаря не создают."""
    result = retriever.retrieve("bir şey", search_phrases=["əyləc diski"])
    assert "EY-002" in result.codes
    assert all(c.external_code in retriever.dict for c in result.candidates)


# ── справки для других слоёв ────────────────────────────────────────────────
def test_head_codes_only_returns_exact_dictionary_terms(retriever):
    assert retriever.head_codes("termostat") == ["SO-004"]
    assert retriever.head_codes("qwerty zxcvbn") == []


def test_known_word_protects_dictionary_words_from_being_stripped(retriever):
    assert retriever.is_known_word("termostat")
    assert not retriever.is_known_word("mercedes")


def test_lexical_support_separates_the_part_from_an_unrelated_one(retriever):
    assert retriever.lexical_support("əyləc diski", "EY-002")
    assert not retriever.lexical_support("əyləc diski", "KZ-020")


def test_modifiers_are_stripped_from_content(retriever):
    assert strip_modifiers("Salam sol ön 2 eded bufer lazimdir") == "bufer"
    assert "sol" not in content_tokens("sol güzgü")


def test_normalization_matches_the_project_engine():
    assert normalize("Əyləc diski") == "eylec diski"
    assert normalize("MÜHƏRRIK") == "muherrik"
