"""Что делать, когда система не уверена. Правило заказчика.

Раньше конвейер знал два исхода: код или ``UNKNOWN``. Заказчик задал третий —
не угадывать, а разговаривать:

* уверенность ниже порога → **переспросить** покупателя, попросив описать
  деталь иначе;
* со второго раза не поняли → **отдать заявку сырым текстом магазинам**. Пусть
  продавец прочитает своими глазами: это честнее, чем подставить не ту деталь и
  выдать неверную цену;
* покупатель сам пишет, что не знает названия → **просить фотографию**. Фото
  доходит до магазина вместе с заявкой, так что это рабочий выход, а не тупик.

Модуль решает ровно один вопрос: какое действие следует из результата
конвейера. Он ничего не выбирает и не отменяет решений арбитра — только
переводит их в действие, которое поймёт бот.
"""

from __future__ import annotations

import re

from .types import (
    ARB_CLARIFY,
    ARB_ERROR,
    ARB_SELECT,
    ARB_UNKNOWN,
    FINAL_ERROR,
    FINAL_REVIEW,
    FINAL_SELECT,
    FINAL_UNKNOWN,
    ArbiterDecision,
    ValidatorResult,
    VAL_DOWNGRADE,
    VAL_PASS,
    VAL_REJECT,
)

# ── действия ────────────────────────────────────────────────────────────────
#: Деталь определена, код отдаётся дальше.
ACTION_ANSWER = "ANSWER"
#: Переспросить покупателя: попросить описать деталь другими словами.
ACTION_ASK_BUYER = "ASK_BUYER"
#: Попросить фотографию — покупатель сам не знает названия.
ACTION_ASK_PHOTO = "ASK_PHOTO"
#: Отдать заявку сырым текстом магазинам, пусть продавец прочитает глазами.
ACTION_PASS_TO_SHOP = "PASS_TO_SHOP"
#: Сбой конвейера — не ответ и не переспрос, а разбор.
ACTION_ERROR = "ERROR"

ACTIONS = (ACTION_ANSWER, ACTION_ASK_BUYER, ACTION_ASK_PHOTO,
           ACTION_PASS_TO_SHOP, ACTION_ERROR)

#: Покупатель прямо пишет, что не знает названия детали.
#: Фразы взяты из живых заявок, а не придуманы.
_NO_NAME_PATTERNS = (
    r"ad[ıi]n[ıi]?\s*bilmir",          # «adını bilmirəm» — не знаю названия
    r"ad[ıi]\s*bilmirem",
    r"basqa\s*ad[ıi]",                 # «başqa adı bilmirəm»
    r"n[əe]\s*ad?lan[ıi]r",            # «nə adlanır» — как называется
    r"n[əe]dir\s*bilmirem",
    r"не\s*зна[юе][^.]{0,12}назы",     # «не знаю как называется»
    r"как\s*назы\w*\s*не\s*зна",
    r"не\s*зна[юе]\s*назван",
    r"шлю\s*фото|отправил\s*фото|фото\s*отправ",
    r"[şs][əe]kil\s*(g[öo]nd[əe]r|at)",   # «şəkil göndərdim» — отправил фото
    r"foto\s*(g[öo]nd[əe]r|at)",
)
_NO_NAME_RE = re.compile("|".join(_NO_NAME_PATTERNS), re.IGNORECASE)


def buyer_says_they_do_not_know_the_name(text: str) -> bool:
    """Покупатель сам признаёт, что не знает названия детали."""
    return bool(text) and bool(_NO_NAME_RE.search(text))


def decide_action(final_status: str, arbiter: ArbiterDecision,
                  validator: ValidatorResult, original_text: str,
                  attempt: int = 1, has_photo: bool = False) -> tuple[str, str]:
    """Вернуть ``(действие, причина)``.

    ``attempt`` — какой это заход по этой заявке. На первом непонятном заходе
    переспрашиваем, на втором отдаём магазинам: держать покупателя в переписке
    дольше одного уточнения заказчик не хочет.
    """
    if final_status == FINAL_ERROR:
        return ACTION_ERROR, f"сбой конвейера: {arbiter.reason or validator.reason}"

    if final_status == FINAL_SELECT:
        return ACTION_ANSWER, "деталь определена"

    # Дальше — все случаи, когда кода нет.
    if not has_photo and buyer_says_they_do_not_know_the_name(original_text):
        return (ACTION_ASK_PHOTO,
                "покупатель пишет, что не знает названия — нужна фотография")

    if attempt >= 2:
        return (ACTION_PASS_TO_SHOP,
                "со второго раза деталь не определена — отдаём сырой текст "
                "магазинам, чтобы продавец прочитал сам")

    if arbiter.decision == ARB_CLARIFY:
        return ACTION_ASK_BUYER, (arbiter.clarification_text
                                  or arbiter.reason
                                  or "нужно уточнение")
    if validator.status == VAL_DOWNGRADE:
        return ACTION_ASK_BUYER, f"низкая надёжность выбора: {validator.reason}"
    if validator.status == VAL_REJECT:
        return ACTION_ASK_BUYER, f"ответ не прошёл проверку: {validator.reason}"
    return ACTION_ASK_BUYER, (arbiter.reason
                              or "деталь не определена — просим описать иначе")


def question_for(action: str, reason: str) -> str | None:
    """Текст, который бот покажет покупателю. ``None`` — писать нечего."""
    if action == ACTION_ASK_PHOTO:
        return ("Не могу понять деталь по описанию. Пришлите, пожалуйста, "
                "фотографию — так магазин точно поймёт, что нужно.")
    if action == ACTION_ASK_BUYER:
        return ("Не могу однозначно определить деталь. Опишите её, пожалуйста, "
                "другими словами — или пришлите фото.")
    if action == ACTION_PASS_TO_SHOP:
        return None          # покупателю ничего не пишем, заявка уходит дальше
    return None
