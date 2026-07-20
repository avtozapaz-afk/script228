"""Side / direction / location attribute extraction from free client text.

Only consulted for a ``single_match`` whose part carries the relevant flag.
Matching is **token-based (whole words)**, case-insensitive (`tokens()` lowercases
with Azerbaijani-aware I-handling), so a keyword is a *word*, never a substring —
``sag`` does not match inside ``saqqal`` (beard), and multi-word keywords such as
``sol tərəf`` are matched as a contiguous run of tokens.

Three independent flags, each with a two-valued Azerbaijani canonical output:
  * side      → sol / sağ        (left / right)
  * direction → ön / arxa        (front / rear)
  * location  → daxili / xarici  (inner / outer — ŞRUS, lambda, engine mounts)

The keyword lists below are the single source of truth (the offline
``matcher_tool.html`` inlines these exact lists via ``build_html.py``). They cover
Azerbaijani, Russian, transliteration, common typos and diacritic-free /
keyboard variants.

Homonym note: ``"on"`` is the diacritic-free spelling of ``ön`` (front) **and** the
Azerbaijani numeral 10. It is treated as ``ön`` *except* when it is a counted
quantity — ``"on ədəd"`` ("10 pieces") — i.e. when the next token is a counter
word or a digit (see ``_COUNTER_WORDS`` and ``_present``). ``"sağ"`` is kept
although it doubles as ``sağ ol`` (thanks); it is the only word for "right".
"""

from __future__ import annotations

from .normalize import tokens

# ── keyword lists (single source of truth; mirrored into matcher_tool.html) ───
SIDE_LEFT = [
    # Azerbaijani
    "sol", "sola", "solda", "soldan", "soldakı", "soldaki",
    "sol tərəf", "sol teref",
    # English
    "left", "lh",
    # Russian
    "левый", "левая", "левые", "левых", "левое", "левого", "левой",
    "лево", "слева",
    # Russian transliteration
    "levy", "leviy", "levii", "levo", "levoy",
]
SIDE_RIGHT = [
    # Azerbaijani — incl. diacritic-free / keyboard variants
    "sağ", "sag", "saq", "sağa", "sağda", "sagda", "saqda",
    "sağdan", "sagdan", "sağdakı", "sagdaki",
    "sağ tərəf", "sag teref", "saq teref",
    # English
    "right", "rh",
    # Russian
    "правый", "правая", "правые", "правых", "правое", "правого", "правой",
    "право", "справа",
    # Russian transliteration
    "pravy", "praviy", "pravii", "pravo", "pravoy",
]
DIRECTION_FRONT = [
    # Azerbaijani — incl. diacritic-free / keyboard variants ("on" is guarded)
    "ön", "on", "öndə", "önde", "onde", "öndeki", "öndəki", "önki",
    "qabaq", "qabag", "gabag", "gabaq", "qabağ", "qabaqda", "qabagda",
    "qabaqdakı", "qabaqdaki", "qabağdakı",
    # English
    "front", "fr",
    # Russian
    "передний", "передняя", "передние", "передних", "переднее", "переднего",
    "перед", "спереди",
    # Russian transliteration
    "peredni", "peredny", "perednie", "peredniy", "perednii", "speredi",
]
DIRECTION_REAR = [
    # Azerbaijani
    "arxa", "arxada", "arxadakı", "arxadaki", "arxadan", "arxadaku",
    # English
    "rear", "back",
    # Russian
    "задний", "задняя", "задние", "задних", "заднее", "заднего",
    "зад", "задн", "сзади",
    # Russian transliteration
    "zadni", "zadny", "zadnie", "zadniy", "zadnii", "zad",
]

# location = inner / outer. ``iç`` / ``çöl`` are the everyday AZ terms used in the
# CV-joint synonyms ("iç qranat" / "çöl qranat"); ``daxili`` / ``xarici`` are the
# canonical forms. до / после (and əvvəl / sonra / before / after) are the
# lambda-sensor "before/after catalyst" wording, folded onto the same flag.
LOCATION_INNER = [
    "daxili", "daxildəki", "daxildaki", "daxil",
    "iç", "içəri", "icheri", "içdəki", "icdeki",
    "inner", "internal",
    "внутренний", "внутренняя", "внутреннее", "внутренних", "внутреннего",
    "внутри", "vnutrenniy",
    "до", "before", "əvvəl", "əvvəlki", "evvel", "öncə", "once",
]
LOCATION_OUTER = [
    "xarici", "xaricdəki", "xaricdaki", "xaric",
    "çöl", "cöl", "col", "çöldəki", "coldeki",
    "outer", "external", "outside",
    "наружный", "наружная", "наружное", "наружных", "наружного",
    "снаружи", "naruzhnyy",
    "после", "after", "sonra", "sonrakı", "sonraki",
]

#: ``"on"`` is the numeral 10, not ``ön``, when it directly precedes one of these
#: (or a digit). Normalized token forms.
_COUNTER_WORDS = {"ədəd", "eded", "dənə", "dene", "əd"}


def _prep(words: list[str]) -> tuple[set[str], list[list[str]]]:
    """Normalize each keyword into tokens; split into single tokens vs phrases."""
    singles: set[str] = set()
    phrases: list[list[str]] = []
    for word in words:
        toks = tokens(word)
        if not toks:
            continue
        if len(toks) == 1:
            singles.add(toks[0])
        else:
            phrases.append(toks)
    return singles, phrases


_LEFT_S, _LEFT_P = _prep(SIDE_LEFT)
_RIGHT_S, _RIGHT_P = _prep(SIDE_RIGHT)
_FRONT_S, _FRONT_P = _prep(DIRECTION_FRONT)
_REAR_S, _REAR_P = _prep(DIRECTION_REAR)
_INNER_S, _INNER_P = _prep(LOCATION_INNER)
_OUTER_S, _OUTER_P = _prep(LOCATION_OUTER)


def _phrase_present(toks: list[str], phrases: list[list[str]]) -> bool:
    for phrase in phrases:
        n = len(phrase)
        for i in range(len(toks) - n + 1):
            if toks[i:i + n] == phrase:
                return True
    return False


def _present(toks: list[str], singles: set[str], phrases: list[list[str]]) -> bool:
    for i, tok in enumerate(toks):
        if tok in singles:
            if tok == "on":
                nxt = toks[i + 1] if i + 1 < len(toks) else ""
                if nxt in _COUNTER_WORDS or nxt.isdigit():
                    continue  # "on ədəd" = 10 pieces, not "ön" (front)
            return True
    return _phrase_present(toks, phrases)


def _detect(text: str,
            left_s: set[str], left_p: list[list[str]],
            right_s: set[str], right_p: list[list[str]],
            left_val: str, right_val: str) -> str | None:
    toks = tokens(text)
    has_left = _present(toks, left_s, left_p)
    has_right = _present(toks, right_s, right_p)
    if has_left and has_right:
        return f"{left_val},{right_val}"
    if has_left:
        return left_val
    if has_right:
        return right_val
    return None


def detect_side(text: str) -> str | None:
    """Return 'sol', 'sağ', 'sol,sağ' or None."""
    return _detect(text, _LEFT_S, _LEFT_P, _RIGHT_S, _RIGHT_P, "sol", "sağ")


def detect_direction(text: str) -> str | None:
    """Return 'ön', 'arxa', 'ön,arxa' or None."""
    return _detect(text, _FRONT_S, _FRONT_P, _REAR_S, _REAR_P, "ön", "arxa")


def detect_location(text: str) -> str | None:
    """Return 'daxili', 'xarici', 'daxili,xarici' or None."""
    return _detect(text, _INNER_S, _INNER_P, _OUTER_S, _OUTER_P, "daxili", "xarici")
