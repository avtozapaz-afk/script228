"""Разговор с OpenAI: разобрать вопрос и сложить фразу ответа.

Языковая модель здесь делает ровно две вещи:

1. переводит вопрос человека в понятный программе план (какой раздел, какой
   период, что посчитать);
2. складывает готовые цифры в человеческую фразу.

**Считает всегда программа.** Никаких чисел модель не придумывает: в ответ
ей передаются уже посчитанные значения, а сами данные остаются на компьютере.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from typing import Any

import requests

from . import config
from .api import Stopped

API_URL = "https://api.openai.com/v1/chat/completions"

# Если названная модель недоступна, пробуем следующую из списка.
MODEL_FALLBACKS = ("gpt-4o-mini", "gpt-4.1-mini", "gpt-4o", "gpt-3.5-turbo")

OPERATIONS = ("посчитать", "последняя_дата", "список", "топ", "без_пары")

PERIOD_KINDS = ("сегодня", "вчера", "неделя", "месяц", "диапазон", "всё")

PLANNER_RULES = """Ты помощник в программе, которая читает данные админки магазина автозапчастей.
Твоя задача — превратить вопрос сотрудника в план работы для программы. Считать ничего не нужно:
все подсчёты делает программа сама.

Отвечай ТОЛЬКО одним объектом JSON такого вида:
{
  "понятно": true или false,
  "уточнение": "если непонятно — чего именно не хватает, по-русски, обращаясь к сотруднику",
  "операция": "посчитать" | "последняя_дата" | "список" | "топ" | "без_пары",
  "раздел": "ключ раздела из списка ниже",
  "раздел_2": "ключ второго раздела или null (нужен только для операции без_пары)",
  "ключ": "колонка раздела_2, которая ссылается на раздел, или null",
  "ключ_2": "колонка раздела, на которую ссылаются (обычно id), или null",
  "период_к_разделу": true или false,
  "период": {"вид": "сегодня|вчера|неделя|месяц|диапазон|всё", "с": "ГГГГ-ММ-ДД или null", "по": "ГГГГ-ММ-ДД или null"},
  "поиск": {"текст": "что искать или null", "варианты": ["написание по-русски", "написание латиницей"],
            "колонка": "колонка или null", "раздел": "раздел, где лежит это название, или null",
            "ключ": "колонка основного раздела, ссылающаяся на тот раздел, или null"},
  "группировка": "колонка, по которой считать топ, или null",
  "подпись": "колонка с человеческим названием для топа, или null",
  "предел": число или null,
  "в_файл": true или false
}

Что означают операции:
- "посчитать" — сколько записей в разделе за период (при необходимости с поиском);
- "последняя_дата" — когда была самая свежая запись, подходящая под поиск (например, последняя активность магазина);
- "список" — выдать записи (для «выгрузи…», «покажи список…»); ставь "в_файл": true;
- "топ" — сгруппировать по колонке и выдать самых частых (например, топ магазинов по числу откликов);
- "без_пары" — записи раздела, на которые НЕТ ссылок из раздела_2 (например, заявки без единого отклика
  или магазины, ни разу не ответившие: раздел=магазины, раздел_2=отклики, ключ=колонка магазина в откликах,
  ключ_2=id магазина).

Правила:
- "раздел" и все колонки бери ТОЛЬКО из списка ниже, дословно. Ничего не выдумывай.
- Даты не вычисляй: для «сегодня», «вчера», «за неделю», «за месяц» просто ставь нужный вид периода.
  Точные даты ("диапазон") заполняй, только если сотрудник назвал их сам.
- Если период не назван — ставь вид "всё".
- В "варианты" всегда клади оба написания названий марок, моделей и магазинов: по-русски и латиницей
  (например ["тойота королла", "toyota corolla"]).
- Если название (магазина, марки) хранится в другом разделе, а в нужном лежит только его номер,
  заполни "поиск.раздел" (где лежит название) и "поиск.ключ" (колонка со ссылкой на него).
  Например «сколько откликов дал магазин Автомир»: раздел=отклики, поиск.раздел=магазины,
  поиск.ключ=store_id.
- Если вопрос не про эти данные, непонятен или нужного раздела нет в списке — ставь "понятно": false
  и напиши в "уточнение", чего не хватает. Не подбирай похожий раздел наугад.
- "период_к_разделу" ставь true, если период относится к самим записям раздела (заявки за неделю),
  и false, если период относится к связанным записям (магазины, не отвечавшие за месяц)."""

WRITER_RULES = """Ты помогаешь программе ответить сотруднику по-русски.
Тебе дают вопрос и уже посчитанные программой данные.

Строгие правила:
- Пиши только то, что есть в данных. НИКАКИХ своих чисел, оценок и догадок.
- Все числа переписывай из данных дословно.
- Отвечай коротко: одна-три фразы, простым человеческим языком.
- Не упоминай ни разделы админки, ни названия колонок, ни файлы, ни техническую кухню.
- Если в данных сказано, что чего-то нет — так и напиши, не смягчая и не придумывая замену."""


class AiError(Exception):
    """Понятная человеку ошибка обращения к OpenAI."""


class NoKey(AiError):
    """Ключ OpenAI ещё не сохранён."""


def _clean_json(text: str) -> str:
    """Убрать обрамление ```json, если модель его добавила."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


class AiClient:
    """Тонкая обёртка над OpenAI. Ключ наружу не выдаёт и в журнал не пишет."""

    def __init__(
        self,
        api_key: str,
        *,
        model: str | None = None,
        timeout: float = 60.0,
        logger: logging.Logger | None = None,
        stop_event: threading.Event | None = None,
    ) -> None:
        if not api_key or not api_key.strip():
            raise NoKey("Не сохранён ключ OpenAI")
        self._key = api_key.strip()
        self.log = logger or logging.getLogger("avtozap")
        self.timeout = timeout
        self.stop_event = stop_event or threading.Event()
        first = (model or "").strip()
        self.models = [first] if first else []
        self.models += [name for name in MODEL_FALLBACKS if name != first]
        self.model = self.models[0]

    # ------------------------------------------------------------------ запрос

    def _post(self, payload: dict) -> dict:
        try:
            response = requests.post(
                API_URL,
                headers={
                    "Authorization": f"Bearer {self._key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self.timeout,
            )
        except requests.exceptions.Timeout as error:
            raise AiError("Не дождался ответа — попробуйте спросить ещё раз") from error
        except requests.exceptions.RequestException as error:
            raise AiError("Нет интернета — не смог обратиться за разбором вопроса") from error

        if response.status_code == 401:
            raise AiError("Ключ OpenAI не подошёл — проверьте его и введите заново")
        if response.status_code == 429:
            detail = response.text.lower()
            if "quota" in detail or "billing" in detail:
                raise AiError("На ключе OpenAI закончились средства")
            raise AiError("Слишком много запросов подряд — подождите немного")
        if response.status_code == 404:
            raise LookupError("модель недоступна")
        if response.status_code >= 500:
            raise AiError("OpenAI сейчас недоступен — попробуйте позже")
        if response.status_code >= 400:
            raise AiError(f"Не удалось разобрать вопрос (ответ {response.status_code})")

        try:
            return response.json()
        except ValueError as error:
            raise AiError("Пришёл непонятный ответ") from error

    def _ask(self, system: str, user: str, *, as_json: bool, temperature: float) -> str:
        """Спросить модель, при необходимости перебрав запасные."""
        if self.stop_event.is_set():
            raise Stopped()
        last_error: Exception | None = None
        for model in list(self.models):
            payload: dict[str, Any] = {
                "model": model,
                "temperature": temperature,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            }
            if as_json:
                payload["response_format"] = {"type": "json_object"}
            try:
                data = self._post(payload)
            except LookupError as error:  # эта модель недоступна — берём следующую
                last_error = error
                self.log.info("Модель %s недоступна, пробую следующую", model)
                continue
            if self.stop_event.is_set():
                raise Stopped()
            self.model = model
            try:
                return data["choices"][0]["message"]["content"] or ""
            except (KeyError, IndexError, TypeError) as error:
                raise AiError("Пришёл пустой ответ") from error
        raise AiError("Ни одна из моделей OpenAI не доступна по этому ключу") from last_error

    # ------------------------------------------------------------------ разбор

    def plan(self, question: str, catalog: str, today: str) -> dict:
        """Превратить вопрос в план работы для программы."""
        user = (
            f"Сегодня {today}.\n\n"
            f"Разделы админки и их колонки:\n{catalog}\n\n"
            f"Вопрос сотрудника: {question}"
        )
        raw = self._ask(PLANNER_RULES, user, as_json=True, temperature=0)
        try:
            plan = json.loads(_clean_json(raw))
        except ValueError as error:
            raise AiError("Не удалось разобрать вопрос — попробуйте сказать иначе") from error
        if not isinstance(plan, dict):
            raise AiError("Не удалось разобрать вопрос — попробуйте сказать иначе")
        return plan

    def phrase(self, question: str, facts: dict) -> str:
        """Сложить человеческую фразу из уже посчитанных программой данных."""
        user = (
            f"Вопрос сотрудника: {question}\n\n"
            f"Что посчитала программа (только эти числа можно использовать):\n"
            f"{json.dumps(facts, ensure_ascii=False, indent=2)}"
        )
        text = self._ask(WRITER_RULES, user, as_json=False, temperature=0.2)
        return text.strip()


def get_key() -> str | None:
    """Ключ OpenAI из ``.env``, если он там есть."""
    value = config.read_env().get(config.OPENAI_KEY_NAME, "").strip()
    return value or None


def save_key(key: str) -> None:
    """Сохранить ключ рядом с логином и паролем."""
    config.save_value(config.OPENAI_KEY_NAME, key.strip())
