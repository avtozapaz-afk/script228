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
V7           DOWNGRADE  уверенность `low` (точность важнее полноты)
===========  =========  ==========================================================

REJECT переводит итог в ``UNKNOWN``, DOWNGRADE — в ``REVIEW``.
"""

from __future__ import annotations

from .config import ACCEPTED_CONFIDENCE
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
                 accepted_confidence=frozenset(ACCEPTED_CONFIDENCE),
                 layer0=None):
        self.retriever = retriever
        self.accepted_confidence = frozenset(accepted_confidence)
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
        if arbiter.decision != ARB_SELECT and arbiter.external_code:
            return ValidatorResult(
                VAL_REJECT, "V6",
                f"решение {arbiter.decision} с непустым external_code")

        # --- V5: Layer 0 не доделил предмет ----------------------------------
        multi = self._still_multiple_items(item)
        if multi:
            return ValidatorResult(
                VAL_REJECT, "V5",
                f"в предмете осталось несколько разных типов деталей: {multi}")

        if arbiter.decision != ARB_SELECT:
            return ValidatorResult(VAL_PASS, "",
                                   f"арбитр не выбирал деталь ({arbiter.decision})")

        code = arbiter.external_code or ""

        # --- V1: код существует в словаре ------------------------------------
        if code not in self.retriever.dict:
            return ValidatorResult(
                VAL_REJECT, "V1",
                f"external_code {code!r} отсутствует в словаре 571")

        # --- V2: код был среди разрешённых кандидатов -------------------------
        if code not in set(retriever.codes):
            return ValidatorResult(
                VAL_REJECT, "V2",
                f"external_code {code!r} не входит в shortlist ретривера")

        # --- V4: неразрешённый конфликт OEM ------------------------------------
        if oem.status == OEM_CONFLICT and oem.resolved_external_code != code:
            return ValidatorResult(
                VAL_DOWNGRADE, "V4",
                f"OEM указывает на {oem.resolved_external_code}, а выбран {code} — "
                "конфликт не разрешён")

        # --- V3: выбран родитель/сосед вместо запрошенного объекта ------------
        if not self._supported(item, code, oem):
            return ValidatorResult(
                VAL_DOWNGRADE, "V3",
                f"в тексте запроса нет опоры для {code} — похоже на "
                "родительский/соседний узел, а не на запрошенную деталь")

        # --- V7: слишком низкая уверенность -----------------------------------
        confidence = (arbiter.confidence or "").lower()
        if not confidence:
            return ValidatorResult(VAL_DOWNGRADE, "V7",
                                   "арбитр не сообщил уверенность")
        if confidence not in self.accepted_confidence:
            return ValidatorResult(
                VAL_DOWNGRADE, "V7",
                f"уверенность {confidence!r} ниже принимаемого уровня "
                f"({'/'.join(sorted(self.accepted_confidence))})")

        return ValidatorResult(VAL_PASS, "", "все проверки пройдены")

    # ── внутреннее ──────────────────────────────────────────────────────────
    def _supported(self, item: Layer0Item, code: str, oem: OemEvidence) -> bool:
        """Есть ли у выбранной детали опора в тексте, номере или фото."""
        if oem.resolved_external_code == code:
            return True
        text = " ".join([item.item_raw, *item.search_phrases])
        return self.retriever.lexical_support(text, code)

    def _still_multiple_items(self, item: Layer0Item) -> list[str] | None:
        """Разбивается ли ``item_raw`` на несколько РАЗНЫХ деталей.

        Прогоняем Layer 0 повторно уже по атомарному предмету. Но самого факта
        деления мало: правило должно ловить «наклаdka и тормозной диск», а не
        любую заявку, которую сегментатор захотел порезать.

        Живой прогон 469 показал ровно эту разницу. Заявка
        ``32700-3K070 — PEDAL ASSY - ACCELERATOR`` — это **одна** деталь,
        названная дважды: каталожным номером и по-английски. Сегментатор резал
        её на три куска, правило отвергало верный ответ арбитра, и заявка
        уходила в UNKNOWN. То же с ``ABS bloku 58500 - N6000``: номер считался
        отдельной деталью.

        Поэтому куски сначала приводятся к деталям словаря, и правило
        срабатывает, только если разных деталей действительно несколько. Это та
        же проверка, которой конвейер решает, дорезать предмет или нет
        (``distinct_part_groups``), — правило одно, а не два похожих.
        """
        if self.layer0 is None:
            return None
        again = self.layer0.segment(item.item_raw)
        if len(again.items) < 2:
            return None
        pieces = [i.item_raw for i in again.items]
        if len(distinct_part_groups(pieces, self.retriever)) < 2:
            return None
        return pieces


def distinct_part_groups(pieces: list[str], retriever: RetrieverV2) -> set[str]:
    """На сколько РАЗНЫХ деталей словаря указывают куски предмета.

    Кусок, который ни на что не указывает, группой не считается: каталожный
    номер, английская подпись к нему, обрывок вроде «- N6000» — это не деталь,
    а способ назвать ту же самую. Считать их отдельными предметами значит
    отвергать верные ответы на заявках, где покупатель дал и номер, и название.

    Возвращает множество групп (категорий словаря); пустое — значит куски
    ничего осмысленного не дали.
    """
    groups: set[str] = set()
    for piece in pieces:
        codes = retriever.head_codes(piece)
        if not codes:
            codes = retriever.retrieve(piece).codes[:1]
        if not codes:
            continue
        part = retriever.dict.get(codes[0])
        groups.add(part.category if part else codes[0])
    return groups
