"""Слой фото-доказательств. Работает ДО ретривера.

Фото — вспомогательное свидетельство, а не разрешение выдумать деталь, которой
нет в словаре: слой возвращает только текстовое описание и слова-подсказки,
никогда ``part_id``.

Если картинки нет или доступа к ней нет — это фиксируется отдельным статусом, и
конвейер спокойно едет дальше:

``NO_IMAGE``          в запросе нет изображения;
``IMAGE_UNAVAILABLE`` изображение указано, но недоступно (нет файла, слой выключен);
``OK``                описание получено;
``ERROR``             вызов не удался.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os

from .config import DEFAULT_VISION_MODEL
from .llm import LlmClient, LlmError
from .types import PHOTO_ERROR, PHOTO_NONE, PHOTO_OK, PHOTO_UNAVAILABLE, PhotoEvidence

_SYSTEM = (
    "Ты — слой распознавания фото в конвейере подбора автозапчастей. "
    "Опиши, ЧТО ЗА ДЕТАЛЬ на фотографии, максимально конкретно и коротко. "
    "Никогда не придумывай артикул, код детали или каталожный номер. "
    "Верни JSON: {\"summary\": \"<одно предложение по-русски>\", "
    "\"hints\": [\"<слово-подсказка>\", ...]}. "
    "Если понять деталь нельзя, верни summary=\"не удалось определить\" и hints=[]."
)

_MAX_BYTES = 8 * 1024 * 1024


def _encode(path: str) -> tuple[str, str]:
    mime = mimetypes.guess_type(path)[0] or "image/jpeg"
    with open(path, "rb") as fh:
        raw = fh.read(_MAX_BYTES + 1)
    if len(raw) > _MAX_BYTES:
        raise ValueError(f"файл больше {_MAX_BYTES // (1024 * 1024)} МБ")
    return mime, base64.b64encode(raw).decode("ascii")


class PhotoLayer:
    def __init__(self, client: LlmClient | None, model: str = DEFAULT_VISION_MODEL,
                 enabled: bool = False, base_dir: str | None = None):
        self.client = client
        self.model = model
        self.enabled = enabled
        self.base_dir = base_dir

    def analyze(self, image_ref: str | None) -> PhotoEvidence:
        if not image_ref:
            return PhotoEvidence(status=PHOTO_NONE, reason="изображения нет в запросе")
        if not self.enabled or self.client is None:
            return PhotoEvidence(status=PHOTO_UNAVAILABLE, reason="слой фото выключен")

        url = image_ref if image_ref.startswith(("http://", "https://")) else None
        if url is None:
            path = image_ref
            if self.base_dir and not os.path.isabs(path):
                path = os.path.join(self.base_dir, path)
            if not os.path.exists(path):
                return PhotoEvidence(status=PHOTO_UNAVAILABLE,
                                     reason=f"файл изображения не найден: {image_ref}")
            try:
                mime, encoded = _encode(path)
            except (OSError, ValueError) as exc:
                return PhotoEvidence(status=PHOTO_ERROR,
                                     reason=f"не удалось прочитать изображение: {exc}")
            url = f"data:{mime};base64,{encoded}"

        try:
            response = self.client.complete(
                model=self.model,
                messages=[
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": [
                        {"type": "text", "text": "Что за деталь на фото?"},
                        {"type": "image_url", "image_url": {"url": url}},
                    ]},
                ],
                temperature=0.0,
            )
        except LlmError as exc:
            return PhotoEvidence(status=PHOTO_ERROR, reason=f"ошибка API: {exc}")

        try:
            data = json.loads(response.text)
            summary = str(data.get("summary", "")).strip()
            hints = [str(h) for h in (data.get("hints") or []) if str(h).strip()]
        except (json.JSONDecodeError, TypeError, AttributeError):
            return PhotoEvidence(status=PHOTO_ERROR,
                                 reason="ответ слоя фото не является корректным JSON")

        if not summary:
            return PhotoEvidence(status=PHOTO_ERROR, reason="пустое описание")
        return PhotoEvidence(status=PHOTO_OK, summary=summary[:500],
                             hints=hints[:10], reason="описание получено")
