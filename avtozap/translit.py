"""Кириллица → латиница и «согласный скелет» токена.

Зачем это нужно retriever'у: покупатели пишут русские слова латиницей и с
неустойчивыми гласными. Словарь это подтверждает — в нём рядом лежат
``apornu / opornu / opornik / oporniy``, ``tormuz şlankı`` и ``тормозной диск``.

Два дешёвых лексических приёма закрывают почти весь этот разброс:

* **транслитерация** — ``тормозной`` → ``tormoznoy``, после чего кириллические
  синонимы становятся сопоставимы с латинским вводом;
* **согласный скелет** — ``tormuz`` и ``tormoz`` → ``trmz``: гласные выкидываются,
  и фонетический разнобой перестаёт мешать.

Оба приёма чисто механические: они не добавляют в словарь ни одной записи и не
создают новых соответствий деталей — только позволяют найти уже существующие.
"""

from __future__ import annotations

_CYR_TO_LAT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "j", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "x", "ц": "ts", "ч": "ç", "ш": "ş", "щ": "ş",
    "ъ": "", "ы": "ı", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}

# Азербайджанские/латинские буквы → базовая латиница (для скелета и префиксов).
_FOLD = {
    "ə": "e", "ğ": "g", "ı": "i", "ö": "o", "ü": "u", "ç": "c", "ş": "s",
}

_VOWELS = set("aeiouıəöü")


def translit(token: str) -> str:
    """Транслитерировать кириллицу в латиницу; латиница возвращается как есть."""
    if not token:
        return token
    out = []
    changed = False
    for ch in token:
        repl = _CYR_TO_LAT.get(ch)
        if repl is None:
            out.append(ch)
        else:
            out.append(repl)
            changed = True
    return "".join(out) if changed else token


def fold(token: str) -> str:
    """Свернуть диакритику к базовой латинице: ``əyləc`` → ``eylec``."""
    return "".join(_FOLD.get(ch, ch) for ch in translit(token))


def skeleton(token: str) -> str:
    """Согласный скелет: свёрнутый токен без гласных (``tormuz`` → ``trmz``)."""
    return "".join(ch for ch in fold(token) if ch not in _VOWELS and ch.isalnum())


def common_prefix_len(a: str, b: str) -> int:
    """Длина общего префикса двух свёрнутых токенов."""
    fa, fb = fold(a), fold(b)
    n = 0
    for x, y in zip(fa, fb):
        if x != y:
            break
        n += 1
    return n
