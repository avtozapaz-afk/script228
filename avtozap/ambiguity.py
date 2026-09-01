"""Неоднозначные термины: не угадывать, а задать конкретный вопрос.

Откуда правила. Их написал проект: в словаре v7 появился лист
``AMBIGUOUS_RULES``, где четыре термина объявлены неоднозначными — к каждому
пара кодов, правило для матчера и **готовый уточняющий вопрос покупателю**.
Скрипт ``scripts/extract_ambiguous_rules.py`` переносит лист в
``data/ambiguous_rules.json``; здесь правила только исполняются.

Зачем отдельный слой, если алиасы уже сняты. Потому что снятия недостаточно —
проверено. Убрав ``park radari`` из уверенных алиасов EL-014 и AK-028, движок
не замолчал: на голом запросе он всё равно отдаёт кандидата со score 1.0, а на
``teker sensoru`` срабатывает контекстное правило ``context:teker+sensor``.
То есть арбитр видит уверенного кандидата там, где по вашему же правилу
уверенности быть не должно, и три из четырёх терминов на голом запросе давали
код, а ``Arxa şveller`` — и вовсе KZ-012 (стекло багажника), которого нет ни в
одной из двух пар. Слой закрывает именно это.

Что считаем «голым» термином. Ровно то, что этим словом называете вы: запрос,
в котором кроме самого термина нет ничего, кроме слов положения и вежливости
(``arxa``, ``sol``, ``lazımdır``). Как только покупатель дописал значащее
слово — ``porog sveleri``, ``park radari bloku`` — запрос голым быть перестаёт,
и его разбирают ваши же контекстные правила словаря, а не этот слой. Так
уточняющий вопрос не может отобрать правильный ответ у того, кто его уже дал:
проверено на всех четырёх терминах.

Слой ничего не выбирает и не придумывает: он либо молчит, либо возвращает
вопрос, написанный вами.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache

from .dictionary import normalize

_DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "data")
RULES_PATH = os.path.join(_DATA, "ambiguous_rules.json")
#: Слова-уточнители к отдельным правилам — см. data/ambiguity_qualifiers.json.
QUALIFIERS_PATH = os.path.join(_DATA, "ambiguity_qualifiers.json")


class AmbiguousTerm:
    """Сработавшее правило: пара кодов и вопрос покупателю."""

    __slots__ = ("term", "codes", "parts", "question", "matcher_rule")

    def __init__(self, term: str, codes: list[str], parts: list[str],
                 question: str, matcher_rule: str) -> None:
        self.term = term
        self.codes = codes
        self.parts = parts
        self.question = question
        self.matcher_rule = matcher_rule

    def __repr__(self) -> str:                     # pragma: no cover — отладка
        return f"AmbiguousTerm({self.term!r}, {self.codes!r})"


class AmbiguityRules:
    """Правила неоднозначности словаря."""

    def __init__(self, rules: list[dict],
                 qualifiers: dict[str, list[str]] | None = None) -> None:
        # Уточнители по нормализованному термину: с ними термин разрешается,
        # без них — переспрашиваем, даже если в заявке есть другие слова.
        self._qualifiers = {normalize(term): {normalize(word) for word in words}
                            for term, words in (qualifiers or {}).items()}
        # Нормализованный вариант термина -> правило. Нормализация та же, что у
        # словаря, поэтому «şveller» и «sveller» — одно и то же.
        self._by_term: dict[str, dict] = {}
        for rule in rules:
            for variant in rule.get("variants", []):
                key = normalize(variant)
                if key:
                    self._by_term.setdefault(key, rule)

    def __len__(self) -> int:
        return len(self._by_term)

    @property
    def terms(self) -> list[str]:
        return sorted(self._by_term)

    def _find_qualified_term(self, tokens: list[str]):
        """Термин с уточнителями внутри фразы — если ни одного уточнителя нет.

        Возвращает ``(термин, правило)`` или ``(None, None)``. Правило с
        уточнителями применяется шире голой формы намеренно: заказчик
        подтвердил, что ``çaşka`` без ``alt``/``aşağı``/``purjun`` или
        ``üst``/``amortizator``/``opora`` — это переспрос, а не догадка.
        """
        present = set(tokens)
        for term, allowed in self._qualifiers.items():
            rule = self._by_term.get(term)
            if rule is None or term not in present:
                continue
            if present & allowed:
                return None, None        # уточнитель есть — разводит словарь
            return term, rule
        return None, None

    def check(self, item_raw: str, content_tokens,
              resolve_exact=None) -> AmbiguousTerm | None:
        """Голый неоднозначный термин в запросе — или ``None``.

        ``content_tokens`` — та же функция, которой пользуется ретривер: она
        убирает слова положения, количества и вежливости. Что осталось, и есть
        содержание запроса; если это ровно неоднозначный термин — отвечать
        нельзя.

        ``resolve_exact`` — «какие коды словарь даёт на эту фразу целиком, если
        знает её точным термином». Нужен потому, что для одного правила слово
        положения — мусор, а для другого оно и есть уточнение. В правиле
        ``çaşka`` прямо сказано: ``alt`` → AS-029, ``üst`` → AS-034, и словарь
        обе формы знает. Отбросив ``alt`` как слово положения, слой переспросил
        бы там, где ответ уже есть.

        Поэтому решает не список слов, а сам словарь: знает фразу целиком и
        ведёт ею в одну из двух деталей правила — вопрос не нужен. Ни одного
        слова-исключения при этом выписывать не приходится.
        """
        tokens = content_tokens(item_raw or "")
        if not tokens:
            return None
        key = " ".join(tokens)
        rule = self._by_term.get(key)
        if rule is None:
            # Термин с явными уточнителями ловим и внутри длинной фразы:
            # по решению заказчика «çaşka» без alt/üst — это переспрос, даже
            # если рядом стоит марка («Qabaq sağ çaşka lenforderle»).
            key, rule = self._find_qualified_term(tokens)
            if rule is None:
                return None
        if resolve_exact is not None:
            resolved = set(resolve_exact(item_raw) or ())
            if resolved and resolved <= set(rule.get("codes", ())):
                # Словарь сам развёл фразу внутри пары — спрашивать нечего.
                return None
        return AmbiguousTerm(term=key,
                             codes=list(rule.get("codes", [])),
                             parts=list(rule.get("parts", [])),
                             question=rule.get("question", ""),
                             matcher_rule=rule.get("matcher_rule", ""))


def load_rules(path: str = RULES_PATH) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return json.load(fh).get("rules", [])


def load_qualifiers(path: str = QUALIFIERS_PATH) -> dict[str, list[str]]:
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        terms = json.load(fh).get("terms", {})
    return {term: list(body.get("qualifiers", [])) for term, body in terms.items()}


@lru_cache(maxsize=1)
def load(path: str = RULES_PATH) -> AmbiguityRules:
    return AmbiguityRules(load_rules(path), load_qualifiers())
