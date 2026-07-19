"""Layer 1 (category) + funnel tests — spec Part 4."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from slovar_matcher.category import CategoryMatcher  # noqa: E402
from slovar_matcher.matcher import Matcher  # noqa: E402


@pytest.fixture(scope="module")
def cat() -> CategoryMatcher:
    return CategoryMatcher.from_file()


@pytest.fixture(scope="module")
def matcher() -> Matcher:
    return Matcher.from_file()


# ── Layer 1 ──────────────────────────────────────────────────────────────────
def test_radiator_category_resolved_no_question(cat):
    r = cat.resolve("radiator")
    assert r["status"] == "category_resolved"
    assert r["category"] == "Система охлаждения"


def test_fara_category_resolved(cat):
    r = cat.resolve("fara")
    assert r["status"] == "category_resolved"
    assert r["category"] == "Кузов и оптика"


def test_emblema_category_ambiguous(cat):
    r = cat.resolve("emblema")
    assert r["status"] == "category_ambiguous"
    assert set(r["candidates"]) == {"Кузов и оптика", "Салон и аксессуары"}
    assert r["question"]


def test_unknown_word_is_category_unknown(cat):
    r = cat.resolve("qwertyuiop", raw_text="qwertyuiop lazımdır")
    assert r["status"] == "category_unknown"
    assert r["raw_text"] == "qwertyuiop lazımdır"


def test_category_typo_near_match(cat):
    # a typo on a long-enough word (>=90% similar) still resolves its category.
    r = cat.resolve("amortizatorr")   # extra 'r' -> 0.917 similar to "amortizator"
    assert r["status"] == "category_resolved"
    assert r["category"] == "Подвеска"
    assert 0.90 <= r["match_score"] < 1.0


# ── Part 2: restrict_category funnel ─────────────────────────────────────────
def test_restrict_category_narrows_to_one(matcher):
    # "emblema" is a synonym in both Кузов and Салон groups -> ambiguous overall.
    wide = matcher.match("emblema")
    assert wide.status == "ambiguous"
    cats = {c["part_id"][:2] for c in wide.candidates}
    assert len(cats) >= 1

    narrowed = matcher.match("emblema", restrict_category="Кузов и оптика")
    # every surviving candidate must be in the requested category
    parts = matcher.dict.parts
    if narrowed.status == "single_match":
        assert parts[narrowed.part_id].category == "Кузов и оптика"
    else:
        assert narrowed.status == "ambiguous"
        for c in narrowed.candidates:
            assert parts[c["part_id"]].category == "Кузов и оптика"


def test_restrict_category_no_match_when_absent(matcher):
    # A cooling word restricted to an unrelated category -> no_match.
    r = matcher.match("radiator", restrict_category="Тормозная система")
    assert r.status == "no_match"


def test_full_funnel_emblema(cat, matcher):
    # Layer 1 asks; after the user picks "Кузов и оптика", Layer 2 stays inside it.
    step1 = cat.resolve("emblema")
    assert step1["status"] == "category_ambiguous"
    chosen = "Кузов и оптика"
    assert chosen in step1["candidates"]
    step2 = matcher.match("emblema", restrict_category=chosen)
    parts = matcher.dict.parts
    ids = [step2.part_id] if step2.part_id else [c["part_id"] for c in step2.candidates]
    assert ids and all(parts[i].category == chosen for i in ids)
