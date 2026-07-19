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
