"""Харнесс: чтение входа, сериализация, возобновление, отчёт, устойчивость."""

import json
import os

import pytest

import run_test
from avtozap.io_utils import (
    CSV_COLUMNS,
    ResultWriter,
    flatten_record,
    load_done_keys,
    read_requests,
    read_results,
    write_csv,
)
from avtozap.report import build_summary, render_markdown


# ── чтение входа ────────────────────────────────────────────────────────────
def test_jsonl_input_is_read(tmp_path):
    path = tmp_path / "in.jsonl"
    path.write_text('{"rfq_id":"a","original_text":"bufer"}\n'
                    '// комментарий\n'
                    '{"rfq_id":"b","text":"fara"}\n', encoding="utf-8")
    rows = read_requests(str(path))
    assert [r["original_text"] for r in rows] == ["bufer", "fara"]


def test_csv_input_is_read(tmp_path):
    path = tmp_path / "in.csv"
    path.write_text("rfq_id,original_text\na,bufer\nb,fara\n", encoding="utf-8")
    assert len(read_requests(str(path))) == 2


def test_rfq_id_is_generated_when_absent(tmp_path):
    path = tmp_path / "in.jsonl"
    path.write_text('{"original_text":"bufer"}\n', encoding="utf-8")
    assert read_requests(str(path))[0]["rfq_id"] == "row-0"


def test_row_without_text_fails_loudly(tmp_path):
    path = tmp_path / "in.jsonl"
    path.write_text('{"rfq_id":"a","note":"нет текста"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="нет текста запроса"):
        read_requests(str(path))


def test_missing_input_file_fails_loudly(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_requests(str(tmp_path / "нет.jsonl"))


def test_empty_input_fails_loudly(tmp_path):
    path = tmp_path / "in.jsonl"
    path.write_text("", encoding="utf-8")
    with pytest.raises(ValueError):
        read_requests(str(path))


def test_supplied_smoke_set_is_readable():
    rows = read_requests(os.path.join("data", "sample_requests.jsonl"))
    assert len(rows) >= 10 and all(r["original_text"] for r in rows)


# ── запись и возобновление ──────────────────────────────────────────────────
def test_records_survive_a_run_killed_mid_line(tmp_path):
    """Оборванная строка не должна съедать следующую валидную запись."""
    path = str(tmp_path / "results.jsonl")
    with ResultWriter(path) as writer:
        writer.append({"rfq_id": "a", "item_index": 0})
    with open(path, "a", encoding="utf-8") as fh:
        fh.write('{"rfq_id": "оборв')          # процесс убили посреди записи
    with ResultWriter(path) as writer:
        writer.append({"rfq_id": "b", "item_index": 0})
    assert [r["rfq_id"] for r in read_results(path)] == ["a", "b"]


def test_done_keys_drive_resume(tmp_path):
    path = str(tmp_path / "results.jsonl")
    with ResultWriter(path) as writer:
        writer.append({"rfq_id": "a", "item_index": 0})
        writer.append({"rfq_id": "a", "item_index": 1})
    assert load_done_keys(path) == {"a#0", "a#1"}


def test_done_keys_of_a_missing_file_are_empty(tmp_path):
    assert load_done_keys(str(tmp_path / "нет.jsonl")) == set()


# ── сериализация ────────────────────────────────────────────────────────────
def test_csv_has_every_declared_column(tmp_path):
    record = {"rfq_id": "a", "item_index": 0, "original_text": "bufer",
              "retriever": {"status": "OK", "candidates": [
                  {"part_id": "KZ-001", "name_ru": "Бампер", "score": 1.0,
                   "reason": "exact_name"}]},
              "arbiter": {"decision": "SELECT", "part_id": "KZ-001"},
              "validator": {"status": "PASS"},
              "final_part_id": "KZ-001", "final_status": "SELECT"}
    path = str(tmp_path / "out.csv")
    write_csv([record], path)
    text = open(path, encoding="utf-8-sig").read()
    header = text.splitlines()[0].split(",")
    assert header == CSV_COLUMNS
    assert "KZ-001" in text


def test_flatten_survives_a_sparse_record():
    flat = flatten_record({"rfq_id": "a"})
    assert set(flat) == set(CSV_COLUMNS)


# ── отчёт ───────────────────────────────────────────────────────────────────
def _record(rfq, final_status, final_id=None, expected=None, candidates=()):
    return {
        "rfq_id": rfq, "item_index": 0, "final_status": final_status,
        "final_part_id": final_id, "expected_part_id": expected,
        "layer0_status": "OK", "failure_layer": "-",
        "oem": {"status": "NONE"}, "photo": {"status": "NO_IMAGE"},
        "retriever": {"status": "OK",
                      "candidates": [{"part_id": c} for c in candidates]},
        "arbiter": {"decision": "SELECT"}, "validator": {"status": "PASS"},
    }


def test_summary_counts_final_statuses():
    summary = build_summary(
        [_record("a", "SELECT"), _record("b", "UNKNOWN"), _record("c", "REVIEW")], 3)
    assert summary["final_status"] == {"SELECT": 1, "UNKNOWN": 1, "REVIEW": 1, "ERROR": 0}


def test_summary_grades_by_request_not_by_item():
    """У запроса из двух предметов эталон относится к запросу целиком."""
    records = [
        _record("a", "SELECT", "EY-001", "EY-001", ["EY-001"]),
        dict(_record("a", "SELECT", "EY-002", "EY-001", ["EY-002"]), item_index=1),
    ]
    assert build_summary(records, 1)["graded"]["accuracy"] == 1.0


def test_summary_counts_a_retriever_miss():
    records = [_record("a", "UNKNOWN", None, "EY-001", ["KZ-001"])]
    assert build_summary(records, 1)["retriever_misses_vs_expected"] == 1


def test_summary_without_expectations_says_so():
    summary = build_summary([_record("a", "SELECT", "EY-001")], 1)
    assert summary["graded"] is None
    assert "expected_part_id" in render_markdown(summary)


def test_markdown_renders_every_section():
    text = render_markdown(build_summary([_record("a", "SELECT")], 1))
    for heading in ("Итоговые статусы", "По слоям", "Точность", "слабому звену"):
        assert heading in text


# ── CLI ─────────────────────────────────────────────────────────────────────
def test_dry_run_needs_no_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert run_test.main([
        "--input", os.path.join("data", "sample_requests.jsonl"),
        "--out-dir", str(tmp_path), "--dry-run", "--limit", "3"]) == 0


def test_missing_input_returns_an_error_code(tmp_path):
    assert run_test.main(["--input", str(tmp_path / "нет.jsonl"),
                          "--out-dir", str(tmp_path), "--mock"]) == 2


def test_api_key_is_taken_from_the_environment(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-тест")
    assert run_test.resolve_api_key(True) == "sk-тест"


def test_no_key_is_never_needed_when_not_calling_the_api():
    assert run_test.resolve_api_key(False) is None
