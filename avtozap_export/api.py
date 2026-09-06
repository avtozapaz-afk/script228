"""Разговор с админкой АвтоЗап. Только чтение: GET-запросы и один вход.

Токен живёт около часа. Если сервер отвечает «401», программа сама
заходит заново и повторяет запрос — пользователя ни о чём не спрашивают.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from typing import Any, Callable, Iterator

import requests

from . import config
from .core import extract_rows, pages_estimate


class ApiError(Exception):
    """Понятная человеку ошибка (текст уже по-русски)."""


class AuthError(ApiError):
    """Не пустило в админку."""


class NetworkError(ApiError):
    """Не достучались до сервера."""


class Stopped(Exception):
    """Пользователь нажал «Стоп»."""


NO_INTERNET = "Нет интернета — проверьте подключение к сети"
BAD_LOGIN = "Не пустило в админку — проверьте логин и пароль"


def _find_token(payload: Any, depth: int = 0) -> str | None:
    """Найти токен в ответе на вход, как бы он ни был завёрнут."""
    if depth > 4:
        return None
    if isinstance(payload, dict):
        for key in ("admin_access_token", "access_token", "accessToken", "token", "jwt"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        for value in payload.values():
            if isinstance(value, (dict, list)):
                found = _find_token(value, depth + 1)
                if found:
                    return found
    elif isinstance(payload, list):
        for value in payload:
            found = _find_token(value, depth + 1)
            if found:
                return found
    return None


class AdminClient:
    """Клиент админки. Умеет входить, читать страницы и проверять адреса."""

    def __init__(
        self,
        username: str,
        password: str,
        *,
        base_url: str = config.BASE_URL,
        pause: float = config.REQUEST_PAUSE,
        timeout: float = 60.0,
        logger: logging.Logger | None = None,
        stop_event: threading.Event | None = None,
    ) -> None:
        self._username = username
        self._password = password
        self.base_url = base_url.rstrip("/")
        self.pause = pause
        self.timeout = timeout
        self.log = logger or logging.getLogger("avtozap")
        self.stop_event = stop_event or threading.Event()
        self.session = requests.Session()
        self.session.headers.update(config.COMMON_HEADERS)
        self._logged_in = False
        self._last_request = 0.0

    # ------------------------------------------------------------------ вход

    def login(self) -> None:
        """Войти в админку и запомнить токен в куке."""
        url = f"{self.base_url}{config.LOGIN_PATH}"
        self.log.info("Вход в админку")
        try:
            response = self.session.post(
                url,
                json={"username": self._username, "password": self._password},
                headers={"Content-Type": "application/json"},
                timeout=self.timeout,
            )
        except requests.exceptions.SSLError as error:
            raise NetworkError("Не удалось установить защищённое соединение с сервером") from error
        except requests.exceptions.Timeout as error:
            raise NetworkError("Сервер админки не отвечает — попробуйте позже") from error
        except requests.exceptions.RequestException as error:
            raise NetworkError(NO_INTERNET) from error

        if response.status_code in (400, 401, 403, 422):
            raise AuthError(BAD_LOGIN)
        if response.status_code >= 500:
            raise NetworkError("Сервер админки временно недоступен — попробуйте позже")
        if response.status_code >= 400:
            raise ApiError(f"Админка ответила ошибкой при входе (код {response.status_code})")

        try:
            payload = response.json()
        except ValueError:
            payload = None

        token = _find_token(payload)
        if token:
            self.session.cookies.set(config.TOKEN_COOKIE, token, domain="api.avtozap.pro", path="/")
        elif not self.session.cookies.get(config.TOKEN_COOKIE):
            raise AuthError("Админка не выдала пропуск — проверьте логин и пароль")

        self._logged_in = True
        self.log.info("Вход выполнен")

    def ensure_login(self) -> None:
        if not self._logged_in:
            self.login()

    # -------------------------------------------------------------- запросы

    def _check_stop(self) -> None:
        if self.stop_event.is_set():
            raise Stopped()

    def _wait_turn(self) -> None:
        """Небольшая пауза между запросами, чтобы не нагружать сервер."""
        elapsed = time.monotonic() - self._last_request
        remaining = self.pause - elapsed
        while remaining > 0:
            self._check_stop()
            time.sleep(min(0.1, remaining))
            remaining = self.pause - (time.monotonic() - self._last_request)
        self._last_request = time.monotonic()

    def raw_get(self, path: str, params: dict | None = None) -> requests.Response:
        """GET с одним автоматическим перезаходом при «401»."""
        self._check_stop()
        self.ensure_login()
        url = f"{self.base_url}{path if path.startswith('/') else '/' + path}"

        for attempt in (1, 2):
            self._wait_turn()
            self._check_stop()
            try:
                response = self.session.get(url, params=params, timeout=self.timeout)
            except requests.exceptions.Timeout as error:
                raise NetworkError("Сервер долго не отвечает — попробуйте позже") from error
            except requests.exceptions.RequestException as error:
                raise NetworkError(NO_INTERNET) from error

            if response.status_code == 401 and attempt == 1:
                self.log.info("Пропуск устарел, захожу заново")
                self._logged_in = False
                self.login()
                continue
            return response
        return response  # pragma: no cover - цикл всегда возвращает раньше

    def get_json(self, path: str, params: dict | None = None) -> Any:
        """GET, который отдаёт разобранный ответ или понятную ошибку."""
        response = self.raw_get(path, params)
        if response.status_code == 401:
            raise AuthError(BAD_LOGIN)
        if response.status_code == 403:
            raise ApiError("Нет доступа к этому разделу")
        if response.status_code == 404:
            raise ApiError("Раздел не найден в админке")
        if response.status_code == 429:
            raise ApiError("Админка просит подождать — слишком много запросов")
        if response.status_code >= 500:
            raise ApiError("Сервер админки ответил ошибкой")
        if response.status_code >= 400:
            raise ApiError(f"Админка ответила кодом {response.status_code}")
        try:
            return response.json()
        except ValueError as error:
            raise ApiError("Сервер прислал непонятный ответ") from error

    def probe(self, path: str, params: dict | None = None) -> tuple[int, Any]:
        """Тихая проверка адреса: код ответа и содержимое, без ошибок."""
        try:
            response = self.raw_get(path, params)
        except Stopped:
            raise
        except ApiError:
            return 0, None
        try:
            return response.status_code, response.json()
        except ValueError:
            return response.status_code, None

    # ------------------------------------------------------------- страницы

    def iter_pages(
        self,
        path: str,
        *,
        params: dict | None = None,
        per_page: int = config.PAGE_SIZE,
        on_page: Callable[[int, int | None, int], None] | None = None,
    ) -> Iterator[list[dict]]:
        """Пройти все страницы раздела до конца.

        ``on_page`` вызывается как ``(номер страницы, всего страниц или None,
        сколько записей уже скачано)`` — для полосы прогресса.
        """
        page = 1
        pages_total: int | None = None
        downloaded = 0
        previous_signature: str | None = None

        while page <= config.MAX_PAGES:
            self._check_stop()
            query = dict(params or {})
            query.update({"page": page, "per_page": per_page})
            payload = self.get_json(path, query)
            rows = extract_rows(payload)

            if pages_total is None:
                pages_total = pages_estimate(payload, per_page)

            if not rows:
                if on_page:
                    on_page(page, pages_total, downloaded)
                return

            signature = hashlib.sha1(
                json.dumps(rows, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
            ).hexdigest()
            if signature == previous_signature:
                # Сервер отдаёт одно и то же — дальше листать бессмысленно.
                self.log.info("Раздел %s: страницы повторяются, останавливаюсь", path)
                return
            previous_signature = signature

            downloaded += len(rows)
            if on_page:
                on_page(page, pages_total, downloaded)
            yield rows

            if len(rows) < per_page:
                return
            if pages_total is not None and page >= pages_total:
                return
            page += 1

        self.log.info("Раздел %s: достигнут предел в %s страниц", path, config.MAX_PAGES)

    def fetch_all(
        self,
        path: str,
        *,
        params: dict | None = None,
        per_page: int = config.PAGE_SIZE,
        on_page: Callable[[int, int | None, int], None] | None = None,
    ) -> list[dict]:
        """Скачать раздел целиком."""
        rows: list[dict] = []
        for chunk in self.iter_pages(path, params=params, per_page=per_page, on_page=on_page):
            rows.extend(chunk)
        return rows
