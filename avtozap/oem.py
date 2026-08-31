"""OEM / part-number резолвер. Работает ДО Retriever V2.

Отвечает на два разных вопроса и никогда их не смешивает:

1. **Извлечение** — есть ли в сообщении что-то, похожее на номер детали, и не
   является ли это на самом деле VIN, годом, кодом кузова, объёмом двигателя,
   размером шины или телефоном.
2. **Разрешение** — можно ли этот номер сопоставить с деталью, и согласуется ли
   результат с тем, что человек написал словами.

Статусы:

``MATCH``       номер разрешён и указывает на тот же объект, что и текст;
``CONFLICT``    номер разрешён, но указывает на ДРУГОЙ объект, чем текст;
``UNRESOLVED``  похоже на номер, но сопоставить не с чем;
``NONE``        полезного номера в сообщении нет.

Ключевое правило: **номер никогда молча не перебивает текст**. При расхождении
статус ``CONFLICT`` доезжает до валидатора, который отправляет случай на
разбор, — вместо тихого выбора «детали по номеру».

Каталог номеров (``data/oem_catalog.json``) в поставке отсутствует. Пока его
нет, любой извлечённый номер честно получает ``UNRESOLVED``: выдумывать
соответствие номера детали запрещено. Формат файла, когда он появится::

    {"06A115561B": {"external_code": "MU-028"}, "1K0615301AA": {"external_code": "EY-002"}}
"""

from __future__ import annotations

import json
import os
import re

from .retriever import RetrieverV2

# Азербайджанские заглавные, которые str.lower() обрабатывает неверно.
_AZ_LOWER = str.maketrans({
    "İ": "i", "I": "ı", "Ə": "ə", "Ç": "ç", "Ş": "ş", "Ğ": "ğ", "Ö": "ö", "Ü": "ü",
})


def az_lower(text: str) -> str:
    """Нижний регистр С СОХРАНЕНИЕМ пунктуации.

    Номера деталей и типоразмеры шин разбираются вместе со слэшами, точками и
    дефисами («205/55R16», «1K0-615-301»), поэтому здесь нельзя использовать
    словарную нормализацию: она вычищает все небуквенные знаки.
    """
    return str(text or "").translate(_AZ_LOWER).lower()
from .types import OEM_CONFLICT, OEM_MATCH, OEM_NONE, OEM_UNRESOLVED, OemEvidence

# Кандидат в номер: «слово» из букв/цифр/разделителей, где есть хотя бы одна цифра.
_TOKEN_RE = re.compile(r"[0-9A-Za-zА-Яа-я][0-9A-Za-zА-Яа-я._/-]{3,}")
_YEAR_RE = re.compile(r"^(19[5-9]\d|20[0-4]\d)$")
_CHASSIS_RE = re.compile(r"^[a-z]{1,2}\d{2,3}$")
_TIRE_RE = re.compile(r"^\d{3}[/-]\d{2}[a-z]?\d{2}$")
_ENGINE_RE = re.compile(r"^\d[.,]\d$|^\d{2}v$|^\d{3,4}cc$")
_PHONE_RE = re.compile(r"^\+?(994|0)\d{8,11}$")
_VIN_RE = re.compile(r"^[a-hj-npr-z0-9]{17}$")

#: Минимум буквенно-цифровых знаков, чтобы считать строку номером детали.
_MIN_ALNUM = 5


def _alnum(text: str) -> str:
    return "".join(ch for ch in text if ch.isalnum())


def classify_number(token: str) -> str | None:
    """Причина отбраковки, либо ``None`` если токен похож на номер детали."""
    low = az_lower(token).strip(".,;:")
    bare = _alnum(low)
    if not bare or not any(ch.isdigit() for ch in bare):
        return "нет цифр"
    if _PHONE_RE.match(bare):
        return "телефон"
    if _VIN_RE.match(bare) and not bare.isdigit():
        return "VIN (17 знаков)"
    if _YEAR_RE.match(bare):
        return "год выпуска"
    if _TIRE_RE.match(low.replace(" ", "")):
        return "типоразмер шины"
    if _ENGINE_RE.match(low):
        return "объём/тип двигателя"
    if _CHASSIS_RE.match(bare):
        return "код кузова/модели"
    if len(bare) < _MIN_ALNUM:
        return f"слишком коротко (<{_MIN_ALNUM} знаков)"
    if bare.isdigit() and len(bare) < 6:
        return "просто число"
    return None


#: Группа номера, записанного через пробел: «1K0 615 301 AA», «82 00 123 456».
_GROUP_RE = re.compile(r"^[0-9A-Za-z]{1,5}$")
_MIN_JOINED_ALNUM = 8
_MIN_JOINED_DIGITS = 5


def _is_group(word: str) -> bool:
    """Короткая группа номера: с цифрой, либо буквенный суффикс вроде «AA»."""
    if not _GROUP_RE.match(word):
        return False
    return any(ch.isdigit() for ch in word) or len(word) <= 2


def _joined_candidates(text: str) -> list[str]:
    """Номера, записанные группами через пробел, склеенные в один ключ.

    Заводские номера часто печатают группами (VW ``1K0 615 301 AA``, Renault
    ``82 00 123 456``). Берём МАКСИМАЛЬНЫЕ цепочки таких групп и отсекаем всё,
    что группами лишь притворяется: перечисление годов, «код кузова + год»
    и телефонные номера.
    """
    out: list[str] = []
    words = [w.strip(".,;:") for w in (text or "").split()]
    i = 0
    while i < len(words):
        if not _is_group(words[i]):
            i += 1
            continue
        j = i
        while j < len(words) and _is_group(words[j]):
            j += 1
        run, i = words[i:j], j
        if len(run) < 2:
            continue
        bare_words = [_alnum(az_lower(w)) for w in run]
        if any(_YEAR_RE.match(w) or _CHASSIS_RE.match(w) for w in bare_words):
            continue                           # контекст авто, а не номер детали
        bare = "".join(bare_words).upper()
        digits = sum(ch.isdigit() for ch in bare)
        if len(bare) < _MIN_JOINED_ALNUM or digits < _MIN_JOINED_DIGITS:
            continue
        if bare.isdigit() and bare.startswith("0") and len(bare) <= 11:
            continue                           # телефон, записанный группами
        if bare not in out:
            out.append(bare)
    return out


def extract_numbers(text: str) -> tuple[list[str], list[dict[str, str]]]:
    """Вернуть ``(похожие_на_номер, отбракованные_с_причиной)``."""
    accepted: list[str] = []
    rejected: list[dict[str, str]] = []
    for match in _TOKEN_RE.finditer(text or ""):
        token = match.group(0).strip(".,;:")
        reason = classify_number(token)
        if reason is None:
            normalized = _alnum(az_lower(token)).upper()
            if normalized not in accepted:
                accepted.append(normalized)
        elif reason != "нет цифр":
            rejected.append({"token": token, "reason": reason})

    for joined in _joined_candidates(text):
        # Склейка не должна дублировать уже найденный слитный номер.
        if joined not in accepted and not any(joined in a or a in joined
                                              for a in accepted):
            accepted.append(joined)
    return accepted, rejected


class OemResolver:
    def __init__(self, retriever: RetrieverV2, catalog: dict[str, dict] | None = None):
        self.retriever = retriever
        self.catalog = catalog or {}

    @classmethod
    def from_file(cls, retriever: RetrieverV2, path: str) -> "OemResolver":
        catalog: dict[str, dict] = {}
        if path and os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                raw = json.load(fh)
            for key, value in raw.items():
                catalog[_alnum(str(key)).upper()] = value
        return cls(retriever, catalog)

    def resolve(self, item_raw: str, original_text: str) -> OemEvidence:
        # Номер ищем во всём сообщении: покупатель часто пишет его отдельной
        # строкой, вне фразы с названием детали.
        numbers, rejected = extract_numbers(original_text)
        text_head = self.retriever.head_codes(item_raw)

        if not numbers:
            return OemEvidence(status=OEM_NONE, numbers=[], rejected=rejected,
                               text_head_codes=text_head,
                               reason="номер детали в сообщении не найден")

        if not self.catalog:
            return OemEvidence(
                status=OEM_UNRESOLVED, numbers=numbers, rejected=rejected,
                text_head_codes=text_head,
                reason="каталог OEM не подключён — номер не с чем сопоставлять",
            )

        for number in numbers:
            entry = self.catalog.get(number)
            if not entry:
                continue
            code = entry.get("external_code") or entry.get("part_id")
            part = self.retriever.dict.get(code) if code else None
            if part is None:
                # Номер есть в каталоге, но ведёт на part_id вне словаря —
                # это не совпадение, а рассинхронизация данных.
                return OemEvidence(
                    status=OEM_UNRESOLVED, numbers=numbers, rejected=rejected,
                    text_head_codes=text_head,
                    reason=f"каталог указывает на {code!r}, которого нет в словаре",
                )
            agrees = self._agrees(code, text_head)
            return OemEvidence(
                status=OEM_MATCH if agrees else OEM_CONFLICT,
                numbers=numbers, rejected=rejected,
                resolved_external_code=code,
                resolved_name=part.name_ru,
                text_head_codes=text_head,
                reason=("номер и текст указывают на один объект" if agrees else
                        f"номер даёт {code} ({part.name_ru}), "
                        f"текст — {text_head or 'объект не распознан'}"),
            )

        return OemEvidence(status=OEM_UNRESOLVED, numbers=numbers, rejected=rejected,
                           text_head_codes=text_head,
                           reason="номера нет в каталоге OEM")

    def _agrees(self, code: str, text_head: list[str]) -> bool:
        """Согласуются ли объект по номеру и объект по тексту.

        Если текст не дал однозначного объекта, конфликта нет — противоречить
        нечему; такой случай остаётся ``MATCH`` и разбирается арбитром.
        """
        if not text_head:
            return True
        if code in text_head:
            return True
        # Соседняя деталь того же листа словаря — не конфликт, а уточнение
        # внутри группы.
        parts = self.retriever.dict.parts
        leaf = parts[code].category
        return any(parts[c].category == leaf for c in text_head if c in parts)
