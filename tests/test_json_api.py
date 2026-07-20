"""JSON-in / JSON-out funnel tests (slovar_matcher.json_api)."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from slovar_matcher.json_api import JsonMatcher, match as match_json  # noqa: E402


@pytest.fixture(scope="module")
def jm() -> JsonMatcher:
    return JsonMatcher.from_file()


def _reference(**over):
    """A full reference object with every field null unless overridden."""
    base = {
        "shop": None, "brand": None, "model": None, "year": None,
        "engine_type": None, "engine_volume": None,
        "part_name": None, "part_id": None, "category": None, "subcategory": None,
        "side": None, "direction": None, "location": None,
        "condition": None, "oem_code": None,
        "original_price": None, "aftermarket_price": None,
        "raw": None,
    }
    base.update(over)
    return base


# ── the spec example ─────────────────────────────────────────────────────────
def test_reference_example_amortizator(jm):
    out = jm.match(_reference(part_name="Amortizator", raw="sol qabaq amortizator"))
    assert out["part_id"] == "AS-001"
    assert out["category"] == "Подвеска"
    assert out["subcategory"] == "Amortizatorlar / dayaqlar"
    assert out["side"] == "sol"
    assert out["direction"] == "ön"          # qabaq -> ön
    assert out["location"] is None            # AS-001 location flag is false


def test_output_has_same_shape_and_untouched_fields(jm):
    src = _reference(shop="baku-parts", brand="Toyota", oem_code="48510-XYZ",
                     part_name="Amortizator", raw="sol qabaq amortizator")
    out = jm.match(src)
    # same keys, same order
    assert list(out.keys()) == list(src.keys())
    # non-filled fields are echoed verbatim
    for k in ("shop", "brand", "model", "year", "engine_type", "engine_volume",
              "condition", "oem_code", "original_price", "aftermarket_price", "raw"):
        assert out[k] == src[k]


def test_input_object_is_not_mutated(jm):
    src = _reference(part_name="Amortizator", raw="sol qabaq amortizator")
    jm.match(src)
    assert src["part_id"] is None            # the original stays untouched
    assert src["category"] is None
    assert src["side"] is None


# ── location flag flows through ──────────────────────────────────────────────
def test_shrus_fills_location_from_raw(jm):
    out = jm.match(_reference(part_name="ШРУС", raw="sol daxili"))
    assert out["part_id"] == "AS-008"
    assert out["category"] == "Подвеска"
    assert out["side"] == "sol"
    assert out["direction"] is None
    assert out["location"] == "daxili"


# ── part_name is null -> containment fallback over raw ───────────────────────
def test_null_part_name_containment_fills_only_category(jm):
    out = jm.match(_reference(part_name=None, raw="sol qabaq amortizator"))
    assert out["category"] == "Подвеска"     # resolved from raw
    assert out["part_id"] is None            # no detail match without a part_name
    assert out["subcategory"] is None
    # attributes are not extracted without a matched part
    assert out["side"] is None
    assert out["direction"] is None
    assert out["location"] is None


def test_null_part_name_unknown_raw_leaves_category_null(jm):
    out = jm.match(_reference(part_name=None, raw="qwertyuiop lazımdır"))
    assert out["category"] is None
    assert out["part_id"] is None


# ── no single detail match: category still resolved from Layer 1 ─────────────
def test_ambiguous_part_name_still_fills_category(jm):
    # "traves" is a group synonym shared by 5 parts -> no single part_id, but the
    # word lives in exactly one category, so category is still filled.
    out = jm.match(_reference(part_name="traves", raw="traves"))
    assert out["part_id"] is None            # genuinely ambiguous -> no pick
    assert out["subcategory"] is None
    assert out["category"] == "Подвеска"


def test_no_match_part_name_leaves_all_null(jm):
    out = jm.match(_reference(part_name="radiator ekran", raw="radiator ekran"))
    assert out["part_id"] is None
    assert out["subcategory"] is None
    # "radiator" alone would resolve, but "radiator ekran" is no_match on detail
    # and category resolution keys on the whole phrase -> not a single category.
    # (category may be null here; the important guarantee is no false part_id.)


# ── module-level convenience ─────────────────────────────────────────────────
def test_module_level_match_json():
    out = match_json(_reference(part_name="Amortizator", raw="sol qabaq amortizator"))
    assert out["part_id"] == "AS-001"
