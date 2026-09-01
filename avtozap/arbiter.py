"""Arbiter V3 — ЗАМОРОЖЕН.

Промпт в ``prompts/arbiter_v3.txt`` — точная копия
``01_PIPELINE/ARBITER_V3_PROMPT.txt`` из поставки проекта, байт в байт. Он
защищён контрольной суммой в ``arbiter_v3.sha256``, и тест
``test_prompt_is_frozen`` падает при любом расхождении.

Контракт ответа задан самим промптом и здесь только исполняется::

    {"decision":"select|unknown|clarify","external_code":null,
     "confidence":"high|medium|low","reason":"max 18 words",
     "clarification_text":null}

Модуль не «дорабатывает» решение: если модель вернула код, которого не было
среди кандидатов, это ошибка формата, попадающая в отчёт, а не повод подставить
что-то похожее. Проверки согласованности — отдельный слой, валидатор.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any

from .config import (
    ARBITER_MAX_TOKENS,
    ARBITER_PROMPT_PATH,
    ARBITER_PROMPT_SHA_PATH,
    ARBITER_V4_PROMPT_PATH,
    ARBITER_V4_PROMPT_SHA_PATH,
    DEFAULT_ARBITER_MODEL,
    DEFAULT_TEMPERATURE,
)
from .llm import LlmClient, LlmError, TruncatedResponse
from .types import (
    ARB_CLARIFY,
    ARB_ERROR,
    ARB_SELECT,
    ARB_UNKNOWN,
    CONFIDENCE_LEVELS,
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
                       translation: str | None = None,
                       search_phrases: list[str] | None = None,
                       other_items: list[str] | None = None) -> str:
    """Вход арбитра. Список кандидатов — единственный источник допустимых кодов.

    Порядок полей не косметика. Промпт V3 судит **item_raw** («If item_raw
    itself clearly contains TWO DIFFERENT requested part types… return
    clarify»), но про ``original_text`` в нём не сказано ни слова — это поле
    добавляет харнесс. В живом прогоне из 12 отказов «в запросе две разные
    детали» 11 пришлись на предметы, которые Layer 0 уже разрезал верно:
    ``item_raw="tormuz disk"`` получал clarify со ссылкой на «Naklatka и tormuz
    disk», то есть модель применяла правило к целому сообщению.

    Поэтому ``item_raw`` идёт первым, а остальные предметы того же сообщения
    передаются отдельным полем — как факт, что ими занимаются свои запуски.
    Это данные, а не указания: замороженный промпт не меняется.
    """
    payload: dict[str, Any] = {
        "item_raw": item_raw,
        "original_text": original_text,
        "candidates": [
            {
                "external_code": c.external_code,
                "name_az": c.name_az,
                "name_ru": c.name_ru,
                "category": c.category,
                "synonyms": c.synonyms,
                "retrieval_score": c.score,
                "retrieval_reason": c.reason,
            }
            for c in retriever.candidates
        ],
    }
    if other_items:
        payload["other_items_in_same_message"] = other_items
    if translation:
        payload["translation"] = translation
    if search_phrases:
        payload["search_phrases"] = search_phrases
    if vehicle_context:
        payload["vehicle_context"] = vehicle_context

    payload["oem"] = {
        "status": oem.status,
        "numbers": oem.numbers,
        "resolved_external_code": oem.resolved_external_code,
        "note": oem.reason,
    }
    payload["photo"] = {"status": photo.status, "evidence": photo.summary or None}
    return json.dumps(payload, ensure_ascii=False, indent=2)


def parse_response(text: str, allowed_codes: set[str]) -> tuple[dict[str, Any], str | None]:
    """Разобрать ответ арбитра строго по контракту промпта.

    Возвращает ``(данные, ошибка)``; ошибка — это факт для отчёта, а не сигнал
    что-то исправить за модель.
    """
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {}, "ответ арбитра не является корректным JSON"
    if not isinstance(data, dict):
        return {}, "ответ арбитра не является объектом JSON"

    decision = str(data.get("decision", "")).strip().lower()
    if decision not in _VALID_DECISIONS:
        return data, f"недопустимое решение {data.get('decision')!r}"

    code = data.get("external_code")
    if code is not None and not isinstance(code, str):
        return data, "external_code должен быть строкой либо null"
    code = (code or "").strip() or None

    if decision == ARB_SELECT:
        if not code:
            return data, "select без external_code"
        if code not in allowed_codes:
            return data, f"external_code {code!r} отсутствует среди кандидатов"
    elif code:
        return data, f"решение {decision} не должно содержать external_code"

    confidence = data.get("confidence")
    if confidence is not None:
        if str(confidence).strip().lower() not in CONFIDENCE_LEVELS:
            return data, (f"confidence {confidence!r} вне набора "
                          f"{'/'.join(CONFIDENCE_LEVELS)}")
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
               vehicle_context: str = "", translation: str | None = None,
               search_phrases: list[str] | None = None,
               other_items: list[str] | None = None) -> ArbiterDecision:
        allowed = set(retriever.codes)
        if not allowed:
            # Кандидатов нет — выбирать не из чего, звать модель незачем.
            return ArbiterDecision(
                decision=ARB_UNKNOWN, confidence="low", model=self.model,
                reason="ретривер не нашёл ни одного кандидата в словаре")
        if self.client is None:
            raise LlmError("клиент OpenAI не сконфигурирован")

        user = build_user_message(original_text, item_raw, oem, photo, retriever,
                                  vehicle_context, translation, search_phrases,
                                  other_items)
        try:
            response = self.client.complete(
                model=self.model,
                messages=[{"role": "system", "content": self.system_prompt},
                          {"role": "user", "content": user}],
                temperature=self.temperature,
                max_tokens=ARBITER_MAX_TOKENS)
        except TruncatedResponse as exc:
            return ArbiterDecision(decision=ARB_ERROR, model=self.model,
                                   reason=f"ответ арбитра оборван: {exc}",
                                   error=str(exc))
        except LlmError as exc:
            return ArbiterDecision(decision=ARB_ERROR, model=self.model,
                                   reason="ошибка вызова API", error=str(exc))

        data, error = parse_response(response.text, allowed)
        if error:
            return ArbiterDecision(
                decision=ARB_ERROR, model=response.model,
                latency_ms=response.latency_ms, attempts=response.attempts,
                reason=error, error=error, raw_response=response.text[:2000])

        confidence = data.get("confidence")
        return ArbiterDecision(
            decision=str(data["decision"]).strip().lower(),
            external_code=(data.get("external_code") or None),
            confidence=(str(confidence).strip().lower() if confidence else None),
            reason=str(data.get("reason", ""))[:500],
            clarification_text=(str(data["clarification_text"])[:500]
                                if data.get("clarification_text") else None),
            model=response.model, latency_ms=response.latency_ms,
            attempts=response.attempts, raw_response=response.text[:2000])


class ArbiterV4(ArbiterV3):
    """Arbiter V4 — та же механика вызова, другой системный промпт.

    Заказчик разрешил менять промпт и логику арбитра, но менять их надо
    **рядом с V3**, а не вместо него: иначе сравнить две версии будет не с чем.
    Поэтому V4 наследует весь обвес — сборку сообщения, разбор ответа, контракт
    и обработку ошибок, — и отличается ровно тем, чем должен: текстом правил.

    Промпт V4 не заморожен, но его контрольная сумма ведётся и попадает в
    отчёт: любая правка обязана быть видимой, иначе два прогона нельзя будет
    сопоставить.

    Что изменено против V3 и почему — каждое изменение отвечает измеренному
    классу ошибок живого прогона 200, а не отдельной заявке:

    * **Судить только ``item_raw``.** Из отказов «в запросе две детали» часть
      приходилась на предметы, которые Layer 0 уже разрезал верно: модель
      применяла правило к целому сообщению. В V4 сказано прямо, что остальные
      предметы разбираются своими запусками.
    * **Первый кандидат — ответ по умолчанию.** В восьми ошибках верный код
      стоял первым, и модель уходила от него к «более типичной» детали. Теперь
      уйти от первого можно, только назвав слово из запроса, которое покрывает
      другой кандидат.
    * **Точный термин и OEM — факты, а не подсказки.** В payload уже лежат
      ``retrieval_reason`` вида ``exact:`` / ``context:`` и разрешённый номер
      OEM; V3 про них не знал и называл ранг «лишь подсказкой».
    * **Отказ на «пакет/марка/не знаю названия».** Два опасных ответа прогона
      были именно там, где деталь не названа вовсе.
    """

    def __init__(self, client, model: str = DEFAULT_ARBITER_MODEL,
                 temperature: float = DEFAULT_TEMPERATURE,
                 prompt_path: str = ARBITER_V4_PROMPT_PATH):
        super().__init__(client, model=model, temperature=temperature,
                         prompt_path=prompt_path)
        self.version = "v4"


#: Версия арбитра -> (класс, путь к промпту, путь к контрольной сумме).
ARBITERS = {
    "v3": (ArbiterV3, ARBITER_PROMPT_PATH, ARBITER_PROMPT_SHA_PATH),
    "v4": (ArbiterV4, ARBITER_V4_PROMPT_PATH, ARBITER_V4_PROMPT_SHA_PATH),
}


def build_arbiter(version: str, client, model: str = DEFAULT_ARBITER_MODEL,
                  temperature: float = DEFAULT_TEMPERATURE):
    """Собрать арбитра нужной версии. Неизвестная версия — ошибка, не догадка."""
    key = (version or "v3").strip().lower()
    if key not in ARBITERS:
        raise ValueError(f"неизвестная версия арбитра: {version!r}; "
                         f"есть {sorted(ARBITERS)}")
    cls, prompt_path, _ = ARBITERS[key]
    return cls(client, model=model, temperature=temperature,
               prompt_path=prompt_path)
