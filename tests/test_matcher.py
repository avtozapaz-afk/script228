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
    # KZ-020 has side:true but direction/location:false -> only "side" is asked.
    r = matcher.match("yan güzgü")
    assert r.status == "single_match"
    assert r.part_id == "KZ-020"
    assert r.needs_clarification == ["side"]
    assert r.attributes == {"side": None, "direction": None, "location": None}
    assert r.category == "Кузов и оптика"


def test_turbo_nadduv_datciki_single_match_no_questions(matcher):
    r = matcher.match("Turbo (nadduv) datçiki")
    assert r.status == "single_match"
    assert r.part_id == "MU-075"
    assert r.needs_clarification == []
    assert r.attributes == {"side": None, "direction": None, "location": None}


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
def test_side_filled_direction_still_needed(matcher):
    # AS-007 has side:true and direction:true (location:false) -> side is filled
    # from the raw text, direction stays an open question.
    r = matcher.match("Stupitsa podşipniki", raw_text="sol tərəf podşipnik")
    assert r.status == "single_match"
    assert r.attributes["side"] == "sol"
    assert r.attributes["direction"] is None
    assert r.needs_clarification == ["direction"]


def test_side_extracted_direction_not_asked_when_flag_false(matcher):
    # KZ-020 direction flag is false -> "qabaq" is ignored, direction not asked.
    r = matcher.match("yan güzgü", raw_text="sol qabaq güzgü")
    assert r.status == "single_match"
    assert r.attributes == {"side": "sol", "direction": None, "location": None}
    assert r.needs_clarification == []


def test_side_and_direction_when_both_flags_true(matcher):
    # AS-007 has side:true and direction:true -> both extracted from raw.
    r = matcher.match("Stupitsa podşipniki", raw_text="sol qabaq")
    assert r.status == "single_match"
    assert r.attributes == {"side": "sol", "direction": "ön", "location": None}
    assert r.needs_clarification == []


def test_both_sides_detected(matcher):
    r = matcher.match("yan güzgü", raw_text="sol ve sağ güzgü")
    assert r.attributes["side"] == "sol,sağ"


# ── location flag (three-flag migration) ─────────────────────────────────────
def test_shrus_needs_location_clarification(matcher):
    # AS-008 ШРУС has side/direction/location all true; with no attribute words
    # in the text, "location" is among the open questions.
    r = matcher.match("ШРУС")
    assert r.status == "single_match"
    assert r.part_id == "AS-008"
    assert "location" in r.needs_clarification


def test_shrus_location_filled_from_text(matcher):
    # side + inner given (matches the HANDOFF example) -> only direction stays open.
    r = matcher.match("ШРУС", raw_text="sol daxili")
    assert r.status == "single_match"
    assert r.part_id == "AS-008"
    assert r.attributes == {"side": "sol", "direction": None, "location": "daxili"}
    assert r.needs_clarification == ["direction"]


def test_brake_pad_needs_direction_not_location(matcher):
    # EY-001 колодка has side:true, direction:true, location:false -> "direction"
    # is asked but "location" never is.
    r = matcher.match("Тормозная колодка")
    assert r.status == "single_match"
    assert r.part_id == "EY-001"
    assert "direction" in r.needs_clarification
    assert "location" not in r.needs_clarification


def test_location_outer_word_detected(matcher):
    r = matcher.match("ШРУС", raw_text="çöl qranat")
    assert r.attributes["location"] == "xarici"


def test_lambda_location_before_after_words(matcher):
    from slovar_matcher.attributes import detect_location
    # lambda's "before/after the catalyst" wording folds onto the location flag.
    assert detect_location("до катализатора") == "daxili"
    assert detect_location("после катализатора") == "xarici"


def test_flags_false_never_ask(matcher):
    # MU-037 Turbo: side/direction/location all false.
    r = matcher.match("Turbo", raw_text="sol ön turbo")
    assert r.status == "single_match"
    assert r.part_id == "MU-037"
    assert r.needs_clarification == []
    assert r.attributes == {"side": None, "direction": None, "location": None}


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


# ── "on" is the diacritic-free spelling of "ön" and is always read as front ──
def test_on_is_read_as_front(matcher):
    # By explicit choice "on" always maps to direction "ön" (front), even next to
    # a counter word — the diacritic-free spelling wins over the numeral reading.
    r = matcher.match(
        "Stupitsa podşipniki",
        raw_text="mənə sol tərəf üçün on ədəd stupitsa podşipniki lazımdır",
    )
    assert r.status == "single_match"
    assert r.attributes["side"] == "sol"
    assert r.attributes["direction"] == "ön"
    assert "direction" not in r.needs_clarification


def test_real_front_word_still_detected(matcher):
    r = matcher.match("Stupitsa podşipniki", raw_text="sol ön tərəf")
    assert r.attributes["direction"] == "ön"


# ── expanded keyword coverage (transliteration / typos / keyboard variants) ──
def test_expanded_keywords_saq_and_peredni():
    from slovar_matcher.attributes import detect_direction, detect_side
    # keyboard variant "saq" for "sağ", and transliteration "peredni" for "ön"
    assert detect_side("saq amortizator") == "sağ"
    assert detect_direction("peredni bufer") == "ön"


def test_saq_resolves_side_on_a_real_part(matcher):
    # end-to-end: AS-001 (amortizator, side:true) picks up "saq" as sağ
    r = matcher.match("amortizator", raw_text="saq amortizator")
    assert r.status == "single_match"
    assert r.part_id == "AS-001"
    assert r.attributes["side"] == "sağ"


def test_expanded_keywords_do_not_break_word_boundary():
    from slovar_matcher.attributes import detect_side
    # "saq" must not match as a substring inside "saqqal" (beard)
    assert detect_side("saqqal") is None


# ── Bug 2: "fara" must not silently match the bulb (EL-025) ───────────────────
def test_fara_not_silently_the_bulb(matcher):
    r = matcher.match("fara")
    assert r.status in ("single_match", "ambiguous")
    if r.status == "single_match":
        assert r.part_id == "KZ-005"          # the real headlight
    else:
        assert "KZ-005" in {c["part_id"] for c in r.candidates}
    # in no case is it a silent single match on the lamp
    assert not (r.status == "single_match" and r.part_id == "EL-025")


# ── Bug 3: name that collides with another group's synonym -> ambiguous ───────
# ── punctuation / stray symbols in the query are ignored ─────────────────────
def test_punctuation_is_stripped(matcher):
    from slovar_matcher.normalize import normalize
    assert normalize("Radiator.") == "radiator"
    assert normalize("(fara)") == "fara"
    assert normalize("Turbo (nadduv) datçiki") == "turbo nadduv datçiki"
    assert normalize("sol/sağ, ön!") == "sol sağ ön"


def test_query_with_punctuation_matches_same_as_clean(matcher):
    for dirty, clean in [("fara.", "fara"), ("(fara)", "fara"),
                         ("yan güzgü,", "yan güzgü"), ("traves.", "traves")]:
        rd, rc = matcher.match(dirty), matcher.match(clean)
        assert rd.status == rc.status
        assert rd.part_id == rc.part_id
        assert [c["part_id"] for c in rd.candidates] == [c["part_id"] for c in rc.candidates]


def test_full_name_matches_without_parentheses(matcher):
    # the de-parenthesised full name now also matches (punctuation-insensitive)
    r = matcher.match("turbo nadduv datçiki")
    assert r.status == "single_match"
    assert r.part_id == "MU-075"


# ── slash-alternative names expand into exact variants ───────────────────────
def test_slash_alternative_name_is_exact(matcher):
    # KZ-022 "Güzgü korpusu/qapağı" — each alternative is an exact 100% match,
    # not a ~65% near-match that needed the threshold dropped to 0.5.
    for q in ("güzgü korpusu", "güzgü qapağı", "крышка зеркала", "корпус зеркала"):
        r = matcher.match(q)
        assert r.status == "single_match", q
        assert r.part_id == "KZ-022", q
        assert r.match_score == 1.0, q


def test_slash_alternatives_across_the_catalogue(matcher):
    # a few more "/"-alternative names resolve to their single part exactly
    assert matcher.match("benzin bakı zamoku").part_id == "YA-005"
    assert matcher.match("amortizator podşipniki").part_id == "AS-003"


def test_bare_slash_word_not_over_promoted(matcher):
    # a name that is entirely "A/B" (KZ-039 Emblema/logo) must NOT turn a bare
    # generic word into a single match — it stays a group-synonym ambiguity.
    r = matcher.match("emblema")
    assert r.status == "ambiguous"
    assert "KZ-039" in {c["part_id"] for c in r.candidates}


def test_cross_group_collision_is_ambiguous():
    from slovar_matcher.matcher import Matcher
    from slovar_matcher.parser import parse_text

    text = (
        "════════ ДЕТАЛИЗАЦИЯ ════════\n"
        "########## TestCat ##########\n"
        "  ▸ Qrup A / Группа А  [grp-a]  (1 дет)\n"
        "      XX-001  Альфа  |  Alfa (widget)  [side:false|direction:false|location:false]\n"
        "      синонимы: alfa\n"
        "  ▸ Qrup B / Группа Б  [grp-b]  (2 дет)\n"
        "      XX-002  Бета  |  Beta  [side:false|direction:false|location:false]\n"
        "      XX-003  Гамма  |  Gamma  [side:false|direction:false|location:false]\n"
        "      синонимы: widget, beta syn\n"
    )
    m = Matcher(parse_text(text))
    # "widget" is XX-001's parenthetical name AND a synonym of the other group.
    r = m.match("widget")
    assert r.status == "ambiguous"
    ids = {c["part_id"] for c in r.candidates}
    assert ids == {"XX-001", "XX-002", "XX-003"}


# ── structural sanity of the parsed dictionary ───────────────────────────────
def test_dictionary_shape(matcher):
    assert len(matcher.dict.groups) == 86
    assert len(matcher.dict.parts) == 442     # 438 + 4 airbag positions (EL-073..076)
    categories = {g.category for g in matcher.dict.groups.values()}
    assert len(categories) == 15


# ── airbag positions (EL-073..076) ───────────────────────────────────────────
def test_sukan_airbag_is_el073_not_curtain(matcher):
    # exact name wins over the shared airbag-group synonyms -> EL-073, not EL-062
    r = matcher.match("sükan airbaqı")
    assert r.status == "single_match"
    assert r.part_id == "EL-073"
    assert r.subcategory == "Airbag / SRS bloku"


def test_sernisin_airbag_is_el074(matcher):
    r = matcher.match("sərnişin airbaqı")
    assert r.status == "single_match"
    assert r.part_id == "EL-074"


def test_diz_alti_airbag_side_sol(matcher):
    # phrase = normalized name, raw = full client text carrying the side word
    r = matcher.match("diz altı airbaqı", raw_text="sol diz altı airbaqı")
    assert r.status == "single_match"
    assert r.part_id == "EL-075"
    assert r.attributes["side"] == "sol"


def test_oturacaq_airbag_side_sag(matcher):
    r = matcher.match("oturacaq airbaqı", raw_text="sağ oturacaq airbaqı")
    assert r.status == "single_match"
    assert r.part_id == "EL-076"
    assert r.attributes["side"] == "sağ"


def test_sol_fara_side_is_azerbaijani(matcher):
    # side output is Azerbaijani "sol" — never the English input alias "left"
    r = matcher.match("fara", raw_text="sol fara")
    assert r.attributes["side"] == "sol"
    assert r.attributes["side"] != "left"
