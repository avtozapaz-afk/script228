"""JSON-in / JSON-out funnel over the reference part object.

The Seller/normalizer LLM produces a full reference object; the matcher takes
that object and fills only the fields it can decide deterministically, echoing
everything else back untouched::

    {
      "shop": null, "brand": null, ...,
      "part_name": "Amortizator",          # <- input
      "part_id": null, "category": null, "subcategory": null,
      "side": null, "direction": null, "location": null,
      ...,
      "raw": "sol qabaq amortizator"       # <- input
    }

Filled fields (nothing else is ever written):

* ``part_id``    — matched part ID (Layer 2), only on a single match;
* ``category``   — top category (Layer 1 / the matched part's category);
* ``subcategory``— leaf name (Layer 2), only on a single match;
* ``side``       — ``sol`` / ``sağ`` / null, extracted from ``raw``;
* ``direction``  — ``ön`` / ``arxa`` / null, extracted from ``raw``;
* ``location``   — ``daxili`` / ``xarici`` / null, extracted from ``raw``.

When ``part_name`` is null/empty, the detail matcher has nothing to match, so
we run a containment fallback over ``raw``: if it resolves to exactly one
category we fill ``category`` and leave ``part_id`` / ``subcategory`` null.
"""

from __future__ import annotations

from typing import Any

from .category import CategoryMatcher
from .matcher import DEFAULT_THRESHOLD, Matcher
from .normalize import normalize

#: Fields the funnel is allowed to write; everything else is passed through.
FILLED_FIELDS = ("part_id", "category", "subcategory", "side", "direction", "location")


class JsonMatcher:
    """Two-layer funnel with a JSON-object interface (Layer 1 + Layer 2)."""

    def __init__(self, matcher: Matcher, category_matcher: CategoryMatcher):
        self.matcher = matcher
        self.category = category_matcher

    @classmethod
    def from_file(cls) -> "JsonMatcher":
        return cls(Matcher.from_file(), CategoryMatcher.from_file())

    def match(self, part_json: dict[str, Any],
              threshold: float = DEFAULT_THRESHOLD) -> dict[str, Any]:
        """Take the reference JSON object, return it with the filled fields.

        The input is never mutated; a shallow copy is returned.
        """
        out = dict(part_json)
        part_name = out.get("part_name")
        raw = out.get("raw")

        if part_name and normalize(part_name):
            detail = self.matcher.match(part_name, raw_text=raw, threshold=threshold)
            if detail.status == "single_match":
                out["part_id"] = detail.part_id
                out["category"] = detail.category
                out["subcategory"] = detail.subcategory
                out["side"] = detail.attributes["side"]
                out["direction"] = detail.attributes["direction"]
                out["location"] = detail.attributes["location"]
            else:
                # ambiguous / no_match: no single part to name, but Layer 1 may
                # still pin down the category — fill it when it is unambiguous.
                cat = self.category.resolve(part_name, raw_text=raw, threshold=threshold)
                if cat["status"] == "category_resolved":
                    out["category"] = cat["category"]
        else:
            # part_name is null/empty -> containment fallback over raw.
            cats = self.category.categories_in_text(raw or "")
            if len(cats) == 1:
                out["category"] = cats[0]

        return out


def match(part_json: dict[str, Any]) -> dict[str, Any]:
    """Convenience one-shot: build the default funnel and match one JSON object."""
    return JsonMatcher.from_file().match(part_json)
