"""Путь к API: ретраи, backoff, таймауты, разбор ответа арбитра.

Настоящий ключ здесь не нужен и не используется — весь транспорт подставной,
поэтому эти тесты проверяют ровно ту логику, которая иначе осталась бы
непроверенной до первого реального прогона.
"""

import pytest

from avtozap.arbiter import ArbiterV3
from avtozap.llm import LlmClient, LlmError, _is_retryable
from avtozap.retriever import RetrieverV2
from avtozap.types import (
    ARB_ERROR,
    ARB_SELECT,
    Candidate,
    OemEvidence,
    PhotoEvidence,
    RetrieverResult,
)


# ── подставной транспорт ────────────────────────────────────────────────────
class _Message:
    def __init__(self, content): self.content = content


class _Choice:
    def __init__(self, content): self.message = _Message(content)


class _Response:
    def __init__(self, content, model="stub"):
        self.choices = [_Choice(content)]
        self.model = model


class RateLimitError(Exception):
    status_code = 429


class APITimeoutError(Exception):
    pass


class BadRequestError(Exception):
    status_code = 400


class _Completions:
    def __init__(self, script):
        self.script = list(script)
        self.calls = 0
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        self.calls += 1
        item = self.script.pop(0) if self.script else _Response("{}")
        if isinstance(item, Exception):
            raise item
        return item


class StubBackend:
    def __init__(self, *script):
        self.chat = type("Chat", (), {})()
        self.chat.completions = _Completions(script)


def client_for(*script, max_retries=5) -> LlmClient:
    delays: list[float] = []
    stub = StubBackend(*script)
    client = LlmClient("ключ-не-используется", max_retries=max_retries,
                       backoff_base_s=2.0, sleep=delays.append, backend=stub)
    client.stub = stub          # type: ignore[attr-defined]
    client.delays = delays      # type: ignore[attr-defined]
    return client


# ── классификация ошибок ────────────────────────────────────────────────────
@pytest.mark.parametrize("exc", [RateLimitError(), APITimeoutError()])
def test_transient_errors_are_retryable(exc):
    assert _is_retryable(exc)


def test_a_bad_request_is_not_retried():
    assert not _is_retryable(BadRequestError())


# ── ретраи и backoff ────────────────────────────────────────────────────────
def test_a_transient_failure_is_retried_and_then_succeeds():
    client = client_for(RateLimitError(), APITimeoutError(), _Response('{"ok":1}'))
    response = client.complete("m", [{"role": "user", "content": "x"}])
    assert response.text == '{"ok":1}'
    assert response.attempts == 3


def test_backoff_grows_exponentially():
    client = client_for(RateLimitError(), RateLimitError(), _Response("{}"))
    client.complete("m", [{"role": "user", "content": "x"}])
    first, second = client.delays          # type: ignore[attr-defined]
    assert second > first                  # 2 с → 4 с (с джиттером)


def test_retries_stop_at_the_limit_and_raise():
    client = client_for(*[RateLimitError()] * 9, max_retries=3)
    with pytest.raises(LlmError):
        client.complete("m", [{"role": "user", "content": "x"}])
    assert client.stub.chat.completions.calls == 3    # type: ignore[attr-defined]


def test_a_permanent_error_is_not_retried():
    client = client_for(BadRequestError(), _Response("{}"))
    with pytest.raises(LlmError):
        client.complete("m", [{"role": "user", "content": "x"}])
    assert client.stub.chat.completions.calls == 1    # type: ignore[attr-defined]


# ── детерминизм запроса ─────────────────────────────────────────────────────
def test_judge_calls_are_deterministic_and_json_only():
    client = client_for(_Response("{}"))
    client.complete("m", [{"role": "user", "content": "x"}], temperature=0.0)
    kwargs = client.stub.chat.completions.last_kwargs   # type: ignore[attr-defined]
    assert kwargs["temperature"] == 0.0
    assert kwargs["response_format"] == {"type": "json_object"}


# ── арбитр поверх подставного транспорта ───────────────────────────────────
@pytest.fixture(scope="module")
def candidates() -> RetrieverResult:
    part = RetrieverV2().dict.get("EY-002")
    return RetrieverResult(status="OK", candidates=[Candidate(
        external_code="EY-002", name_ru=part.name_ru, name_az=part.name_az,
        category=part.category, score=0.999, reason="exact:eylec diski")])


def decide(client, candidates):
    return ArbiterV3(client, model="stub").decide(
        original_text="əyləc diski", item_raw="əyləc diski",
        oem=OemEvidence(), photo=PhotoEvidence(), retriever=candidates)


def test_a_well_formed_answer_becomes_a_selection(candidates):
    client = client_for(_Response(
        '{"decision":"select","external_code":"EY-002","confidence":"high",'
        '"reason":"диск"}'))
    decision = decide(client, candidates)
    assert (decision.decision, decision.external_code) == (ARB_SELECT, "EY-002")
    assert decision.confidence == "high"


def test_an_invented_code_is_reported_as_an_error_not_accepted(candidates):
    """Код вне списка кандидатов не «чинится» — он попадает в отчёт как ошибка."""
    client = client_for(_Response(
        '{"decision":"select","external_code":"ZZ-999","confidence":"high"}'))
    decision = decide(client, candidates)
    assert decision.decision == ARB_ERROR
    assert "отсутствует среди кандидатов" in decision.reason


def test_broken_json_is_reported_as_an_error(candidates):
    decision = decide(client_for(_Response("не json")), candidates)
    assert decision.decision == ARB_ERROR


def test_an_api_failure_becomes_an_error_record_not_an_exception(candidates):
    """Один упавший вызов не должен ронять прогон из 300 кейсов."""
    client = client_for(*[RateLimitError()] * 5, max_retries=2)
    decision = decide(client, candidates)
    assert decision.decision == ARB_ERROR
    assert decision.error
