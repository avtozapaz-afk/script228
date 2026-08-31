"""Пути, значения по умолчанию и словари служебных слов."""

from __future__ import annotations

import os
from dataclasses import dataclass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
OUT_DIR = os.path.join(ROOT, "out")
PROMPT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts")

SLOVAR_PATH = os.path.join(DATA_DIR, "SLOVAR_FINAL.txt")
CATEGORY_INDEX_PATH = os.path.join(DATA_DIR, "category_index.json")
# Необязательный каталог OEM-номеров. Его нет в поставке — пока файла нет,
# резолвер честно возвращает UNRESOLVED и НИКОГДА не выдумывает соответствие.
OEM_CATALOG_PATH = os.path.join(DATA_DIR, "oem_catalog.json")

ARBITER_PROMPT_PATH = os.path.join(PROMPT_DIR, "arbiter_v3.txt")
ARBITER_PROMPT_SHA_PATH = os.path.join(PROMPT_DIR, "arbiter_v3.sha256")

# ── Retriever V2 ────────────────────────────────────────────────────────────
RETRIEVER_TOP_K = 12
RETRIEVER_MIN_SCORE = 0.45
RETRIEVER_FUZZY_THRESHOLD = 0.80

# ── Arbiter V3 ──────────────────────────────────────────────────────────────
DEFAULT_ARBITER_MODEL = os.environ.get("AVTOZAP_ARBITER_MODEL", "gpt-4o")
DEFAULT_VISION_MODEL = os.environ.get("AVTOZAP_VISION_MODEL", "gpt-4o")
DEFAULT_TEMPERATURE = 0.0
DEFAULT_TIMEOUT_S = 60.0
DEFAULT_MAX_RETRIES = 5
DEFAULT_BACKOFF_BASE_S = 2.0
DEFAULT_BACKOFF_CAP_S = 60.0

# ── Validator ───────────────────────────────────────────────────────────────
# Точность важнее навязанной полноты: ниже этого порога уверенности SELECT
# понижается до REVIEW, а не отдаётся как готовый ответ.
MIN_CONFIDENCE_FOR_SELECT = 0.45

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


@dataclass
class RunConfig:
    """Настройки одного прогона харнесса."""

    input_path: str
    out_dir: str = OUT_DIR
    dict_path: str = SLOVAR_PATH
    category_index_path: str = CATEGORY_INDEX_PATH
    oem_catalog_path: str = OEM_CATALOG_PATH
    model: str = DEFAULT_ARBITER_MODEL
    vision_model: str = DEFAULT_VISION_MODEL
    temperature: float = DEFAULT_TEMPERATURE
    timeout_s: float = DEFAULT_TIMEOUT_S
    max_retries: int = DEFAULT_MAX_RETRIES
    top_k: int = RETRIEVER_TOP_K
    min_score: float = RETRIEVER_MIN_SCORE
    min_confidence: float = MIN_CONFIDENCE_FOR_SELECT
    mock: bool = False
    enable_photo: bool = False
    limit: int | None = None
    resume: bool = True
