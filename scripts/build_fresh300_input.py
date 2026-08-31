#!/usr/bin/env python3
"""Собрать вход для сквозного прогона из реального fresh-300 devset.

Из каждого кейса берётся ТОЛЬКО ``original_text`` — сырое сообщение покупателя.
``item_raw`` и ``candidates`` из devset намеренно НЕ переносятся: это результат
работы прежних Layer 0 и Retriever V2, и подавать их обратно на вход значило бы
измерять конвейер по его же подсказкам. В отчётах они доступны отдельно как
справочный материал (``data/reference/``).

``old_external_code`` тоже не переносится как эталон: по правилам поставки это
диагностическое поле прежней боевой системы, а не размеченная истина.

    python scripts/build_fresh300_input.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SRC = os.path.join(ROOT, "data", "reference", "AVTOZAP_FRESH_300_V4_DEVSET.json")
DEFAULT_OUT = os.path.join(ROOT, "data", "requests_300.jsonl")


def build(src: str, out: str) -> int:
    with open(src, encoding="utf-8") as fh:
        cases = json.load(fh)

    seen: set[str] = set()
    rows: list[dict] = []
    for case in cases:
        text = str(case.get("original_text") or "").strip()
        if not text:
            continue
        rfq_id = str(case.get("rfq_id") or "").strip()
        idx = case.get("idx")
        # rfq_id в devset повторяется у разных предметов одной заявки, поэтому
        # ключом строки берём idx — он уникален и сохраняет порядок.
        key = f"fresh-{idx:03d}" if isinstance(idx, int) else f"fresh-{len(rows):03d}"
        if key in seen:
            continue
        seen.add(key)
        rows.append({"rfq_id": key, "original_text": text, "source_rfq_id": rfq_id})

    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", default=DEFAULT_SRC)
    parser.add_argument("--out", default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    count = build(args.src, args.out)
    print(f"{args.out}: {count} сыр. запрос(ов) — только original_text")
    return 0


if __name__ == "__main__":
    sys.exit(main())
