"""Программа сама находит разделы админки.

Полного списка адресов нет, поэтому мы перебираем правдоподобные пути,
оставляем те, что отвечают «200» и возвращают список записей, и
запоминаем находки в ``endpoints.json`` — чтобы в следующий раз не искать.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Callable

from . import config
from .api import AdminClient, Stopped
from .core import extract_rows, looks_like_list_response

# Вероятные адреса разделов. Что не ответит — просто отбросим.
CANDIDATE_PATHS: tuple[str, ...] = (
    "/offers",
    "/rfq",
    "/users",
    "/stores",
    "/shops",
    "/agents",
    "/managers",
    "/admins",
    "/clients",
    "/sellers",
    "/buyers",
    "/cars",
    "/vehicles",
    "/brands",
    "/marks",
    "/models",
    "/generations",
    "/modifications",
    "/parts",
    "/categories",
    "/complaints",
    "/batches",
    "/campaigns",
    "/support",
    "/tickets",
    "/orders",
    "/requests",
    "/reviews",
    "/feedback",
    "/messages",
    "/chats",
    "/notifications",
    "/payments",
    "/transactions",
    "/invoices",
    "/balances",
    "/subscriptions",
    "/tariffs",
    "/promocodes",
    "/banners",
    "/news",
    "/cities",
    "/regions",
    "/countries",
    "/roles",
    "/logs",
    "/devices",
    "/documents",
)

# Названия разделов по-русски.
RU_LABELS: dict[str, str] = {
    "offers": "Отклики",
    "rfq": "Заявки",
    "users": "Пользователи",
    "stores": "Магазины",
    "shops": "Магазины (лавки)",
    "agents": "Агенты",
    "managers": "Менеджеры",
    "admins": "Администраторы",
    "clients": "Клиенты",
    "sellers": "Продавцы",
    "buyers": "Покупатели",
    "cars": "Автомобили",
    "vehicles": "Транспорт",
    "brands": "Марки машин",
    "marks": "Марки",
    "models": "Модели машин",
    "generations": "Поколения",
    "modifications": "Модификации",
    "parts": "Запчасти",
    "categories": "Категории",
    "complaints": "Жалобы",
    "batches": "Партии",
    "campaigns": "Рассылки",
    "support": "Поддержка",
    "tickets": "Обращения в поддержку",
    "orders": "Заказы",
    "requests": "Обращения",
    "reviews": "Отзывы",
    "feedback": "Обратная связь",
    "messages": "Сообщения",
    "chats": "Переписки",
    "notifications": "Уведомления",
    "payments": "Платежи",
    "transactions": "Операции",
    "invoices": "Счета",
    "balances": "Балансы",
    "subscriptions": "Подписки",
    "tariffs": "Тарифы",
    "promocodes": "Промокоды",
    "banners": "Баннеры",
    "news": "Новости",
    "cities": "Города",
    "regions": "Регионы",
    "countries": "Страны",
    "roles": "Роли",
    "logs": "Журнал действий",
    "devices": "Устройства",
    "documents": "Документы",
}

# Разделы, которые показываем в списке первыми.
PRIORITY = ("offers", "rfq", "users", "stores")

# Возможные названия параметров с датами — проверяем, понимает ли их сервер.
DATE_PARAM_PAIRS: tuple[tuple[str, str], ...] = (
    ("created_from", "created_to"),
    ("date_from", "date_to"),
    ("from", "to"),
    ("start_date", "end_date"),
)


def path_key(path: str) -> str:
    return path.strip("/").replace("/", "_") or "root"


def label_for(path: str) -> str:
    """Название раздела по-русски. Незнакомый — покажем как есть."""
    key = path_key(path)
    if key in RU_LABELS:
        return RU_LABELS[key]
    return key.replace("_", " ").replace("-", " ").capitalize()


def _sort_key(section: dict) -> tuple[int, str]:
    key = section["key"]
    if key in PRIORITY:
        return (PRIORITY.index(key), "")
    return (len(PRIORITY), section["label"].lower())


def detect_date_params(client: AdminClient, path: str) -> list[str] | None:
    """Понимает ли раздел фильтр по датам, и как эти параметры зовутся.

    Проверяем хитростью: просим заведомо невозможный период далёкого
    будущего. Если раздел стал пустым — значит, фильтр работает.
    """
    status, payload = client.probe(path, {"page": 1, "per_page": 1})
    if status != 200 or not extract_rows(payload):
        return None  # проверить не на чем

    future_from = (dt.date.today() + dt.timedelta(days=3650)).isoformat()
    future_to = (dt.date.today() + dt.timedelta(days=3651)).isoformat()
    for name_from, name_to in DATE_PARAM_PAIRS:
        status, payload = client.probe(
            path, {"page": 1, "per_page": 1, name_from: future_from, name_to: future_to}
        )
        if status == 200 and looks_like_list_response(payload) and not extract_rows(payload):
            return [name_from, name_to]
    return None


def detect_has_price(client: AdminClient, path: str) -> bool:
    """Поддерживает ли раздел фильтр «только с ценой»."""
    status, payload = client.probe(path, {"page": 1, "per_page": 1, "has_price": "true"})
    return status == 200 and looks_like_list_response(payload)


def discover(
    client: AdminClient,
    *,
    on_progress: Callable[[str, int, int], None] | None = None,
    paths: tuple[str, ...] = CANDIDATE_PATHS,
) -> list[dict]:
    """Перебрать вероятные адреса и оставить рабочие.

    ``on_progress`` вызывается как ``(что проверяем, номер, всего)``.
    """
    found: list[dict] = []
    total = len(paths)
    for number, path in enumerate(paths, start=1):
        if on_progress:
            on_progress(label_for(path), number, total)
        status, payload = client.probe(path, {"page": 1, "per_page": 1})
        if status != 200 or not looks_like_list_response(payload):
            continue
        key = path_key(path)
        section = {
            "key": key,
            "path": path,
            "label": label_for(path),
            "date_params": detect_date_params(client, path),
            "has_price_filter": key == "offers" or detect_has_price(client, path),
        }
        found.append(section)
    found.sort(key=_sort_key)
    return found


def save_sections(sections: list[dict], path: Path | None = None) -> None:
    """Запомнить найденные разделы."""
    path = path or config.ENDPOINTS_PATH
    payload = {
        "обновлено": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "разделы": sections,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_sections(path: Path | None = None) -> list[dict]:
    """Прочитать разделы, найденные в прошлый раз. Нет файла — пусто."""
    path = path or config.ENDPOINTS_PATH
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []
    sections = payload.get("разделы") if isinstance(payload, dict) else payload
    if not isinstance(sections, list):
        return []
    cleaned = []
    for section in sections:
        if isinstance(section, dict) and section.get("path"):
            section.setdefault("key", path_key(section["path"]))
            section.setdefault("label", label_for(section["path"]))
            section.setdefault("date_params", None)
            section.setdefault("has_price_filter", section["key"] == "offers")
            cleaned.append(section)
    return cleaned


def updated_at(path: Path | None = None) -> str | None:
    """Когда список разделов обновляли в последний раз."""
    path = path or config.ENDPOINTS_PATH
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    return payload.get("обновлено") if isinstance(payload, dict) else None
