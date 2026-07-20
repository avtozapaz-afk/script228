"""Deterministic AVTOZAP parts matcher (dictionary-based, no LLM)."""

from .category import CategoryMatcher, resolve_category
from .head_parts import HeadParts
from .json_api import JsonMatcher
from .json_api import match as match_json
from .matcher import Matcher, MatchResult, match
from .parser import Dictionary, Group, Part, parse_file, parse_text

__all__ = [
    "Matcher",
    "MatchResult",
    "match",
    "CategoryMatcher",
    "resolve_category",
    "HeadParts",
    "JsonMatcher",
    "match_json",
    "Dictionary",
    "Group",
    "Part",
    "parse_file",
    "parse_text",
]
