"""Проверки чистой логики: разбор ответов, даты, csv, сводка."""

import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from avtozap_export import core  # noqa: E402


# ── разбор ответа сервера ────────────────────────────────────────────────────
def test_extract_rows_from_bare_list():
    assert core.extract_rows([{"id": 1}, {"id": 2}]) == [{"id": 1}, {"id": 2}]


def test_extract_rows_from_wrappers():
    assert core.extract_rows({"data": [{"id": 1}]}) == [{"id": 1}]
    assert core.extract_rows({"items": [{"id": 2}], "total": 5}) == [{"id": 2}]
    assert core.extract_rows({"data": {"items": [{"id": 3}]}}) == [{"id": 3}]


def test_extract_rows_finds_unknown_key():
    assert core.extract_rows({"offers": [{"id": 7}]}) == [{"id": 7}]


def test_extract_rows_on_junk():
    assert core.extract_rows({"ok": True}) == []
    assert core.extract_rows("не json") == []


def test_looks_like_list_response():
    assert core.looks_like_list_response([])
    assert core.looks_like_list_response({"data": []})
    assert not core.looks_like_list_response({"id": 1, "name": "настройка"})


def test_pages_estimate_from_total_and_pages():
    assert core.pages_estimate({"items": [], "total": 250}, 100) == 3
    assert core.pages_estimate({"items": [], "pages": 12}, 100) == 12
    assert core.pages_estimate({"items": []}, 100) is None


# ── даты ─────────────────────────────────────────────────────────────────────
def test_parse_datetime_understands_common_shapes():
    assert core.parse_datetime("2026-09-06T10:20:30Z").date() == dt.date(2026, 9, 6)
    assert core.parse_datetime("2026-09-06 10:20:30").date() == dt.date(2026, 9, 6)
    assert core.parse_datetime("06.09.2026").date() == dt.date(2026, 9, 6)
    assert core.parse_datetime("2026-09-06T10:20:30.123456+03:00").date() == dt.date(2026, 9, 6)
    assert core.parse_datetime(1757145600).year >= 2025


def test_parse_datetime_rejects_nonsense():
    assert core.parse_datetime(None) is None
    assert core.parse_datetime("") is None
    assert core.parse_datetime("не дата") is None
    assert core.parse_datetime(42) is None
    assert core.parse_datetime(True) is None


def test_find_date_field_prefers_creation_date():
    rows = [{"id": 1, "name": "x", "created_at": "2026-09-06T10:00:00"}]
    assert core.find_date_field(rows) == "created_at"


def test_filter_rows_by_date_keeps_only_period():
    rows = [
        {"id": 1, "created_at": "2026-09-05T23:59:00"},
        {"id": 2, "created_at": "2026-09-06T00:01:00"},
        {"id": 3, "created_at": "2026-09-07T12:00:00"},
    ]
    kept = core.filter_rows_by_date(rows, dt.date(2026, 9, 6), dt.date(2026, 9, 6))
    assert [row["id"] for row in kept] == [2]


def test_filter_rows_without_date_field_keeps_everything():
    rows = [{"id": 1, "name": "Марка"}]
    assert core.filter_rows_by_date(rows, dt.date(2026, 9, 6), dt.date(2026, 9, 6)) == rows


# ── цены и магазины ──────────────────────────────────────────────────────────
def test_has_price():
    assert core.has_price({"price": 1200})
    assert core.has_price({"price": "1 200,50"})
    assert not core.has_price({"price": 0})
    assert not core.has_price({"price": None})
    assert not core.has_price({"id": 5})


def test_count_stores_counts_distinct():
    rows = [
        {"store_id": 1},
        {"store_id": 1},
        {"store_id": 2},
        {"store": {"id": 9}},
        {"id": 4},
    ]
    assert core.count_stores(rows) == 3


# ── csv ──────────────────────────────────────────────────────────────────────
def test_write_csv_keeps_server_columns_and_bom(tmp_path):
    rows = [
        {"id": 1, "цена": 100, "оплачен": True, "магазин": {"id": 7, "имя": "Авто"}},
        {"id": 2, "новое_поле": "да"},
    ]
    path = tmp_path / "offers.csv"
    assert core.write_csv(path, rows) == 2

    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")  # BOM для Excel
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    assert lines[0] == "id;цена;оплачен;магазин;новое_поле"
    assert lines[1].startswith("1;100;да;")
    assert "Авто" in lines[1]  # вложенный объект уехал в ячейку целиком
    assert lines[2] == "2;;;;да"


def test_write_csv_on_empty_section(tmp_path):
    path = tmp_path / "пусто.csv"
    assert core.write_csv(path, []) == 0
    assert path.read_text(encoding="utf-8-sig").strip() == "нет данных"


# ── тексты по-русски ─────────────────────────────────────────────────────────
def test_period_phrase():
    day = dt.date(2026, 9, 6)
    assert core.period_phrase("today", day, day) == "За сегодня, 6 сентября"
    assert core.period_phrase("yesterday", day, day) == "За вчера, 6 сентября"
    assert (
        core.period_phrase("week", dt.date(2026, 8, 31), day)
        == "За период с 31 августа по 6 сентября"
    )
    assert core.period_phrase("all", None, None) == "За всё время"


def test_plural():
    assert core.plural(1, "магазин", "магазина", "магазинов") == "магазин"
    assert core.plural(2, "магазин", "магазина", "магазинов") == "магазина"
    assert core.plural(5, "магазин", "магазина", "магазинов") == "магазинов"
    assert core.plural(11, "магазин", "магазина", "магазинов") == "магазинов"
    assert core.plural(21, "магазин", "магазина", "магазинов") == "магазин"


def test_build_summary_matches_expected_shape():
    text = core.build_summary(
        "За сегодня, 6 сентября",
        [
            {"key": "users", "label": "Пользователи", "count": 24, "extra": {}},
            {"key": "rfq", "label": "Заявки", "count": 61, "extra": {}},
            {
                "key": "offers",
                "label": "Отклики",
                "count": 143,
                "extra": {"with_price": 118, "without_price": 25, "stores": 37},
            },
        ],
    )
    assert text.splitlines() == [
        "За сегодня, 6 сентября:",
        "Новых пользователей — 24",
        "Создано заявок — 61",
        "Откликов от магазинов — 143, из них с ценой 118, без цены 25",
        "Ответили 37 магазинов",
    ]


def test_build_summary_lists_other_sections_and_errors():
    text = core.build_summary(
        "За вчера, 5 сентября",
        [{"key": "brands", "label": "Марки машин", "count": 12, "extra": {}}],
        errors=["Раздел «Магазины» не отвечает"],
    )
    assert "Марки машин — 12" in text
    assert "Не удалось выгрузить:" in text
    assert "— Раздел «Магазины» не отвечает" in text
