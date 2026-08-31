"""Валидатор — жёсткие проверки согласованности ПОСЛЕ Arbiter V3.

Валидатор не пытается угадать правильный ответ. Он отвечает на один вопрос:
можно ли отдать решение арбитра как готовое. Каждое правило имеет код, чтобы в
отчёте было видно не только «отклонено», но и чем именно.

===========  =========  ==========================================================
код          действие   правило
===========  =========  ==========================================================
V1           REJECT     выбранного part_id нет в словаре
V2           REJECT     выбранный код отсутствует среди разрешённых кандидатов
V3           DOWNGRADE  запрошенного объекта нет — выбран родитель/сосед
V4           DOWNGRADE  конфликт OEM с выбранной деталью не разрешён безопасно
V5           REJECT     Layer 0 оставил в предмете несколько разных типов деталей
V6           REJECT     состояние конвейера внутренне противоречиво
V7           DOWNGRADE  уверенность ниже порога (точность важнее полноты)
===========  =========  ==========================================================

REJECT переводит итог в ``UNKNOWN``, DOWNGRADE — в ``REVIEW``.
"""

from __future__ import annotations

from .config import MIN_CONFIDENCE_FOR_SELECT
from .retriever import RetrieverV2
from .types import (
    ARB_CLARIFY,
    ARB_ERROR,
    ARB_SELECT,
    ARB_UNKNOWN,
    OEM_CONFLICT,
    VAL_DOWNGRADE,
    VAL_PASS,
    VAL_REJECT,
    ArbiterDecision,
    Layer0Item,
    OemEvidence,
    RetrieverResult,
    ValidatorResult,
)


class Validator:
    def __init__(self, retriever: RetrieverV2,
                 min_confidence: float = MIN_CONFIDENCE_FOR_SELECT,
                 layer0=None):
        self.retriever = retriever
        self.min_confidence = min_confidence
        self.layer0 = layer0

    def validate(self, item: Layer0Item, arbiter: ArbiterDecision,
                 retriever: RetrieverResult, oem: OemEvidence) -> ValidatorResult:
        # --- V6: внутренняя согласованность состояния конвейера --------------
        if arbiter.decision == ARB_ERROR:
            return ValidatorResult(VAL_REJECT, "V6",
                                   f"арбитр вернул ошибку: {arbiter.reason}")
        if arbiter.decision not in {ARB_SELECT, ARB_UNKNOWN, ARB_CLARIFY}:
            return ValidatorResult(VAL_REJECT, "V6",
                                   f"неизвестное решение арбитра: {arbiter.decision!r}")
        if arbiter.decision != ARB_SELECT and arbiter.part_id:
            return ValidatorResult(VAL_REJECT, "V6",
                                   f"решение {arbiter.decision} с непустым part_id")

        # --- V5: Layer 0 не доделил предмет ----------------------------------
        multi = self._still_multiple_items(item)
        if multi:
            return ValidatorResult(
                VAL_REJECT, "V5",
                f"в предмете осталось несколько разных типов деталей: {multi}")

        if arbiter.decision != ARB_SELECT:
            return ValidatorResult(VAL_PASS, "",
                                   f"арбитр не выбирал деталь ({arbiter.decision})")

        part_id = arbiter.part_id or ""

        # --- V1: код существует в словаре ------------------------------------
        if part_id not in self.retriever.dict.parts:
            return ValidatorResult(VAL_REJECT, "V1",
                                   f"part_id {part_id!r} отсутствует в словаре")

        # --- V2: код был среди разрешённых кандидатов -------------------------
        if part_id not in set(retriever.part_ids):
            return ValidatorResult(
                VAL_REJECT, "V2",
                f"part_id {part_id!r} не входит в список кандидатов ретривера")

        # --- V4: неразрешённый конфликт OEM ------------------------------------
        if oem.status == OEM_CONFLICT and oem.resolved_part_id != part_id:
            return ValidatorResult(
                VAL_DOWNGRADE, "V4",
                f"OEM указывает на {oem.resolved_part_id}, а выбрана {part_id} — "
                "конфликт не разрешён")

        # --- V3: выбран родитель/сосед вместо запрошенного объекта ------------
        if not self._supported(item, part_id, oem):
            return ValidatorResult(
                VAL_DOWNGRADE, "V3",
                f"в тексте запроса нет опоры для {part_id} — похоже на "
                "родительский/соседний узел, а не на запрошенную деталь")

        # --- V7: слишком низкая уверенность -----------------------------------
        confidence = arbiter.confidence
        if confidence is None:
            return ValidatorResult(VAL_DOWNGRADE, "V7",
                                   "арбитр не сообщил уверенность")
        if confidence < self.min_confidence:
            return ValidatorResult(
                VAL_DOWNGRADE, "V7",
                f"уверенность {confidence:.2f} ниже порога {self.min_confidence:.2f}")

        return ValidatorResult(VAL_PASS, "", "все проверки пройдены")

    # ── внутреннее ──────────────────────────────────────────────────────────
    def _supported(self, item: Layer0Item, part_id: str, oem: OemEvidence) -> bool:
        """Есть ли у выбранной детали опора в тексте, номере или фото."""
        if oem.resolved_part_id == part_id:
            return True
        return self.retriever.lexical_support(item.item_raw, part_id)

    def _still_multiple_items(self, item: Layer0Item) -> list[str] | None:
        """Разбивается ли ``item_raw`` сегментатором ещё раз.

        Прогоняем Layer 0 повторно уже по атомарному предмету: если он снова
        распадается на несколько предметов, значит первый проход не доделил
        работу, и такой результат нельзя отдавать как готовый.
        """
        if self.layer0 is None:
            return None
        again = self.layer0.segment(item.item_raw)
        if len(again.items) > 1:
            return [i.item_raw for i in again.items]
        return None
