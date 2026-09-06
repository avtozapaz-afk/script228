"""Простой журнал работы. Пароли и токены в него не пишутся никогда."""

from __future__ import annotations

import datetime as dt
import logging
import re

from . import config

_SECRET_PATTERNS = (
    re.compile(r"(?i)(password|passwd|pwd|token|authorization|cookie|secret)\s*[:=]\s*\S+"),
    re.compile(r"(?i)admin_access_token=[^;\s]+"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]+"),
    re.compile(r"eyJ[A-Za-z0-9._\-]{10,}"),  # похоже на JWT
)


class _RedactFilter(logging.Filter):
    """Вырезает из сообщений всё, что похоже на пароль или токен."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        cleaned = message
        for pattern in _SECRET_PATTERNS:
            cleaned = pattern.sub("<скрыто>", cleaned)
        if cleaned != message:
            record.msg = cleaned
            record.args = ()
        return True


def setup() -> logging.Logger:
    """Настроить журнал в файл ``logs/выгрузка-ГГГГ-ММ-ДД.log``."""
    config.ensure_dirs()
    logger = logging.getLogger("avtozap")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    logger.propagate = False
    today = dt.date.today().isoformat()
    handler = logging.FileHandler(config.LOG_DIR / f"выгрузка-{today}.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s  %(levelname)s  %(message)s"))
    handler.addFilter(_RedactFilter())
    logger.addHandler(handler)
    # Библиотеки не должны шуметь в наш файл и в консоль.
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    return logger
