"""Проверки выгрузки, поиска разделов, хранения логина и журнала.

Сеть не трогаем: вместо клиента админки — простая подделка.
"""

import datetime as dt
import logging
import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from avtozap_export import config, discovery, logging_setup  # noqa: E402
from avtozap_export.api import ApiError, Stopped, _find_token  # noqa: E402
from avtozap_export.exporter import ExportOptions, period_bounds, run_export  # noqa: E402

QUIET = logging.getLogger("тест")
QUIET.addHandler(logging.NullHandler())


class FakeClient:
    """Подделка админки: отдаёт заранее заданные страницы."""

    def __init__(self, pages: dict, broken: tuple = (), stop_on: str | None = None):
        self.pages = pages  # путь -> список страниц (список списков записей)
        self.broken = broken
        self.stop_on = stop_on
        self.seen_params: dict[str, dict] = {}

    def iter_pages(self, path, *, params=None, per_page=100, on_page=None):
        self.seen_params[path] = dict(params or {})
        if path in self.broken:
            raise ApiError("Раздел не найден в админке")
        if path == self.stop_on:
            raise Stopped()
        chunks = self.pages.get(path, [])
        for number, chunk in enumerate(chunks, start=1):
            if on_page:
                on_page(number, len(chunks), number * per_page)
            yield chunk


def section(key, path=None, **extra):
    base = {
        "key": key,
        "path": path or f"/{key}",
        "label": discovery.label_for(path or f"/{key}"),
        "date_params": None,
        "has_price_filter": key == "offers",
    }
    base.update(extra)
    return base


TODAY = dt.date.today().isoformat()


def offers_rows():
    return [
        {"id": 1, "price": 100, "store_id": 1, "created_at": f"{TODAY}T10:00:00"},
        {"id": 2, "price": None, "store_id": 2, "created_at": f"{TODAY}T11:00:00"},
        {"id": 3, "price": 300, "store_id": 1, "created_at": f"{TODAY}T12:00:00"},
    ]


# ── выгрузка ─────────────────────────────────────────────────────────────────
def test_export_writes_one_csv_per_section(tmp_path):
    client = FakeClient(
        {
            "/offers": [offers_rows()],
            "/rfq": [[{"id": 10, "created_at": f"{TODAY}T09:00:00"}]],
        }
    )
    options = ExportOptions(
        sections=[section("offers"), section("rfq")],
        period_kind="today",
        date_from=dt.date.today(),
        date_to=dt.date.today(),
    )
    result = run_export(client, options, out_root=tmp_path, logger=QUIET)

    out_dir = tmp_path / TODAY
    assert (out_dir / "offers.csv").exists()
    assert (out_dir / "rfq.csv").exists()
    assert (out_dir / "сводка.txt").exists()
    assert result.out_dir == out_dir
    assert [item.count for item in result.sections] == [3, 1]
    assert not result.errors


def test_export_summary_counts_prices_and_stores(tmp_path):
    client = FakeClient({"/offers": [offers_rows()]})
    options = ExportOptions(sections=[section("offers")], date_from=None, date_to=None)
    result = run_export(client, options, out_root=tmp_path, logger=QUIET)

    assert "Откликов от магазинов — 3, из них с ценой 2, без цены 1" in result.summary
    assert "Ответили 2 магазина" in result.summary


def test_export_price_filter_without_price(tmp_path):
    client = FakeClient({"/offers": [offers_rows()]})
    options = ExportOptions(sections=[section("offers")], price_filter="without")
    result = run_export(client, options, out_root=tmp_path, logger=QUIET)

    assert result.sections[0].count == 1
    assert "только отклики без цены" in result.summary


def test_export_price_filter_with_price_asks_server(tmp_path):
    client = FakeClient({"/offers": [offers_rows()]})
    options = ExportOptions(sections=[section("offers")], price_filter="with")
    run_export(client, options, out_root=tmp_path, logger=QUIET)

    assert client.seen_params["/offers"]["has_price"] == "true"


def test_export_uses_server_date_params_when_supported(tmp_path):
    client = FakeClient({"/rfq": [[{"id": 1}]]})
    options = ExportOptions(
        sections=[section("rfq", date_params=["created_from", "created_to"])],
        period_kind="custom",
        date_from=dt.date(2026, 9, 1),
        date_to=dt.date(2026, 9, 6),
    )
    run_export(client, options, out_root=tmp_path, logger=QUIET)

    assert client.seen_params["/rfq"] == {
        "created_from": "2026-09-01",
        "created_to": "2026-09-06",
    }


def test_export_filters_by_date_when_server_cannot(tmp_path):
    rows = [
        {"id": 1, "created_at": "2026-09-01T10:00:00"},
        {"id": 2, "created_at": "2026-09-06T10:00:00"},
    ]
    client = FakeClient({"/rfq": [rows]})
    options = ExportOptions(
        sections=[section("rfq")],
        period_kind="custom",
        date_from=dt.date(2026, 9, 6),
        date_to=dt.date(2026, 9, 6),
    )
    result = run_export(client, options, out_root=tmp_path, logger=QUIET)

    assert result.sections[0].count == 1
    assert client.seen_params["/rfq"] == {}


def test_broken_section_does_not_stop_the_rest(tmp_path):
    client = FakeClient({"/rfq": [[{"id": 1}]]}, broken=("/stores",))
    options = ExportOptions(sections=[section("stores"), section("rfq")])
    result = run_export(client, options, out_root=tmp_path, logger=QUIET)

    assert len(result.errors) == 1
    assert "Магазины" in result.errors[0]
    assert (tmp_path / TODAY / "rfq.csv").exists()
    assert not (tmp_path / TODAY / "stores.csv").exists()  # пустышку не пишем
    assert "Не удалось выгрузить:" in result.summary


def test_stop_saves_what_is_already_downloaded(tmp_path):
    client = FakeClient({"/offers": [offers_rows()]}, stop_on="/rfq")
    options = ExportOptions(sections=[section("offers"), section("rfq")])
    result = run_export(client, options, out_root=tmp_path, logger=QUIET)

    assert result.stopped
    assert (tmp_path / TODAY / "offers.csv").exists()
    assert "Выгрузка остановлена вами" in result.summary


def test_progress_says_which_page_is_downloading(tmp_path):
    client = FakeClient({"/offers": [offers_rows(), offers_rows()]})
    messages: list[str] = []
    run_export(
        client,
        ExportOptions(sections=[section("offers")]),
        out_root=tmp_path,
        progress=lambda text, fraction: messages.append(text),
        logger=QUIET,
    )
    assert "Качаю отклики, страница 1 из 2" in messages
    assert "Качаю отклики, страница 2 из 2" in messages


@pytest.mark.parametrize(
    "kind, expected_days",
    [("today", 0), ("yesterday", 1), ("week", 6)],
)
def test_period_bounds(kind, expected_days):
    date_from, date_to = period_bounds(kind)
    assert (dt.date.today() - date_from).days == expected_days
    assert date_to <= dt.date.today()


def test_period_bounds_all_means_no_limits():
    assert period_bounds("all") == (None, None)


# ── поиск разделов ───────────────────────────────────────────────────────────
class ProbeClient:
    """Подделка для проверки перебора адресов."""

    def __init__(self, alive: dict):
        self.alive = alive

    def probe(self, path, params=None):
        if path not in self.alive:
            return 404, {"detail": "Not Found"}
        params = params or {}
        rows = self.alive[path]
        if any(key in params for key in ("created_from", "date_from", "from", "start_date")):
            return 200, {"data": []}  # раздел умеет фильтровать по датам
        if params.get("has_price") and path != "/offers":
            return 404, None
        return 200, {"data": rows}


def test_discover_keeps_only_working_sections():
    client = ProbeClient({"/offers": [{"id": 1}], "/users": [{"id": 2}]})
    found = discovery.discover(client, paths=("/offers", "/users", "/нет-такого"))

    assert [item["key"] for item in found] == ["offers", "users"]
    assert found[0]["label"] == "Отклики"
    assert found[0]["date_params"] == ["created_from", "created_to"]


def test_discover_puts_main_sections_first():
    client = ProbeClient({"/brands": [{"id": 1}], "/offers": [{"id": 2}]})
    found = discovery.discover(client, paths=("/brands", "/offers"))
    assert [item["key"] for item in found] == ["offers", "brands"]


def test_sections_are_remembered_between_runs(tmp_path):
    path = tmp_path / "endpoints.json"
    sections = [section("offers"), section("users")]
    discovery.save_sections(sections, path)

    loaded = discovery.load_sections(path)
    assert [item["key"] for item in loaded] == ["offers", "users"]
    assert discovery.updated_at(path)


def test_load_sections_survives_broken_file(tmp_path):
    path = tmp_path / "endpoints.json"
    path.write_text("не json", encoding="utf-8")
    assert discovery.load_sections(path) == []


def test_label_for_unknown_path_is_readable():
    assert discovery.label_for("/loyalty_cards") == "Loyalty cards"


# ── логин и пароль ───────────────────────────────────────────────────────────
def test_credentials_round_trip(tmp_path, monkeypatch):
    monkeypatch.delenv(config.USERNAME_KEY, raising=False)
    monkeypatch.delenv(config.PASSWORD_KEY, raising=False)
    env = tmp_path / ".env"
    assert config.get_credentials(env) is None

    config.save_credentials("менеджер", "секрет 123", env)
    assert config.get_credentials(env) == ("менеджер", "секрет 123")
    assert "секрет 123" in env.read_text(encoding="utf-8")


def test_save_credentials_keeps_other_lines(tmp_path):
    env = tmp_path / ".env"
    env.write_text("ДРУГОЕ=значение\n", encoding="utf-8")
    config.save_credentials("кто-то", "пароль", env)
    assert "ДРУГОЕ=значение" in env.read_text(encoding="utf-8")


def test_env_values_in_quotes_are_understood(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        f'# комментарий\n{config.USERNAME_KEY}="имя"\n{config.PASSWORD_KEY}=\'пароль\'\n',
        encoding="utf-8",
    )
    assert config.get_credentials(env) == ("имя", "пароль")


# ── безопасность ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "message",
    [
        "вход password=супертайна",
        "cookie: admin_access_token=eyJhbGciOiJIUzI1NiJ9.abcdef",
        "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.QQQQQQQQ",
    ],
)
def test_logs_never_keep_passwords_or_tokens(message):
    record = logging.LogRecord("тест", logging.INFO, __file__, 1, message, None, None)
    assert logging_setup._RedactFilter().filter(record)
    assert "<скрыто>" in record.getMessage()
    assert "супертайна" not in record.getMessage()
    assert "eyJhbGciOiJIUzI1NiJ9" not in record.getMessage()


def test_token_is_found_in_any_wrapper():
    assert _find_token({"access_token": "abc"}) == "abc"
    assert _find_token({"data": {"admin_access_token": "xyz"}}) == "xyz"
    assert _find_token({"результат": [{"token": "qwe"}]}) == "qwe"
    assert _find_token({"ok": True}) is None


def test_stop_event_is_honoured_between_sections(tmp_path):
    stop = threading.Event()

    class StoppingClient(FakeClient):
        def iter_pages(self, path, *, params=None, per_page=100, on_page=None):
            stop.set()
            raise Stopped()

    result = run_export(
        StoppingClient({}),
        ExportOptions(sections=[section("offers"), section("rfq")]),
        out_root=tmp_path,
        logger=QUIET,
    )
    assert result.stopped
    assert len(result.sections) == 1  # до второго раздела дело не дошло
