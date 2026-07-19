"""Dictionary-integrity guards that run on every change to SLOVAR_FINAL.txt.

* no name / other-group-synonym collisions (Bug 3 regression);
* the LLM canonical-names list stays generated from the library (Task 4).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.check_collisions import find_collisions  # noqa: E402
from scripts.generate_canonical_names import _OUT, render  # noqa: E402
from scripts.generate_category_index import _OUT as _CAT_OUT  # noqa: E402
from scripts.generate_category_index import render as render_cat  # noqa: E402


def test_no_name_synonym_collisions():
    collisions = find_collisions()
    assert collisions == [], f"Unhandled collisions: {collisions}"


def test_canonical_names_in_sync():
    assert os.path.exists(_OUT), "run scripts/generate_canonical_names.py"
    with open(_OUT, encoding="utf-8") as fh:
        assert fh.read() == render(), (
            "data/canonical_names.md is stale — regenerate it from SLOVAR_FINAL.txt"
        )


def test_category_index_in_sync():
    assert os.path.exists(_CAT_OUT), "run scripts/generate_category_index.py"
    with open(_CAT_OUT, encoding="utf-8") as fh:
        assert fh.read() == render_cat(), (
            "data/category_index.json is stale — regenerate it from SLOVAR_FINAL.txt"
        )


def test_canonical_names_include_ya025():
    # YA-025 was the entry missing from the hand-maintained docx (Task 4).
    assert "YA-025" in render()
