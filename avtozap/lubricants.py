"""Распознавание масел и технических жидкостей по вязкости, а не по марке.

Согласовано с проектом. Покупатель почти всегда пишет марку и вязкость:
``Meguin 0/20``, ``Prista ultra plus 5w-40``, ``Castrol Edge 5W30``. Марка —
слово, которого в словаре нет и быть не должно: список брендов бесконечен,
а заведённые в синонимы они начнут цеплять чужие заявки.

Зато **вязкость** — это формат, а не словарь. ``5w-40``, ``0w20``, ``10W‑40``
однозначно говорят, что речь о масле, независимо от того, какая марка рядом.
Одно правило покрывает и все будущие марки.

Модуль ничего не выбирает сам: он лишь сообщает, что в тексте есть маркер
масла, и отдаёт поисковую подсказку. Итоговый код по-прежнему приходит от
ретривера и проверяется валидатором.
"""

from __future__ import annotations

import re

#: Вязкость моторного масла по SAE: 5w-40, 0W20, 10w 40, 5/30.
#: Разделитель между числами может быть любым (или отсутствовать), потому что
#: покупатели пишут и «0/20», и «5w40», и «10W‑40».
_VISCOSITY_RE = re.compile(
    r"\b(?:0|5|10|15|20|25)\s*[wв]\s*[-‑–/ ]?\s*(?:8|12|16|20|30|40|50|60)\b"
    r"|\b(?:0|5|10|15|20|25)\s*/\s*(?:8|12|16|20|30|40|50|60)\b",
    re.IGNORECASE)

#: Вязкость трансмиссионного масла: 75w-90, 80w90.
_GEAR_VISCOSITY_RE = re.compile(
    r"\b(?:70|75|80|85)\s*[wв]\s*[-‑–/ ]?\s*(?:80|90|110|140)\b",
    re.IGNORECASE)

#: Спецификации тормозной жидкости — тоже формат, а не марка.
_BRAKE_FLUID_RE = re.compile(r"\bdot\s*-?\s*[3456](?:\.1)?\b", re.IGNORECASE)

#: Слова, при которых вязкость относится НЕ к моторному маслу.
#: «Karopka yağı 75w-90» — это трансмиссионное, а не моторное.
_GEARBOX_WORDS = ("karopka", "karobka", "qutu", "suretler", "transmissiya",
                  "korobk", "трансмисс", "коробк", "акпп", "мкпп", "atf")

#: Подсказки для ретривера. Не коды: код всё равно даст ретривер из словаря.
HINT_ENGINE_OIL = "mühərrik yağı"
HINT_GEAR_OIL = "transmissiya yağı"
HINT_BRAKE_FLUID = "əyləc mayesi"


def viscosity_in(text: str) -> str | None:
    """Вернуть найденную вязкость или спецификацию жидкости, иначе ``None``."""
    for pattern in (_VISCOSITY_RE, _GEAR_VISCOSITY_RE, _BRAKE_FLUID_RE):
        found = pattern.search(text or "")
        if found:
            return found.group(0).strip()
    return None


def hint_for(text: str) -> str | None:
    """Поисковая подсказка по маркеру жидкости в тексте.

    ``None`` — маркера нет, правило не применяется. Никакого выбора детали
    здесь не происходит: подсказка идёт в ретривер наравне с текстом запроса.
    """
    text = text or ""
    if _BRAKE_FLUID_RE.search(text):
        return HINT_BRAKE_FLUID
    if _GEAR_VISCOSITY_RE.search(text):
        return HINT_GEAR_OIL
    if _VISCOSITY_RE.search(text):
        lowered = text.lower()
        if any(word in lowered for word in _GEARBOX_WORDS):
            return HINT_GEAR_OIL
        return HINT_ENGINE_OIL
    return None
