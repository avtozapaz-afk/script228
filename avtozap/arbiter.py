"""Arbiter V3 — ЗАМОРОЖЕН.

Промпт лежит в ``prompts/arbiter_v3.txt`` и защищён контрольной суммой в
``prompts/arbiter_v3.sha256`` (тест ``test_arbiter_frozen.py`` падает при любом
расхождении). Изменить промпт можно только осознанно — правкой обоих файлов;
случайный «доводкой по ходу дела» он измениться не может.

Модуль отвечает только за подачу входа в замороженный промпт и за строгий
разбор ответа. Никакой доводки решения здесь нет: если модель вернула код,
которого не было в кандидатах, это ошибка формата, а не повод «починить» ответ.
Реальную проверку выполняет валидатор — отдельным слоем.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any

from .config import (
    ARBITER_PROMPT_PATH,
    ARBITER_PROMPT_SHA_PATH,
    DEFAULT_ARBITER_MODEL,
    DEFAULT_TEMPERATURE,
)
from .llm import LlmClient, LlmError
from .types import (
    ARB_CLARIFY,
    ARB_ERROR,
    ARB_SELECT,
    ARB_UNKNOWN,
    ArbiterDecision,
    OemEvidence,
    PhotoEvidence,
    RetrieverResult,
)

_VALID_DECISIONS = {ARB_SELECT, ARB_UNKNOWN, ARB_CLARIFY}


def load_prompt(path: str = ARBITER_PROMPT_PATH) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def prompt_sha256(path: str = ARBITER_PROMPT_PATH) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def frozen_sha256(path: str = ARBITER_PROMPT_SHA_PATH) -> str | None:
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return fh.read().strip()


def build_user_message(original_text: str, item_raw: str,
                       oem: OemEvidence, photo: PhotoEvidence,
                       retriever: RetrieverResult,
                       vehicle_context: str = "",
                       translation: str | None = None) -> str:
    """Собрать вход арбитра. Кандидаты — единственный источник допустимых кодов."""
    lines = [
        f"ORIGINAL_MESSAGE: {original_text}",
        f"ATOMIC_ITEM: {item_raw}",
    ]
    if translation:
        lines.append(f"TRANSLATION: {translation}")
    if vehicle_context:
        lines.append(f"VEHICLE_CONTEXT: {vehicle_context}")

    lines.append(f"OEM_STATUS: {oem.status}")
    lines.append(f"OEM_NUMBERS: {', '.join(oem.numbers) if oem.numbers else '-'}")
    if oem.resolved_part_id:
        lines.append(f"OEM_RESOLVED_PART: {oem.resolved_part_id} ({oem.resolved_name})")
    lines.append(f"OEM_NOTE: {oem.reason}")

    lines.append(f"PHOTO_STATUS: {photo.status}")
    if photo.summary:
        lines.append(f"PHOTO_EVIDENCE: {photo.summary}")

    lines.append("")
    lines.append("CANDIDATES (the ONLY part_ids you may return):")
    if not retriever.candidates:
        lines.append("  (none)")
    else:
        for c in retriever.candidates:
            lines.append(
                f"  {c.part_id} | {c.name_ru} | {c.name_az} | "
                f"категория: {c.category} / {c.subcategory} | "
                f"score={c.score} | почему: {c.reason}"
            )
    return "\n".join(lines)


def parse_response(text: str, allowed_ids: set[str]) -> tuple[dict[str, Any], str | None]:
    """Разобрать JSON-ответ арбитра. Возвращает ``(данные, ошибка)``."""
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {}, "ответ арбитра не является корректным JSON"
    if not isinstance(data, dict):
        return {}, "ответ арбитра не является объектом JSON"

    decision = str(data.get("decision", "")).strip().upper()
    if decision not in _VALID_DECISIONS:
        return data, f"недопустимое решение {decision!r}"

    part_id = data.get("part_id")
    if part_id is not None and not isinstance(part_id, str):
        return data, "part_id должен быть строкой либо null"
    if decision == ARB_SELECT:
        if not part_id:
            return data, "SELECT без part_id"
        if part_id not in allowed_ids:
            # Не «чиним» ответ: несуществующий код — это факт, который должен
            # доехать до валидатора и до отчёта.
            return data, f"part_id {part_id!r} отсутствует среди кандидатов"
    elif part_id:
        return data, f"решение {decision} не должно содержать part_id"

    confidence = data.get("confidence")
    if confidence is not None:
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            return data, "confidence не является числом"
        if not 0.0 <= confidence <= 1.0:
            return data, "confidence вне диапазона [0, 1]"
    return data, None


class ArbiterV3:
    def __init__(self, client: LlmClient | None, model: str = DEFAULT_ARBITER_MODEL,
                 temperature: float = DEFAULT_TEMPERATURE,
                 prompt_path: str = ARBITER_PROMPT_PATH):
        self.client = client
        self.model = model
        self.temperature = temperature
        self.system_prompt = load_prompt(prompt_path)

    def decide(self, original_text: str, item_raw: str, oem: OemEvidence,
               photo: PhotoEvidence, retriever: RetrieverResult,
               vehicle_context: str = "",
               translation: str | None = None) -> ArbiterDecision:
        allowed = set(retriever.part_ids)
        if not allowed:
            # Ретривер не дал ни одного реального кандидата — арбитру не из чего
            # выбирать, и звать модель незачем.
            return ArbiterDecision(
                decision=ARB_UNKNOWN, part_id=None, confidence=0.0,
                reason="ретривер не нашёл ни одного кандидата в словаре",
                model=self.model,
            )
        if self.client is None:
            raise LlmError("клиент OpenAI не сконфигурирован")

        user = build_user_message(original_text, item_raw, oem, photo, retriever,
                                  vehicle_context, translation)
        try:
            response = self.client.complete(
                model=self.model,
                messages=[{"role": "system", "content": self.system_prompt},
                          {"role": "user", "content": user}],
                temperature=self.temperature,
            )
        except LlmError as exc:
            return ArbiterDecision(decision=ARB_ERROR, model=self.model,
                                   reason="ошибка вызова API", error=str(exc))

        data, error = parse_response(response.text, allowed)
        if error:
            return ArbiterDecision(
                decision=ARB_ERROR, model=response.model,
                latency_ms=response.latency_ms, attempts=response.attempts,
                reason=error, error=error, raw_response=response.text[:2000],
            )

        decision = str(data["decision"]).strip().upper()
        return ArbiterDecision(
            decision=decision,
            part_id=data.get("part_id") or None,
            confidence=(float(data["confidence"])
                        if data.get("confidence") is not None else None),
            reason=str(data.get("reason", ""))[:500],
            model=response.model,
            latency_ms=response.latency_ms,
            attempts=response.attempts,
            raw_response=response.text[:2000],
        )
