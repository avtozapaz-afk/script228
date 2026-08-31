"""Layer 0 — семантическая сегментация по промпту проекта.

Промпт ``prompts/segmenter_v1.txt`` — точная копия ``AVTOZAP_SEGMENTER_V1.txt``
из поставки, тоже под контрольной суммой. По спецификации это LLM PASS A: модель
только делит сообщение на запрошенные предметы и сохраняет формулировку
покупателя. Она НЕ выбирает ``external_code``, не придумывает канонические
названия и не выдаёт коды — за этим следит разбор ответа.

Когда сети нет (``--mock``, ``--dry-run``) или вызов не удался, работает
детерминированный сегментатор из ``layer0.py``. Это не «упрощённый режим ради
удобства»: сообщение обязано быть разобрано всегда, иначе один сбой API уронил
бы весь прогон, — но в записи честно проставляется, кто именно сегментировал.
"""

from __future__ import annotations

import json
import os
from typing import Any

from .encoding_guard import check as check_encoding
from .config import (
    SEGMENTER_MAX_TOKENS,
    SEGMENTER_PROMPT_PATH,
    SEGMENTER_PROMPT_SHA_PATH,
)
from .llm import LlmClient, LlmError, TruncatedResponse
from .types import L0_FALLBACK, L0_OK, Layer0Item, Layer0Result

#: Источник разбиения — попадает в отчёт, чтобы ошибки Layer 0 были различимы.
SOURCE_LLM = "llm_segmenter_v1"
SOURCE_FALLBACK = "deterministic_fallback"

MAX_ITEMS = 12


def load_prompt(path: str = SEGMENTER_PROMPT_PATH) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def prompt_sha256(path: str = SEGMENTER_PROMPT_PATH) -> str:
    import hashlib
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def frozen_sha256(path: str = SEGMENTER_PROMPT_SHA_PATH) -> str | None:
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return fh.read().strip()


def parse_response(text: str) -> tuple[dict[str, Any], str | None]:
    """Разобрать ответ сегментера строго по контракту промпта."""
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {}, "ответ сегментера не является корректным JSON"
    if not isinstance(data, dict):
        return {}, "ответ сегментера не является объектом JSON"

    items = data.get("items")
    if not isinstance(items, list) or not items:
        return data, "сегментер не вернул ни одного предмета"

    cleaned: list[dict[str, Any]] = []
    for entry in items[:MAX_ITEMS]:
        if not isinstance(entry, dict):
            return data, "элемент items не является объектом"
        raw = str(entry.get("raw") or "").strip()
        if not raw:
            continue
        phrases = entry.get("search_phrases") or []
        if not isinstance(phrases, list):
            phrases = []
        cleaned.append({
            "raw": raw,
            "search_phrases": [str(p).strip() for p in phrases[:3] if str(p).strip()],
            "oem_code": (str(entry["oem_code"]).strip()
                         if entry.get("oem_code") else None),
            "is_part_request": entry.get("is_part_request", True) is not False,
        })
    if not cleaned:
        return data, "у всех предметов пустое поле raw"

    data["items"] = cleaned
    vehicle = data.get("vehicle_context")
    data["vehicle_context"] = str(vehicle).strip() if vehicle else ""
    return data, None


class Segmenter:
    """LLM PASS A с детерминированным запасным вариантом."""

    def __init__(self, client: LlmClient | None, model: str,
                 fallback, temperature: float = 0.0,
                 prompt_path: str = SEGMENTER_PROMPT_PATH):
        self.client = client
        self.model = model
        self.fallback = fallback
        self.temperature = temperature
        self.system_prompt = load_prompt(prompt_path)

    def segment(self, original_text: str) -> Layer0Result:
        text = (original_text or "").strip()
        if not text or self.client is None:
            return self._fallback(text, "клиент недоступен" if text else "")

        try:
            response = self.client.complete(
                model=self.model,
                messages=[{"role": "system", "content": self.system_prompt},
                          {"role": "user", "content": text}],
                temperature=self.temperature,
                max_tokens=SEGMENTER_MAX_TOKENS)
        except TruncatedResponse as exc:
            # Заявка на десяток деталей не уместилась в лимит вывода.
            return self._fallback(text, f"ответ оборван по лимиту: {exc}")
        except LlmError as exc:
            return self._fallback(text, f"ошибка API: {exc}")

        data, error = parse_response(response.text)
        if error:
            return self._fallback(text, error)

        items: list[Layer0Item] = []
        for index, entry in enumerate(data["items"]):
            # Текст пришёл извне: если он испорчен кодировкой, дальше считать
            # бессмысленно — но и молча портить прогон нельзя, поэтому факт
            # фиксируется в записи и попадает в сводку.
            warning = check_encoding(entry["raw"], "ответ сегментера")
            items.append(Layer0Item(
                item_index=index,
                item_raw=entry["raw"],
                status=L0_OK,
                reason=(SOURCE_LLM if entry["is_part_request"]
                        else f"{SOURCE_LLM}: не запрос детали (услуга/ремонт)"),
                search_phrases=entry["search_phrases"],
                oem_code=entry["oem_code"],
                is_part_request=entry["is_part_request"],
                encoding_warning=warning,
                source_fragments=[entry["raw"]],
            ))
        return Layer0Result(
            items=items, status=L0_OK, source=SOURCE_LLM,
            reason=f"{len(items)} предмет(ов) от сегментера",
            vehicle_context=data.get("vehicle_context", ""),
            raw_fragments=[i.item_raw for i in items])

    def _fallback(self, text: str, why: str) -> Layer0Result:
        result = self.fallback.segment(text)
        result.source = SOURCE_FALLBACK
        note = f"детерминированный запасной сегментатор ({why})" if why else result.reason
        result.reason = note
        if result.status == L0_OK:
            result.status = L0_OK if not why else L0_FALLBACK
        for item in result.items:
            item.reason = f"{SOURCE_FALLBACK}: {item.reason}"
        return result
