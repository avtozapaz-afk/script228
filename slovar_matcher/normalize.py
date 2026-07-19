"""Azerbaijani/Russian-aware text normalisation.

The dictionary mixes Azerbaijani (Latin, with əöüışçğ / İI) and Russian
(Cyrillic).  Python's built-in ``str.lower()`` mangles the dotted/dotless
``I`` pair, so we normalise those explicitly before lowercasing the rest.
"""

import re

# Uppercase -> lowercase for the letters str.lower() gets wrong (or that we
# want handled deterministically) for Azerbaijani.  Everything else is left to
# str.lower(), which is correct for the remaining Latin and all Cyrillic.
_AZ_UPPER_MAP = str.maketrans({
    "İ": "i",   # U+0130 dotted capital I -> i
    "I": "ı",   # dotless capital I -> ı
    "Ə": "ə",
    "Ç": "ç",
    "Ş": "ş",
    "Ğ": "ğ",
    "Ö": "ö",
    "Ü": "ü",
})

# Letters that make up a "word" token (Latin, Azerbaijani specials, Cyrillic).
_TOKEN_RE = re.compile(r"[0-9a-zçğıəöüşA-ZÇĞİIƏÖÜŞЀ-ӿ]+")


def az_lower(text: str) -> str:
    """Lowercase ``text`` with correct Azerbaijani I-handling."""
    return text.translate(_AZ_UPPER_MAP).lower()


def normalize(text: str) -> str:
    """Lowercase and collapse whitespace — the canonical key form."""
    return re.sub(r"\s+", " ", az_lower(text).strip())


def tokens(text: str) -> list[str]:
    """Return the lowercased word tokens of ``text`` (for attribute search)."""
    return [az_lower(m) for m in _TOKEN_RE.findall(text)]


def _levenshtein(a: str, b: str) -> int:
    m, n = len(a), len(b)
    if not m:
        return n
    if not n:
        return m
    prev = list(range(n + 1))
    for i in range(1, m + 1):
        cur = [i] + [0] * n
        ai = a[i - 1]
        for j in range(1, n + 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ai != b[j - 1]))
        prev = cur
    return prev[n]


def similarity(a: str, b: str) -> float:
    """Normalized similarity in [0, 1]: ``1 - levenshtein / max(len)``.

    A couple of extra/typo'd letters on a longer phrase stay well above 0.9,
    while short strings need a near-exact match to clear the bar — which is the
    behaviour we want (typos tolerated, but not loose guessing).
    """
    longest = max(len(a), len(b))
    if longest == 0:
        return 1.0
    return 1.0 - _levenshtein(a, b) / longest
