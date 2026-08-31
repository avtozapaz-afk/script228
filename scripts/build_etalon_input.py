#!/usr/bin/env python3
"""Собрать вход для сверки с эталоном из ``AVTOZAP_etalon_200.xlsx``.

Из каждой строки берётся ТОЛЬКО ``текст_заявки`` — сырое сообщение покупателя.
Всё остальное (что ответила боевая система, её статус, разметка уверенности)
идёт в отдельные справочные поля и на вход конвейера не подаётся.

Пустой ``ПРАВИЛЬНЫЙ_КОД`` — это не пропуск разметки, а полноценный ответ:
«кода быть не должно». Такие строки получают ``expected_external_code:
"UNKNOWN"``, и попаданием считается отказ или переспрос, а не код.

    python scripts/build_etalon_input.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SRC = os.path.join(ROOT, "data", "reference", "AVTOZAP_etalon_200.xlsx")
DEFAULT_OUT = os.path.join(ROOT, "data", "etalon_200.jsonl")

#: Ответ «кода быть не должно» кодируется этим значением.
NO_CODE = "UNKNOWN"


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def build(src: str, out: str, duplicates: dict[str, str] | None = None) -> dict:
    import openpyxl

    duplicates = duplicates or {}
    workbook = openpyxl.load_workbook(src, read_only=True, data_only=True)
    rows = list(workbook[workbook.sheetnames[0]].iter_rows(values_only=True))
    header = [_text(h) for h in rows[0]]

    records: list[dict] = []
    for index, raw in enumerate(rows[1:]):
        if not any(cell is not None for cell in raw):
            continue
        row = dict(zip(header, raw))
        text = _text(row.get("текст_заявки"))
        if not text:
            continue
        expected = _text(row.get("ПРАВИЛЬНЫЙ_КОД"))
        # Код-дубль в разметке приводим к каноническому: сверять надо с тем
        # кодом, который останется в словаре.
        expected = duplicates.get(expected, expected)
        records.append({
            "rfq_id": f"etalon-{index + 1:03d}",
            "original_text": text,
            "expected_external_code": expected or NO_CODE,
            # Справочно — на вход конвейера НЕ подаётся.
            "reference_production_code": _text(row.get("что_ответила_система")),
            "reference_production_status": _text(row.get("статус")),
            "reference_production_correct": _text(row.get("система_права")),
            "reference_label_confidence": _text(row.get("уверенность")),
            "reference_comment": _text(row.get("комментарий")),
            "source_rfq_id": _text(row.get("rfq_id")),
        })

    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    no_code = sum(1 for r in records if r["expected_external_code"] == NO_CODE)
    baseline = sum(1 for r in records
                   if r["reference_production_correct"].lower() == "да")
    return {"rows": len(records), "no_code": no_code, "production_baseline": baseline}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", default=DEFAULT_SRC)
    parser.add_argument("--out", default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    sys.path.insert(0, ROOT)
    from avtozap.dictionary import load_duplicates

    stats = build(args.src, args.out, load_duplicates())
    print(f"{args.out}: {stats['rows']} заявок")
    print(f"  из них с ответом «кода быть не должно»: {stats['no_code']}")
    print(f"  точка отсчёта (боевая система права)  : {stats['production_baseline']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
