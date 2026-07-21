"""Layer 1 — category resolution (deterministic, dictionary-only).

Before the detail matcher runs, Layer 1 decides which of the 15 top categories a
word belongs to — purely from how many categories the word physically appears in
across the dictionary (`category_index.json`). No statistics, no probabilities:

* the word maps to exactly **one** category → ``category_resolved``;
* it maps to **two or more** categories → ``category_ambiguous`` (ask, never guess);
* it is not found and no near-match ≥ threshold → ``category_unknown``.

Near-match (default 0.90, same as the detail matcher) tolerates typos; if the best
near-match spans several categories, that is ``category_ambiguous`` too.

Every result carries ``raw_text_passthrough`` — the whole normalized request text,
forwarded to Layer 2 **verbatim**. Layer 1 does not parse, analyse, or reference
it; it only decides the category and attaches the text unchanged.
"""

from __future__ import annotations

import json
import os
from typing import Any

from .normalize import normalize, similarity, tokens

_INDEX_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "category_index.json",
)

_QUESTION = "К какой категории относится деталь?"
_EPS = 1e-9


class CategoryMatcher:
    def __init__(self, index_data: dict[str, Any]):
        self.index: dict[str, list[str]] = index_data["index"]
        self.ambiguous_words: dict[str, list[str]] = index_data.get("ambiguous_words", {})

    @classmethod
    def from_file(cls, path: str = _INDEX_PATH) -> "CategoryMatcher":
        with open(path, encoding="utf-8") as fh:
            return cls(json.load(fh))

    def resolve(self, word: str, raw_text: str | None = None,
                threshold: float = 0.90) -> dict[str, Any]:
        # The whole normalized request text is forwarded verbatim to Layer 2.
        # Layer 1 never touches it — it only decides the category.
        passthrough = raw_text if raw_text else word
        key = normalize(word)

        cats = self.index.get(key)
        score: float = 1.0
        if cats is None:
            score, cats = self._near_match(key, threshold)
            if cats is None:
                # Nothing matched at the given precision. Last resort: gather
                # every category where this word physically appears (as a
                # substring of any dictionary entry) and ask across all of them
                # — never silently give up if the word is present somewhere.
                fallback = self._containment_categories(key)
                if not fallback:
                    return {
                        "status": "category_unknown",
                        "raw_text_passthrough": passthrough,
                    }
                return {
                    "status": "category_ambiguous",
                    "candidates": fallback,
                    "question": _QUESTION,
                    "raw_text_passthrough": passthrough,
                    "matched_by": "containment",
                }

        if len(cats) == 1:
            return {
                "status": "category_resolved",
                "category": cats[0],
                "raw_text_passthrough": passthrough,
                "match_score": score,
            }
        return {
            "status": "category_ambiguous",
            "candidates": list(cats),
            "question": _QUESTION,
            "raw_text_passthrough": passthrough,
            "match_score": score,
        }

    def categories_in_text(self, text: str) -> list[str]:
        """Every category any token of ``text`` maps to (deduped, sorted).

        Used by the JSON funnel when ``part_name`` is null — a containment
        fallback over the raw request. Exact index-key hits are preferred; only
        if no token is an exact key do we widen to substring/containment, so a
        clean word (``amortizator``) resolves precisely and a stray fragment
        still surfaces something rather than nothing.
        """
        cats: list[str] = []
        toks = tokens(text or "")
        for tok in toks:
            for cat in self.index.get(tok, []):
                if cat not in cats:
                    cats.append(cat)
        if cats:
            return sorted(cats)
        for tok in toks:
            for cat in self._containment_categories(tok):
                if cat not in cats:
                    cats.append(cat)
        return sorted(cats)

    def _containment_categories(self, key: str) -> list[str]:
        """Every category whose entries contain ``key`` as a substring.

        Precision-independent fallback for when neither an exact nor a near-match
        hit at the configured threshold. Guarded to keys of length ≥ 3 so a very
        short fragment cannot drag in half the catalogue.
        """
        if len(key) < 3:
            return []
        cats: list[str] = []
        for index_key, kcats in self.index.items():
            if key in index_key:
                for cat in kcats:
                    if cat not in cats:
                        cats.append(cat)
        return sorted(cats)

    def _near_match(self, key: str, threshold: float) -> tuple[float, list[str] | None]:
        best = 0.0
        hits: list[tuple[float, str]] = []
        for index_key in self.index:
            s = similarity(key, index_key)
            if s >= threshold:
                hits.append((s, index_key))
                if s > best:
                    best = s
        if not hits:
            return 0.0, None
        cats: list[str] = []
        for s, index_key in hits:
            if abs(s - best) < _EPS:
                for cat in self.index[index_key]:
                    if cat not in cats:
                        cats.append(cat)
        return round(best, 3), sorted(cats)


def resolve_category(word: str, raw_text: str | None = None,
                     threshold: float = 0.90) -> dict[str, Any]:
    """Convenience one-shot: build the default Layer-1 matcher and resolve a word."""
    return CategoryMatcher.from_file().resolve(word, raw_text=raw_text, threshold=threshold)
