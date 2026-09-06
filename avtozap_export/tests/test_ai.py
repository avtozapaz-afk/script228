"""Проверки обращения к OpenAI и сопоставления названий. Без настоящей сети."""

import json
import os
import sys
import threading

import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from avtozap_export import config, matching  # noqa: E402
from avtozap_export.ai import AiClient, AiError, NoKey, get_key, save_key  # noqa: E402
from avtozap_export.api import Stopped  # noqa: E402


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text or json.dumps(payload or {}, ensure_ascii=False)

    def json(self):
        if self._payload is None:
            raise ValueError("не json")
        return self._payload


def reply(content: str) -> FakeResponse:
    return FakeResponse(200, {"choices": [{"message": {"content": content}}]})


@pytest.fixture
def posts(monkeypatch):
    """Перехватываем обращения к OpenAI и подсовываем ответы."""
    sent: list[dict] = []
    queue: list = []

    def fake_post(url, headers=None, json=None, timeout=None):
        sent.append({"url": url, "headers": headers or {}, "body": json or {}})
        item = queue.pop(0) if queue else reply("{}")
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr("avtozap_export.ai.requests.post", fake_post)
    return sent, queue


def client(**kwargs):
    return AiClient("sk-тестовый-ключ", **kwargs)


# ── ключ ─────────────────────────────────────────────────────────────────────
def test_empty_key_is_refused():
    with pytest.raises(NoKey):
        AiClient("")


def test_key_is_stored_next_to_the_password(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    monkeypatch.setattr(config, "ENV_PATH", env)
    config.save_credentials("менеджер", "пароль", env)
    save_key("sk-секрет")

    assert get_key() == "sk-секрет"
    text = env.read_text(encoding="utf-8")
    assert "пароль" in text and "sk-секрет" in text  # логин с паролем не потеряли


def test_key_goes_only_into_the_authorization_header(posts):
    sent, queue = posts
    queue.append(reply('{"понятно": true}'))
    client().plan("вопрос", "каталог", "6 сентября")

    assert sent[0]["headers"]["Authorization"].startswith("Bearer ")
    assert "sk-тестовый-ключ" not in json.dumps(sent[0]["body"], ensure_ascii=False)


# ── разбор вопроса ───────────────────────────────────────────────────────────
def test_plan_is_parsed(posts):
    _sent, queue = posts
    queue.append(reply('{"операция": "посчитать", "раздел": "users"}'))
    plan = client().plan("сколько новых пользователей", "users (Пользователи): id", "6 сентября")
    assert plan["операция"] == "посчитать"


def test_plan_survives_markdown_fences(posts):
    _sent, queue = posts
    queue.append(reply('```json\n{"операция": "список"}\n```'))
    assert client().plan("вопрос", "каталог", "дата")["операция"] == "список"


def test_broken_answer_is_reported_in_russian(posts):
    _sent, queue = posts
    queue.append(reply("это не json"))
    with pytest.raises(AiError, match="сказать иначе"):
        client().plan("вопрос", "каталог", "дата")


def test_question_and_catalog_are_sent(posts):
    sent, queue = posts
    queue.append(reply("{}"))
    client().plan("сколько заявок", "rfq (Заявки): id, created_at", "6 сентября")
    body = json.dumps(sent[0]["body"], ensure_ascii=False)
    assert "сколько заявок" in body
    assert "rfq (Заявки)" in body


def test_planner_asks_for_json(posts):
    sent, queue = posts
    queue.append(reply("{}"))
    client().plan("вопрос", "каталог", "дата")
    assert sent[0]["body"]["response_format"] == {"type": "json_object"}
    assert sent[0]["body"]["temperature"] == 0


# ── формулировка ответа ──────────────────────────────────────────────────────
def test_phrase_gets_only_counted_values(posts):
    sent, queue = posts
    queue.append(reply("Вчера зарегистрировались 24 человека."))
    text = client().phrase("сколько новых", {"что": "количество", "количество": 24})
    assert text == "Вчера зарегистрировались 24 человека."
    assert "24" in json.dumps(sent[0]["body"], ensure_ascii=False)


# ── ошибки по-русски ─────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "response, expected",
    [
        (FakeResponse(401, {}), "Ключ OpenAI не подошёл"),
        (FakeResponse(429, {}, text='{"error": {"code": "insufficient_quota"}}'), "закончились средства"),
        (FakeResponse(429, {}, text="rate limit"), "Слишком много запросов"),
        (FakeResponse(500, {}), "недоступен"),
        (requests.exceptions.ConnectionError(), "Нет интернета"),
        (requests.exceptions.Timeout(), "Не дождался ответа"),
    ],
)
def test_errors_are_explained_in_plain_russian(posts, response, expected):
    _sent, queue = posts
    queue.append(response)
    with pytest.raises(AiError, match=expected):
        client().plan("вопрос", "каталог", "дата")


def test_unavailable_model_falls_back_to_the_next(posts):
    sent, queue = posts
    queue.append(FakeResponse(404, {}))
    queue.append(reply('{"операция": "посчитать"}'))
    brain = client(model="какая-то-новая-модель")
    plan = brain.plan("вопрос", "каталог", "дата")

    assert plan["операция"] == "посчитать"
    assert sent[0]["body"]["model"] == "какая-то-новая-модель"
    assert sent[1]["body"]["model"] != "какая-то-новая-модель"
    assert len(sent) == 2


def test_all_models_unavailable_is_reported(posts):
    _sent, queue = posts
    queue.extend(FakeResponse(404, {}) for _ in range(10))
    with pytest.raises(AiError, match="Ни одна из моделей"):
        client().plan("вопрос", "каталог", "дата")


def test_stop_button_interrupts_before_the_request(posts):
    sent, _queue = posts
    stop = threading.Event()
    stop.set()
    with pytest.raises(Stopped):
        client(stop_event=stop).plan("вопрос", "каталог", "дата")
    assert sent == []


# ── сопоставление названий ───────────────────────────────────────────────────
@pytest.mark.parametrize(
    "russian, latin",
    [
        ("тойота", "Toyota"),
        ("королла", "Corolla"),
        ("ниссан", "Nissan"),
        ("мерседес", "Mercedes"),
        ("хендай", "Hyundai"),
    ],
)
def test_russian_and_latin_spellings_meet(russian, latin):
    rows = [{"brand": latin}]
    assert matching.filter_rows(rows, [russian]) == rows


def test_two_words_must_both_match():
    rows = [{"brand": "Toyota", "model": "Camry"}]
    assert matching.filter_rows(rows, ["тойота королла"]) == []
    assert matching.filter_rows(rows, ["тойота камри"]) == rows


def test_search_is_case_insensitive_and_partial():
    rows = [{"name": "Магазин АВТОМИР Плюс"}]
    assert matching.filter_rows(rows, ["автомир"]) == rows


def test_unrelated_name_is_not_matched():
    rows = [{"name": "Автомир"}, {"name": "Мотор Плюс"}]
    assert matching.filter_rows(rows, ["Колесо"]) == []


def test_display_name_prefers_human_fields():
    assert matching.display_name({"id": 5, "name": "Автомир"}) == "Автомир"
    assert matching.display_name({"id": 5, "title": "фара"}) == "фара"
    assert matching.display_name({"id": 5}) == "№ 5"


def test_similar_names_offer_real_options():
    rows = [{"name": "Автомир"}, {"name": "Мотор Плюс"}, {"name": "Запчасть Сервис"}]
    assert "Автомир" in matching.similar_names("Автолюкс", rows)


def test_resolve_column_finds_the_real_name():
    columns = ["id", "store_id", "created_at"]
    assert matching.resolve_column(columns, "Store_ID") == "store_id"
    assert matching.resolve_column(columns, "created") == "created_at"
    assert matching.resolve_column(columns, "выдуманная") is None
    assert matching.resolve_column(columns, "выдуманная", hints=("id",)) == "id"
