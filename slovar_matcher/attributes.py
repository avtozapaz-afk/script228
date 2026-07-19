"""Side / position attribute extraction from free client text.

Only consulted for a ``single_match`` whose part carries the relevant flag.
Matching is token-based (whole words) to avoid false positives from
substrings like "on" inside unrelated words.
"""

from __future__ import annotations

from .normalize import tokens

# Canonical values are Azerbaijani: sol / sağ, ön / arxa.
_SIDE_LEFT = {
    "sol", "sola", "soldan", "soldakı", "soldaki",
    "left", "lh",
    "лево", "левый", "левая", "левое", "левых", "левого", "слева",
}
_SIDE_RIGHT = {
    "sağ", "sag", "sağa", "sağdan", "sagdan", "sağdakı", "sagdaki",
    "right", "rh",
    "право", "правый", "правая", "правое", "правых", "правого", "справа",
}
_POS_FRONT = {
    "ön", "on", "öndeki", "öndəki", "önki",
    "qabaq", "qabağ", "qabaqdakı", "qabaqdaki", "qabağdakı",
    "front", "fr",
    "перед", "передний", "передняя", "переднее", "передних", "переднего",
    "спереди", "speredi",
}
_POS_REAR = {
    "arxa", "arxadakı", "arxadaki", "arxadan", "arxadaku",
    "rear", "back",
    "зад", "задний", "задняя", "заднее", "задних", "заднего", "сзади",
}


def _detect(text: str, left_set: set[str], right_set: set[str],
            left_val: str, right_val: str) -> str | None:
    toks = set(tokens(text))
    has_left = bool(toks & left_set)
    has_right = bool(toks & right_set)
    if has_left and has_right:
        return f"{left_val},{right_val}"
    if has_left:
        return left_val
    if has_right:
        return right_val
    return None


def detect_side(text: str) -> str | None:
    """Return 'sol', 'sağ', 'sol,sağ' or None."""
    return _detect(text, _SIDE_LEFT, _SIDE_RIGHT, "sol", "sağ")


def detect_position(text: str) -> str | None:
    """Return 'ön', 'arxa', 'ön,arxa' or None."""
    return _detect(text, _POS_FRONT, _POS_REAR, "ön", "arxa")
