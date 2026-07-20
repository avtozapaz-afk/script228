"""Deterministic part matcher — no LLM, no silent guessing.

Given a normalized phrase (already cleaned up by the Seller model) it returns
exactly one of three honest outcomes:

* ``single_match``  – one concrete ``part_id``;
* ``ambiguous``     – 2+ candidates (never pick one silently);
* ``no_match``      – nothing found in names or synonyms.

Matching is **exact-first, then a near-match (≥ threshold) fallback** so that a
couple of extra/typo'd letters still resolve, without ever loosening into a
guess: name matches take priority over synonym matches, and when the best
near-match maps to several distinct parts the result stays ``ambiguous``.

Only for ``single_match`` are side/direction/location attributes extracted, and
only for the flags the part actually declares.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from .attributes import detect_direction, detect_location, detect_side
from .head_parts import HeadParts
from .normalize import normalize, similarity
from .parser import Dictionary, parse_file

_DEFAULT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "SLOVAR_FINAL.txt",
)

#: Default near-match threshold. Anything below this is not considered a match.
DEFAULT_THRESHOLD = 0.90
_EPS = 1e-9


@dataclass
class MatchResult:
    status: str                                   # single_match | ambiguous | no_match
    part_id: str | None = None
    name_ru: str | None = None
    name_az: str | None = None
    category: str | None = None
    subcategory: str | None = None
    attributes: dict[str, Any] = field(
        default_factory=lambda: {"side": None, "direction": None, "location": None})
    needs_clarification: list[str] = field(default_factory=list)
    candidates: list[dict[str, str]] = field(default_factory=list)
    match_score: float | None = None              # 1.0 exact, <1.0 near-match

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
        if self.match_score is not None:
            d["match_score"] = self.match_score
        return d


class Matcher:
    def __init__(self, dictionary: Dictionary, head_parts: HeadParts | None = None):
        self.dict = dictionary
        # No config by default so a synthetic Matcher(parse_text(...)) stays pure;
        # Matcher.from_file() loads the real head-part table.
        self.head_parts = head_parts if head_parts is not None else HeadParts.empty()

    @classmethod
    def from_file(cls, path: str = _DEFAULT_PATH,
                  head_parts: HeadParts | None = None) -> "Matcher":
        return cls(parse_file(path), head_parts or HeadParts.from_file())

    # ── public API ───────────────────────────────────────────────────────────
    def match(
        self,
        phrase: str,
        raw_text: str | None = None,
        threshold: float = DEFAULT_THRESHOLD,
        restrict_category: str | None = None,
    ) -> MatchResult:
        result = self._match_core(phrase, raw_text, threshold, restrict_category)
        # Head-part layer: only ever refines an ``ambiguous`` group hit into a
        # concrete part; ``single_match`` / ``no_match`` are returned untouched.
        if result.status == "ambiguous":
            head = self._resolve_head_part(phrase, raw_text, restrict_category)
            if head is not None:
                return head
        return result

    def _match_core(
        self,
        phrase: str,
        raw_text: str | None,
        threshold: float,
        restrict_category: str | None,
    ) -> MatchResult:
        key = normalize(phrase)

        # Step 1a — exact name match (highest priority).
        name_hit = self.dict.name_index.get(key)
        if name_hit is not None:
            ids = list(name_hit)
            # Collision guard: if this exact name is *also* a synonym of some
            # OTHER group, it is not an unambiguous identifier — surface both
            # sides as ``ambiguous`` instead of letting the name win silently.
            syn_hit = self.dict.synonym_index.get(key)
            if syn_hit is not None:
                owner = {self.dict.parts[p].leaf_code for p in name_hit}
                extra = [code for code in syn_hit if code not in owner]
                for pid in self._group_ids(extra):
                    if pid not in ids:
                        ids.append(pid)
            return self._build(ids, phrase, raw_text, 1.0, restrict_category)

        # Step 1b — exact synonym match -> whole leaf group.
        if key in self.dict.synonym_index:
            return self._build(self._group_ids(self.dict.synonym_index[key]),
                               phrase, raw_text, 1.0, restrict_category)

        # Step 2a — near-match on names (≥ threshold), best score wins.
        score, pids = self._fuzzy_refs(key, self.dict.name_index, threshold)
        if pids:
            return self._build(pids, phrase, raw_text, round(score, 3), restrict_category)

        # Step 2b — near-match on synonyms -> whole leaf group(s).
        score, codes = self._fuzzy_refs(key, self.dict.synonym_index, threshold)
        if codes:
            return self._build(self._group_ids(codes), phrase, raw_text,
                               round(score, 3), restrict_category)

        return MatchResult(status="no_match")

    # ── internals ────────────────────────────────────────────────────────────
    def _resolve_head_part(self, phrase: str, raw_text: str | None,
                           restrict_category: str | None) -> MatchResult | None:
        """Refine an ambiguous head word into its default / qualifier part.

        Returns ``None`` (keep the ambiguous result) when the phrase is not a
        head word, the target part is unknown, or a category restriction is in
        force and the target lies outside it.
        """
        key = normalize(phrase)
        search = " ".join(x for x in (phrase, raw_text) if x)
        target = self.head_parts.resolve(key, search)
        if target is None:
            return None
        part = self.dict.parts.get(target)
        if part is None:
            return None
        if restrict_category is not None and part.category != restrict_category:
            return None
        # Deterministic exact resolution -> score 1.0, attributes from raw.
        return self._single(target, phrase, raw_text, 1.0)

    def _group_ids(self, leaf_codes: list[str]) -> list[str]:
        ids: list[str] = []
        for code in leaf_codes:
            for pid in self.dict.groups[code].part_ids:
                if pid not in ids:
                    ids.append(pid)
        return ids

    def _fuzzy_refs(self, key: str, index: dict[str, list[str]],
                    threshold: float) -> tuple[float, list[str]]:
        """Return (best_score, refs) for index keys at the best score ≥ threshold.

        Only the top-scoring keys contribute, so a clear typo resolves to one
        part, while a genuine tie between distinct parts stays ambiguous.
        """
        best = 0.0
        hits: list[tuple[float, str]] = []
        for index_key in index:
            score = similarity(key, index_key)
            if score >= threshold:
                hits.append((score, index_key))
                if score > best:
                    best = score
        if not hits:
            return 0.0, []
        refs: list[str] = []
        for score, index_key in hits:
            if abs(score - best) < _EPS:
                for ref in index[index_key]:
                    if ref not in refs:
                        refs.append(ref)
        return best, refs

    def _build(self, ids: list[str], phrase: str, raw_text: str | None,
               score: float, restrict_category: str | None = None) -> MatchResult:
        if restrict_category is not None:
            # Layer-2 funnel: once the category is known, keep only candidates
            # in that category (see Part 2 of the spec).
            ids = [i for i in ids if self.dict.parts[i].category == restrict_category]
            if not ids:
                return MatchResult(status="no_match")
        if len(ids) == 1:
            return self._single(ids[0], phrase, raw_text, score)
        return self._ambiguous(ids, score)

    def _single(self, part_id: str, phrase: str, raw_text: str | None,
                score: float) -> MatchResult:
        part = self.dict.parts[part_id]
        search_space = " ".join(x for x in (phrase, raw_text) if x)

        attributes: dict[str, Any] = {"side": None, "direction": None, "location": None}
        needs: list[str] = []

        if part.side_flag:
            side = detect_side(search_space)
            if side:
                attributes["side"] = side
            else:
                needs.append("side")

        if part.direction_flag:
            direction = detect_direction(search_space)
            if direction:
                attributes["direction"] = direction
            else:
                needs.append("direction")

        if part.location_flag:
            location = detect_location(search_space)
            if location:
                attributes["location"] = location
            else:
                needs.append("location")

        return MatchResult(
            status="single_match",
            part_id=part.part_id,
            name_ru=part.name_ru,
            name_az=part.name_az,
            category=part.category,
            subcategory=part.subcategory,
            attributes=attributes,
            needs_clarification=needs,
            match_score=score,
        )

    def _ambiguous(self, ids: list[str], score: float) -> MatchResult:
        candidates = [
            {
                "part_id": p.part_id,
                "name_ru": p.name_ru,
                "name_az": p.name_az,
            }
            for p in (self.dict.parts[i] for i in ids)
        ]
        return MatchResult(status="ambiguous", candidates=candidates, match_score=score)


def match(phrase: str, raw_text: str | None = None,
          threshold: float = DEFAULT_THRESHOLD) -> dict[str, Any]:
    """Convenience one-shot: build the default matcher and match a phrase."""
    return Matcher.from_file().match(phrase, raw_text=raw_text, threshold=threshold).to_dict()
