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
from .segmenter import Segmenter
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
    механики харнесса (resume, сериализация, отчёты) и для того, чтобы весь
    конвейер прогонялся без сети, — но не для оценки качества.
    """

    #: Минимальный отрыв лидера, при котором mock решается выбрать.
    MARGIN = 0.05

    def __init__(self, model: str = "mock"):
        self.model = model

    def decide(self, original_text: str, item_raw: str, oem: OemEvidence,
               photo: PhotoEvidence, retriever: RetrieverResult,
               vehicle_context: str = "", translation: str | None = None,
               search_phrases: list[str] | None = None,
               other_items: list[str] | None = None) -> ArbiterDecision:
        candidates = retriever.candidates
        if not candidates:
            return ArbiterDecision(decision=ARB_UNKNOWN, confidence="low",
                                   model=self.model, reason="кандидатов нет")
        top = candidates[0]
        runner_up = candidates[1].score if len(candidates) > 1 else 0.0
        if top.score - runner_up >= self.MARGIN:
            return ArbiterDecision(
                decision=ARB_SELECT, external_code=top.external_code,
                confidence="high" if top.score >= 0.95 else "medium",
                model=self.model, reason="mock: единственный явный лидер по score")
        tied = [c.external_code for c in candidates
                if abs(c.score - top.score) < 1e-9]
        return ArbiterDecision(
            decision=ARB_CLARIFY, confidence="low", model=self.model,
            reason=f"mock: кандидаты неразличимы по score {tied[:5]}",
            clarification_text="mock: требуется уточнение")


class Pipeline:
    def __init__(self, config: RunConfig, client: LlmClient | None = None):
        self.config = config
        self.retriever = RetrieverV2(limit=config.limit)
        # Детерминированный сегментатор остаётся запасным вариантом Layer 0.
        self.fallback_layer0 = Layer0(self.retriever)
        self.layer0 = Segmenter(
            None if config.mock else client, model=config.segmenter_model,
            fallback=self.fallback_layer0, temperature=config.temperature)
        self.oem = OemResolver.from_file(self.retriever, config.oem_catalog_path)
        self.photo = PhotoLayer(client, model=config.vision_model,
                                enabled=config.enable_photo and client is not None)
        self.validator = Validator(self.retriever, config.accepted_confidence,
                                   layer0=self.fallback_layer0)
        if config.mock:
            self.arbiter: Any = MockArbiter()
        else:
            self.arbiter = ArbiterV3(client, model=config.model,
                                     temperature=config.temperature)

    def process_request(self, row: dict[str, Any], source_index: int) -> list[ItemRecord]:
        rfq_id = str(row.get("rfq_id") or f"row-{source_index}")
        original_text = str(row.get("original_text") or row.get("text") or "")
        image_ref = row.get("image_path") or row.get("image_url")
        expected_ids = expected_codes(row)
        expected = ", ".join(expected_ids) if expected_ids else None

        layer0 = self.layer0.segment(original_text)
        if layer0.status == L0_EMPTY or not layer0.items:
            return [ItemRecord(
                source_index=source_index, rfq_id=rfq_id,
                original_text=original_text, item_index=0, item_raw="",
                layer0_status=layer0.status, layer0_reason=layer0.reason,
                layer0_item_count=0, layer0_source=layer0.source,
                vehicle_context=layer0.vehicle_context,
                oem=OemEvidence(), photo=PhotoEvidence(),
                retriever=RetrieverResult(status="EMPTY", reason="Layer 0 не дал предметов"),
                arbiter=ArbiterDecision(decision=ARB_UNKNOWN, confidence="low",
                                        reason="пустое сообщение"),
                validator=ValidatorResult(VAL_PASS, "", "нечего проверять"),
                final_external_code=None, final_status=FINAL_UNKNOWN,
                expected_external_code=expected, failure_layer=LAYER_L0,
            )]

        # Предмет, в котором осталось две разные детали, дорезаем ДО арбитра:
        # иначе правило MULTI-PART GUARD вернёт clarify, и запрос будет потерян.
        layer0.items = self._resplit_multi_part(layer0.items)

        # Фото относится ко всему запросу — считаем один раз на сообщение.
        photo = self.photo.analyze(image_ref)

        # Остальные предметы сообщения передаются арбитру как факт: ими
        # занимаются свои запуски. Без этого правило MULTI-PART GUARD
        # срабатывало на целом сообщении и отказывало по уже разрезанным
        # предметам (11 из 12 таких отказов в живом прогоне).
        all_items = [i.item_raw for i in layer0.items]

        records: list[ItemRecord] = []
        for item in layer0.items:
            others = [t for j, t in enumerate(all_items) if j != item.item_index]
            records.append(self._process_item(
                source_index, rfq_id, original_text, layer0, item, photo,
                expected, others))
        # Эталон задан на весь запрос, а предметов у запроса может быть несколько,
        # поэтому слабое звено определяем по запросу целиком, а не по предмету.
        attribute_failures(records, expected_ids)
        return records

    def _resplit_multi_part(self, items: list[Layer0Item]) -> list[Layer0Item]:
        """Дорезать предмет, в котором остались две РАЗНЫЕ детали.

        Проверяем детерминированным сегментатором и режем только тогда, когда
        куски указывают на разные группы словаря. «ön bufer arxa bufer» — одна
        деталь в двух положениях, куски дают одну группу, и он остаётся целым;
        «Qabaq arxa apornu ve naklatka» — диск и колодка, разные группы, режем.

        Без этого арбитр честно отвечает clarify по правилу MULTI-PART GUARD, а
        заявка уходит в никуда: в живом прогоне так терялись реальные детали.
        """
        out: list[Layer0Item] = []
        for item in items:
            pieces = self._split_into_distinct_parts(item)
            if not pieces:
                out.append(item)
                continue
            for piece in pieces:
                out.append(Layer0Item(
                    item_index=len(out),
                    item_raw=piece,
                    status=item.status,
                    reason=f"{item.reason}; resplit_multi_part",
                    search_phrases=item.search_phrases,
                    oem_code=item.oem_code,
                    is_part_request=item.is_part_request,
                    encoding_warning=item.encoding_warning,
                    side_hint=item.side_hint,
                    position_hint=item.position_hint,
                    source_fragments=list(item.source_fragments),
                ))
        # Перенумеровать, если ничего не резалось — индексы уже верные.
        for index, item in enumerate(out):
            item.item_index = index
        return out

    def _split_into_distinct_parts(self, item: Layer0Item) -> list[str] | None:
        """Куски предмета, если он распадается на РАЗНЫЕ детали, иначе None."""
        again = self.fallback_layer0.segment(item.item_raw)
        if len(again.items) < 2:
            return None
        groups: set[str] = set()
        pieces: list[str] = []
        for piece in again.items:
            codes = self.retriever.head_codes(piece.item_raw)
            if not codes:
                result = self.retriever.retrieve(piece.item_raw)
                codes = result.codes[:1]
            if not codes:
                return None                  # кусок ни на что не указывает
            part = self.retriever.dict.get(codes[0])
            groups.add(part.category if part else codes[0])
            pieces.append(piece.item_raw)
        return pieces if len(groups) > 1 else None

    # ── один атомарный предмет ──────────────────────────────────────────────
    def _process_item(self, source_index: int, rfq_id: str, original_text: str,
                      layer0, item: Layer0Item, photo: PhotoEvidence,
                      expected: str | None,
                      other_items: list[str] | None = None) -> ItemRecord:
        started = time.time()

        def record(oem, retrieved, arbiter, validated, final_id, final_status,
                   failure_layer, error=None) -> ItemRecord:
            return ItemRecord(
                source_index=source_index, rfq_id=rfq_id,
                original_text=original_text, item_index=item.item_index,
                item_raw=item.item_raw, layer0_status=item.status,
                layer0_reason=item.reason, layer0_item_count=len(layer0.items),
                layer0_source=layer0.source, vehicle_context=layer0.vehicle_context,
                oem=oem, photo=photo, retriever=retrieved, arbiter=arbiter,
                validator=validated, final_external_code=final_id,
                final_status=final_status, expected_external_code=expected,
                search_phrases=list(item.search_phrases),
                is_part_request=item.is_part_request,
                encoding_warning=item.encoding_warning,
                failure_layer=failure_layer,
                latency_ms=int((time.time() - started) * 1000), error=error,
            )

        try:
            oem = self.oem.resolve(item.item_raw, original_text)
            retrieved = self.retriever.retrieve(
                item.item_raw, search_phrases=item.search_phrases,
                extra_hints=photo.hints or None)
            arbiter = self.arbiter.decide(
                original_text=original_text, item_raw=item.item_raw,
                oem=oem, photo=photo, retriever=retrieved,
                vehicle_context=layer0.vehicle_context,
                search_phrases=item.search_phrases,
                other_items=other_items)
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
        return arbiter.external_code, FINAL_REVIEW
    return arbiter.external_code, FINAL_SELECT


def expected_codes(row: dict[str, Any]) -> list[str]:
    """Эталонные ответы строки входа.

    Принимаем и ``expected_part_id`` (одно значение или список), и
    ``expected_part_ids``. Слова ``UNKNOWN`` / ``NONE`` / ``-`` означают, что
    правильный ответ — честное «не знаю».
    """
    raw = row.get("expected_external_code",
                  row.get("expected_part_ids", row.get("expected_part_id")))
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
            record.failure_layer = _blame_empty_shortlist(record)
        else:
            record.failure_layer = LAYER_NONE

    if not expected_ids:
        return

    produced = {r.final_external_code for r in records if r.final_external_code}

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
        owners = [r for r in records if expected in set(r.retriever.codes)]
        if not owners:
            owner = _weakest_item(records)
            if owner is not None:
                owner.failure_layer = _blame_empty_shortlist(owner)
            continue

        owner = owners[0]
        if owner.oem.status == OEM_CONFLICT and owner.validator.code == "V4":
            owner.failure_layer = LAYER_OEM
        elif owner.arbiter.external_code == expected and owner.validator.status != VAL_PASS:
            owner.failure_layer = LAYER_VALIDATOR    # арбитр был прав, зарубил валидатор
        else:
            owner.failure_layer = LAYER_ARBITER      # кандидат был на руках, но не выбран


def _blame_empty_shortlist(record: ItemRecord) -> str:
    """Кто виноват, когда ретривер не дал ни одного кандидата.

    Пустой shortlist — ещё не промах ретривера. Чаще это значит, что до него
    доехало то, что деталью и не было: спецификация автомобиля, вежливый хвост
    или голый номер. Свалить всё на ретривер значило бы отправить чинить не тот
    слой, а ради этого разбора прогон и делается.
    """
    if not record.is_part_request:
        return LAYER_L0
    # Заявка из одного лишь артикула: текстом искать нечего, а разрешить номер
    # не по чему — каталог OEM в проект не передавался, статус UNRESOLVED.
    if record.oem.numbers and _is_bare_number(record):
        return LAYER_OEM
    if not content_tokens(record.item_raw):
        return LAYER_L0
    return LAYER_RETRIEVER


def _is_bare_number(record: ItemRecord) -> bool:
    """Состоит ли предмет только из номера детали, без слов."""
    tokens = content_tokens(record.item_raw)
    if not tokens:
        return True
    numbers = {"".join(ch for ch in n.lower() if ch.isalnum())
               for n in record.oem.numbers}
    return all(any(token in number for number in numbers) for token in tokens)


def _weakest_item(records: list[ItemRecord]) -> ItemRecord | None:
    """Предмет с самым слабым поиском — на него и вешаем промах ретривера."""
    if not records:
        return None
    def strength(record: ItemRecord) -> float:
        scores = [c.score for c in record.retriever.candidates]
        return max(scores) if scores else 0.0
    return min(records, key=strength)
