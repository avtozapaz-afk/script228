"""Head-part layer tests — deterministic default/qualifier resolution of core
optics words that would otherwise be ``ambiguous``."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from slovar_matcher.head_parts import HeadParts  # noqa: E402
from slovar_matcher.matcher import Matcher  # noqa: E402
from slovar_matcher.parser import parse_file  # noqa: E402


@pytest.fixture(scope="module")
def matcher() -> Matcher:
    return Matcher.from_file()


# ── default (no qualifier in raw) ────────────────────────────────────────────
def test_fara_defaults_to_headlight(matcher):
    r = matcher.match("fara")
    assert r.status == "single_match"
    assert r.part_id == "KZ-005"          # the headlight itself, not ambiguous


def test_stop_defaults_to_tail_light(matcher):
    r = matcher.match("stop")
    assert r.status == "single_match"
    assert r.part_id == "KZ-007"


def test_cyrillic_and_plural_head_words_resolve(matcher):
    assert matcher.match("фара").part_id == "KZ-005"
    assert matcher.match("стоп").part_id == "KZ-007"
    assert matcher.match("faralar").part_id == "KZ-005"


# ── qualifier in raw resolves to the specific part ───────────────────────────
def test_fara_glass_qualifier(matcher):
    r = matcher.match("fara", raw_text="fara şüşəsi lazımdır")
    assert r.status == "single_match"
    assert r.part_id == "KZ-010"          # Стекло фары


def test_stop_glass_qualifier(matcher):
    r = matcher.match("stop", raw_text="stop şüşəsi")
    assert r.status == "single_match"
    assert r.part_id == "KZ-048"          # Стекло заднего фонаря


def test_fara_lens_qualifier(matcher):
    assert matcher.match("fara", raw_text="fara linzası").part_id == "KZ-066"


def test_stop_led_board_qualifier(matcher):
    assert matcher.match("stop", raw_text="arxa stop led platası").part_id == "KZ-063"


def test_russian_qualifier(matcher):
    # a Russian qualifier in raw also resolves
    assert matcher.match("fara", raw_text="стекло фары").part_id == "KZ-010"


# ── attributes still extracted for the resolved part ─────────────────────────
def test_resolved_head_part_still_extracts_side(matcher):
    r = matcher.match("fara", raw_text="sol fara şüşəsi")
    assert r.part_id == "KZ-010"
    assert r.attributes["side"] == "sol"
    assert r.needs_clarification == []     # side filled, direction/location false


def test_default_head_part_asks_for_its_flags(matcher):
    # KZ-005 has side:true -> with no side in raw, side is still asked.
    r = matcher.match("fara")
    assert r.part_id == "KZ-005"
    assert "side" in r.needs_clarification


# ── guarantees: nothing else changes ─────────────────────────────────────────
def test_non_head_word_stays_ambiguous(matcher):
    r = matcher.match("traves")
    assert r.status == "ambiguous"
    assert len(r.candidates) == 5


def test_existing_single_match_untouched(matcher):
    r = matcher.match("Amortizator", raw_text="sol qabaq")
    assert r.status == "single_match"
    assert r.part_id == "AS-001"


def test_no_match_untouched(matcher):
    assert matcher.match("qwertyuiop").status == "no_match"


def test_head_layer_respects_restrict_category(matcher):
    # inside the right category the head word still resolves ...
    assert matcher.match("fara", restrict_category="Кузов и оптика").part_id == "KZ-005"
    # ... but restricted to an unrelated category the group is absent -> no_match,
    # and the head layer never fabricates a cross-category part.
    r = matcher.match("fara", restrict_category="Двигатель")
    assert r.status == "no_match"


# ── config is a separate, editable table ─────────────────────────────────────
def test_head_parts_resolve_is_pure_lookup():
    hp = HeadParts.from_file()
    assert hp.resolve("fara", "") == "KZ-005"                 # default
    assert hp.resolve("fara", "fara şüşəsi") == "KZ-010"      # qualifier
    assert hp.resolve("notahead", "whatever") is None         # unknown word


def test_custom_config_changes_behaviour():
    # a synthetic Matcher with no head config leaves "fara" ambiguous ...
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    plain = Matcher(parse_file(os.path.join(root, "data", "SLOVAR_FINAL.txt")))
    assert plain.match("fara").status == "ambiguous"
    # ... and an editable config can pin any ambiguous word to a chosen part.
    hp = HeadParts.from_data({"head_parts": [
        {"words": ["traves"], "default": "AS-018", "qualifiers": []},
    ]})
    custom = Matcher(parse_file(os.path.join(root, "data", "SLOVAR_FINAL.txt")), hp)
    r = custom.match("traves")
    assert r.status == "single_match"
    assert r.part_id == "AS-018"
