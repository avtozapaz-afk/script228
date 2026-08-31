"""Сводный отчёт по прогону: цифры + список провалов по слоям."""

from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from typing import Any

from .types import (
    ARB_CLARIFY,
    ARB_ERROR,
    ARB_UNKNOWN,
    FINAL_ERROR,
    FINAL_REVIEW,
    FINAL_SELECT,
    FINAL_UNKNOWN,
    L0_EMPTY,
    L0_FALLBACK,
    LAYER_NONE,
    OEM_CONFLICT,
    OEM_UNRESOLVED,
    VAL_DOWNGRADE,
    VAL_REJECT,
)

_LAYER_TITLES = {
    "LAYER0_SEGMENTATION": "Layer 0 — сегментация",
    "NORMALIZATION_DICTIONARY": "Нормализация / словарь",
    "OEM_RESOLVER": "OEM-резолвер",
    "PHOTO_EVIDENCE": "Слой фото",
    "RETRIEVER_V2_MISS": "Retriever V2 — правильной детали не было среди кандидатов",
    "ARBITER_V3_SEMANTIC": "Arbiter V3 — семантическая ошибка (кандидат был на руках)",
    "VALIDATOR": "Валидатор — зарубил верный ответ",
    "TRUE_DICTIONARY_ABSENCE": "Детали действительно нет в словаре",
    "PIPELINE_ERROR": "Ошибка конвейера",
}


def build_summary(records: list[dict[str, Any]],
                  total_requests: int) -> dict[str, Any]:
    finals = Counter(r.get("final_status") for r in records)
    rfq_ids = {r.get("rfq_id") for r in records}

    layer0_failures = [r for r in records
                       if r.get("layer0_status") in (L0_EMPTY, L0_FALLBACK)]
    oem_conflicts = [r for r in records
                     if (r.get("oem") or {}).get("status") == OEM_CONFLICT]
    oem_unresolved = [r for r in records
                      if (r.get("oem") or {}).get("status") == OEM_UNRESOLVED]
    retriever_empty = [r for r in records
                       if (r.get("retriever") or {}).get("status") == "EMPTY"]
    arbiter_refusals = [r for r in records
                        if (r.get("arbiter") or {}).get("decision")
                        in (ARB_UNKNOWN, ARB_CLARIFY)]
    arbiter_errors = [r for r in records
                      if (r.get("arbiter") or {}).get("decision") == ARB_ERROR]
    validator_rejects = [r for r in records
                         if (r.get("validator") or {}).get("status") == VAL_REJECT]
    validator_downgrades = [r for r in records
                            if (r.get("validator") or {}).get("status") == VAL_DOWNGRADE]

    # Оцениваем ЗАПРОСАМИ, а не предметами: эталон задаётся на сообщение, а
    # предметов у сообщения может быть несколько, и требовать от каждого из них
    # совпадения с одним и тем же part_id было бы неверно.
    by_rfq: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        by_rfq[str(r.get("rfq_id"))].append(r)

    graded_rfqs: list[str] = []
    correct_rfqs: list[str] = []
    for rfq_id, items in by_rfq.items():
        expected = _expected_of(items)
        if not expected:
            continue
        graded_rfqs.append(rfq_id)
        produced = {i.get("final_part_id") for i in items if i.get("final_part_id")}
        statuses = {i.get("final_status") for i in items}
        ok = True
        for want in expected:
            if want.upper() in {"UNKNOWN", "NONE", "-"}:
                # Правильный ответ — «не знаю»: засчитываем, если ничего не выбрали.
                ok = ok and FINAL_SELECT not in statuses
            else:
                ok = ok and want in produced
        if ok:
            correct_rfqs.append(rfq_id)

    by_layer: dict[str, list[str]] = defaultdict(list)
    for r in records:
        layer = r.get("failure_layer") or LAYER_NONE
        if layer == LAYER_NONE:
            continue
        by_layer[layer].append(f"{r.get('rfq_id')}#{r.get('item_index')}")

    # Промахи ретривера считаем только там, где эталон известен, — иначе это
    # не факт, а догадка. Промах = ни у одного предмета запроса правильной
    # детали не было среди кандидатов.
    retriever_misses: list[str] = []
    for rfq_id in graded_rfqs:
        items = by_rfq[rfq_id]
        available: set[str] = set()
        for i in items:
            available |= {c.get("part_id")
                          for c in ((i.get("retriever") or {}).get("candidates") or [])}
        for want in _expected_of(items):
            if want.upper() in {"UNKNOWN", "NONE", "-"}:
                continue
            if want not in available:
                retriever_misses.append(f"{rfq_id}:{want}")

    summary: dict[str, Any] = {
        "total_raw_requests": total_requests,
        "processed_raw_requests": len(rfq_ids),
        "total_atomic_items": len(records),
        "final_status": {
            "SELECT": finals.get(FINAL_SELECT, 0),
            "UNKNOWN": finals.get(FINAL_UNKNOWN, 0),
            "REVIEW": finals.get(FINAL_REVIEW, 0),
            "ERROR": finals.get(FINAL_ERROR, 0),
        },
        "layer0_failures": len(layer0_failures),
        "oem_conflicts": len(oem_conflicts),
        "oem_unresolved": len(oem_unresolved),
        "retriever_no_candidates": len(retriever_empty),
        "retriever_misses_vs_expected": len(retriever_misses),
        "arbiter_refusals": len(arbiter_refusals),
        "arbiter_errors": len(arbiter_errors),
        "validator_rejections": len(validator_rejects),
        "validator_downgrades": len(validator_downgrades),
        "failed_ids_by_weakest_layer": {k: sorted(v) for k, v in sorted(by_layer.items())},
    }

    if graded_rfqs:
        summary["graded"] = {
            "requests_with_expected": len(graded_rfqs),
            "requests_correct": len(correct_rfqs),
            "accuracy": round(len(correct_rfqs) / len(graded_rfqs), 4),
            "incorrect_rfq_ids": sorted(set(graded_rfqs) - set(correct_rfqs)),
        }
    else:
        summary["graded"] = None
    return summary


def _expected_of(items: list[dict[str, Any]]) -> list[str]:
    """Эталонные ответы запроса (записаны одинаково во всех его предметах)."""
    for item in items:
        raw = item.get("expected_part_id")
        if raw:
            return [v.strip() for v in str(raw).split(",") if v.strip()]
    return []


def render_markdown(summary: dict[str, Any], config_note: str = "") -> str:
    f = summary["final_status"]
    total_items = summary["total_atomic_items"] or 1

    def pct(n: int) -> str:
        return f"{n} ({n / total_items * 100:.1f}%)"

    lines = [
        "# AVTOZAP — сводка прогона",
        "",
        f"* сырых запросов во входе: **{summary['total_raw_requests']}**",
        f"* обработано запросов: **{summary['processed_raw_requests']}**",
        f"* атомарных предметов после Layer 0: **{summary['total_atomic_items']}**",
        "",
        "## Итоговые статусы",
        "",
        "| статус | предметов |",
        "|---|---|",
        f"| SELECT | {pct(f['SELECT'])} |",
        f"| UNKNOWN | {pct(f['UNKNOWN'])} |",
        f"| REVIEW | {pct(f['REVIEW'])} |",
        f"| ERROR | {pct(f['ERROR'])} |",
        "",
        "## По слоям",
        "",
        "| показатель | значение |",
        "|---|---|",
        f"| сбои Layer 0 | {summary['layer0_failures']} |",
        f"| конфликты OEM | {summary['oem_conflicts']} |",
        f"| неразрешённые номера OEM | {summary['oem_unresolved']} |",
        f"| ретривер не дал кандидатов | {summary['retriever_no_candidates']} |",
        f"| промахи ретривера (при известном эталоне) | {summary['retriever_misses_vs_expected']} |",
        f"| отказы арбитра (UNKNOWN/CLARIFY) | {summary['arbiter_refusals']} |",
        f"| ошибки арбитра/API | {summary['arbiter_errors']} |",
        f"| отклонения валидатора | {summary['validator_rejections']} |",
        f"| понижения валидатора до REVIEW | {summary['validator_downgrades']} |",
    ]

    graded = summary.get("graded")
    lines += ["", "## Точность", ""]
    if graded:
        lines += [
            f"* запросов с эталоном: **{graded['requests_with_expected']}**",
            f"* полностью верных запросов: **{graded['requests_correct']}**",
            f"* точность: **{graded['accuracy'] * 100:.1f}%**",
        ]
        wrong = graded.get("incorrect_rfq_ids") or []
        if wrong:
            lines += ["", "Неверные запросы: `" + "`, `".join(wrong[:60]) + "`"
                      + (f" … и ещё {len(wrong) - 60}" if len(wrong) > 60 else "")]
    else:
        lines.append(
            "_Во входном файле нет поля `expected_part_id`, поэтому точность не "
            "считается. Разбор ошибок по слоям без эталона тоже неполон: добавьте "
            "эталонные ответы, чтобы получить настоящую картину слабого звена._")

    lines += ["", "## Провалы по слабому звену", ""]
    by_layer = summary.get("failed_ids_by_weakest_layer") or {}
    if not by_layer:
        lines.append("_Провалов не зафиксировано._")
    for layer, ids in by_layer.items():
        title = _LAYER_TITLES.get(layer, layer)
        lines.append(f"### {title} — {len(ids)}")
        lines.append("")
        lines.append("`" + "`, `".join(ids[:60]) + "`"
                     + (f" … и ещё {len(ids) - 60}" if len(ids) > 60 else ""))
        lines.append("")

    if config_note:
        lines += ["---", "", config_note]
    return "\n".join(lines) + "\n"


def write_summary(summary: dict[str, Any], out_dir: str,
                  config_note: str = "") -> tuple[str, str]:
    os.makedirs(out_dir, exist_ok=True)
    json_path = os.path.join(out_dir, "summary.json")
    md_path = os.path.join(out_dir, "summary.md")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(render_markdown(summary, config_note))
    return json_path, md_path
