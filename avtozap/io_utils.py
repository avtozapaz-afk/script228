"""Чтение входа и запись результатов.

Вход — JSONL или CSV. Обязательное поле одно: текст запроса
(``original_text`` либо ``text``). Необязательные:

``rfq_id``            идентификатор запроса (иначе подставляется ``row-N``);
``image_path`` / ``image_url``  фото для слоя фото-доказательств;
``expected_external_code``  эталон; включает разбор ошибок по слоям в отчёте.

Выход пишется по одной записи на атомарный предмет и сразу сбрасывается на
диск: прогон можно прервать в любой момент, ничего не потеряв.
"""

from __future__ import annotations

import csv
import json
import os
from typing import Any, Iterator

TEXT_FIELDS = ("original_text", "text", "request", "message", "запрос", "текст")

CSV_COLUMNS = [
    "source_index", "rfq_id", "original_text",
    "item_index", "item_raw", "search_phrases", "is_part_request",
    "layer0_source", "layer0_status", "layer0_reason", "layer0_item_count",
    "vehicle_context",
    "oem_numbers", "oem_status", "oem_resolved_code", "oem_resolved_name",
    "oem_reason", "oem_rejected",
    "photo_status", "photo_summary",
    "retriever_status", "retriever_candidate_codes", "retriever_candidate_names",
    "retriever_scores", "retriever_reasons", "retriever_query_keys",
    "arbiter_decision", "arbiter_external_code", "arbiter_confidence",
    "arbiter_reason", "arbiter_clarification", "arbiter_model",
    "arbiter_attempts", "arbiter_error",
    "validator_status", "validator_code", "validator_reason",
    "final_external_code", "final_status",
    "expected_external_code", "failure_layer", "latency_ms", "error",
]


def read_requests(path: str) -> list[dict[str, Any]]:
    """Прочитать входной файл (JSONL или CSV) и проверить его пригодность."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"входной файл не найден: {path}")
    rows = (list(_read_jsonl(path)) if path.lower().endswith((".jsonl", ".ndjson"))
            else list(_read_csv(path)))
    if not rows:
        raise ValueError(f"во входном файле нет ни одной строки: {path}")

    normalized: list[dict[str, Any]] = []
    for i, row in enumerate(rows):
        text = ""
        for field in TEXT_FIELDS:
            value = row.get(field)
            if value and str(value).strip():
                text = str(value).strip()
                break
        if not text:
            raise ValueError(
                f"строка {i}: нет текста запроса; ожидалось одно из полей "
                f"{', '.join(TEXT_FIELDS)}; найдены поля: {sorted(row)}")
        row = dict(row)
        row["original_text"] = text
        row.setdefault("rfq_id", f"row-{i}")
        normalized.append(row)
    return normalized


def _read_jsonl(path: str) -> Iterator[dict[str, Any]]:
    with open(path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line or line.startswith("//"):
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{lineno}: некорректный JSON — {exc}") from exc
            if not isinstance(data, dict):
                raise ValueError(f"{path}:{lineno}: ожидался объект JSON")
            yield data


def _read_csv(path: str) -> Iterator[dict[str, Any]]:
    with open(path, encoding="utf-8-sig", newline="") as fh:
        sample = fh.read(8192)
        fh.seek(0)
        try:
            dialect: Any = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        for row in csv.DictReader(fh, dialect=dialect):
            yield {k.strip(): v for k, v in row.items() if k}


def load_done_keys(path: str) -> set[str]:
    """Ключи уже обработанных предметов — основа возобновления прогона.

    Битые/оборванные строки (прогон убили на середине записи) молча
    пропускаются: это ровно тот случай, ради которого resume и нужен.
    """
    done: set[str] = set()
    if not os.path.exists(path):
        return done
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            rfq_id = data.get("rfq_id")
            item_index = data.get("item_index")
            if rfq_id is not None and item_index is not None:
                done.add(f"{rfq_id}#{item_index}")
    return done


def read_results(path: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


class ResultWriter:
    """Дописывает JSONL со сбросом на диск после каждой записи."""

    def __init__(self, path: str):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.path = path
        self._terminate_partial_line(path)
        self._fh = open(path, "a", encoding="utf-8")

    @staticmethod
    def _terminate_partial_line(path: str) -> None:
        """Закрыть оборванную строку, если прошлый прогон убили посреди записи.

        Без этого следующая дозапись приклеилась бы к обрывку и испортила уже
        валидную запись — то есть падение прогона стоило бы двух результатов
        вместо одного.
        """
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            return
        with open(path, "rb+") as fh:
            fh.seek(-1, os.SEEK_END)
            if fh.read(1) != b"\n":
                fh.write(b"\n")

    def append(self, record: dict[str, Any]) -> None:
        self._fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._fh.flush()
        os.fsync(self._fh.fileno())

    def close(self) -> None:
        if not self._fh.closed:
            self._fh.close()

    def __enter__(self) -> "ResultWriter":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def flatten_record(record: dict[str, Any]) -> dict[str, Any]:
    """Развернуть вложенную запись в плоскую строку CSV."""
    oem = record.get("oem") or {}
    photo = record.get("photo") or {}
    retr = record.get("retriever") or {}
    arb = record.get("arbiter") or {}
    val = record.get("validator") or {}
    candidates = retr.get("candidates") or []

    return {
        "source_index": record.get("source_index"),
        "rfq_id": record.get("rfq_id"),
        "original_text": record.get("original_text"),
        "item_index": record.get("item_index"),
        "item_raw": record.get("item_raw"),
        "search_phrases": " | ".join(record.get("search_phrases") or []),
        "is_part_request": record.get("is_part_request"),
        "layer0_source": record.get("layer0_source"),
        "layer0_status": record.get("layer0_status"),
        "layer0_reason": record.get("layer0_reason"),
        "layer0_item_count": record.get("layer0_item_count"),
        "vehicle_context": record.get("vehicle_context"),
        "oem_numbers": " | ".join(oem.get("numbers") or []),
        "oem_status": oem.get("status"),
        "oem_resolved_code": oem.get("resolved_external_code"),
        "oem_resolved_name": oem.get("resolved_name"),
        "oem_reason": oem.get("reason"),
        "oem_rejected": " | ".join(f"{r.get('token')}({r.get('reason')})"
                                   for r in (oem.get("rejected") or [])),
        "photo_status": photo.get("status"),
        "photo_summary": photo.get("summary"),
        "retriever_status": retr.get("status"),
        "retriever_candidate_codes": " | ".join(c.get("external_code", "")
                                                for c in candidates),
        "retriever_candidate_names": " | ".join(c.get("name_ru", "") for c in candidates),
        "retriever_scores": " | ".join(str(c.get("score", "")) for c in candidates),
        "retriever_reasons": " | ".join(c.get("reason", "") for c in candidates),
        "retriever_query_keys": " | ".join(retr.get("query_keys") or []),
        "arbiter_decision": arb.get("decision"),
        "arbiter_external_code": arb.get("external_code"),
        "arbiter_confidence": arb.get("confidence"),
        "arbiter_reason": arb.get("reason"),
        "arbiter_clarification": arb.get("clarification_text"),
        "arbiter_model": arb.get("model"),
        "arbiter_attempts": arb.get("attempts"),
        "arbiter_error": arb.get("error"),
        "validator_status": val.get("status"),
        "validator_code": val.get("code"),
        "validator_reason": val.get("reason"),
        "final_external_code": record.get("final_external_code"),
        "final_status": record.get("final_status"),
        "expected_external_code": record.get("expected_external_code"),
        "failure_layer": record.get("failure_layer"),
        "latency_ms": record.get("latency_ms"),
        "error": record.get("error"),
    }


def write_csv(records: list[dict[str, Any]], path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    # utf-8-sig: Excel иначе показывает кириллицу и азербайджанские буквы кракозябрами.
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for record in records:
            writer.writerow(flatten_record(record))
