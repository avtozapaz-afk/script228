"""Side / direction / location attribute extraction from free client text.

Only consulted for a ``single_match`` whose part carries the relevant flag.
Matching is token-based (whole words) to avoid false positives from
substrings like "on" inside unrelated words.

Three independent flags, each with a two-valued Azerbaijani canonical output:
  * side      → sol / sağ        (left / right)
  * direction → ön / arxa        (front / rear)
  * location  → daxili / xarici  (inner / outer — ŞRUS, lambda, engine mounts)

For the lambda sensor (EG-002) the "location" distinction is *before / after*
the catalyst; до / əvvəl / before are folded onto ``daxili`` (upstream) and
после / sonra / after onto ``xarici`` (downstream), so the one location flag
covers both the inner/outer and the up/down-stream cases.
"""

from __future__ import annotations

from .normalize import tokens

# Canonical values are Azerbaijani: sol / sağ, ön / arxa.
#
# Homonym audit (all four sets were reviewed word-by-word for short Azerbaijani
# tokens that collide with common everyday words or numbers — see Bug 1):
#   * REMOVED "on" from _POS_FRONT — it is the Azerbaijani numeral "10", not a
#     short form of "ön"; "on ədəd" ("10 pieces") was being read as "front".
#   * "sağ" is kept although it doubles as "sağ ol" (thanks) / "alive": it is the
#     only word for "right", so it must stay; higher-order phrase disambiguation
#     is out of scope here.
#   * Everything else is an unambiguous directional word or a diacritic/latin
#     spelling variant of one. Whole-token matching (see `_detect`) prevents
#     substring false positives.
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
_DIR_FRONT = {
    "ön", "öndeki", "öndəki", "önki",
    "qabaq", "qabağ", "qabaqdakı", "qabaqdaki", "qabağdakı",
    "front", "fr",
    "перед", "передний", "передняя", "переднее", "передних", "переднего",
    "спереди", "speredi",
}
_DIR_REAR = {
    "arxa", "arxadakı", "arxadaki", "arxadan", "arxadaku",
    "rear", "back",
    "зад", "задний", "задняя", "заднее", "задних", "заднего", "сзади",
}

# location = inner / outer. ``iç`` / ``çöl`` are the everyday AZ terms used in the
# CV-joint synonyms ("iç qranat" / "çöl qranat"); ``daxili`` / ``xarici`` are the
# canonical forms. до / после (and əvvəl / sonra / before / after) are the
# lambda-sensor "before/after catalyst" wording, folded onto the same flag.
_LOC_INNER = {
    "daxili", "daxildəki", "daxildaki", "daxil",
    "iç", "içəri", "icheri", "içdəki", "icdeki",
    "inner", "internal",
    "внутренний", "внутренняя", "внутреннее", "внутренних", "внутреннего",
    "внутри", "vnutrenniy",
    "до", "before", "əvvəl", "əvvəlki", "evvel", "öncə", "once",
}
_LOC_OUTER = {
    "xarici", "xaricdəki", "xaricdaki", "xaric",
    "çöl", "cöl", "col", "çöldəki", "coldeki",
    "outer", "external", "outside",
    "наружный", "наружная", "наружное", "наружных", "наружного",
    "снаружи", "naruzhnyy",
    "после", "after", "sonra", "sonrakı", "sonraki",
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


def detect_direction(text: str) -> str | None:
    """Return 'ön', 'arxa', 'ön,arxa' or None."""
    return _detect(text, _DIR_FRONT, _DIR_REAR, "ön", "arxa")


def detect_location(text: str) -> str | None:
    """Return 'daxili', 'xarici', 'daxili,xarici' or None."""
    return _detect(text, _LOC_INNER, _LOC_OUTER, "daxili", "xarici")
