"""Deterministic part matcher — no LLM, no silent guessing.

Given a normalized phrase (already cleaned up by the Seller model) it returns
exactly one of three honest outcomes:

* ``single_match``  – one concrete ``part_id``;
* ``ambiguous``     – 2+ candidates (never pick one silently);
* ``no_match``      – nothing found in names or synonyms.

Only for ``single_match`` are side/position attributes extracted, and only for
the flags the part actually declares.
"""

from __future__ import annotations

import difflib
import os
from dataclasses import dataclass, field
from typing import Any

from .attributes import detect_position, detect_side
from .normalize import normalize
from .parser import Dictionary, parse_file

_DEFAULT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "SLOVAR_FINAL.txt",
)


@dataclass
class MatchResult:
    status: str                                   # single_match | ambiguous | no_match
    part_id: str | None = None
    name_ru: str | None = None
    name_az: str | None = None
    category: str | None = None
    subcategory: str | None = None
    attributes: dict[str, Any] = field(default_factory=lambda: {"side": None, "position": None})
    needs_clarification: list[str] = field(default_factory=list)
    candidates: list[dict[str, str]] = field(default_factory=list)
    fuzzy_suggestions: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "status": self.status,
            "part_id": self.part_id,
            "name_ru": self.name_ru,
            "name_az": self.name_az,
            "category": self.category,
            "subcategory": self.subcategory,
            "attributes": self.attributes,
            "needs_clarification": self.needs_clarification,
            "candidates": self.candidates,
        }
        if self.fuzzy_suggestions:
            d["fuzzy_suggestions"] = self.fuzzy_suggestions
        return d


class Matcher:
    def __init__(self, dictionary: Dictionary):
        self.dict = dictionary

    @classmethod
    def from_file(cls, path: str = _DEFAULT_PATH) -> "Matcher":
        return cls(parse_file(path))

    # ── public API ───────────────────────────────────────────────────────────
    def match(
        self,
        phrase: str,
        raw_text: str | None = None,
        fuzzy: bool = False,
        fuzzy_cutoff: float = 0.82,
    ) -> MatchResult:
        key = normalize(phrase)

        # Step 1a — exact match on a canonical name variant (highest priority).
        if key in self.dict.name_index:
            ids = self.dict.name_index[key]
            return self._build(ids, phrase, raw_text)

        # Step 1b — synonym hit -> the whole leaf group (all its part_ids).
        if key in self.dict.synonym_index:
            ids: list[str] = []
            for leaf_code in self.dict.synonym_index[key]:
                for pid in self.dict.groups[leaf_code].part_ids:
                    if pid not in ids:
                        ids.append(pid)
            return self._build(ids, phrase, raw_text)

        # No match — optionally *suggest* (never auto-select) via fuzzy search.
        result = MatchResult(status="no_match")
        if fuzzy:
            result.fuzzy_suggestions = self._fuzzy_suggest(key, fuzzy_cutoff)
        return result

    # ── internals ────────────────────────────────────────────────────────────
    def _build(self, ids: list[str], phrase: str, raw_text: str | None) -> MatchResult:
        if len(ids) == 1:
            return self._single(ids[0], phrase, raw_text)
        return self._ambiguous(ids)

    def _single(self, part_id: str, phrase: str, raw_text: str | None) -> MatchResult:
        part = self.dict.parts[part_id]
        search_space = " ".join(x for x in (phrase, raw_text) if x)

        attributes: dict[str, Any] = {"side": None, "position": None}
        needs: list[str] = []

        if part.side_flag:
            side = detect_side(search_space)
            if side:
                attributes["side"] = side
            else:
                needs.append("side")

        if part.position_flag:
            position = detect_position(search_space)
            if position:
                attributes["position"] = position
            else:
                needs.append("position")

        return MatchResult(
            status="single_match",
            part_id=part.part_id,
            name_ru=part.name_ru,
            name_az=part.name_az,
            category=part.category,
            subcategory=part.subcategory,
            attributes=attributes,
            needs_clarification=needs,
        )

    def _ambiguous(self, ids: list[str]) -> MatchResult:
        candidates = [
            {
                "part_id": p.part_id,
                "name_ru": p.name_ru,
                "name_az": p.name_az,
            }
            for p in (self.dict.parts[i] for i in ids)
        ]
        return MatchResult(status="ambiguous", candidates=candidates)

    def _fuzzy_suggest(self, key: str, cutoff: float) -> list[dict[str, str]]:
        """Nearest names/synonyms — advisory only, status stays ``no_match``."""
        pool: dict[str, list[str]] = {}
        for name_key, pids in self.dict.name_index.items():
            pool.setdefault(name_key, []).extend(pids)
        for syn_key, codes in self.dict.synonym_index.items():
            for code in codes:
                pool.setdefault(syn_key, []).extend(self.dict.groups[code].part_ids)

        close = difflib.get_close_matches(key, list(pool), n=5, cutoff=cutoff)
        seen: set[str] = set()
        suggestions: list[dict[str, str]] = []
        for matched_key in close:
            for pid in pool[matched_key]:
                if pid in seen:
                    continue
                seen.add(pid)
                part = self.dict.parts[pid]
                suggestions.append({
                    "part_id": part.part_id,
                    "name_ru": part.name_ru,
                    "name_az": part.name_az,
                    "matched_on": matched_key,
                })
        return suggestions


def match(phrase: str, raw_text: str | None = None, fuzzy: bool = False) -> dict[str, Any]:
    """Convenience one-shot: build the default matcher and match a phrase."""
    return Matcher.from_file().match(phrase, raw_text=raw_text, fuzzy=fuzzy).to_dict()
