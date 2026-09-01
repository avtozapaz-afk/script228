#!/usr/bin/env python3
"""Сравнить две версии арбитра на одном эталоне.

Две цифры, ради которых сравнение и делается:

**Правильные ANSWER** — сколько заявок закрыто верно. Заявка засчитана, только
если выданы ВСЕ ожидаемые коды (многодетальная — целиком).

**Опасные ложные ANSWER** — сколько раз система уверенно выдала код там, где по
разметке кода быть не должно: покупатель не назвал деталь, просил уточнения или
прислал несколько деталей одной строкой. Это худший вид ошибки: покупатель
получит цену не на ту деталь и не узнает об этом.

Остальное — разбивка, которая объясняет разницу между версиями:

* *ошиблись кодом* — ответили, но не тем: заявка отвечаемая, ответ неверный;
* *переспросили зря* — отказались там, где верный код был в shortlist;
* *переспросили верно* — отказались там, где отказ и требовался.

Разделять их важно. Осторожная версия набирает мало «опасных», но много
«переспросили зря», и наоборот — по одной сумме этого не видно.

    python scripts/compare_arbiters.py --etalon data/etalon_200.jsonl \\
        --a out/etalon200_v3/results.jsonl --b out/etalon200_v4/results.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

NO_CODE = "UNKNOWN"
#: Статусы, при которых система кода не выдала.
NO_ANSWER_STATUSES = {"UNKNOWN", "REVIEW", "ERROR"}


def _codes(raw: str) -> list[str]:
    return [x.strip() for x in str(raw or "").split(",") if x.strip()]


def load_etalon(path: str) -> dict[str, dict]:
    rows = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                row = json.loads(line)
                rows[row["rfq_id"]] = row
    return rows


def load_results(path: str) -> dict[str, list[dict]]:
    by: dict[str, list[dict]] = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            by.setdefault(str(record.get("rfq_id")), []).append(record)
    return by


def grade(etalon: dict[str, dict], results: dict[str, list[dict]]) -> dict:
    """Разложить прогон на классы исходов."""
    out = {"scored": 0, "correct": [], "dangerous": [], "wrong_code": [],
           "asked_in_vain": [], "asked_rightly": [], "missing": []}
    for rfq_id, row in etalon.items():
        items = results.get(rfq_id)
        if not items:
            out["missing"].append(rfq_id)
            continue
        out["scored"] += 1
        wants = _codes(row["expected_external_code"])
        produced = {i.get("final_external_code") for i in items
                    if i.get("final_external_code")}
        answered = bool({i.get("final_status") for i in items} - NO_ANSWER_STATUSES)

        if wants == [NO_CODE]:
            if answered:
                out["dangerous"].append(rfq_id)
            else:
                out["correct"].append(rfq_id)
                out["asked_rightly"].append(rfq_id)
            continue

        if all(want in produced for want in wants):
            out["correct"].append(rfq_id)
        elif answered:
            out["wrong_code"].append(rfq_id)
        else:
            out["asked_in_vain"].append(rfq_id)
    return out


def _share(part: int, whole: int) -> str:
    return f"{part / whole * 100:.1f}%" if whole else "—"


def report(name_a: str, a: dict, name_b: str, b: dict, total: int) -> None:
    rows = [
        ("правильные ANSWER", "correct"),
        ("ОПАСНЫЕ ложные ANSWER", "dangerous"),
        ("ошиблись кодом", "wrong_code"),
        ("переспросили зря", "asked_in_vain"),
        ("переспросили верно", "asked_rightly"),
    ]
    width = max(len(label) for label, _ in rows)
    print(f"  {'':{width}s} {name_a:>12s} {name_b:>12s} {'разница':>9s}")
    for label, key in rows:
        x, y = len(a[key]), len(b[key])
        delta = y - x
        mark = ""
        if key == "dangerous" and delta > 0:
            mark = "  ← ХУЖЕ"
        elif key == "correct" and delta > 0:
            mark = "  ← лучше"
        print(f"  {label:{width}s} {x:>12d} {y:>12d} {delta:>+9d}{mark}")
    print()
    print(f"  точность: {name_a} {len(a['correct'])}/{total} = "
          f"{_share(len(a['correct']), total)} · "
          f"{name_b} {len(b['correct'])}/{total} = "
          f"{_share(len(b['correct']), total)}")


def verdict(a: dict, b: dict) -> str:
    """Принимать ли B. Правило заказчика: лучше семантика без роста опасных."""
    better = len(b["correct"]) - len(a["correct"])
    riskier = len(b["dangerous"]) - len(a["dangerous"])
    if better > 0 and riskier <= 0:
        return (f"ПРИНИМАТЬ: +{better} верных заявок, опасных ответов не больше "
                f"({riskier:+d}).")
    if better > 0 and riskier > 0:
        return (f"РЕШАТЬ ЗАКАЗЧИКУ: +{better} верных, но опасных ответов "
                f"стало на {riskier} больше.")
    if better == 0 and riskier < 0:
        return f"ПРИНИМАТЬ: точность та же, опасных ответов меньше на {-riskier}."
    return (f"НЕ ПРИНИМАТЬ: верных {better:+d}, опасных {riskier:+d} — "
            "выигрыша нет.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--etalon", required=True)
    parser.add_argument("--a", required=True, help="results.jsonl первой версии")
    parser.add_argument("--b", required=True, help="results.jsonl второй версии")
    parser.add_argument("--name-a", default="V3")
    parser.add_argument("--name-b", default="V4")
    parser.add_argument("--details", action="store_true",
                        help="показать заявки, на которых версии разошлись")
    args = parser.parse_args(argv)

    etalon = load_etalon(args.etalon)
    a = grade(etalon, load_results(args.a))
    b = grade(etalon, load_results(args.b))

    print(f"Эталон: {os.path.basename(args.etalon)} — {len(etalon)} заявок")
    for name, side in ((args.name_a, a), (args.name_b, b)):
        if side["missing"]:
            print(f"  ⚠ {name}: нет в результатах {len(side['missing'])} заявок")
    print()
    report(args.name_a, a, args.name_b, b, len(etalon))
    print()
    print("  " + verdict(a, b))

    if args.details:
        gained = sorted(set(b["correct"]) - set(a["correct"]))
        lost = sorted(set(a["correct"]) - set(b["correct"]))
        new_danger = sorted(set(b["dangerous"]) - set(a["dangerous"]))
        for title, ids in ((f"починил {args.name_b}", gained),
                           (f"сломал {args.name_b}", lost),
                           ("новые опасные ответы", new_danger)):
            if not ids:
                continue
            print(f"\n  {title}: {len(ids)}")
            for rfq_id in ids:
                row = etalon[rfq_id]
                print(f"    {rfq_id} ждали {row['expected_external_code']:>12s} "
                      f"| {row['original_text'][:52]!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
