"""Head-part layer — a deterministic, editable override for core optics words.

Some words (``fara``, ``stop`` …) are shared synonyms of a whole optics group, so
the base matcher honestly returns ``ambiguous`` — the group has a real headlight,
its glass, its lens, its LED board, etc. But a shop that just says "fara" almost
always means *the headlight itself*; only when they add a qualifier ("fara şüşəsi")
do they mean the glass.

This layer encodes exactly that, from a hand-editable config
(``data/head_parts.json``): each head word carries a ``default`` part and a list
of qualifier rules (``keywords -> part_id``). It is consulted **only** when the
base match is ``ambiguous`` (see ``Matcher._resolve_head_part``), so it never
changes a working ``single_match`` and never invents a match where there was
none. No neural network, no statistics — a table lookup and a substring test.
"""

from __future__ import annotations

import json
import os
from typing import Any

from .normalize import normalize, tokens

_DEFAULT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "head_parts.json",
)


class HeadParts:
    """Loaded head-part rules: normalized word -> {default, qualifiers}."""

    def __init__(self, rules: dict[str, dict[str, Any]]):
        self.rules = rules

    @classmethod
    def empty(cls) -> "HeadParts":
        return cls({})

    @classmethod
    def from_file(cls, path: str = _DEFAULT_PATH) -> "HeadParts":
        if not os.path.exists(path):
            return cls.empty()
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return cls.from_data(data)

    @classmethod
    def from_data(cls, data: dict[str, Any]) -> "HeadParts":
        rules: dict[str, dict[str, Any]] = {}
        for entry in data.get("head_parts", []):
            default = entry.get("default")
            qualifiers = [
                {
                    # keep only the non-empty normalized keyword tokens
                    "keywords": [normalize(k) for k in q.get("keywords", []) if normalize(k)],
                    "part_id": q.get("part_id"),
                }
                for q in entry.get("qualifiers", [])
            ]
            rule = {"default": default, "qualifiers": qualifiers}
            for word in entry.get("words", []):
                key = normalize(word)
                if key:
                    rules[key] = rule
        return cls(rules)

    def resolve(self, phrase_key: str, search_text: str) -> str | None:
        """Return the target ``part_id`` for a head word, or ``None``.

        ``phrase_key`` must be the normalized phrase; if it is not a head word,
        returns ``None`` (the caller keeps the ambiguous result). Otherwise the
        first qualifier whose keyword appears in ``search_text`` wins; with no
        qualifier present, the head word's ``default`` is returned.
        """
        rule = self.rules.get(phrase_key)
        if rule is None:
            return None
        toks = tokens(search_text or "")
        for qual in rule["qualifiers"]:
            for kw in qual["keywords"]:
                if any(kw in tok for tok in toks):
                    return qual["part_id"]
        return rule["default"]
