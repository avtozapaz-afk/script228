#!/usr/bin/env python3
"""Сверка конвейера с размеченными эталонами.

По умолчанию берётся основной набор точности — 469 заявок
(``data/etalon_469.jsonl``). Набор поведения на 200 заявок
(``--etalon data/etalon_200.jsonl``) меряет другое: только в нём есть 27
заявок, где верный ответ — отказ или переспрос, и точка отсчёта «боевая
система права в 128 из 200».

Две независимые части.

**Потолок ретривера** считается всегда и API не требует: в скольких заявках
правильный код вообще попал в shortlist. Это верхняя граница для арбитра —
выше неё цепочка подняться не может физически, потому что арбитр выбирает
только из предложенного.

**Результат прогона** считается, если передан ``out/results.jsonl``: цифра,
которую надо ставить рядом с боевыми 128, плюс разбивка по уверенности
арбитра — по ней и выбирается порог, ниже которого не угадывать.

Правило подсчёта то же, что в разметке: у 27 заявок правильный ответ —
**пусто**. Выдала система код — ошибка; переспросила, попросила фото или
отказалась — попадание.

    python scripts/score_etalon.py                      # только потолок
    python scripts/score_etalon.py --results out/results.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from avtozap.layer0 import Layer0  # noqa: E402
from avtozap.retriever import RetrieverV2  # noqa: E402

DEFAULT_ETALON = os.path.join(ROOT, "data", "etalon_469.jsonl")
NO_CODE = "UNKNOWN"
#: Статусы, при которых система кода НЕ выдала.
NO_ANSWER_STATUSES = {"UNKNOWN", "REVIEW", "ERROR"}


def load_etalon(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def load_results(path: str) -> dict[str, list[dict]]:
    by_rfq: dict[str, list[dict]] = defaultdict(list)
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            by_rfq[str(record.get("rfq_id"))].append(record)
    return by_rfq


# ── потолок ретривера ───────────────────────────────────────────────────────
def retriever_ceiling(etalon: list[dict], retriever: RetrieverV2,
                      layer0: Layer0 | None = None) -> dict:
    """В скольких заявках правильный код вообще попал в shortlist.

    Заявки с ответом «кода быть не должно» здесь не считаются: у них нечему
    попадать в shortlist.

    ``layer0`` задан — ищем по каждому атомарному предмету, как это делает
    сквозной прогон; иначе по сырому тексту целиком. Разница между двумя
    замерами и есть вклад сегментации: в длинном сообщении на десяток деталей
    поиск по всему тексту нужную деталь физически не находит.
    """
    gradable = [r for r in etalon
                if r["expected_external_code"] != NO_CODE
                and r.get("scorable", True)]
    in_list = rank1 = 0
    misses: list[dict] = []
    for row in gradable:
        want = row["expected_external_code"]
        if layer0 is None:
            per_item = [retriever.retrieve(row["original_text"]).codes]
        else:
            per_item = [retriever.retrieve(item.item_raw).codes
                        for item in layer0.segment(row["original_text"]).items]
        found = any(want in codes for codes in per_item)
        if found:
            in_list += 1
            if any(codes and codes[0] == want for codes in per_item):
                rank1 += 1
        else:
            flat: list[str] = []
            for codes in per_item:
                flat.extend(codes)
            misses.append({"rfq_id": row["rfq_id"], "expected": want,
                           "text": row["original_text"][:70],
                           "got": flat[:5]})
    return {
        "gradable": len(gradable),
        "in_shortlist": in_list,
        "rank1": rank1,
        "misses": misses,
    }


# ── результат прогона ───────────────────────────────────────────────────────
def score_run(etalon: list[dict], by_rfq: dict[str, list[dict]]) -> dict:
    """Сколько заявок из 200 закрыты правильно."""
    correct: list[str] = []
    wrong: list[dict] = []
    missing = 0
    by_confidence: dict[str, Counter] = defaultdict(Counter)
    by_action = Counter()

    unscorable = 0
    for row in etalon:
        if not row.get("scorable", True):
            # Ожидаемого кода нет в нашей версии словаря: такую строку нельзя
            # ни засчитать, ни провалить.
            unscorable += 1
            continue
        rfq_id = row["rfq_id"]
        items = by_rfq.get(rfq_id)
        if not items:
            missing += 1
            continue

        want = row["expected_external_code"]
        produced = {i.get("final_external_code") for i in items
                    if i.get("final_external_code")}
        statuses = {i.get("final_status") for i in items}
        answered = statuses - NO_ANSWER_STATUSES

        if want == NO_CODE:
            # Правильный ответ — «кода быть не должно».
            hit = not answered
        else:
            hit = want in produced

        for item in items:
            confidence = (item.get("arbiter") or {}).get("confidence") or "—"
            by_confidence[confidence]["всего"] += 1
            by_confidence[confidence]["верно" if hit else "неверно"] += 1
            if item.get("action"):
                by_action[item["action"]] += 1

        if hit:
            correct.append(rfq_id)
        else:
            wrong.append({
                "rfq_id": rfq_id,
                "expected": want,
                "produced": sorted(produced) or ["—"],
                "statuses": sorted(statuses),
                "text": row["original_text"][:70],
                "production_was_right": row.get("reference_production_correct"),
            })

    return {
        "total": len(etalon),
        "scored": len(etalon) - missing - unscorable,
        "missing_from_results": missing,
        "unscorable": unscorable,
        "correct": len(correct),
        "wrong": wrong,
        "by_confidence": {k: dict(v) for k, v in by_confidence.items()},
        "by_action": dict(by_action),
    }


def compare_with_production(etalon: list[dict], scored: dict) -> dict:
    """Где мы лучше боевой системы, а где хуже — на одной выборке."""
    wrong_ids = {w["rfq_id"] for w in scored["wrong"]}
    production_right = {r["rfq_id"] for r in etalon
                        if str(r.get("reference_production_correct", "")).lower() == "да"}
    ours_right = {r["rfq_id"] for r in etalon if r["rfq_id"] not in wrong_ids}
    return {
        "production_correct": len(production_right),
        "ours_correct": len(ours_right),
        "both_correct": len(production_right & ours_right),
        "only_ours": sorted(ours_right - production_right),
        "only_production": sorted(production_right - ours_right),
        "both_wrong": len(set(r["rfq_id"] for r in etalon)
                          - production_right - ours_right),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--etalon", default=DEFAULT_ETALON)
    parser.add_argument("--results", default=None,
                        help="out/results.jsonl прогона; без него считается "
                             "только потолок ретривера")
    parser.add_argument("--limit", type=int, default=24)
    args = parser.parse_args(argv)

    etalon = load_etalon(args.etalon)
    baseline = sum(1 for r in etalon
                   if str(r.get("reference_production_correct", "")).lower() == "да")
    no_code = sum(1 for r in etalon if r["expected_external_code"] == NO_CODE)
    unscorable = sum(1 for r in etalon if not r.get("scorable", True))

    print(f"Эталон: {os.path.basename(args.etalon)} — {len(etalon)} заявок")
    if no_code:
        print(f"  из них с ответом «кода быть не должно»: {no_code}")
    else:
        print("  заявок с ответом «кода быть не должно» нет — поведение "
              "«переспроси / попроси фото» этот набор не меряет")
    if unscorable:
        print(f"  ⚠ вне подсчёта (код отсутствует в словаре 541): {unscorable}")
    if baseline:
        print(f"Точка отсчёта — боевая система: {baseline}/{len(etalon)} "
              f"= {baseline / len(etalon) * 100:.1f}%")
    else:
        print("Точки отсчёта в этом наборе нет: колонки с ответом боевой "
              "системы в нём не записано")
    print()

    print("=== Потолок ретривера (API не нужен) ===")
    retriever = RetrieverV2(limit=args.limit)
    raw = retriever_ceiling(etalon, retriever)
    segmented = retriever_ceiling(etalon, retriever, Layer0(retriever))
    n = segmented["gradable"]
    print(f"  заявок с ожидаемым кодом           : {n}")
    print(f"  код в shortlist по сырому тексту   : {raw['in_shortlist']}/{n} "
          f"= {raw['in_shortlist'] / n * 100:.1f}%")
    print(f"  код в shortlist после сегментации  : "
          f"{segmented['in_shortlist']}/{n} "
          f"= {segmented['in_shortlist'] / n * 100:.1f}%  "
          f"(вклад Layer 0: +{segmented['in_shortlist'] - raw['in_shortlist']})")
    print(f"  из них он на первом месте          : {segmented['rank1']}/{n} "
          f"= {segmented['rank1'] / n * 100:.1f}%")
    scorable_total = len(etalon) - unscorable
    ceiling_total = segmented["in_shortlist"] + no_code
    print(f"  ПОТОЛОК всей цепочки               : "
          f"{ceiling_total}/{scorable_total} "
          f"= {ceiling_total / scorable_total * 100:.1f}%"
          + (f" (с учётом {no_code} заявок, где верный ответ — отказ)"
             if no_code else ""))
    if baseline:
        print(f"  запас над боевой системой          : "
              f"+{ceiling_total - baseline} заявок")
    ceiling = segmented
    if ceiling["misses"]:
        print(f"\n  Ретривер потерял код в {len(ceiling['misses'])} заявк(ах):")
        for miss in ceiling["misses"][:12]:
            print(f"    {miss['rfq_id']} ждали {miss['expected']:8s} "
                  f"{miss['text']!r}")
            print(f"        дал: {miss['got']}")

    if not args.results:
        print("\nЧтобы получить цифру рядом со 128, нужен прогон по API:")
        print("  python run_test.py --input data/etalon_200.jsonl")
        print("  python scripts/score_etalon.py --results out/results.jsonl")
        return 0

    print("\n=== Результат прогона ===")
    scored = score_run(etalon, load_results(args.results))
    print(f"  правильно закрыто: {scored['correct']}/{scored['scored']} "
          f"= {scored['correct'] / max(scored['scored'], 1) * 100:.1f}%")
    if baseline:
        print(f"  боевая система   : {baseline}/{len(etalon)}")
    if scored.get("unscorable"):
        print(f"  вне подсчёта     : {scored['unscorable']}")
    if scored["missing_from_results"]:
        print(f"  нет в результатах: {scored['missing_from_results']}")

    print("\n  Разбивка по уверенности арбитра (для выбора порога):")
    print(f"    {'уверенность':14s} {'всего':>6s} {'верно':>6s} {'доля':>7s}")
    for level, counts in sorted(scored["by_confidence"].items()):
        total = counts.get("всего", 0)
        right = counts.get("верно", 0)
        share = f"{right / total * 100:.1f}%" if total else "—"
        print(f"    {level:14s} {total:6d} {right:6d} {share:>7s}")

    print("\n  Действия по правилу заказчика:")
    for action, count in sorted(scored["by_action"].items(), key=lambda kv: -kv[1]):
        print(f"    {action:14s} {count}")

    if not baseline:
        return 0

    comparison = compare_with_production(etalon, scored)
    print("\n  Против боевой системы на той же выборке:")
    print(f"    обе правы           : {comparison['both_correct']}")
    print(f"    только мы           : {len(comparison['only_ours'])}")
    print(f"    только боевая       : {len(comparison['only_production'])}")
    print(f"    обе ошиблись        : {comparison['both_wrong']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
