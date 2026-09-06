"""Проверки ответов на вопросы: считает программа, модель только формулирует.

Сети здесь нет: вместо админки — данные в памяти, вместо OpenAI — подделка,
которая отдаёт заранее заданный план.
"""

import datetime as dt
import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from avtozap_export import qa  # noqa: E402
from avtozap_export.ai import AiError  # noqa: E402

QUIET = logging.getLogger("тест")
QUIET.addHandler(logging.NullHandler())

TODAY = dt.date(2026, 9, 6)


def day(offset: int) -> str:
    return (TODAY - dt.timedelta(days=offset)).isoformat() + "T12:00:00"


DATA = {
    "stores": [
        {"id": 1, "name": "Автомир", "city": "Баку", "created_at": day(100)},
        {"id": 2, "name": "Мотор Плюс", "city": "Гянджа", "created_at": day(90)},
        {"id": 3, "name": "Запчасть Сервис", "city": "Баку", "created_at": day(80)},
    ],
    "offers": [
        {"id": 10, "rfq_id": 100, "store_id": 1, "price": 120, "created_at": day(0)},
        {"id": 11, "rfq_id": 100, "store_id": 1, "price": None, "created_at": day(2)},
        {"id": 12, "rfq_id": 101, "store_id": 2, "price": 300, "created_at": day(3)},
        {"id": 13, "rfq_id": 101, "store_id": 1, "price": 90, "created_at": day(20)},
    ],
    "rfq": [
        {"id": 100, "title": "фара", "brand": "Toyota", "model": "Corolla", "created_at": day(1)},
        {"id": 101, "title": "бампер", "brand": "Toyota", "model": "Corolla", "created_at": day(4)},
        {"id": 102, "title": "стекло", "brand": "Nissan", "model": "Almera", "created_at": day(2)},
        {"id": 103, "title": "капот", "brand": "Toyota", "model": "Camry", "created_at": day(40)},
    ],
    "users": [
        {"id": 200, "name": "Иван", "created_at": day(0)},
        {"id": 201, "name": "Пётр", "created_at": day(1)},
        {"id": 202, "name": "Ольга", "created_at": day(1)},
        {"id": 203, "name": "Артур", "created_at": day(10)},
    ],
    "parts": [
        {"id": 300, "name": "Фара левая"},
        {"id": 301, "name": "Бампер передний"},
        {"id": 302, "name": "Капот"},
    ],
}

SECTIONS = [
    {"key": "offers", "path": "/offers", "label": "Отклики"},
    {"key": "rfq", "path": "/rfq", "label": "Заявки"},
    {"key": "stores", "path": "/stores", "label": "Магазины"},
    {"key": "users", "path": "/users", "label": "Пользователи"},
    {"key": "parts", "path": "/parts", "label": "Детали"},
]


class FakeAdmin:
    """Админка в памяти: отдаёт заранее заданные записи."""

    def __init__(self, data=None):
        self.data = data if data is not None else DATA
        self.fetched: list[str] = []

    def fetch_all(self, path, *, params=None, per_page=100, on_page=None):
        key = path.strip("/")
        self.fetched.append(key)
        return [dict(row) for row in self.data.get(key, [])]

    def probe(self, path, params=None):
        key = path.strip("/")
        rows = self.data.get(key, [])
        return 200, {"data": rows[:1]}


class FakeAi:
    """Подделка OpenAI: отдаёт готовый план и переписывает цифры как есть."""

    def __init__(self, plan, phrase_fails=False):
        self._plan = plan
        self.phrase_fails = phrase_fails
        self.seen_catalog = ""
        self.seen_facts: dict | None = None

    def plan(self, question, catalog, today):
        self.seen_catalog = catalog
        return dict(self._plan)

    def phrase(self, question, facts):
        self.seen_facts = facts
        if self.phrase_fails:
            raise AiError("OpenAI недоступен")
        return "ОТВЕТ: " + qa.local_phrase(facts)


@pytest.fixture
def store(tmp_path):
    return qa.DataStore(FakeAdmin(), SECTIONS, out_dir=tmp_path / "выгрузка", logger=QUIET)


def ask(store, plan, question="вопрос", phrase_fails=False):
    brain = FakeAi(plan, phrase_fails)
    answer = qa.answer_question(question, store, brain, today=TODAY, logger=QUIET)
    return answer, brain


# ── период считает программа, а не модель ────────────────────────────────────
@pytest.mark.parametrize(
    "kind, expected_from, expected_to",
    [
        ("сегодня", TODAY, TODAY),
        ("вчера", TODAY - dt.timedelta(days=1), TODAY - dt.timedelta(days=1)),
        ("неделя", TODAY - dt.timedelta(days=6), TODAY),
        ("месяц", TODAY - dt.timedelta(days=29), TODAY),
        ("всё", None, None),
    ],
)
def test_period_is_computed_by_the_program(kind, expected_from, expected_to):
    date_from, date_to, _text = qa.period_from_plan({"период": {"вид": kind}}, TODAY)
    assert (date_from, date_to) == (expected_from, expected_to)


def test_named_range_is_taken_as_is():
    date_from, date_to, text = qa.period_from_plan(
        {"период": {"вид": "диапазон", "с": "2026-09-01", "по": "2026-09-05"}}, TODAY
    )
    assert (date_from, date_to) == (dt.date(2026, 9, 1), dt.date(2026, 9, 5))
    assert "1 сентября" in text and "5 сентября" in text


def test_range_without_dates_asks_to_clarify():
    with pytest.raises(qa.Unclear, match="за какой период"):
        qa.period_from_plan({"период": {"вид": "диапазон"}}, TODAY)


# ── вопросы из задания ───────────────────────────────────────────────────────
def test_new_users_yesterday(store):
    answer, brain = ask(store, {"операция": "посчитать", "раздел": "users",
                                "период": {"вид": "вчера"}})
    assert answer.ok
    assert brain.seen_facts["количество"] == 2  # Пётр и Ольга
    assert "2" in answer.text


def test_offers_from_one_store_for_a_week(store):
    """Название магазина лежит в другом разделе — программа связывает сама."""
    answer, brain = ask(store, {
        "операция": "посчитать", "раздел": "offers", "период": {"вид": "неделя"},
        "поиск": {"текст": "Автомир", "варианты": ["Автомир", "Avtomir"],
                  "раздел": "stores", "ключ": "store_id"},
    })
    assert brain.seen_facts["количество"] == 2  # третий отклик старше недели


def test_store_name_is_found_even_without_hints(store):
    """Модель не подсказала раздел с названиями — программа догадывается по связи."""
    answer, brain = ask(store, {
        "операция": "посчитать", "раздел": "offers", "период": {"вид": "всё"},
        "поиск": {"текст": "автомир", "варианты": ["автомир"]},
    })
    assert brain.seen_facts["количество"] == 3


def test_requests_by_brand_and_model_in_russian(store):
    answer, brain = ask(store, {
        "операция": "посчитать", "раздел": "rfq", "период": {"вид": "неделя"},
        "поиск": {"текст": "тойота королла", "варианты": ["тойота королла", "toyota corolla"]},
    })
    assert brain.seen_facts["количество"] == 2  # Camry старше недели, Nissan не подходит


def test_total_parts_count(store):
    answer, brain = ask(store, {"операция": "посчитать", "раздел": "parts",
                                "период": {"вид": "всё"}})
    assert brain.seen_facts["количество"] == 3


def test_last_activity_of_a_store(store):
    answer, brain = ask(store, {
        "операция": "последняя_дата", "раздел": "offers",
        "поиск": {"текст": "Автомир", "раздел": "stores", "ключ": "store_id"},
    })
    assert brain.seen_facts["дата"] == f"{TODAY.day} сентября {TODAY.year} года"
    assert brain.seen_facts["дней_назад"] == 0


def test_requests_without_a_single_offer(store):
    answer, brain = ask(store, {
        "операция": "без_пары", "раздел": "rfq", "раздел_2": "offers",
        "ключ": "rfq_id", "ключ_2": "id", "период": {"вид": "всё"},
    })
    assert brain.seen_facts["количество"] == 2  # заявки 102 и 103
    assert answer.table_path is not None and answer.table_path.exists()


def test_stores_that_never_answered_this_month(store):
    answer, brain = ask(store, {
        "операция": "без_пары", "раздел": "stores", "раздел_2": "offers",
        "ключ": "store_id", "ключ_2": "id", "период": {"вид": "месяц"},
        "период_к_разделу": False,
    })
    assert brain.seen_facts["количество"] == 1  # «Запчасть Сервис» не отвечал
    assert brain.seen_facts["примеры"] == ["Запчасть Сервис"]


def test_top_stores_by_offers(store):
    answer, brain = ask(store, {
        "операция": "топ", "раздел": "offers", "период": {"вид": "месяц"},
        "группировка": "store_id", "предел": 10,
    })
    rows = brain.seen_facts["строки"]
    assert [item["сколько"] for item in rows] == [3, 1]
    assert answer.table_path is not None


def test_export_all_parts_to_a_file(store):
    answer, brain = ask(store, {"операция": "список", "раздел": "parts",
                                "период": {"вид": "всё"}, "в_файл": True})
    assert answer.table_rows == 3
    assert answer.table_path.exists()
    assert "Сохранил в файл" in answer.text
    assert str(answer.table_path) in answer.text


def test_saved_file_opens_in_excel(store):
    answer, _brain = ask(store, {"операция": "список", "раздел": "parts",
                                 "период": {"вид": "всё"}, "в_файл": True})
    raw = answer.table_path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")  # UTF-8 с BOM
    assert "Фара левая" in raw.decode("utf-8-sig")


def test_second_file_does_not_overwrite_the_first(store):
    first, _ = ask(store, {"операция": "список", "раздел": "parts", "в_файл": True})
    second, _ = ask(store, {"операция": "список", "раздел": "parts", "в_файл": True})
    assert first.table_path != second.table_path
    assert first.table_path.exists() and second.table_path.exists()


# ── честность: не выдумываем ─────────────────────────────────────────────────
def test_unknown_store_suggests_similar_names(store):
    answer, _brain = ask(store, {
        "операция": "посчитать", "раздел": "offers", "период": {"вид": "всё"},
        "поиск": {"текст": "Автолюкс", "раздел": "stores", "ключ": "store_id"},
    })
    assert not answer.ok
    assert "Не нашёл" in answer.text
    assert "Автомир" in answer.text  # предлагаем то, что есть на самом деле


def test_small_typo_in_the_store_name_still_finds_it(store):
    """«Автомер» вместо «Автомир» — обычная опечатка, а не другой магазин."""
    _answer, brain = ask(store, {
        "операция": "посчитать", "раздел": "offers", "период": {"вид": "всё"},
        "поиск": {"текст": "Автомер", "раздел": "stores", "ключ": "store_id"},
    })
    assert brain.seen_facts["количество"] == 3


def test_known_store_without_records_answers_zero_not_confusion(store):
    """Магазин есть, откликов за период нет — это ноль, а не «не нашёл»."""
    answer, brain = ask(store, {
        "операция": "посчитать", "раздел": "offers", "период": {"вид": "сегодня"},
        "поиск": {"текст": "Запчасть Сервис", "раздел": "stores", "ключ": "store_id"},
    })
    assert answer.ok
    assert brain.seen_facts["количество"] == 0


def test_model_says_it_did_not_understand(store):
    answer, _brain = ask(store, {"понятно": False, "уточнение": "Не понял, за какой период"})
    assert not answer.ok
    assert answer.text == "Не понял, за какой период"


def test_unknown_section_is_reported_with_the_real_list(store):
    answer, _brain = ask(store, {"операция": "посчитать", "раздел": "синонимы"})
    assert not answer.ok
    assert "Магазины" in answer.text and "Отклики" in answer.text


def test_missing_date_field_is_admitted(store):
    answer, brain = ask(store, {
        "операция": "последняя_дата", "раздел": "parts",
        "поиск": {"текст": "Капот"},
    })
    assert "нет" in brain.seen_facts["нет_данных"].lower()
    assert answer.ok


def test_top_without_grouping_asks_to_clarify(store):
    answer, _brain = ask(store, {"операция": "топ", "раздел": "offers",
                                 "период": {"вид": "месяц"}, "группировка": "неведомая_колонка"})
    assert not answer.ok
    assert "уточните" in answer.text.lower()


def test_unlinkable_sections_are_reported(store):
    answer, _brain = ask(store, {
        "операция": "без_пары", "раздел": "parts", "раздел_2": "users",
        "ключ": "какая-то_колонка", "ключ_2": "тоже_нет",
    })
    assert not answer.ok
    assert "иначе" in answer.text.lower() or "не понял" in answer.text.lower()


# ── цифры всегда свои, даже если модель молчит ───────────────────────────────
def test_numbers_come_from_data_not_from_the_model(store):
    answer, brain = ask(store, {"операция": "посчитать", "раздел": "users",
                                "период": {"вид": "вчера"}}, phrase_fails=True)
    assert answer.ok
    assert "2" in answer.text  # фразу сложили сами, число — из данных


def test_model_only_gets_counted_values_never_the_rows(store):
    _answer, brain = ask(store, {"операция": "список", "раздел": "stores",
                                 "период": {"вид": "всё"}, "в_файл": True})
    sent = repr(brain.seen_facts)
    assert "Баку" not in sent  # города, телефоны и прочее наружу не уходят
    assert "created_at" not in sent  # и названия колонок тоже


def test_catalog_sent_to_the_model_has_no_data(store):
    _answer, brain = ask(store, {"операция": "посчитать", "раздел": "users"})
    assert "users (Пользователи)" in brain.seen_catalog
    assert "Иван" not in brain.seen_catalog  # только названия колонок, без значений


# ── данные берутся из свежей выгрузки ────────────────────────────────────────
def test_fresh_export_is_reused_instead_of_the_admin_panel(tmp_path):
    out_dir = tmp_path / "выгрузка"
    out_dir.mkdir()
    from avtozap_export.core import write_csv

    write_csv(out_dir / "users.csv", [{"id": 1, "name": "Из файла", "created_at": day(0)}])
    qa.save_manifest(out_dir, [{"ключ": "users", "файл": "users.csv", "с": None, "по": None,
                                "фильтр_цены": "all", "строк": 1}])

    admin = FakeAdmin()
    store = qa.DataStore(admin, SECTIONS, out_dir=out_dir, logger=QUIET)
    _answer, brain = ask(store, {"операция": "посчитать", "раздел": "users",
                                 "период": {"вид": "всё"}})
    assert brain.seen_facts["количество"] == 1
    assert "users" not in admin.fetched  # в админку не ходили


def test_yesterdays_export_is_not_reused(tmp_path):
    out_dir = tmp_path / "выгрузка"
    out_dir.mkdir()
    (out_dir / qa.MANIFEST_NAME).write_text(
        '{"дата": "2000-01-01", "разделы": [{"ключ": "users", "файл": "users.csv"}]}',
        encoding="utf-8",
    )
    assert qa.load_manifest(out_dir) == []


def test_narrow_export_is_not_used_for_a_wider_question(tmp_path):
    """Файл за сегодня не годится для вопроса за неделю."""
    out_dir = tmp_path / "выгрузка"
    out_dir.mkdir()
    from avtozap_export.core import write_csv

    write_csv(out_dir / "users.csv", [{"id": 1, "created_at": day(0)}])
    qa.save_manifest(out_dir, [{"ключ": "users", "файл": "users.csv",
                                "с": TODAY.isoformat(), "по": TODAY.isoformat(),
                                "фильтр_цены": "all", "строк": 1}])
    admin = FakeAdmin()
    store = qa.DataStore(admin, SECTIONS, out_dir=out_dir, logger=QUIET)
    _answer, brain = ask(store, {"операция": "посчитать", "раздел": "users",
                                 "период": {"вид": "неделя"}})
    assert "users" in admin.fetched  # пошли в админку за полными данными
    assert brain.seen_facts["количество"] == 3


def test_offers_export_with_price_filter_is_not_reused(tmp_path):
    entry = {"ключ": "offers", "файл": "offers.csv", "с": None, "по": None, "фильтр_цены": "with"}
    assert not qa._covers(entry, None, None)
