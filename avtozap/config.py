"""Пути, значения по умолчанию и словари служебных слов."""

from __future__ import annotations

import os
from dataclasses import dataclass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
OUT_DIR = os.path.join(ROOT, "out")
PROMPT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts")

# Словарь на 571 деталь — единственный источник истины по external_code.
SLOVAR_XLSX_PATH = os.path.join(DATA_DIR, "AVTOZAP_slovar_FINAL_571.xlsx")
# Старый текстовый словарь репозитория (438 деталей) оставлен только для
# ранее существовавшего slovar_matcher и в новом конвейере НЕ используется.
SLOVAR_PATH = os.path.join(DATA_DIR, "SLOVAR_FINAL.txt")
CATEGORY_INDEX_PATH = os.path.join(DATA_DIR, "category_index.json")
# Необязательный каталог OEM-номеров. Его нет в поставке — пока файла нет,
# резолвер честно возвращает UNRESOLVED и НИКОГДА не выдумывает соответствие.
OEM_CATALOG_PATH = os.path.join(DATA_DIR, "oem_catalog.json")

ARBITER_PROMPT_PATH = os.path.join(PROMPT_DIR, "arbiter_v3.txt")
ARBITER_PROMPT_SHA_PATH = os.path.join(PROMPT_DIR, "arbiter_v3.sha256")
SEGMENTER_PROMPT_PATH = os.path.join(PROMPT_DIR, "segmenter_v1.txt")
SEGMENTER_PROMPT_SHA_PATH = os.path.join(PROMPT_DIR, "segmenter_v1.sha256")

# ── Retriever V2 ────────────────────────────────────────────────────────────
# limit=24. Выбран по замеру на реальном fresh-300 (scripts/measure_retriever.py):
# именно при 24 наш shortlist совпадает со ссылочным из devset на 100%, то есть
# воспроизводит эталонный прогон проекта один-в-один, и даёт лучшую полноту,
# чем 16 (93.8% против 92.9% по old_external_code). Значение 32 добавляет ещё
# 0.4 п.п., но перестаёт совпадать со ссылкой и удорожает каждый вызов арбитра.
RETRIEVER_LIMIT = 24

# ── Arbiter V3 ──────────────────────────────────────────────────────────────
# Продуктовая модель проекта для всех LLM-стадий.
DEFAULT_ARBITER_MODEL = os.environ.get("AVTOZAP_ARBITER_MODEL", "gpt-4.1-mini")
DEFAULT_VISION_MODEL = os.environ.get("AVTOZAP_VISION_MODEL", "gpt-4.1-mini")
DEFAULT_SEGMENTER_MODEL = os.environ.get("AVTOZAP_SEGMENTER_MODEL", "gpt-4.1-mini")
DEFAULT_TEMPERATURE = 0.0
DEFAULT_TIMEOUT_S = 90.0

# Лимиты вывода. В живом прогоне три заявки на десяток деталей оборвались на
# полуслове и выглядели как «ошибка разбора JSON»: сегментер должен уместить
# JSON со всеми предметами и их подсказками, поэтому лимит у него щедрый.
SEGMENTER_MAX_TOKENS = 4000
ARBITER_MAX_TOKENS = 500
DEFAULT_MAX_RETRIES = 5
DEFAULT_BACKOFF_BASE_S = 2.0
DEFAULT_BACKOFF_CAP_S = 60.0

# ── Validator ───────────────────────────────────────────────────────────────
# Точность важнее навязанной полноты. Промпт V3 отдаёт уверенность словом,
# поэтому порог — множество уровней, которые считаются достаточными для SELECT.
# «low» сюда не входит: такой ответ понижается до REVIEW.
ACCEPTED_CONFIDENCE = {"high", "medium"}

# ── служебные слова ─────────────────────────────────────────────────────────
# Разделители, по которым Layer 0 режет сообщение на фрагменты.
CONJUNCTIONS = {
    "və", "ve", "и", "and", "plus", "həm", "hem",
}

# Слова-атрибуты: сами по себе НЕ образуют отдельный запрошенный предмет.
SIDE_WORDS = {
    "sol", "sola", "soldan", "soldakı", "soldaki", "left", "lh",
    "sağ", "sag", "sağa", "sağdan", "sagdan", "sağdakı", "sagdaki", "right", "rh",
    "лево", "левый", "левая", "левое", "левых", "левого", "слева",
    "право", "правый", "правая", "правое", "правых", "правого", "справа",
}
POSITION_WORDS = {
    # NB: "on" сюда НЕ входит — это азербайджанское числительное «10»,
    # а не сокращение от "ön" (регрессия из slovar_matcher/attributes.py).
    "ön", "öndeki", "öndəki", "önki",
    "qabaq", "qabağ", "qabaqdakı", "qabaqdaki", "qabağdakı",
    "front", "fr", "arxa", "arxadakı", "arxadaki", "arxadan", "rear", "back",
    "перед", "передний", "передняя", "переднее", "передних", "переднего", "спереди",
    "зад", "задний", "задняя", "заднее", "задних", "заднего", "сзади",
    "yuxarı", "aşağı", "верх", "верхний", "низ", "нижний",
}
QUANTITY_WORDS = {
    "ədəd", "eded", "dənə", "dene", "dana", "komplekt", "kompleg", "dəst", "dest",
    "cüt", "cut", "штук", "шт", "пара", "комплект", "набор", "pcs", "set",
}
# Вежливость/шум — не влияют на смысл запроса.
NOISE_WORDS = {
    "salam", "salamlar", "xahiş", "xahis", "edirəm", "edirem", "zəhmət", "zehmet",
    "olmasa", "lazımdır", "lazimdir", "lazım", "lazim", "var", "varmı", "varmi",
    "olar", "olarmı", "olarmi", "üçün", "ucun", "please", "pls",
    "здравствуйте", "привет", "нужен", "нужна", "нужно", "нужны", "есть", "ли",
    "для", "пожалуйста", "добрый", "день", "утро", "вечер", "спасибо",
    "qiymət", "qiymeti", "qiyməti", "neçəyədir", "necedir", "цена", "сколько", "стоит",
}

# Марки/бренды авто: контекст автомобиля, а не запрошенная деталь.
# Токен вырезается только если он НЕ является ключом словаря (см. layer0).
VEHICLE_BRANDS = {
    "mercedes", "benz", "mercedes-benz", "bmw", "audi", "volkswagen", "vw",
    "toyota", "lexus", "honda", "nissan", "infiniti", "mazda", "mitsubishi",
    "subaru", "suzuki", "hyundai", "kia", "ssangyong", "daewoo", "chevrolet",
    "opel", "ford", "renault", "dacia", "peugeot", "citroen", "fiat", "alfa",
    "romeo", "skoda", "seat", "volvo", "saab", "porsche", "jeep", "chrysler",
    "dodge", "cadillac", "gmc", "land", "rover", "range", "jaguar", "mini",
    "tesla", "chery", "geely", "haval", "byd", "changan", "lifan", "great",
    "wall", "lada", "vaz", "gaz", "uaz", "kamaz", "niva", "priora", "granta",
    "iveco", "man", "scania", "daf", "isuzu", "hino", "prius", "camry",
    "corolla", "avensis", "rav4", "sonata", "elantra", "accent", "tucson",
    "santafe", "sorento", "sportage", "optima", "cerato", "rio", "octavia",
    "passat", "golf", "polo", "tiguan", "touareg", "transporter", "sprinter",
    "vito", "viano", "captiva", "cruze", "lacetti", "nexia", "matiz", "spark",
    "qashqai", "juke", "teana", "almera", "primera", "patrol", "pathfinder",
    "x5", "x6", "x3", "e39", "e46", "e60", "e90", "f10", "f30", "g30",
    "w124", "w140", "w202", "w203", "w204", "w210", "w211", "w212", "w220", "w221",
}


def _normalized(words: set[str]) -> frozenset[str]:
    """Привести набор служебных слов к нормализации словаря проекта.

    ``na()`` снимает азербайджанскую диакритику, поэтому «ön» в тексте
    становится «on», а «və» — «ve». Наборы выше записаны в читаемом виде, а
    сравнивать их надо в той же форме, в какой приходят слова запроса.
    """
    from .dictionary import normalize
    out: set[str] = set()
    for word in words:
        key = normalize(word)
        if key:
            out.update(key.split())
    return frozenset(out)


# Наборы в нормализованной форме — именно их используют слои конвейера.
CONJUNCTIONS_NA = _normalized(CONJUNCTIONS)
SIDE_WORDS_NA = _normalized(SIDE_WORDS)
QUANTITY_WORDS_NA = _normalized(QUANTITY_WORDS)
NOISE_WORDS_NA = _normalized(NOISE_WORDS)
VEHICLE_BRANDS_NA = _normalized(VEHICLE_BRANDS)

# «ön» после снятия диакритики совпадает с числительным «on» (10). Держим его
# отдельно: как слово позиции оно засчитывается только тогда, когда рядом нет
# счётного слова (см. layer0._position_words_in).
AMBIGUOUS_FRONT = "on"
POSITION_WORDS_NA = _normalized(POSITION_WORDS) | {AMBIGUOUS_FRONT}


@dataclass
class RunConfig:
    """Настройки одного прогона харнесса."""

    input_path: str
    out_dir: str = OUT_DIR
    dict_path: str = SLOVAR_XLSX_PATH
    category_index_path: str = CATEGORY_INDEX_PATH
    oem_catalog_path: str = OEM_CATALOG_PATH
    model: str = DEFAULT_ARBITER_MODEL
    segmenter_model: str = DEFAULT_SEGMENTER_MODEL
    vision_model: str = DEFAULT_VISION_MODEL
    temperature: float = DEFAULT_TEMPERATURE
    timeout_s: float = DEFAULT_TIMEOUT_S
    max_retries: int = DEFAULT_MAX_RETRIES
    limit: int = RETRIEVER_LIMIT
    accepted_confidence: frozenset = frozenset(ACCEPTED_CONFIDENCE)
    mock: bool = False
    enable_photo: bool = False
    max_requests: int | None = None
    # Какой это заход по заявке. На втором непонятном заходе правило заказчика
    # велит отдать сырой текст магазинам, а не переспрашивать снова.
    attempt: int = 1
    resume: bool = True
