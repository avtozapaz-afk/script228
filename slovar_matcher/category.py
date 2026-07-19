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

from .normalize import normalize, similarity

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
                return {
                    "status": "category_unknown",
                    "raw_text_passthrough": passthrough,
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
