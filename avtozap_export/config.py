"""Где что лежит и как хранится логин с паролем.

Логин и пароль читаются из файла ``.env`` рядом с программой и никогда
не попадают ни в код, ни в логи, ни на экран.
"""

from __future__ import annotations

import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
ENV_PATH = APP_DIR / ".env"
ENDPOINTS_PATH = APP_DIR / "endpoints.json"
DATA_DIR = APP_DIR / "data"
LOG_DIR = APP_DIR / "logs"

USERNAME_KEY = "AVTOZAP_USERNAME"
PASSWORD_KEY = "AVTOZAP_PASSWORD"
OPENAI_KEY_NAME = "OPENAI_API_KEY"
OPENAI_MODEL_NAME = "OPENAI_MODEL"

BASE_URL = "https://api.avtozap.pro/admin"
LOGIN_PATH = "/auth/login"
TOKEN_COOKIE = "admin_access_token"

COMMON_HEADERS = {
    "accept": "application/json",
    "Origin": "https://admin.avtozap.pro",
    "Referer": "https://admin.avtozap.pro/",
}

# Пауза между запросами к серверу, секунды — чтобы не нагружать админку.
REQUEST_PAUSE = 0.3
# Сколько записей просить за один раз.
PAGE_SIZE = 100
# Предохранитель от бесконечного листания.
MAX_PAGES = 5000


def read_env(path: Path | None = None) -> dict[str, str]:
    """Прочитать ``.env``. Файла нет — пустой словарь."""
    path = path or ENV_PATH
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def get_credentials(path: Path | None = None) -> tuple[str, str] | None:
    """Логин и пароль, если они уже сохранены."""
    values = read_env(path)
    username = values.get(USERNAME_KEY, "").strip() or os.environ.get(USERNAME_KEY, "").strip()
    password = values.get(PASSWORD_KEY, "") or os.environ.get(PASSWORD_KEY, "")
    if username and password:
        return username, password
    return None


def save_credentials(username: str, password: str, path: Path | None = None) -> None:
    """Сохранить логин и пароль в ``.env``, не трогая остальные строки."""
    values = read_env(path)
    values[USERNAME_KEY] = username
    values[PASSWORD_KEY] = password
    write_env(values, path)  # на Windows прав может не быть — это не ошибка


def write_env(values: dict[str, str], path: Path | None = None) -> None:
    """Переписать ``.env`` целиком: логин и пароль сверху, остальное следом."""
    path = path or ENV_PATH
    values = dict(values)
    lines = [
        "# Доступ в админку АвтоЗап и ключ OpenAI. Этот файл никому не показывайте.",
        f"{USERNAME_KEY}={values.pop(USERNAME_KEY, '')}",
        f"{PASSWORD_KEY}={values.pop(PASSWORD_KEY, '')}",
    ]
    lines.extend(f"{key}={item}" for key, item in values.items())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass  # на Windows прав может не быть — это не ошибка


def save_value(name: str, value: str, path: Path | None = None) -> None:
    """Записать одну строку в ``.env``, не трогая остальные."""
    values = read_env(path)
    values[name] = value
    write_env(values, path)


def get_model(path: Path | None = None) -> str:
    """Какую модель OpenAI просить. Пусто — программа выберет сама."""
    return read_env(path).get(OPENAI_MODEL_NAME, "").strip()


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
