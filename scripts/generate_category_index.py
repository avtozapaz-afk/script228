#!/usr/bin/env python3
"""Генерирует лёгкий индекс `слово -> категория(и)` для Слоя 1 из SLOVAR_FINAL.txt.

Слою 1 (определение категории) не нужны детали, подкатегории, синонимы
детального уровня, флаги side/direction/location — только соответствие слова категории.
Этот скрипт — единственный источник этого индекса, чтобы он не разъезжался с
основной библиотекой. Перегенерировать при каждом изменении SLOVAR_FINAL.txt.

Использование:
    python scripts/generate_category_index.py            # пишет data/category_index.json
    python scripts/generate_category_index.py --check    # проверяет, что он актуален
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from slovar_matcher.parser import parse_file  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_ROOT, "data", "SLOVAR_FINAL.txt")
_OUT = os.path.join(_ROOT, "data", "category_index.json")


def build_index(dict_path: str = _SRC) -> dict:
    d = parse_file(dict_path)
    word_to_cats: dict[str, set[str]] = defaultdict(set)

    # каждое имя-вариант детали -> категория этой детали
    for word, pids in d.name_index.items():
        for pid in pids:
            word_to_cats[word].add(d.parts[pid].category)
    # каждый синоним группы -> категория группы
    for word, codes in d.synonym_index.items():
        for code in codes:
            word_to_cats[word].add(d.groups[code].category)

    index = {w: sorted(cats) for w, cats in sorted(word_to_cats.items())}
    multi = {w: c for w, c in index.items() if len(c) > 1}
    return {
        "index": index,             # слово -> [категория, ...]
        "ambiguous_words": multi,   # только слова, ведущие в 2+ категории (триггеры вопроса)
        "meta": {
            "total_words": len(index),
            "ambiguous_count": len(multi),
            "categories": sorted({c for cats in index.values() for c in cats}),
        },
    }


def render() -> str:
    return json.dumps(build_index(), ensure_ascii=False, indent=1) + "\n"


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    content = render()
    if "--check" in argv:
        existing = ""
        if os.path.exists(_OUT):
            with open(_OUT, encoding="utf-8") as fh:
                existing = fh.read()
        if existing != content:
            print("FAIL: data/category_index.json is stale — run "
                  "scripts/generate_category_index.py")
            return 1
        print("OK: category_index.json is up to date.")
        return 0

    with open(_OUT, "w", encoding="utf-8") as fh:
        fh.write(content)
    data = build_index()
    m = data["meta"]
    print(f"Записан {_OUT}")
    print(f"  слов: {m['total_words']} | многокатегорийных (триггеры вопроса): {m['ambiguous_count']}")
    print(f"  категорий: {len(m['categories'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
