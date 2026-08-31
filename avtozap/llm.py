"""Тонкая обёртка над OpenAI: ретраи, backoff, таймауты, детерминизм.

Ключ никогда не логируется и не пишется в файлы — он живёт только в памяти
клиента. Модуль импортируется и без установленного ``openai``: в mock-режиме
сеть не нужна вовсе, и харнесс должен запускаться на голой машине.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Any, Callable

from .config import (
    DEFAULT_BACKOFF_BASE_S,
    DEFAULT_BACKOFF_CAP_S,
    DEFAULT_MAX_RETRIES,
    DEFAULT_TIMEOUT_S,
)


class LlmError(RuntimeError):
    """Вызов не удался после всех попыток."""


@dataclass
class LlmResponse:
    text: str
    model: str
    latency_ms: int
    attempts: int


def _is_retryable(exc: BaseException) -> bool:
    """Временная ли ошибка. Классифицируем по имени класса и коду статуса.

    По имени, а не по isinstance, чтобы не тащить зависимость от конкретной
    версии SDK: набор классов исключений в ``openai`` между версиями менялся.
    """
    name = type(exc).__name__
    if name in {"APITimeoutError", "APIConnectionError", "RateLimitError",
                "InternalServerError", "APIStatusError", "APIError",
                "Timeout", "ConnectionError"}:
        status = getattr(exc, "status_code", None)
        if status is None:
            return True
        return status == 429 or status >= 500
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status == 429 or status >= 500
    return False


class LlmClient:
    """Клиент чат-комплишенов с ретраями и экспоненциальным backoff."""

    def __init__(self, api_key: str, timeout_s: float = DEFAULT_TIMEOUT_S,
                 max_retries: int = DEFAULT_MAX_RETRIES,
                 backoff_base_s: float = DEFAULT_BACKOFF_BASE_S,
                 backoff_cap_s: float = DEFAULT_BACKOFF_CAP_S,
                 sleep: Callable[[float], None] = time.sleep,
                 backend: Any = None):
        """``backend`` — шов для тестов и альтернативного транспорта.

        Если он передан, готовый клиент используется как есть; иначе строится
        обычный ``openai.OpenAI``. Благодаря этому логику ретраев и разбора
        ответа можно проверить без сети и без настоящего ключа.
        """
        if backend is not None:
            self._client = backend
        else:
            try:
                from openai import OpenAI
            except ImportError as exc:  # pragma: no cover - зависит от окружения
                raise LlmError(
                    "пакет openai не установлен: pip install -r requirements.txt"
                ) from exc
            # Ретраи ведём сами, чтобы иметь единый лог и предсказуемые задержки.
            self._client = OpenAI(api_key=api_key, timeout=timeout_s, max_retries=0)
        self.max_retries = max_retries
        self.backoff_base_s = backoff_base_s
        self.backoff_cap_s = backoff_cap_s
        self._sleep = sleep

    def complete(self, model: str, messages: list[dict[str, Any]],
                 temperature: float = 0.0,
                 json_mode: bool = True,
                 max_tokens: int | None = 700) -> LlmResponse:
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens

        started = time.time()
        last: BaseException | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = self._client.chat.completions.create(**kwargs)
                text = (response.choices[0].message.content or "").strip()
                return LlmResponse(
                    text=text,
                    model=getattr(response, "model", model),
                    latency_ms=int((time.time() - started) * 1000),
                    attempts=attempt,
                )
            except Exception as exc:                     # noqa: BLE001
                last = exc
                if attempt >= self.max_retries or not _is_retryable(exc):
                    break
                self._sleep(self._delay(attempt))
        raise LlmError(f"{type(last).__name__}: {last}") from last

    def _delay(self, attempt: int) -> float:
        """Экспоненциальная задержка с джиттером: 2, 4, 8, 16 … (с потолком)."""
        base = min(self.backoff_base_s * (2 ** (attempt - 1)), self.backoff_cap_s)
        return base * (0.75 + 0.5 * random.random())
