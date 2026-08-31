"""Детектор порчи кодировки (mojibake) в тексте, пришедшем извне.

Зачем. В живом прогоне 300 заявок половина строк ушла в модель и вернулась в
виде ``Matorun paduÅkasÄ±`` вместо ``Matorun paduşkası`` — байты UTF-8 были
прочитаны как latin-1. Прогон при этом завершился «успешно»: ошибок не было,
CSV собрался, и порча выяснилась только при чтении глазами. Доля select на
испорченных строках оказалась на 13 п.п. ниже, чем на чистых.

Такое не должно проходить молча. Клиент на Python этой болезнью не страдает
(httpx кодирует тело явно в UTF-8 и декодирует ответ по заголовку charset), но
проверка стоит копейки, а спасает от бессмысленного прогона на 300 заявок.
"""

from __future__ import annotations

import re

#: Последовательности, возникающие, когда UTF-8 прочитан как latin-1/cp1252.
#: Ведущий байт двухбайтовой последовательности UTF-8 (C2..DF) в latin-1
#: показывается как Â..ß, а следующий за ним байт — как знак из диапазона
#: 0x80..0xBF. Такой пары в осмысленном тексте не бывает.
_LEAD = "À-ß"
_TAIL = "-¿"
_MOJIBAKE_RE = re.compile(f"[{_LEAD}][{_TAIL}]|â")


def looks_mojibake(text: str | None) -> bool:
    """Похож ли текст на UTF-8, прочитанный как latin-1."""
    return bool(text) and bool(_MOJIBAKE_RE.search(text))


def repair(text: str) -> str | None:
    """Попробовать восстановить исходный текст обратным преобразованием.

    Возвращает ``None``, если восстановить не удалось. Восстановление НЕ
    применяется автоматически: молча «чинить» данные опаснее, чем показать
    проблему, — вернуть можно не то, что было. Функция нужна отчёту, чтобы
    показать рядом «пришло» и «вероятно, было».
    """
    for encoding in ("latin-1", "cp1252"):
        try:
            candidate = text.encode(encoding).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        if not looks_mojibake(candidate):
            return candidate
    return None


def check(text: str | None, where: str) -> str | None:
    """Вернуть описание проблемы или ``None``, если текст в порядке."""
    if not looks_mojibake(text):
        return None
    fixed = repair(text or "")
    hint = f"; вероятно, было {fixed!r}" if fixed else ""
    return (f"{where}: текст испорчен кодировкой (UTF-8 прочитан как latin-1)"
            f"{hint}")
