"""Вендорный код Retriever V2 должен оставаться кодом проекта.

Правки, внесённые при переносе, ограничены разрешением путей к файлам. Тесты
ниже фиксируют это: если кто-то начнёт «улучшать» алгоритм проекта прямо в
vendor/, они упадут.
"""

import os
import re

import pytest

VENDOR = os.path.join("vendor", "retriever_v2")


def read(name: str) -> str:
    with open(os.path.join(VENDOR, name), encoding="utf-8") as fh:
        return fh.read()


def test_all_vendor_files_are_present():
    for name in ("avtozap_retriever_v2.py", "avtozap_matcher_engine.py",
                 "avtozap_ambiguity_layer.py", "danger.json"):
        assert os.path.exists(os.path.join(VENDOR, name)), name


def test_retrieval_algorithm_is_untouched():
    """Файл ретривера перенесён без единой правки логики."""
    source = read("avtozap_retriever_v2.py")
    # Опорные точки алгоритма проекта.
    for marker in ("ALIASES", "MODIFIERS", "SUFFIXES", "def stem_token",
                   "class RetrieverV2", "def retrieve_codes",
                   "context:", "fuzzy_phrase:", "fuzzy_token:", "fuzzy_compact:"):
        assert marker in source, marker
    # Единственный дозволенный вид правок — пути; их в этом файле нет вовсе.
    assert "os.path" not in source


def test_only_the_dictionary_path_was_adjusted_in_the_engine():
    source = read("avtozap_matcher_engine.py")
    assert "ЕДИНСТВЕННАЯ правка вендорного кода" in source
    assert "AVTOZAP_slovar_FINAL_541.xlsx" in source
    # Логика движка на месте.
    for marker in ("def phrases", "def match", "PREFER", "fuzzy_blocked"):
        assert marker in source, marker


def test_only_the_danger_path_was_adjusted_in_the_ambiguity_layer():
    source = read("avtozap_ambiguity_layer.py")
    assert "ЕДИНСТВЕННАЯ правка вендорного кода" in source
    for marker in ("CONTEXT_RULES", "def context_rule", "class AmbiguityLayer",
                   "def na"):
        assert marker in source, marker


def test_context_rules_still_carry_the_known_issue_guards():
    """Правила из KNOWN_ISSUES обязаны остаться в данных проекта."""
    source = read("avtozap_ambiguity_layer.py")
    assert '"KZ-005"' in source          # fara + lupa = целая фара
    assert "lupa" in source
    assert '"MU-017"' in source          # muherrik + yastiq = опора двигателя


def test_vendor_is_importable_and_loads_the_541_dictionary():
    from avtozap.dictionary import _ensure_vendor_on_path
    _ensure_vendor_on_path()
    import avtozap_matcher_engine as legacy
    assert len(legacy.PARTS) == 541
