"""Deterministic AVTOZAP parts matcher (dictionary-based, no LLM)."""

from .category import CategoryMatcher, resolve_category
from .matcher import Matcher, MatchResult, match
from .parser import Dictionary, Group, Part, parse_file, parse_text

__all__ = [
    "Matcher",
    "MatchResult",
    "match",
    "CategoryMatcher",
    "resolve_category",
    "Dictionary",
    "Group",
    "Part",
    "parse_file",
    "parse_text",
]
