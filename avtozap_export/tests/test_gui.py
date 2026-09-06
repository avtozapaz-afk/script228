"""Проверки окна. Пропускаются там, где нет экрана или tkinter."""

import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

pytest.importorskip("tkinter", reason="tkinter не установлен")
if not (os.environ.get("DISPLAY") or sys.platform.startswith("win") or sys.platform == "darwin"):
    pytest.skip("нет экрана для окна", allow_module_level=True)

import tkinter as tk  # noqa: E402

from avtozap_export import config, discovery  # noqa: E402
from avtozap_export.exporter import ExportResult, SectionResult  # noqa: E402
from avtozap_export.gui import App, CredentialsDialog, parse_user_date  # noqa: E402

SECTIONS = [
    {"key": "offers", "path": "/offers", "label": "Отклики", "date_params": None, "has_price_filter": True},
    {"key": "rfq", "path": "/rfq", "label": "Заявки", "date_params": None, "has_price_filter": False},
]


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv(config.USERNAME_KEY, "тест")
    monkeypatch.setenv(config.PASSWORD_KEY, "тест")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(config, "ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr(discovery, "load_sections", lambda *a, **k: list(SECTIONS))
    monkeypatch.setattr(discovery, "updated_at", lambda *a, **k: "2026-09-06 10:00")

    try:
        window = App()
    except tk.TclError as error:  # pragma: no cover - экрана всё-таки нет
        pytest.skip(f"окно не открылось: {error}")
    deadline = time.time() + 3
    while time.time() < deadline and not window.section_vars:
        window.update()
        time.sleep(0.02)
    yield window
    window._on_close()


def pump(window, times=5):
    for _ in range(times):
        window.update()


def test_sections_show_up_as_checkboxes(app):
    assert set(app.section_vars) == {"offers", "rfq"}
    assert all(var.get() for var in app.section_vars.values())


def test_select_and_clear_all(app):
    app._set_all(False)
    assert not any(var.get() for var in app.section_vars.values())
    app._set_all(True)
    assert all(var.get() for var in app.section_vars.values())


def test_custom_period_enables_date_fields(app):
    app.period_var.set("custom")
    app._on_period_change()
    assert str(app.date_from_entry.cget("state")) == "normal"
    app.period_var.set("week")
    app._on_period_change()
    assert str(app.date_from_entry.cget("state")) == "disabled"


def test_progress_message_reaches_the_window(app):
    app._set_busy(True)
    app.queue.put(("status", "Качаю отклики, страница 3 из 12", 25.0))
    app._pump()
    pump(app)
    assert app.status_label.cget("text") == "Качаю отклики, страница 3 из 12"
    assert round(float(app.progress.cget("value"))) == 25


def test_stop_button_sets_the_flag(app):
    app._set_busy(True)
    app._on_stop()
    assert app.stop_event.is_set()
    assert "Останавливаю" in app.status_label.cget("text")


def test_result_summary_and_buttons_appear(app):
    result = ExportResult(out_dir=Path("/tmp/выгрузка"))
    result.sections = [SectionResult(key="offers", label="Отклики", count=143)]
    result.summary = "За сегодня, 6 сентября:\nОткликов от магазинов — 143"
    app.queue.put(("done", result, None))
    app._pump()
    pump(app)

    text = app.summary_text.get("1.0", "end")
    assert "Откликов от магазинов — 143" in text
    assert "/tmp/выгрузка" in text
    assert app.open_button.winfo_ismapped()
    assert app.again_button.winfo_ismapped()
    assert not app.busy


def test_export_without_selection_is_refused(app, monkeypatch):
    shown = {}
    monkeypatch.setattr(
        "avtozap_export.gui.messagebox.showinfo",
        lambda title, message, **kwargs: shown.update(title=title),
    )
    app._set_all(False)
    app.start_export()
    assert shown.get("title") == "Ничего не выбрано"
    assert not app.busy


def test_wrong_date_is_reported_in_plain_words(app, monkeypatch):
    shown = {}
    monkeypatch.setattr(
        "avtozap_export.gui.messagebox.showwarning",
        lambda title, message, **kwargs: shown.update(title=title),
    )
    app.period_var.set("custom")
    app.date_from_var.set("вчера")
    app.start_export()
    assert shown.get("title") == "Дата написана неправильно"
    assert not app.busy


def test_backwards_period_is_reported(app, monkeypatch):
    shown = {}
    monkeypatch.setattr(
        "avtozap_export.gui.messagebox.showwarning",
        lambda title, message, **kwargs: shown.update(title=title),
    )
    app.period_var.set("custom")
    app.date_from_var.set("10.09.2026")
    app.date_to_var.set("01.09.2026")
    app.start_export()
    assert shown.get("title") == "Период наоборот"


def test_credentials_dialog_returns_what_was_typed(app):
    dialog = CredentialsDialog(app, app.font_base, username="старый")
    pump(app)
    dialog.username.delete(0, "end")
    dialog.username.insert(0, "новый")
    dialog.password.insert(0, "пароль")
    dialog._save()
    assert dialog.result == ("новый", "пароль")


def test_empty_credentials_are_not_accepted(app, monkeypatch):
    monkeypatch.setattr(
        "avtozap_export.gui.messagebox.showwarning", lambda *args, **kwargs: None
    )
    dialog = CredentialsDialog(app, app.font_base)
    pump(app)
    dialog._save()
    assert dialog.result is None
    dialog.destroy()


@pytest.mark.parametrize(
    "text, expected",
    [("06.09.2026", (2026, 9, 6)), ("2026-09-06", (2026, 9, 6)), ("6.9.26", (2026, 9, 6))],
)
def test_parse_user_date(text, expected):
    parsed = parse_user_date(text)
    assert (parsed.year, parsed.month, parsed.day) == expected


def test_parse_user_date_rejects_nonsense():
    assert parse_user_date("позавчера") is None
    assert parse_user_date("") is None
