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

from avtozap_export import config, discovery, qa  # noqa: E402
from avtozap_export.exporter import ExportResult, SectionResult  # noqa: E402
from avtozap_export import gui  # noqa: E402
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


# ── пароль: показать и вставить ──────────────────────────────────────────────
@pytest.fixture
def dialog(app):
    window = CredentialsDialog(app, app.font_base)
    pump(app)
    yield window
    if window.winfo_exists():
        window.destroy()


def test_password_is_hidden_until_the_checkbox_is_ticked(dialog):
    assert dialog.password.cget("show") == "•"

    dialog.show_password.set(True)
    dialog._toggle_password()
    assert dialog.password.cget("show") == ""

    dialog.show_password.set(False)
    dialog._toggle_password()
    assert dialog.password.cget("show") == "•"


def test_paste_puts_clipboard_text_into_the_field(app, dialog):
    app.clipboard_clear()
    app.clipboard_append("пароль-из-буфера")
    dialog.clipboard.paste(dialog.password)
    assert dialog.password.get() == "пароль-из-буфера"


def test_paste_strips_line_breaks(app, dialog):
    app.clipboard_clear()
    app.clipboard_append("пароль\n")
    dialog.clipboard.paste(dialog.password)
    assert dialog.password.get() == "пароль"


def test_paste_replaces_what_was_selected(app, dialog):
    dialog.password.insert(0, "старое")
    dialog.password.select_range(0, "end")
    app.clipboard_clear()
    app.clipboard_append("новое")
    dialog.clipboard.paste(dialog.password)
    assert dialog.password.get() == "новое"


def test_paste_into_empty_clipboard_does_nothing(app, dialog):
    app.clipboard_clear()
    dialog.password.insert(0, "уже набрано")
    dialog.clipboard.paste(dialog.password)
    assert dialog.password.get() == "уже набрано"


class FakeKey:
    """Нажатие клавиши: код от неё не зависит от раскладки, а буква зависит."""

    def __init__(self, widget, keycode, keysym):
        self.widget = widget
        self.keycode = keycode
        self.keysym = keysym


def test_ctrl_v_works_in_russian_layout(app, dialog):
    """При русской раскладке Ctrl+V приходит как «Ctrl+м» — вставка обязана сработать."""
    app.clipboard_clear()
    app.clipboard_append("вставлено")
    codes = next(iter(gui.clipboard_keycodes()["paste"]))
    handled = dialog.clipboard._on_control_key(FakeKey(dialog.password, codes, "Cyrillic_em"))
    assert handled == "break"  # системную вставку глушим, чтобы не было двойной
    assert dialog.password.get() == "вставлено"


def test_ctrl_v_works_in_latin_layout(app, dialog):
    app.clipboard_clear()
    app.clipboard_append("вставлено")
    dialog.clipboard._on_control_key(FakeKey(dialog.password, 0, "v"))
    assert dialog.password.get() == "вставлено"


def test_ctrl_c_copies_selection(app, dialog):
    app.clipboard_clear()
    dialog.username.insert(0, "менеджер")
    dialog.username.select_range(0, "end")
    dialog.clipboard._on_control_key(FakeKey(dialog.username, 0, "c"))
    assert app.clipboard_get() == "менеджер"


def test_ctrl_a_selects_everything(dialog):
    dialog.username.insert(0, "менеджер")
    dialog.clipboard._on_control_key(FakeKey(dialog.username, 0, "a"))
    assert dialog.username.selection_present()
    assert dialog.username.selection_get() == "менеджер"


def test_other_control_keys_are_left_alone(dialog):
    assert dialog.clipboard._on_control_key(FakeKey(dialog.username, 0, "s")) is None


def test_date_fields_also_accept_paste(app):
    app.clipboard_clear()
    app.clipboard_append("01.09.2026")
    app.period_var.set("custom")
    app._on_period_change()
    app.date_from_entry.delete(0, "end")
    app.clipboard.paste(app.date_from_entry)
    assert app.date_from_var.get() == "01.09.2026"


# ── поле вопроса ─────────────────────────────────────────────────────────────
class FakeKeyPress:
    def __init__(self, state=0):
        self.state = state


def test_enter_sends_the_question_and_shift_enter_does_not(app, monkeypatch):
    sent = []
    monkeypatch.setattr(app, "start_question", lambda: sent.append(True))

    assert app._on_question_enter(FakeKeyPress(state=0)) == "break"
    assert sent == [True]

    assert app._on_question_enter(FakeKeyPress(state=0x0001)) is None  # Shift+Enter
    assert sent == [True]


def test_empty_question_is_ignored(app, monkeypatch):
    started = []
    monkeypatch.setattr(app, "_start_worker", lambda *a, **k: started.append(True))
    app.question.delete("1.0", "end")
    app.start_question()
    assert started == []


def test_question_without_sections_explains_what_to_do(app, monkeypatch):
    shown = {}
    monkeypatch.setattr(
        "avtozap_export.gui.messagebox.showinfo",
        lambda title, message, **kwargs: shown.update(title=title),
    )
    monkeypatch.setattr(app, "_start_worker", lambda *a, **k: shown.update(started=True))
    app.sections = []
    app.question.insert("1.0", "сколько заявок за неделю")
    app.start_question()

    assert shown.get("title") == "Нужен список разделов"
    assert "started" not in shown


def test_asking_shows_the_question_and_clears_the_field(app, monkeypatch):
    monkeypatch.setattr("avtozap_export.gui.ai.get_key", lambda: "sk-ключ")
    monkeypatch.setattr(app, "_start_worker", lambda *a, **k: None)
    app.question.insert("1.0", "сколько новых пользователей за вчера")
    app.start_question()
    pump(app)

    assert "сколько новых пользователей за вчера" in app.summary_text.get("1.0", "end")
    assert app.question.get("1.0", "end").strip() == ""
    assert app.pending_question == "сколько новых пользователей за вчера"


def test_answers_pile_up_as_history(app):
    app._append_summary("Вы спросили: раз")
    app._append_summary("Ответ один")
    app._append_summary("Ответ два")
    text = app.summary_text.get("1.0", "end")
    assert "Ответ один" in text and "Ответ два" in text
    assert text.index("Ответ один") < text.index("Ответ два")


def test_answer_with_a_file_offers_to_open_the_folder(app, tmp_path):
    table = tmp_path / "детали.csv"
    table.write_text("id\n1\n", encoding="utf-8-sig")
    answer = qa.Answer(text="Всего деталей: 3.", table_path=table, table_rows=3)

    app._set_busy(True, running=True)
    app.queue.put(("answer", answer, None))
    app._pump()
    pump(app)

    assert "Всего деталей: 3." in app.summary_text.get("1.0", "end")
    assert app.open_button.winfo_ismapped()
    assert app.last_folder == tmp_path
    assert not app.busy


def test_answer_without_a_file_keeps_the_window_clean(app):
    app._set_busy(True, running=True)
    app.queue.put(("answer", qa.Answer(text="Вчера пришло 24 новых пользователя."), None))
    app._pump()
    pump(app)

    assert "24" in app.summary_text.get("1.0", "end")
    assert not app.open_button.winfo_ismapped()


def test_failed_question_is_reported_in_the_same_area(app):
    app._set_busy(True, running=True)
    app.queue.put(("answer_error", "Ключ OpenAI не подошёл — проверьте его", False))
    app._pump()
    pump(app)

    assert "Ключ OpenAI не подошёл" in app.summary_text.get("1.0", "end")
    assert not app.busy


def test_stopped_question_says_so(app):
    app._set_busy(True, running=True)
    app.queue.put(("stopped_question", None, None))
    app._pump()
    pump(app)
    assert "остановлен" in app.summary_text.get("1.0", "end").lower()


def test_key_dialog_hides_the_key_until_asked(app):
    dialog = gui.KeyDialog(app, app.font_base)
    pump(app)
    assert dialog.key.cget("show") == "•"

    dialog.show_key.set(True)
    dialog._toggle()
    assert dialog.key.cget("show") == ""

    dialog.key.insert(0, "sk-ключ")
    dialog._save()
    assert dialog.result == "sk-ключ"


def test_key_dialog_accepts_paste(app):
    dialog = gui.KeyDialog(app, app.font_base)
    pump(app)
    app.clipboard_clear()
    app.clipboard_append("sk-из-буфера\n")
    dialog.clipboard.paste(dialog.key)
    assert dialog.key.get() == "sk-из-буфера"
    dialog.destroy()


def test_question_field_accepts_paste(app):
    app.clipboard_clear()
    app.clipboard_append("сколько откликов у магазина «Автомир»")
    app.question.delete("1.0", "end")
    app.clipboard.paste(app.question)
    assert "Автомир" in app.question.get("1.0", "end")
