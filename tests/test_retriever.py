"""Retriever V2: только реальные кандидаты и достаточная полнота."""

import pytest

from avtozap.retriever import RetrieverV2, match_token, strip_stopwords
from avtozap.translit import skeleton, translit


@pytest.fixture(scope="module")
def retriever() -> RetrieverV2:
    return RetrieverV2.from_file()


# ── главное свойство: ничего не выдумывается ───────────────────────────────
def test_every_candidate_exists_in_the_dictionary(retriever):
    for query in ["tormuz disk", "naklatka", "qwerty", "sol güzgü", "радиатор"]:
        for candidate in retriever.retrieve(query).candidates:
            assert candidate.part_id in retriever.dict.parts
            part = retriever.dict.parts[candidate.part_id]
            assert candidate.name_ru == part.name_ru
            assert candidate.category == part.category


def test_nonsense_yields_no_candidates(retriever):
    assert retriever.retrieve("qwerty zxcvbn").candidates == []


def test_candidate_order_is_deterministic(retriever):
    first = retriever.retrieve("tormuz disk").part_ids
    second = RetrieverV2.from_file().retrieve("tormuz disk").part_ids
    assert first == second


def test_top_k_is_respected(retriever):
    assert len(RetrieverV2.from_file(top_k=3).retrieve("fara").candidates) <= 3


# ── полнота: правильная деталь обязана быть среди кандидатов ───────────────
@pytest.mark.parametrize("query,expected", [
    ("naklatka", "EY-001"),
    ("əyləc altlığı", "EY-001"),          # покупатель пишет имя листа словаря
    ("tormuz disk", "EY-002"),            # кириллический синоним, латинский ввод
    ("əyləc diski", "EY-002"),
    ("babin", "MU-025"),
    ("sveça", "SR-014"),
    ("mühərrik yastıqları", "MU-017"),
    ("sürətlər qutusunun yastıqları", "MU-018"),
    ("ön arxa bufer", "KZ-001"),
    ("sol güzgü", "KZ-020"),
    ("stupitsa podsipniki", "AS-007"),    # опечатка
    ("radiator", "SO-001"),
    ("fara", "KZ-005"),
    ("amortizator", "AS-001"),
])
def test_expected_part_is_retrieved(retriever, query, expected):
    assert expected in retriever.retrieve(query).part_ids


def test_exact_name_scores_above_everything(retriever):
    top = retriever.retrieve("əyləc diski").candidates[0]
    assert top.part_id == "EY-002" and top.reason == "exact_name"


# ── нормализация, на которой держится полнота ──────────────────────────────
def test_cyrillic_is_transliterated():
    assert translit("тормозной") == "tormoznoy"


def test_consonant_skeleton_folds_unstable_vowels():
    assert skeleton("tormuz") == skeleton("tormoz")
    assert skeleton("apornu") == skeleton("opornu")     # оба варианта есть в словаре


@pytest.mark.parametrize("a,b", [
    ("tormuz", "tormoz"),          # разнобой гласных
    ("yastıqları", "yastığı"),     # агглютинативные окончания
    ("qutusunun", "qutusu"),
    ("əyləc", "eylec"),            # диакритика
])
def test_soft_token_match_accepts_real_variants(a, b):
    assert match_token(a, b) > 0


@pytest.mark.parametrize("a,b", [("bufer", "fara"), ("disk", "motor"), ("sol", "sağ")])
def test_soft_token_match_rejects_unrelated_words(a, b):
    assert match_token(a, b) == 0


def test_stopwords_are_stripped_from_the_query_key():
    assert strip_stopwords("Salam sol ön 2 ədəd bufer lazımdır") == "bufer"


# ── вспомогательные API, на которые опираются другие слои ──────────────────
def test_head_part_ids_only_returns_exact_hits(retriever):
    assert retriever.head_part_ids("bufer")            # точный ключ
    assert retriever.head_part_ids("qwerty zxcvbn") == []


def test_lexical_support_detects_an_unrelated_part(retriever):
    assert retriever.lexical_support("əyləc diski", "EY-002")
    assert not retriever.lexical_support("əyləc diski", "KZ-020")   # зеркало
