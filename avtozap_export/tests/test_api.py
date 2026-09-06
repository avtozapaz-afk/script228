"""Проверки клиента админки без единого настоящего запроса."""

import os
import sys
import threading

import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from avtozap_export import config  # noqa: E402
from avtozap_export.api import (  # noqa: E402
    AdminClient,
    ApiError,
    AuthError,
    NetworkError,
    Stopped,
)


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("не json")
        return self._payload


class FakeCookies(dict):
    def set(self, name, value, **_kwargs):
        self[name] = value

    def get(self, name, default=None):
        return dict.get(self, name, default)


class FakeSession:
    """Подделка requests.Session: отвечает по сценарию и всё запоминает."""

    def __init__(self, responses=None, login_response=None, raise_on_get=None):
        self.headers: dict = {}
        self.cookies = FakeCookies()
        self.responses = list(responses or [])
        self.login_response = login_response or FakeResponse(200, {"access_token": "тайна"})
        self.raise_on_get = raise_on_get
        self.gets: list[tuple[str, dict]] = []
        self.logins = 0

    def post(self, url, **kwargs):
        self.logins += 1
        if isinstance(self.login_response, Exception):
            raise self.login_response
        return self.login_response

    def get(self, url, params=None, timeout=None):
        self.gets.append((url, dict(params or {})))
        if self.raise_on_get is not None:
            raise self.raise_on_get
        if not self.responses:
            return FakeResponse(200, {"data": []})
        item = self.responses.pop(0)
        return item() if callable(item) else item


def make_client(session, **kwargs):
    client = AdminClient("логин", "пароль", pause=0, **kwargs)
    client.session = session
    return client


# ── вход ─────────────────────────────────────────────────────────────────────
def test_login_saves_token_in_cookie():
    session = FakeSession()
    client = make_client(session)
    client.login()
    assert session.cookies[config.TOKEN_COOKIE] == "тайна"


def test_wrong_password_gives_plain_russian_error():
    session = FakeSession(login_response=FakeResponse(401, {"detail": "Unauthorized"}))
    with pytest.raises(AuthError, match="проверьте логин и пароль"):
        make_client(session).login()


def test_no_internet_gives_plain_russian_error():
    session = FakeSession(login_response=requests.exceptions.ConnectionError())
    with pytest.raises(NetworkError, match="Нет интернета"):
        make_client(session).login()


def test_server_error_on_login_is_explained():
    session = FakeSession(login_response=FakeResponse(503, None))
    with pytest.raises(NetworkError, match="временно недоступен"):
        make_client(session).login()


# ── ошибки запросов ──────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "status, expected",
    [
        (403, "Нет доступа"),
        (404, "не найден"),
        (429, "слишком много запросов"),
        (500, "ответил ошибкой"),
    ],
)
def test_error_codes_turn_into_russian_messages(status, expected):
    session = FakeSession(responses=[FakeResponse(status, None)])
    with pytest.raises(ApiError, match=expected):
        make_client(session).get_json("/stores")


def test_token_expiry_triggers_silent_relogin():
    session = FakeSession(
        responses=[FakeResponse(401, None), FakeResponse(200, {"data": [{"id": 1}]})]
    )
    client = make_client(session)
    assert client.get_json("/offers") == {"data": [{"id": 1}]}
    assert session.logins == 2  # зашли заново и повторили запрос
    assert len(session.gets) == 2


def test_second_401_is_reported_not_looped():
    session = FakeSession(responses=[FakeResponse(401, None), FakeResponse(401, None)])
    with pytest.raises(AuthError):
        make_client(session).get_json("/offers")
    assert len(session.gets) == 2


# ── страницы ─────────────────────────────────────────────────────────────────
def page(count, start=0):
    return FakeResponse(200, {"data": [{"id": start + i} for i in range(count)]})


def test_iter_pages_walks_until_the_last_page():
    session = FakeSession(responses=[page(2, 0), page(2, 2), page(1, 4)])
    client = make_client(session)
    rows = client.fetch_all("/offers", per_page=2)
    assert [row["id"] for row in rows] == [0, 1, 2, 3, 4]
    assert [params["page"] for _url, params in session.gets] == [1, 2, 3]


def test_iter_pages_stops_on_empty_page():
    session = FakeSession(responses=[page(2, 0), FakeResponse(200, {"data": []})])
    rows = make_client(session).fetch_all("/offers", per_page=2)
    assert len(rows) == 2


def test_iter_pages_stops_if_server_ignores_paging():
    same = {"data": [{"id": 1}, {"id": 2}]}
    session = FakeSession(responses=[FakeResponse(200, same) for _ in range(5)])
    rows = make_client(session).fetch_all("/offers", per_page=2)
    assert len(rows) == 2  # вторая такая же страница обрывает листание
    assert len(session.gets) == 2


def test_iter_pages_reports_progress_with_total():
    session = FakeSession(
        responses=[
            FakeResponse(200, {"data": [{"id": 1}, {"id": 2}], "total": 3}),
            FakeResponse(200, {"data": [{"id": 3}], "total": 3}),
        ]
    )
    seen = []
    make_client(session).fetch_all(
        "/offers", per_page=2, on_page=lambda page, pages, done: seen.append((page, pages, done))
    )
    assert seen == [(1, 2, 2), (2, 2, 3)]


def test_stop_button_interrupts_paging():
    stop = threading.Event()

    def stop_now():
        stop.set()
        return page(2, 0)

    session = FakeSession(responses=[stop_now, page(2, 2)])
    client = make_client(session, stop_event=stop)
    with pytest.raises(Stopped):
        client.fetch_all("/offers", per_page=2)


def test_probe_never_raises_on_dead_path():
    session = FakeSession(raise_on_get=requests.exceptions.ConnectionError())
    status, payload = make_client(session).probe("/нет-такого")
    assert (status, payload) == (0, None)


def test_required_headers_are_always_sent():
    client = AdminClient("логин", "пароль", pause=0)
    assert client.session.headers["accept"] == "application/json"
    assert client.session.headers["Origin"] == "https://admin.avtozap.pro"
    assert client.session.headers["Referer"] == "https://admin.avtozap.pro/"
