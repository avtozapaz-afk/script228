#!/usr/bin/env python3
"""AVTOZAP — сквозной прогон конвейера по свежим сырым запросам.

    RAW → Layer 0 → OEM → Photo → Retriever V2 → Arbiter V3 → Validator → итог

Быстрый старт::

    pip install -r requirements.txt
    python run_test.py --input data/requests_300.jsonl

Ключ API берётся из переменной ``OPENAI_API_KEY``; если её нет, программа
спросит ключ скрытым вводом. Ключ никогда не печатается и не попадает в файлы.

Прогон возобновляемый: результат каждого атомарного предмета дописывается в
``out/results.jsonl`` сразу же, а повторный запуск той же команды пропускает
всё, что уже посчитано. Прервать можно в любой момент — Ctrl+C не теряет
сделанного.

Полезные флаги::

    --mock            без сети: детерминированная заглушка вместо Arbiter V3
    --limit 5         только первые N запросов (дымовой тест на реальном API)
    --no-resume       начать заново, переписав файл результатов
    --dry-run         проверить вход/словарь/сегментацию и выйти, не трогая API
    --report-only     пересобрать CSV и сводку из уже готового JSONL
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
import traceback
from typing import Any

from avtozap.config import (
    ACCEPTED_CONFIDENCE,
    DEFAULT_ARBITER_MODEL,
    DEFAULT_MAX_RETRIES,
    DEFAULT_SEGMENTER_MODEL,
    DEFAULT_TIMEOUT_S,
    DEFAULT_VISION_MODEL,
    OEM_CATALOG_PATH,
    OUT_DIR,
    RETRIEVER_LIMIT,
    SLOVAR_XLSX_PATH,
    RunConfig,
)
from avtozap.io_utils import (
    ResultWriter,
    load_done_keys,
    read_requests,
    read_results,
    write_csv,
)
from avtozap.report import build_summary, write_failure_analysis, write_summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--input", default=os.path.join("data", "requests_300.jsonl"),
                   help="JSONL или CSV с сырыми запросами "
                        "(по умолчанию data/requests_300.jsonl — реальные fresh-300)")
    p.add_argument("--out-dir", default=OUT_DIR, help="куда писать результаты")
    p.add_argument("--dict", dest="dict_path", default=SLOVAR_XLSX_PATH,
                   help="путь к словарю на 541 деталь (xlsx)")
    p.add_argument("--oem-catalog", default=OEM_CATALOG_PATH,
                   help="необязательный JSON-каталог OEM-номеров")
    p.add_argument("--model", default=DEFAULT_ARBITER_MODEL,
                   help=f"модель для Arbiter V3 (по умолчанию {DEFAULT_ARBITER_MODEL})")
    p.add_argument("--segmenter-model", default=DEFAULT_SEGMENTER_MODEL,
                   help=f"модель для Layer 0 (по умолчанию {DEFAULT_SEGMENTER_MODEL})")
    p.add_argument("--vision-model", default=DEFAULT_VISION_MODEL,
                   help="модель для слоя фото")
    p.add_argument("--limit", type=int, default=RETRIEVER_LIMIT,
                   help=f"размер shortlist Retriever V2 (по умолчанию {RETRIEVER_LIMIT})")
    p.add_argument("--accept-confidence", default=",".join(sorted(ACCEPTED_CONFIDENCE)),
                   help="уровни уверенности арбитра, принимаемые как SELECT; "
                        "остальные понижаются до REVIEW")
    p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S,
                   help="таймаут одного вызова API, секунд")
    p.add_argument("--max-retries", type=int, default=DEFAULT_MAX_RETRIES,
                   help="попыток на один вызов API")
    p.add_argument("--max-requests", type=int, default=None,
                   help="обработать только первые N сырых запросов (дымовой тест)")
    p.add_argument("--enable-photo", action="store_true",
                   help="включить слой фото (нужны image_path/image_url и vision-модель)")
    p.add_argument("--mock", action="store_true",
                   help="без сети: заглушка вместо Arbiter V3")
    p.add_argument("--dry-run", action="store_true",
                   help="проверить вход, словарь и сегментацию; API не вызывать")
    p.add_argument("--no-resume", action="store_true",
                   help="начать заново, перезаписав файл результатов")
    p.add_argument("--report-only", action="store_true",
                   help="пересобрать CSV и сводку из существующего results.jsonl")
    return p.parse_args(argv)


def resolve_api_key(needed: bool) -> str | None:
    """Ключ из окружения, иначе — скрытый ввод. Никогда не печатаем его обратно."""
    if not needed:
        return None
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if key:
        print("Ключ API: взят из переменной окружения OPENAI_API_KEY.")
        return key
    if not sys.stdin.isatty():
        raise SystemExit(
            "OPENAI_API_KEY не задан, а ввод неинтерактивный.\n"
            "Задайте переменную окружения или запустите с --mock / --dry-run.")
    key = getpass.getpass("Введите OPENAI_API_KEY (ввод скрыт): ").strip()
    if not key:
        raise SystemExit("Ключ не введён — прогон отменён.")
    return key


def build_report(out_dir: str, jsonl_path: str, total_requests: int,
                 config_note: str) -> None:
    records = read_results(jsonl_path)
    csv_path = os.path.join(out_dir, "results.csv")
    write_csv(records, csv_path)
    summary = build_summary(records, total_requests)
    json_path, md_path = write_summary(summary, out_dir, config_note)
    analysis_path = write_failure_analysis(records, summary, out_dir)
    f = summary["final_status"]
    print(f"\nЗаписано: {csv_path}\n          {json_path}\n          {md_path}"
          f"\n          {analysis_path}")
    print(f"Итог: предметов {summary['total_atomic_items']} · "
          f"SELECT {f['SELECT']} · UNKNOWN {f['UNKNOWN']} · "
          f"REVIEW {f['REVIEW']} · ERROR {f['ERROR']}")
    graded = summary.get("graded")
    if graded:
        print(f"Точность по эталону: {graded['requests_correct']}"
              f"/{graded['requests_with_expected']} запросов = "
              f"{graded['accuracy'] * 100:.1f}%")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    out_dir = os.path.abspath(args.out_dir)
    os.makedirs(out_dir, exist_ok=True)
    jsonl_path = os.path.join(out_dir, "results.jsonl")
    error_log = os.path.join(out_dir, "errors.log")

    # 1. Вход читаем и проверяем ДО любых обращений к сети.
    try:
        rows = read_requests(args.input)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ОШИБКА ВХОДА: {exc}", file=sys.stderr)
        return 2
    if args.max_requests:
        rows = rows[: args.max_requests]
    print(f"Вход: {args.input} — {len(rows)} сыр. запрос(ов)")

    accepted = frozenset(c.strip().lower() for c in args.accept_confidence.split(",")
                         if c.strip())
    config_note = (f"Модель: `{'mock' if args.mock else args.model}` · "
                   f"Layer 0: `{'детерминированный' if args.mock else args.segmenter_model}` · "
                   f"shortlist={args.limit} · принимаем confidence="
                   f"{'/'.join(sorted(accepted))}")

    if args.report_only:
        build_report(out_dir, jsonl_path, len(rows), config_note)
        return 0

    config = RunConfig(
        input_path=args.input, out_dir=out_dir, dict_path=args.dict_path,
        oem_catalog_path=args.oem_catalog, model=args.model,
        segmenter_model=args.segmenter_model,
        vision_model=args.vision_model, timeout_s=args.timeout,
        max_retries=args.max_retries, limit=args.limit,
        accepted_confidence=accepted, mock=args.mock or args.dry_run,
        enable_photo=args.enable_photo, max_requests=args.max_requests,
        resume=not args.no_resume,
    )

    # 2. Клиент API поднимаем только если он действительно нужен.
    needs_api = not (args.mock or args.dry_run)
    client = None
    if needs_api or (args.enable_photo and not args.dry_run):
        from avtozap.llm import LlmClient
        api_key = resolve_api_key(True)
        client = LlmClient(api_key, timeout_s=args.timeout,
                           max_retries=args.max_retries)

    # 3. Конвейер (здесь же грузится и проверяется словарь).
    from avtozap.pipeline import Pipeline
    try:
        pipeline = Pipeline(config, client=client)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ОШИБКА ЗАГРУЗКИ СЛОВАРЯ: {exc}", file=sys.stderr)
        return 2
    print(f"Словарь: {len(pipeline.retriever.dict)} деталей, "
          f"{len(pipeline.retriever.dict.term_index)} терминов")
    if pipeline.oem.catalog:
        print(f"Каталог OEM: {len(pipeline.oem.catalog)} номеров")
    else:
        print("Каталог OEM: не подключён — номера получат статус UNRESOLVED")

    if args.dry_run:
        return dry_run(pipeline, rows)

    # 4. Возобновление.
    if args.no_resume and os.path.exists(jsonl_path):
        os.remove(jsonl_path)
    done = load_done_keys(jsonl_path) if config.resume else set()
    if done:
        print(f"Возобновление: уже готово {len(done)} атомарн. предмет(ов) — пропускаю")

    total = len(rows)
    processed = skipped = failed = 0
    try:
        with ResultWriter(jsonl_path) as writer, \
                open(error_log, "a", encoding="utf-8") as errors:
            for index, row in enumerate(rows):
                rfq_id = str(row.get("rfq_id"))
                label = f"[{index + 1}/{total}] {rfq_id}"
                try:
                    records = pipeline.process_request(row, index)
                except Exception as exc:              # noqa: BLE001
                    # Сюда попадают только сбои ДО разбора на предметы
                    # (сам разбор предметов ловит свои ошибки сам).
                    failed += 1
                    message = f"{rfq_id}: {type(exc).__name__}: {exc}"
                    errors.write(message + "\n" + traceback.format_exc() + "\n")
                    errors.flush()
                    print(f"{label} ОШИБКА: {message}", file=sys.stderr)
                    continue

                statuses = []
                for record in records:
                    key = f"{record.rfq_id}#{record.item_index}"
                    if key in done:
                        skipped += 1
                        statuses.append("·")
                        continue
                    writer.append(record.to_dict())
                    done.add(key)
                    processed += 1
                    statuses.append(record.final_status[:3])
                    if record.error:
                        errors.write(f"{key}: {record.error}\n")
                        errors.flush()
                print(f"{label} предметов: {len(records)} [{' '.join(statuses)}]")
    except KeyboardInterrupt:
        print("\nПрервано пользователем. Готовые результаты сохранены — "
              "повторный запуск продолжит с места остановки.", file=sys.stderr)

    print(f"\nОбработано: {processed} · пропущено (уже было): {skipped} · "
          f"запросов со сбоем: {failed}")
    build_report(out_dir, jsonl_path, total, config_note)
    return 0


def dry_run(pipeline: Any, rows: list[dict[str, Any]]) -> int:
    """Прогон без сети: проверяем сегментацию и наличие кандидатов."""
    print("\n--- DRY RUN: API не вызывается ---")
    items = 0
    empty_retrieval = 0
    for index, row in enumerate(rows):
        records = pipeline.process_request(row, index)
        items += len(records)
        for record in records:
            if record.retriever.status == "EMPTY":
                empty_retrieval += 1
        if index < 5:
            for record in records:
                top = ", ".join(record.retriever.codes[:5]) or "—"
                print(f"  [{index}] {record.item_raw!r} → кандидаты: {top}")
    print(f"\nЗапросов: {len(rows)} · атомарных предметов: {items} · "
          f"без кандидатов: {empty_retrieval}")
    print("Вход, словарь, сегментация и ретривер работают. "
          "Для реального прогона уберите --dry-run.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
