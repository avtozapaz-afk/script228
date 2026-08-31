"""Сборка конвейера: одно сырое сообщение → записи по атомарным предметам.

    RAW → Layer 0 → OEM → Photo → Retriever V2 → Arbiter V3 → Validator → итог

Исключение в любом слое не роняет прогон: предмет получает статус ``ERROR`` с
текстом ошибки, и обработка продолжается со следующего.
"""

from __future__ import annotations

import time
from typing import Any

from .arbiter import ArbiterV3
from .config import RunConfig
from .layer0 import Layer0
from .llm import LlmClient
from .oem import OemResolver
from .photo import PhotoLayer
from .retriever import RetrieverV2, content_tokens
from .types import (
    ARB_CLARIFY,
    ARB_ERROR,
    ARB_SELECT,
    ARB_UNKNOWN,
    FINAL_ERROR,
    FINAL_REVIEW,
    FINAL_SELECT,
    FINAL_UNKNOWN,
    L0_EMPTY,
    LAYER_ABSENT,
    LAYER_ARBITER,
    LAYER_L0,
    LAYER_NONE,
    LAYER_OEM,
    LAYER_PIPELINE_ERROR,
    LAYER_RETRIEVER,
    LAYER_VALIDATOR,
    OEM_CONFLICT,
    VAL_DOWNGRADE,
    VAL_PASS,
    VAL_REJECT,
    ArbiterDecision,
    ItemRecord,
    Layer0Item,
    OemEvidence,
    PhotoEvidence,
    RetrieverResult,
    ValidatorResult,
)
from .validator import Validator


class MockArbiter:
    """Детерминированная замена Arbiter V3 для прогонов без API.

    Осознанно осторожна и НЕ моделирует качество настоящего арбитра: выбирает
    кандидата, только если он один заметно сильнее прочих. Нужна для проверки
    механики харнесса (resume, сериализация, отчёты), а не для оценки качества.
    """

    #: Минимальный отрыв лидера, при котором mock решается выбрать.
    MARGIN = 0.10

    def __init__(self, model: str = "mock"):
        self.model = model

    def decide(self, original_text: str, item_raw: str, oem: OemEvidence,
               photo: PhotoEvidence, retriever: RetrieverResult,
               vehicle_context: str = "",
               translation: str | None = None) -> ArbiterDecision:
        candidates = retriever.candidates
        if not candidates:
            return ArbiterDecision(decision=ARB_UNKNOWN, confidence=0.0,
                                   model=self.model,
                                   reason="кандидатов нет")
        top = candidates[0]
        runner_up = candidates[1].score if len(candidates) > 1 else 0.0
        if top.score - runner_up >= self.MARGIN:
            return ArbiterDecision(decision=ARB_SELECT, part_id=top.part_id,
                                   confidence=round(min(top.score, 0.99), 2),
                                   model=self.model,
                                   reason="mock: единственный явный лидер по score")
        tied = [c.part_id for c in candidates if abs(c.score - top.score) < 1e-9]
        return ArbiterDecision(decision=ARB_CLARIFY, confidence=0.3,
                               model=self.model,
                               reason=f"mock: кандидаты неразличимы по score {tied[:5]}")


class Pipeline:
    def __init__(self, config: RunConfig, client: LlmClient | None = None):
        self.config = config
        self.retriever = RetrieverV2.from_file(
            config.dict_path, top_k=config.top_k, min_score=config.min_score)
        self.layer0 = Layer0(self.retriever)
        self.oem = OemResolver.from_file(self.retriever, config.oem_catalog_path)
        self.photo = PhotoLayer(client, model=config.vision_model,
                                enabled=config.enable_photo and client is not None)
        self.validator = Validator(self.retriever, config.min_confidence,
                                   layer0=self.layer0)
        if config.mock:
            self.arbiter: Any = MockArbiter()
        else:
            self.arbiter = ArbiterV3(client, model=config.model,
                                     temperature=config.temperature)

    def process_request(self, row: dict[str, Any], source_index: int) -> list[ItemRecord]:
        rfq_id = str(row.get("rfq_id") or f"row-{source_index}")
        original_text = str(row.get("original_text") or row.get("text") or "")
        image_ref = row.get("image_path") or row.get("image_url")
        expected_ids = expected_part_ids(row)
        expected = ", ".join(expected_ids) if expected_ids else None

        layer0 = self.layer0.segment(original_text)
        if layer0.status == L0_EMPTY or not layer0.items:
            return [ItemRecord(
                source_index=source_index, rfq_id=rfq_id,
                original_text=original_text, item_index=0, item_raw="",
                layer0_status=layer0.status, layer0_reason=layer0.reason,
                layer0_item_count=0, vehicle_context=layer0.vehicle_context,
                oem=OemEvidence(), photo=PhotoEvidence(),
                retriever=RetrieverResult(status="EMPTY", reason="Layer 0 не дал предметов"),
                arbiter=ArbiterDecision(decision=ARB_UNKNOWN, confidence=0.0,
                                        reason="пустое сообщение"),
                validator=ValidatorResult(VAL_PASS, "", "нечего проверять"),
                final_part_id=None, final_status=FINAL_UNKNOWN,
                expected_part_id=expected, failure_layer=LAYER_L0,
            )]

        # Фото относится ко всему запросу — считаем один раз на сообщение.
        photo = self.photo.analyze(image_ref)

        records: list[ItemRecord] = []
        for item in layer0.items:
            records.append(self._process_item(
                source_index, rfq_id, original_text, layer0, item, photo, expected))
        # Эталон задан на весь запрос, а предметов у запроса может быть несколько,
        # поэтому слабое звено определяем по запросу целиком, а не по предмету.
        attribute_failures(records, expected_ids)
        return records

    # ── один атомарный предмет ──────────────────────────────────────────────
    def _process_item(self, source_index: int, rfq_id: str, original_text: str,
                      layer0, item: Layer0Item, photo: PhotoEvidence,
                      expected: str | None) -> ItemRecord:
        started = time.time()

        def record(oem, retrieved, arbiter, validated, final_id, final_status,
                   failure_layer, error=None) -> ItemRecord:
            return ItemRecord(
                source_index=source_index, rfq_id=rfq_id,
                original_text=original_text, item_index=item.item_index,
                item_raw=item.item_raw, layer0_status=item.status,
                layer0_reason=item.reason, layer0_item_count=len(layer0.items),
                vehicle_context=layer0.vehicle_context,
                oem=oem, photo=photo, retriever=retrieved, arbiter=arbiter,
                validator=validated, final_part_id=final_id,
                final_status=final_status, expected_part_id=expected,
                failure_layer=failure_layer,
                latency_ms=int((time.time() - started) * 1000), error=error,
            )

        try:
            oem = self.oem.resolve(item.item_raw, original_text)
            retrieved = self.retriever.retrieve(item.item_raw,
                                                extra_hints=photo.hints or None)
            arbiter = self.arbiter.decide(
                original_text=original_text, item_raw=item.item_raw,
                oem=oem, photo=photo, retriever=retrieved,
                vehicle_context=layer0.vehicle_context)
            validated = self.validator.validate(item, arbiter, retrieved, oem)
        except Exception as exc:                          # noqa: BLE001
            # Один сломанный запрос не должен ронять прогон из 300 кейсов.
            return record(
                OemEvidence(), RetrieverResult(status="EMPTY", reason="исключение"),
                ArbiterDecision(decision=ARB_ERROR, reason=str(exc), error=str(exc)),
                ValidatorResult(VAL_REJECT, "V6", "исключение в конвейере"),
                None, FINAL_ERROR, LAYER_PIPELINE_ERROR,
                error=f"{type(exc).__name__}: {exc}")

        final_id, final_status = _finalize(arbiter, validated)
        # Слабое звено проставит attribute_failures(), когда будут готовы все
        # предметы запроса; здесь фиксируем только безусловные случаи.
        failure = (LAYER_PIPELINE_ERROR if final_status == FINAL_ERROR else LAYER_NONE)
        return record(oem, retrieved, arbiter, validated, final_id, final_status,
                      failure)


def _finalize(arbiter: ArbiterDecision,
              validated: ValidatorResult) -> tuple[str | None, str]:
    """Свести решение арбитра и вердикт валидатора к итоговому статусу."""
    if arbiter.decision == ARB_ERROR:
        return None, FINAL_ERROR
    if validated.status == VAL_REJECT:
        return None, FINAL_ERROR if validated.code == "V6" and arbiter.error else FINAL_UNKNOWN
    if arbiter.decision == ARB_CLARIFY:
        return None, FINAL_REVIEW
    if arbiter.decision == ARB_UNKNOWN:
        return None, FINAL_UNKNOWN
    if validated.status == VAL_DOWNGRADE:
        return arbiter.part_id, FINAL_REVIEW
    return arbiter.part_id, FINAL_SELECT


def expected_part_ids(row: dict[str, Any]) -> list[str]:
    """Эталонные ответы строки входа.

    Принимаем и ``expected_part_id`` (одно значение или список), и
    ``expected_part_ids``. Слова ``UNKNOWN`` / ``NONE`` / ``-`` означают, что
    правильный ответ — честное «не знаю».
    """
    raw = row.get("expected_part_ids", row.get("expected_part_id"))
    if raw is None or raw == "":
        return []
    values = raw if isinstance(raw, (list, tuple)) else str(raw).replace(";", ",").split(",")
    return [str(v).strip() for v in values if str(v).strip()]


def attribute_failures(records: list[ItemRecord], expected_ids: list[str]) -> None:
    """Проставить слабое звено по запросу целиком.

    Порядок разбора взят из задания: виноват первый слой, который потерял
    правильный ответ. Arbiter V3 обвиняем ТОЛЬКО тогда, когда правильный
    кандидат был у него на руках.
    """
    for record in records:
        if record.final_status == FINAL_ERROR:
            record.failure_layer = LAYER_PIPELINE_ERROR
        elif (record.final_status == FINAL_UNKNOWN
              and record.retriever.status == "EMPTY" and not expected_ids):
            record.failure_layer = LAYER_RETRIEVER
        else:
            record.failure_layer = LAYER_NONE

    if not expected_ids:
        return

    produced = {r.final_part_id for r in records if r.final_part_id}

    for expected in expected_ids:
        if expected.upper() in {"UNKNOWN", "NONE", "-"}:
            # Правильный ответ — «не знаю»: провал, только если что-то выбрали.
            for record in records:
                if record.final_status == FINAL_SELECT:
                    record.failure_layer = LAYER_ARBITER
            continue
        if expected in produced:
            continue

        # Ответственным считаем предмет, у которого правильная деталь была
        # среди кандидатов; если такого нет — потерял её ретривер.
        owners = [r for r in records if expected in set(r.retriever.part_ids)]
        if not owners:
            owner = _weakest_item(records)
            if owner is not None:
                owner.failure_layer = (
                    LAYER_L0 if not content_tokens(owner.item_raw) else LAYER_RETRIEVER)
            continue

        owner = owners[0]
        if owner.oem.status == OEM_CONFLICT and owner.validator.code == "V4":
            owner.failure_layer = LAYER_OEM
        elif owner.arbiter.part_id == expected and owner.validator.status != VAL_PASS:
            owner.failure_layer = LAYER_VALIDATOR    # арбитр был прав, зарубил валидатор
        else:
            owner.failure_layer = LAYER_ARBITER      # кандидат был на руках, но не выбран


def _weakest_item(records: list[ItemRecord]) -> ItemRecord | None:
    """Предмет с самым слабым поиском — на него и вешаем промах ретривера."""
    if not records:
        return None
    def strength(record: ItemRecord) -> float:
        scores = [c.score for c in record.retriever.candidates]
        return max(scores) if scores else 0.0
    return min(records, key=strength)
