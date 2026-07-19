"""Deterministic AVTOZAP parts matcher (dictionary-based, no LLM)."""

from .matcher import Matcher, MatchResult, match
from .parser import Dictionary, Group, Part, parse_file, parse_text

__all__ = [
    "Matcher",
    "MatchResult",
    "match",
    "Dictionary",
    "Group",
    "Part",
    "parse_file",
    "parse_text",
]
