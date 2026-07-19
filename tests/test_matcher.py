"""Regression tests for the deterministic matcher.

The four cases called out explicitly in the spec are covered first, followed by
additional coverage of names, synonyms, attributes and fuzzy suggestions.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from slovar_matcher.matcher import Matcher  # noqa: E402


@pytest.fixture(scope="module")
def matcher() -> Matcher:
    return Matcher.from_file()


# ── the four spec-mandated cases ─────────────────────────────────────────────
def test_traves_is_ambiguous_with_five_candidates(matcher):
    r = matcher.match("traves")
    assert r.status == "ambiguous"
    ids = {c["part_id"] for c in r.candidates}
    assert ids == {"AS-016", "AS-017", "AS-018", "AS-019", "AS-028"}
    assert r.part_id is None


def test_radiator_ekran_is_no_match(matcher):
    r = matcher.match("radiator ekran")
    assert r.status == "no_match"
    assert r.part_id is None
    assert r.candidates == []


def test_yan_guzgu_is_single_match_needs_side_only(matcher):
    # KZ-020 has side:true but position:false -> only "side" is asked.
    r = matcher.match("yan güzgü")
    assert r.status == "single_match"
    assert r.part_id == "KZ-020"
    assert r.needs_clarification == ["side"]
    assert r.attributes == {"side": None, "position": None}
    assert r.category == "Кузов и оптика"


def test_turbo_nadduv_datciki_single_match_no_questions(matcher):
    r = matcher.match("Turbo (nadduv) datçiki")
    assert r.status == "single_match"
    assert r.part_id == "MU-075"
    assert r.needs_clarification == []
    assert r.attributes == {"side": None, "position": None}


# ── names, synonyms, categories ──────────────────────────────────────────────
def test_exact_russian_name(matcher):
    r = matcher.match("Подрамник")
    assert r.status == "single_match"
    assert r.part_id == "AS-016"


def test_parenthetical_alt_name_matches(matcher):
    # "nadduv" is the parenthetical alt of MU-075 -> single, not ambiguous.
    r = matcher.match("nadduv")
    assert r.status == "single_match"
    assert r.part_id == "MU-075"


def test_stupitsa_podsipniki_category_and_subcategory(matcher):
    r = matcher.match("Stupitsa podşipniki")
    assert r.status == "single_match"
    assert r.part_id == "AS-007"
    assert r.category == "Подвеска"
    assert r.subcategory == "Stupisa (toplar) / podşipniklər"


# ── attribute extraction from raw text ───────────────────────────────────────
def test_side_filled_position_still_needed(matcher):
    r = matcher.match("Stupitsa podşipniki", raw_text="sol tərəf podşipnik")
    assert r.status == "single_match"
    assert r.attributes["side"] == "sol"
    assert r.attributes["position"] is None
    assert r.needs_clarification == ["position"]


def test_side_extracted_position_not_asked_when_flag_false(matcher):
    # KZ-020 position flag is false -> "qabaq" is ignored, position not asked.
    r = matcher.match("yan güzgü", raw_text="sol qabaq güzgü")
    assert r.status == "single_match"
    assert r.attributes == {"side": "sol", "position": None}
    assert r.needs_clarification == []


def test_side_and_position_when_both_flags_true(matcher):
    # AS-007 has side:true and position:true -> both extracted from raw.
    r = matcher.match("Stupitsa podşipniki", raw_text="sol qabaq")
    assert r.status == "single_match"
    assert r.attributes == {"side": "sol", "position": "ön"}
    assert r.needs_clarification == []


def test_both_sides_detected(matcher):
    r = matcher.match("yan güzgü", raw_text="sol ve sağ güzgü")
    assert r.attributes["side"] == "sol,sağ"


def test_flags_false_never_ask(matcher):
    # MU-037 Turbo: side/position both false.
    r = matcher.match("Turbo", raw_text="sol ön turbo")
    assert r.status == "single_match"
    assert r.part_id == "MU-037"
    assert r.needs_clarification == []
    assert r.attributes == {"side": None, "position": None}


# ── near-match (>=90%) & no-match ────────────────────────────────────────────
def test_plain_no_match(matcher):
    r = matcher.match("qwertyuiop")
    assert r.status == "no_match"
    assert r.match_score is None


def test_typo_near_matches_single(matcher):
    # one dropped letter (ş) -> still resolves to AS-007 as a >=90% near-match.
    r = matcher.match("stupitsa podsipniki")
    assert r.status == "single_match"
    assert r.part_id == "AS-007"
    assert 0.90 <= r.match_score < 1.0


def test_exact_match_scores_one(matcher):
    r = matcher.match("Turbo (nadduv) datçiki")
    assert r.status == "single_match"
    assert r.match_score == 1.0


def test_near_match_tie_stays_ambiguous(matcher):
    # "mad sensoru" is equidistant from MAP and MAF sensors -> no silent guess.
    r = matcher.match("mad sensoru")
    assert r.status == "ambiguous"
    ids = {c["part_id"] for c in r.candidates}
    assert ids == {"MU-033", "MU-045"}


def test_radiator_ekran_still_no_match_under_fuzzy(matcher):
    # nearest key is ~0.71 — well below 0.90, so it must not be pulled in.
    r = matcher.match("radiator ekran")
    assert r.status == "no_match"


def test_threshold_one_disables_near_match(matcher):
    r = matcher.match("stupitsa podsipniki", threshold=1.0)
    assert r.status == "no_match"


def test_no_silent_guess_on_ambiguous(matcher):
    r = matcher.match("traves")
    assert r.status == "ambiguous"
    assert len(r.candidates) >= 2


# ── structural sanity of the parsed dictionary ───────────────────────────────
def test_dictionary_shape(matcher):
    assert len(matcher.dict.groups) == 86
    assert len(matcher.dict.parts) == 438
    categories = {g.category for g in matcher.dict.groups.values()}
    assert len(categories) == 15
