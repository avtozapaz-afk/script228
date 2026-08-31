#!/usr/bin/env python3
"""Замер Retriever V2 на реальном fresh-300.

Два независимых вопроса:

1. **Верность адаптеру.** Совпадает ли shortlist, который выдаёт репозиторий,
   с тем, что лежит в devset (его посчитала поставленная реализация V2 на тех
   же ``item_raw``). Расхождение означало бы, что адаптер что-то исказил.
2. **Полнота.** Как часто ``old_external_code`` попадает в shortlist. Это НЕ
   точность: старый боевой код не является размеченной истиной. Метрика ровно
   та же, что в RETRIEVER_V2_NOTES.md, — «ретривер не потерял потенциально
   нужный код», и она позволяет честно сравнить варианты настройки.

    python scripts/measure_retriever.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from avtozap.retriever import RetrieverV2  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEVSET = os.path.join(ROOT, "data", "reference", "AVTOZAP_FRESH_300_V4_DEVSET.json")


def load_cases(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def measure(cases: list[dict], retriever: RetrieverV2, use_original: bool) -> dict:
    """Полнота по ``old_external_code``.

    ``use_original=False`` — поиск по ``item_raw`` из devset (сравнение чистого
    ретривера с эталонным замером). ``use_original=True`` — по сырому тексту
    заявки, то есть так, как работает сквозной прогон.
    """
    total = hit = empty = 0
    rank_sum = 0
    top1 = 0
    misses: list[str] = []
    for case in cases:
        old = case.get("old_external_code")
        if not old:
            continue
        query = case["original_text"] if use_original else case.get("item_raw") or ""
        result = retriever.retrieve(query)
        codes = result.codes
        total += 1
        if not codes:
            empty += 1
        if old in codes:
            hit += 1
            rank = codes.index(old) + 1
            rank_sum += rank
            if rank == 1:
                top1 += 1
        else:
            misses.append(f"{case.get('idx')}:{old}:{query[:40]}")
    return {
        "cases": total,
        "in_shortlist": hit,
        "recall": round(hit / total, 4) if total else 0.0,
        "rank1": top1,
        "rank1_share": round(top1 / total, 4) if total else 0.0,
        "mean_rank_when_found": round(rank_sum / hit, 2) if hit else None,
        "empty_shortlist": empty,
        "misses": misses,
    }


def fidelity(cases: list[dict], retriever: RetrieverV2) -> dict:
    """Насколько shortlist репозитория совпадает с зафиксированным в devset."""
    exact = 0
    contains_all = 0
    total = 0
    for case in cases:
        reference = [c["external_code"] for c in case.get("candidates") or []]
        if not reference:
            continue
        total += 1
        mine = retriever.retrieve(case.get("item_raw") or "").codes
        if mine[: len(reference)] == reference:
            exact += 1
        if set(reference) <= set(mine):
            contains_all += 1
    return {
        "cases": total,
        "identical_prefix": exact,
        "superset_of_reference": contains_all,
        "superset_share": round(contains_all / total, 4) if total else 0.0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--devset", default=DEVSET)
    parser.add_argument("--limit", type=int, default=24)
    args = parser.parse_args(argv)

    cases = load_cases(args.devset)
    print(f"кейсов в devset: {len(cases)}\n")

    plain = RetrieverV2(limit=args.limit)
    print("=== 1. Верность поставленной реализации (поиск по item_raw) ===")
    fid = fidelity(cases, plain)
    print(f"  кейсов со ссылочным shortlist : {fid['cases']}")
    print(f"  совпал префикс один-в-один    : {fid['identical_prefix']}")
    print(f"  наш shortlist содержит весь   : {fid['superset_of_reference']} "
          f"({fid['superset_share'] * 100:.1f}%)")

    print("\n=== 2. Полнота по old_external_code (диагностика, не истина) ===")
    for use_original in (False, True):
        m = measure(cases, plain, use_original)
        label = "сырой текст" if use_original else "item_raw   "
        print(f"  вход={label} | в shortlist {m['in_shortlist']}/{m['cases']} = "
              f"{m['recall'] * 100:.1f}% | rank#1 {m['rank1_share'] * 100:.1f}% | "
              f"пустой shortlist {m['empty_shortlist']}")
    print("\n  Это НЕ точность: old_external_code — код прежней боевой системы,")
    print("  а не размеченная истина. Метрика показывает только, что ретривер")
    print("  не потерял потенциально нужный код.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
