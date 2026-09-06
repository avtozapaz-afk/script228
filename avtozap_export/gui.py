"""Окно программы: галочки разделов, фильтры, кнопка «Выгрузить» и сводка.

Вся работа с сетью идёт в отдельном потоке, поэтому окно не зависает,
а кнопка «Стоп» срабатывает сразу.
"""

from __future__ import annotations

import datetime as dt
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import messagebox, ttk

from . import ai, config, discovery, qa
from .ai import AiClient, AiError, NoKey
from .api import AdminClient, ApiError, AuthError, NetworkError, Stopped
from .exporter import ExportOptions, ExportResult, period_bounds, run_export
from .logging_setup import setup as setup_logging

WINDOW_TITLE = "Выгрузка данных из админки АвтоЗап"

# Чем закрыт пароль в поле ввода.
HIDDEN_CHAR = "•"

PERIODS = (
    ("today", "За сегодня"),
    ("yesterday", "За вчера"),
    ("week", "За 7 дней"),
    ("custom", "Свой период"),
)

PRICE_CHOICES = (
    ("all", "Все отклики"),
    ("with", "Только с ценой"),
    ("without", "Только без цены"),
)

HELLO_TEXT = (
    "Выберите слева разделы, сверху — период,\n"
    "и нажмите большую кнопку «Выгрузить».\n\n"
    "Программа только читает данные из админки.\n"
    "Ничего не меняет и не удаляет."
)


def pick_font_family() -> str:
    """Взять шрифт, который точно есть в этой системе."""
    available = set(tkfont.families())
    for name in ("Segoe UI", "Verdana", "Tahoma", "DejaVu Sans", "Helvetica", "Arial"):
        if name in available:
            return name
    return "TkDefaultFont"


def open_folder(path: Path) -> None:
    """Открыть папку в проводнике."""
    if sys.platform.startswith("win"):
        os.startfile(str(path))  # noqa: S606 - штатный способ для Windows
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def parse_user_date(text: str) -> dt.date | None:
    """Дата, как её напишет человек: 01.09.2026 или 2026-09-01."""
    text = text.strip()
    if not text:
        return None
    for pattern in ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return dt.datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


def clipboard_keycodes() -> dict[str, set[int]]:
    """Коды клавиш C, V, X, A — они не зависят от раскладки клавиатуры.

    При русской раскладке Ctrl+V приходит в программу как «Ctrl+м», поэтому
    по букве его не поймать: смотрим именно на код клавиши.
    """
    if sys.platform.startswith("win"):
        return {"paste": {86}, "copy": {67}, "cut": {88}, "all": {65}}
    return {"paste": {55}, "copy": {54}, "cut": {53}, "all": {38}}


def clean_pasted(text: str) -> str:
    """Убрать из вставленного текста переносы строк и табуляции."""
    return "".join(char for char in text if char not in "\r\n\t")


class ClipboardHelper:
    """Ctrl+C / Ctrl+V / Ctrl+X и меню по правой кнопке — в любой раскладке."""

    def __init__(self, widget: tk.Misc) -> None:
        self.widget = widget
        self.codes = clipboard_keycodes()
        self.menu = tk.Menu(widget, tearoff=0)
        self.menu.add_command(label="Вставить", command=lambda: self.paste(self.target))
        self.menu.add_command(label="Копировать", command=lambda: self.copy(self.target))
        self.menu.add_command(label="Вырезать", command=lambda: self.cut(self.target))
        self.target: tk.Entry | None = None

    def attach(self, entry) -> None:
        """Включить горячие клавиши и меню для поля ввода."""
        entry.bind("<Control-KeyPress>", self._on_control_key)
        for button in ("<Button-3>", "<Button-2>"):  # правая кнопка в разных системах
            entry.bind(button, self._on_right_click)
        entry.bind("<Shift-Insert>", lambda event: self.paste(event.widget) or "break")
        entry.bind("<Control-Insert>", lambda event: self.copy(event.widget) or "break")

    def _on_control_key(self, event):
        code = getattr(event, "keycode", 0)
        key = (getattr(event, "keysym", "") or "").lower()
        if code in self.codes["paste"] or key == "v":
            self.paste(event.widget)
        elif code in self.codes["copy"] or key == "c":
            self.copy(event.widget)
        elif code in self.codes["cut"] or key == "x":
            self.cut(event.widget)
        elif code in self.codes["all"] or key == "a":
            if isinstance(event.widget, tk.Text):
                event.widget.tag_add("sel", "1.0", "end-1c")
            else:
                event.widget.select_range(0, "end")
                event.widget.icursor("end")
        else:
            return None
        return "break"  # не даём системе вставить второй раз

    def _on_right_click(self, event):
        self.target = event.widget
        event.widget.focus_set()
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()
        return "break"

    @staticmethod
    def _has_selection(widget) -> bool:
        if isinstance(widget, tk.Text):
            return bool(widget.tag_ranges("sel"))
        return bool(widget.selection_present())

    def paste(self, widget) -> None:
        if widget is None:
            return
        try:
            text = clean_pasted(widget.clipboard_get())
        except tk.TclError:
            return  # в буфере обмена пусто или лежит не текст
        if self._has_selection(widget):
            widget.delete("sel.first", "sel.last")
        widget.insert("insert", text)

    def copy(self, widget) -> None:
        if widget is None or not self._has_selection(widget):
            return
        text = (
            widget.get("sel.first", "sel.last")
            if isinstance(widget, tk.Text)
            else widget.selection_get()
        )
        widget.clipboard_clear()
        widget.clipboard_append(text)

    def cut(self, widget) -> None:
        if widget is None or not self._has_selection(widget):
            return
        self.copy(widget)
        widget.delete("sel.first", "sel.last")


class CredentialsDialog(tk.Toplevel):
    """Окошко «введите логин и пароль от админки»."""

    def __init__(self, master, font, username: str = "") -> None:
        super().__init__(master)
        self.title("Вход в админку АвтоЗап")
        self.resizable(False, False)
        self.result: tuple[str, str] | None = None
        self.transient(master)
        self.grab_set()

        frame = ttk.Frame(self, padding=24)
        frame.pack(fill="both", expand=True)

        ttk.Label(
            frame,
            text="Введите логин и пароль от админки.\nОни сохранятся на этом компьютере,\nбольше спрашивать не будем.",
            font=font,
            justify="left",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 16))

        ttk.Label(frame, text="Логин:", font=font).grid(row=1, column=0, sticky="w", pady=6)
        self.username = ttk.Entry(frame, font=font, width=28)
        self.username.grid(row=1, column=1, sticky="ew", pady=6, padx=(12, 0))
        self.username.insert(0, username)

        ttk.Label(frame, text="Пароль:", font=font).grid(row=2, column=0, sticky="w", pady=6)
        self.password = ttk.Entry(frame, font=font, width=28, show=HIDDEN_CHAR)
        self.password.grid(row=2, column=1, sticky="ew", pady=6, padx=(12, 0))

        self.show_password = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            frame,
            text="Показать пароль",
            variable=self.show_password,
            command=self._toggle_password,
        ).grid(row=3, column=1, sticky="w", padx=(12, 0))

        ttk.Label(
            frame,
            text="Вставить: Ctrl+V или правой кнопкой мыши → «Вставить»",
            foreground="#555555",
        ).grid(row=4, column=1, sticky="w", padx=(12, 0), pady=(6, 0))

        self.clipboard = ClipboardHelper(self)
        self.clipboard.attach(self.username)
        self.clipboard.attach(self.password)

        buttons = ttk.Frame(frame)
        buttons.grid(row=5, column=0, columnspan=2, sticky="e", pady=(20, 0))
        ttk.Button(buttons, text="Отмена", command=self._cancel).pack(side="right", padx=(8, 0))
        ttk.Button(buttons, text="Сохранить", command=self._save).pack(side="right")

        self.bind("<Return>", lambda _event: self._save())
        self.bind("<Escape>", lambda _event: self._cancel())
        (self.username if not username else self.password).focus_set()
        self.update_idletasks()
        self._center(master)

    def _toggle_password(self) -> None:
        """Показать или снова спрятать пароль звёздочками."""
        self.password.configure(show="" if self.show_password.get() else HIDDEN_CHAR)

    def _center(self, master) -> None:
        try:
            x = master.winfo_rootx() + (master.winfo_width() - self.winfo_width()) // 2
            y = master.winfo_rooty() + (master.winfo_height() - self.winfo_height()) // 3
            self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        except tk.TclError:  # pragma: no cover
            pass

    def _save(self) -> None:
        username = self.username.get().strip()
        password = self.password.get()
        if not username or not password:
            messagebox.showwarning("Пусто", "Заполните и логин, и пароль.", parent=self)
            return
        self.result = (username, password)
        self.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.destroy()



class KeyDialog(tk.Toplevel):
    """Окошко «вставьте ключ OpenAI»."""

    def __init__(self, master, font) -> None:
        super().__init__(master)
        self.title("Ключ для разбора вопросов")
        self.resizable(False, False)
        self.result: str | None = None
        self.transient(master)
        self.grab_set()

        frame = ttk.Frame(self, padding=24)
        frame.pack(fill="both", expand=True)

        ttk.Label(
            frame,
            text=(
                "Чтобы отвечать на вопросы обычными словами,\n"
                "программе нужен ключ OpenAI.\n"
                "Вставьте его сюда — он сохранится на этом компьютере."
            ),
            font=font,
            justify="left",
        ).grid(row=0, column=0, sticky="w", pady=(0, 16))

        self.key = ttk.Entry(frame, font=font, width=46, show=HIDDEN_CHAR)
        self.key.grid(row=1, column=0, sticky="ew")

        self.show_key = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            frame, text="Показать ключ", variable=self.show_key, command=self._toggle
        ).grid(row=2, column=0, sticky="w", pady=(8, 0))

        ttk.Label(
            frame,
            text="Вставить: Ctrl+V или правой кнопкой мыши → «Вставить»",
            foreground="#555555",
        ).grid(row=3, column=0, sticky="w", pady=(6, 0))

        self.clipboard = ClipboardHelper(self)
        self.clipboard.attach(self.key)

        buttons = ttk.Frame(frame)
        buttons.grid(row=4, column=0, sticky="e", pady=(20, 0))
        ttk.Button(buttons, text="Отмена", command=self._cancel).pack(side="right", padx=(8, 0))
        ttk.Button(buttons, text="Сохранить", command=self._save).pack(side="right")

        self.bind("<Return>", lambda _event: self._save())
        self.bind("<Escape>", lambda _event: self._cancel())
        self.key.focus_set()
        self.update_idletasks()

    def _toggle(self) -> None:
        self.key.configure(show="" if self.show_key.get() else HIDDEN_CHAR)

    def _save(self) -> None:
        value = self.key.get().strip()
        if not value:
            messagebox.showwarning("Пусто", "Вставьте ключ.", parent=self)
            return
        self.result = value
        self.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.destroy()


class App(tk.Tk):
    """Главное окно."""

    def __init__(self) -> None:
        super().__init__()
        self.log = setup_logging()
        config.ensure_dirs()

        self.title(WINDOW_TITLE)
        self.minsize(1000, 640)
        self.geometry("1180x760")

        family = pick_font_family()
        self.font_base = (family, 12)
        self.font_bold = (family, 12, "bold")
        self.font_head = (family, 15, "bold")
        self.font_big = (family, 18, "bold")
        self.font_mono = ("Consolas" if family == "Segoe UI" else family, 13)

        self._setup_styles()

        self.queue: queue.Queue = queue.Queue()
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.sections: list[dict] = []
        self.section_vars: dict[str, tk.BooleanVar] = {}
        self.last_result: ExportResult | None = None
        self.last_folder: Path | None = None
        self.qa_store: qa.DataStore | None = None
        self.pending_question = ""
        self.busy = False

        self.period_var = tk.StringVar(value="today")
        self.price_var = tk.StringVar(value="all")
        self.date_from_var = tk.StringVar(value=dt.date.today().strftime("%d.%m.%Y"))
        self.date_to_var = tk.StringVar(value=dt.date.today().strftime("%d.%m.%Y"))

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._pump)
        self.after(200, self._first_run)

    # --------------------------------------------------------------- оформление

    def _setup_styles(self) -> None:
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        elif "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure(".", font=self.font_base)
        style.configure("TButton", font=self.font_base, padding=(14, 8))
        style.configure("Big.TButton", font=self.font_big, padding=(20, 18))
        style.configure("Head.TLabel", font=self.font_head)
        style.configure("TLabelframe.Label", font=self.font_bold)
        style.configure("TCheckbutton", font=self.font_base)
        style.configure("TRadiobutton", font=self.font_base)
        style.configure("Status.TLabel", font=self.font_bold)
        style.configure("Stop.TButton", font=self.font_bold, padding=(28, 14))
        style.configure("Big.Horizontal.TProgressbar", thickness=28)

    # ------------------------------------------------------------------- сборка

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=0, minsize=340)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(1, weight=1)

        self._build_top()
        self._build_left()
        self._build_right()
        self._build_bottom()

    def _build_top(self) -> None:
        top = ttk.Frame(self, padding=(16, 14, 16, 6))
        top.grid(row=0, column=0, columnspan=2, sticky="ew")

        top.columnconfigure(0, weight=1)

        period = ttk.Labelframe(top, text="За какой период", padding=12)
        period.grid(row=0, column=0, sticky="w")
        for value, text in PERIODS:
            ttk.Radiobutton(
                period,
                text=text,
                value=value,
                variable=self.period_var,
                command=self._on_period_change,
            ).pack(side="left", padx=(0, 14))

        self.custom_frame = ttk.Frame(period)
        self.custom_frame.pack(side="left")
        ttk.Label(self.custom_frame, text="с").pack(side="left", padx=(6, 4))
        self.date_from_entry = ttk.Entry(
            self.custom_frame, textvariable=self.date_from_var, width=12, font=self.font_base
        )
        self.date_from_entry.pack(side="left")
        ttk.Label(self.custom_frame, text="по").pack(side="left", padx=(8, 4))
        self.date_to_entry = ttk.Entry(
            self.custom_frame, textvariable=self.date_to_var, width=12, font=self.font_base
        )
        self.date_to_entry.pack(side="left")

        self.clipboard = ClipboardHelper(self)
        self.clipboard.attach(self.date_from_entry)
        self.clipboard.attach(self.date_to_entry)

        price = ttk.Labelframe(top, text="Отклики: какие брать", padding=12)
        price.grid(row=1, column=0, sticky="w", pady=(10, 0))
        for value, text in PRICE_CHOICES:
            ttk.Radiobutton(price, text=text, value=value, variable=self.price_var).pack(
                side="left", padx=(0, 14)
            )

        self._on_period_change()

    def _build_left(self) -> None:
        left = ttk.Frame(self, padding=(16, 6, 8, 6))
        left.grid(row=1, column=0, sticky="nsew")
        left.rowconfigure(2, weight=1)
        left.columnconfigure(0, weight=1)

        ttk.Label(left, text="Что выгружаем", style="Head.TLabel").grid(row=0, column=0, sticky="w")

        buttons = ttk.Frame(left)
        buttons.grid(row=1, column=0, sticky="ew", pady=(8, 8))
        ttk.Button(buttons, text="Выбрать все", command=lambda: self._set_all(True)).pack(
            side="left"
        )
        ttk.Button(buttons, text="Снять все", command=lambda: self._set_all(False)).pack(
            side="left", padx=(8, 0)
        )

        box = ttk.Frame(left, relief="solid", borderwidth=1)
        box.grid(row=2, column=0, sticky="nsew")
        box.rowconfigure(0, weight=1)
        box.columnconfigure(0, weight=1)

        background = ttk.Style(self).lookup("TFrame", "background") or "white"
        self.canvas = tk.Canvas(box, highlightthickness=0, background=background)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(box, orient="vertical", command=self.canvas.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(yscrollcommand=scrollbar.set)

        self.sections_frame = ttk.Frame(self.canvas, padding=10)
        self.sections_window = self.canvas.create_window(
            (0, 0), window=self.sections_frame, anchor="nw"
        )
        self.sections_frame.bind(
            "<Configure>", lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )
        self.canvas.bind(
            "<Configure>", lambda e: self.canvas.itemconfigure(self.sections_window, width=e.width)
        )
        # Колесо мыши крутит именно этот список — и только пока курсор над ним.
        for widget in (self.canvas, self.sections_frame):
            widget.bind("<Enter>", lambda _e: self._bind_wheel(True))
            widget.bind("<Leave>", lambda _e: self._bind_wheel(False))

        self.refresh_button = ttk.Button(
            left, text="Обновить список разделов", command=self.start_discovery
        )
        self.refresh_button.grid(row=3, column=0, sticky="ew", pady=(10, 0))

        self.sections_note = ttk.Label(left, text="", foreground="#555555", wraplength=300)
        self.sections_note.grid(row=4, column=0, sticky="w", pady=(6, 0))

    def _build_right(self) -> None:
        right = ttk.Frame(self, padding=(8, 6, 16, 6))
        right.grid(row=1, column=1, sticky="nsew")
        right.rowconfigure(1, weight=1)
        right.columnconfigure(0, weight=1)

        ttk.Label(right, text="Результат", style="Head.TLabel").grid(row=0, column=0, sticky="w")

        text_box = ttk.Frame(right, relief="solid", borderwidth=1)
        text_box.grid(row=1, column=0, sticky="nsew", pady=(8, 8))
        text_box.rowconfigure(0, weight=1)
        text_box.columnconfigure(0, weight=1)

        self.summary_text = tk.Text(
            text_box,
            wrap="word",
            font=self.font_mono,
            relief="flat",
            padx=14,
            pady=14,
            background="white",
            state="disabled",
        )
        self.summary_text.grid(row=0, column=0, sticky="nsew")
        summary_scroll = ttk.Scrollbar(text_box, orient="vertical", command=self.summary_text.yview)
        summary_scroll.grid(row=0, column=1, sticky="ns")
        self.summary_text.configure(yscrollcommand=summary_scroll.set)
        self._set_summary(HELLO_TEXT)

        self.result_buttons = ttk.Frame(right)
        self.result_buttons.grid(row=2, column=0, sticky="ew")
        self.open_button = ttk.Button(
            self.result_buttons, text="Открыть папку с файлами", command=self._open_result_folder
        )
        self.again_button = ttk.Button(
            self.result_buttons, text="Выгрузить ещё раз", command=self.start_export
        )
        self._show_result_buttons(False)

        self._build_question(right)

    def _build_question(self, right) -> None:
        """Поле «спросите обычными словами» под областью результата."""
        ask = ttk.Labelframe(right, text="Спросите обычными словами", padding=10)
        ask.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        ask.columnconfigure(0, weight=1)

        self.question = tk.Text(ask, height=2, wrap="word", font=self.font_base, relief="solid",
                                borderwidth=1, padx=8, pady=6, highlightthickness=0)
        self.question.grid(row=0, column=0, sticky="ew")
        self.clipboard.attach(self.question)

        self.ask_button = ttk.Button(ask, text="Спросить", command=self.start_question)
        self.ask_button.grid(row=0, column=1, sticky="ns", padx=(10, 0))

        ttk.Label(
            ask,
            text="Например: сколько откликов дал магазин «Автомир» за неделю. "
                 "Enter — задать вопрос, Shift+Enter — новая строка.",
            foreground="#555555",
            wraplength=640,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))

        self.question.bind("<Return>", self._on_question_enter)
        self.question.bind("<KP_Enter>", self._on_question_enter)

    def _build_bottom(self) -> None:
        bottom = ttk.Frame(self, padding=(16, 6, 16, 16))
        bottom.grid(row=2, column=0, columnspan=2, sticky="ew")
        bottom.columnconfigure(0, weight=1)

        self.action_frame = ttk.Frame(bottom)
        self.action_frame.grid(row=0, column=0, sticky="ew")
        self.action_frame.columnconfigure(0, weight=1)

        self.export_button = ttk.Button(
            self.action_frame, text="Выгрузить", style="Big.TButton", command=self.start_export
        )
        self.export_button.grid(row=0, column=0, sticky="ew")

        self.progress_frame = ttk.Frame(bottom)
        self.progress_frame.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(
            self.progress_frame,
            mode="determinate",
            maximum=100,
            style="Big.Horizontal.TProgressbar",
        )
        self.progress.grid(row=0, column=0, sticky="ew", padx=(0, 12))
        self.stop_button = ttk.Button(
            self.progress_frame, text="Стоп", style="Stop.TButton", command=self._on_stop
        )
        self.stop_button.grid(row=0, column=1)
        self.status_label = ttk.Label(self.progress_frame, text="", style="Status.TLabel")
        self.status_label.grid(row=1, column=0, columnspan=2, sticky="w", pady=(10, 0))

    # -------------------------------------------------------------- мелочи окна

    def _bind_wheel(self, active: bool) -> None:
        events = ("<MouseWheel>", "<Button-4>", "<Button-5>")
        for event in events:
            if active:
                self.bind_all(event, self._on_wheel)
            else:
                self.unbind_all(event)

    def _on_wheel(self, event) -> None:
        if getattr(event, "num", None) == 4:
            self.canvas.yview_scroll(-1, "units")
        elif getattr(event, "num", None) == 5:
            self.canvas.yview_scroll(1, "units")
        elif getattr(event, "delta", 0):
            self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    def _on_period_change(self) -> None:
        state = "normal" if self.period_var.get() == "custom" else "disabled"
        self.date_from_entry.configure(state=state)
        self.date_to_entry.configure(state=state)

    def _set_summary(self, text: str) -> None:
        self.summary_text.configure(state="normal")
        self.summary_text.delete("1.0", "end")
        self.summary_text.insert("1.0", text)
        self.summary_text.configure(state="disabled")

    def _append_summary(self, text: str) -> None:
        """Дописать в область результата, не стирая прошлое."""
        self.summary_text.configure(state="normal")
        if self.summary_text.get("1.0", "end").strip():
            self.summary_text.insert("end", "\n\n" + "─" * 40 + "\n\n")
        self.summary_text.insert("end", text)
        self.summary_text.configure(state="disabled")
        # Прокручиваем в самый низ: свежий ответ всегда должен быть виден.
        # Повтор с задержкой — на случай, если высота области ещё меняется
        # (например, только что появились кнопки под ней).
        self.summary_text.yview_moveto(1.0)
        self.summary_text.after_idle(lambda: self.summary_text.yview_moveto(1.0))
        self.summary_text.after(60, lambda: self.summary_text.yview_moveto(1.0))

    def _show_result_buttons(self, visible: bool) -> None:
        if visible:
            self.open_button.pack(side="left")
            self.again_button.pack(side="left", padx=(10, 0))
        else:
            self.open_button.pack_forget()
            self.again_button.pack_forget()

    def _set_busy(self, busy: bool, *, running: bool = False) -> None:
        """Переключить окно между «жду команды» и «работаю».

        ``running`` — когда шагов не сосчитать (поиск ответа): полоса просто бежит.
        """
        self.busy = busy
        self.progress.stop()
        if busy:
            self.action_frame.grid_remove()
            self.progress_frame.grid(row=0, column=0, sticky="ew")
            self.progress.configure(mode="indeterminate" if running else "determinate", value=0)
            if running:
                self.progress.start(15)
            self.refresh_button.state(["disabled"])
            self.ask_button.state(["disabled"])
        else:
            self.progress.configure(mode="determinate", value=0)
            self.progress_frame.grid_remove()
            self.action_frame.grid(row=0, column=0, sticky="ew")
            self.refresh_button.state(["!disabled"])
            self.ask_button.state(["!disabled"])

    def _set_all(self, value: bool) -> None:
        for var in self.section_vars.values():
            var.set(value)

    def _open_result_folder(self) -> None:
        folder = self.last_folder or (self.last_result.out_dir if self.last_result else config.DATA_DIR)
        try:
            open_folder(folder)
        except Exception:  # разные системы — разные проводники
            messagebox.showinfo("Папка с файлами", f"Файлы лежат здесь:\n{folder}")

    # -------------------------------------------------------------- разделы

    def refresh_sections_list(self, sections: list[dict] | None = None) -> None:
        """Перерисовать список разделов с галочками."""
        if sections is not None:
            self.sections = sections
        previous = {key: var.get() for key, var in self.section_vars.items()}
        for widget in self.sections_frame.winfo_children():
            widget.destroy()
        self.section_vars.clear()

        if not self.sections:
            ttk.Label(
                self.sections_frame,
                text="Разделы пока не найдены.\nНажмите «Обновить список разделов».",
                wraplength=280,
                justify="left",
            ).pack(anchor="w")
            self.sections_note.configure(text="")
            return

        for section in self.sections:
            key = section["key"]
            var = tk.BooleanVar(value=previous.get(key, True))
            self.section_vars[key] = var
            ttk.Checkbutton(self.sections_frame, text=section["label"], variable=var).pack(
                anchor="w", pady=3
            )

        when = discovery.updated_at()
        self.sections_note.configure(
            text=f"Найдено разделов: {len(self.sections)}." + (f" Обновлено {when}." if when else "")
        )

    def _selected_sections(self) -> list[dict]:
        return [
            section
            for section in self.sections
            if self.section_vars.get(section["key"]) and self.section_vars[section["key"]].get()
        ]

    # -------------------------------------------------------------- запуск

    def _first_run(self) -> None:
        """Первый запуск: спросить логин и найти разделы."""
        if config.get_credentials() is None and not self._ask_credentials():
            return
        self.refresh_sections_list(discovery.load_sections())
        if not self.sections:
            if messagebox.askyesno(
                "Первый запуск",
                "Нужно один раз найти разделы админки. Это займёт минуту.\nНачать?",
                parent=self,
            ):
                self.start_discovery()

    def _ask_credentials(self) -> bool:
        """Показать окошко входа и сохранить логин с паролем."""
        saved = config.get_credentials()
        dialog = CredentialsDialog(self, self.font_base, username=saved[0] if saved else "")
        self.wait_window(dialog)
        if dialog.result is None:
            return False
        config.save_credentials(*dialog.result)
        self.log.info("Логин и пароль сохранены в .env")
        return True

    def _make_client(self) -> AdminClient | None:
        credentials = config.get_credentials()
        if credentials is None:
            if not self._ask_credentials():
                return None
            credentials = config.get_credentials()
        if credentials is None:  # pragma: no cover - защита
            return None
        self.stop_event = threading.Event()
        return AdminClient(*credentials, logger=self.log, stop_event=self.stop_event)

    def _start_worker(self, target, status: str, *, running: bool = False) -> None:
        if self.busy:
            return
        client = self._make_client()
        if client is None:
            return
        self._set_busy(True, running=running)
        self.status_label.configure(text=status)
        self.worker = threading.Thread(target=target, args=(client,), daemon=True)
        self.worker.start()

    def start_discovery(self) -> None:
        self._show_result_buttons(False)
        self._set_summary("Ищу разделы админки…")
        self._start_worker(self._worker_discovery, "Проверяю адреса разделов…")

    def start_export(self) -> None:
        sections = self._selected_sections()
        if not sections:
            messagebox.showinfo(
                "Ничего не выбрано", "Отметьте галочками хотя бы один раздел слева.", parent=self
            )
            return

        kind = self.period_var.get()
        if kind == "custom":
            date_from = parse_user_date(self.date_from_var.get())
            date_to = parse_user_date(self.date_to_var.get())
            if date_from is None or date_to is None:
                messagebox.showwarning(
                    "Дата написана неправильно",
                    "Напишите даты так: 01.09.2026",
                    parent=self,
                )
                return
            if date_from > date_to:
                messagebox.showwarning(
                    "Период наоборот",
                    "Начало периода позже его конца. Поменяйте даты местами.",
                    parent=self,
                )
                return
        else:
            date_from, date_to = period_bounds(kind)

        self.export_options = ExportOptions(
            sections=sections,
            period_kind=kind,
            date_from=date_from,
            date_to=date_to,
            price_filter=self.price_var.get(),
        )
        self._show_result_buttons(False)
        self._set_summary("Выгружаю…")
        self._start_worker(self._worker_export, "Захожу в админку…")

    # ------------------------------------------------------------- вопросы

    def _on_question_enter(self, event):
        """Enter отправляет вопрос, Shift+Enter переводит строку."""
        if event.state & 0x0001:  # зажат Shift
            return None
        self.start_question()
        return "break"

    def _ask_key(self) -> bool:
        """Спросить ключ OpenAI и сохранить его в .env."""
        dialog = KeyDialog(self, self.font_base)
        self.wait_window(dialog)
        if not dialog.result:
            return False
        ai.save_key(dialog.result)
        self.log.info("Ключ для разбора вопросов сохранён")
        return True

    def start_question(self) -> None:
        """Задать вопрос: проверить всё нужное и уйти считать в поток."""
        if self.busy:
            return
        question = self.question.get("1.0", "end").strip()
        if not question:
            self.question.focus_set()
            return
        if not self.sections:
            messagebox.showinfo(
                "Нужен список разделов",
                "Сначала нажмите «Обновить список разделов» — программе нужно знать,\n"
                "где искать ответ.",
                parent=self,
            )
            return
        if ai.get_key() is None and not self._ask_key():
            return

        self.pending_question = question
        self._append_summary(f"Вы спросили: {question}")
        self.question.delete("1.0", "end")
        self._start_worker(self._worker_question, "Ищу ответ…", running=True)

    def _worker_question(self, client: AdminClient) -> None:
        question = self.pending_question
        try:
            brain = AiClient(
                ai.get_key() or "",
                model=config.get_model(),
                logger=self.log,
                stop_event=self.stop_event,
            )
            if self.qa_store is None:
                self.qa_store = qa.DataStore(client, self.sections, logger=self.log)
            else:
                self.qa_store.attach(client)
                self.qa_store.sections = {item["key"]: item for item in self.sections}
            answer = qa.answer_question(question, self.qa_store, brain, logger=self.log)
            self.queue.put(("answer", answer, None))
        except Stopped:
            self.queue.put(("stopped_question", None, None))
        except NoKey:
            self.queue.put(("error", "Не сохранён ключ для разбора вопросов", False))
        except (AiError, AuthError, NetworkError, ApiError) as error:
            self.queue.put(("answer_error", str(error), isinstance(error, AuthError)))
        except Exception as error:
            self.log.exception("Сбой при ответе на вопрос")
            self.queue.put(("answer_error", f"Не смог ответить: {error}", False))

    def _on_stop(self) -> None:
        self.stop_event.set()
        self.status_label.configure(text="Останавливаю, сохраняю уже скачанное…")
        self.stop_button.state(["disabled"])

    # ----------------------------------------------------- работа в потоке

    def _worker_discovery(self, client: AdminClient) -> None:
        try:
            client.login()
            sections = discovery.discover(
                client,
                on_progress=lambda label, number, total: self.queue.put(
                    ("status", f"Проверяю раздел «{label}» ({number} из {total})", number * 100 / total)
                ),
            )
            discovery.save_sections(sections)
            self.queue.put(("sections", sections, None))
        except Stopped:
            self.queue.put(("stopped_discovery", None, None))
        except (AuthError, NetworkError, ApiError) as error:
            self.queue.put(("error", str(error), isinstance(error, AuthError)))
        except Exception as error:  # неожиданное — но всё равно по-русски
            self.log.exception("Сбой при поиске разделов")
            self.queue.put(("error", f"Не получилось найти разделы: {error}", False))

    def _worker_export(self, client: AdminClient) -> None:
        try:
            client.login()
            result = run_export(
                client,
                self.export_options,
                progress=lambda text, fraction: self.queue.put(("status", text, fraction)),
                logger=self.log,
            )
            self.queue.put(("done", result, None))
        except Stopped:
            self.queue.put(("stopped_discovery", None, None))
        except (AuthError, NetworkError, ApiError) as error:
            self.queue.put(("error", str(error), isinstance(error, AuthError)))
        except Exception as error:
            self.log.exception("Сбой при выгрузке")
            self.queue.put(("error", f"Не получилось выгрузить: {error}", False))

    # ------------------------------------------------------- разбор сообщений

    def _pump(self) -> None:
        """Раз в десятую секунды забираем сообщения из потока."""
        try:
            while True:
                kind, payload, extra = self.queue.get_nowait()
                if kind == "status":
                    self.status_label.configure(text=payload)
                    if extra is not None:
                        self.progress.configure(value=max(0.0, min(100.0, float(extra))))
                elif kind == "sections":
                    self._set_busy(False)
                    self.refresh_sections_list(payload)
                    if payload:
                        self._set_summary(
                            f"Нашлось разделов: {len(payload)}.\n"
                            "Отметьте нужные слева и нажмите «Выгрузить»."
                        )
                    else:
                        self._set_summary(
                            "Ни один раздел не ответил.\n"
                            "Проверьте интернет и попробуйте ещё раз."
                        )
                elif kind == "done":
                    self._finish_export(payload)
                elif kind == "stopped_discovery":
                    self._set_busy(False)
                    self._set_summary("Остановлено. Всё, что успели скачать, сохранено.")
                elif kind == "stopped_question":
                    self._set_busy(False)
                    self.stop_button.state(["!disabled"])
                    self._append_summary("Поиск ответа остановлен.")
                elif kind == "answer":
                    self._finish_answer(payload)
                elif kind == "answer_error":
                    self._set_busy(False)
                    self.stop_button.state(["!disabled"])
                    self._append_summary(payload)
                    if extra:
                        self._ask_credentials()
                elif kind == "error":
                    self._set_busy(False)
                    self._set_summary(payload)
                    messagebox.showerror("Не получилось", payload, parent=self)
                    if extra:  # неверный логин или пароль — спросим заново
                        self._ask_credentials()
        except queue.Empty:
            pass
        finally:
            self.after(100, self._pump)

    def _finish_answer(self, answer: qa.Answer) -> None:
        """Показать ответ и, если он в виде таблицы, дать открыть папку."""
        self._set_busy(False)
        self.stop_button.state(["!disabled"])
        # Кнопки показываем до текста: иначе они сдвинут область уже после прокрутки.
        if answer.table_path is not None:
            self.last_folder = answer.table_path.parent
            self._show_result_buttons(True)
        self._append_summary(answer.text)
        self.question.focus_set()

    def _finish_export(self, result: ExportResult) -> None:
        self.last_result = result
        self.last_folder = result.out_dir
        if self.qa_store is not None:
            self.qa_store.out_dir = result.out_dir
        self._set_busy(False)
        self.stop_button.state(["!disabled"])
        text = result.summary + f"\n\nФайлы лежат в папке:\n{result.out_dir}"
        self._set_summary(text)
        self._show_result_buttons(True)
        self.log.info("Выгрузка завершена: %s", result.out_dir)

    def _on_close(self) -> None:
        self.stop_event.set()
        self.destroy()


def main() -> None:
    app = App()
    app.mainloop()
