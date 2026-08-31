"""Layer 0 — сегментатор: одно сырое сообщение → атомарные запрошенные предметы.

Один item = один запрошенный ТИП детали.

    «Naklatka və tormuz disk»                  → 2 items (колодка + диск)
    «babin və sveça»                           → 2 items (катушка + свеча)
    «mühərrik və sürətlər qutusunun yastıqları» → 2 items (опора двигателя + опора КПП)

Но НЕ дробим, когда та же самая каноническая деталь запрошена в нескольких
позициях — это один предмет с атрибутами:

    «ön və arxa bufer»       → 1 item, position = ön,arxa
    «sol və sağ güzgü»       → 1 item, side = sol,sağ
    «ön bufer və arxa bufer» → 1 item (обе половины дают одну и ту же «голову»)

Порядок работы:

1. режем сообщение по разделителям (запятая, ``+``, перевод строки, союзы
   ``və / ve / и / and``);
2. вырезаем контекст автомобиля (марка, модель, год) — он не деталь;
3. фрагменты из одних атрибутов («ön», «sol») прилипают к соседнему предмету;
4. восстанавливаем эллипсис: «mühərrik və ... yastıqları» → «mühərrik yastıqları»,
   но ТОЛЬКО если получившаяся фраза реально есть в словаре;
5. сливаем фрагменты, у которых совпала словарная «голова».

Полный исходный текст сохраняется отдельно как контекст; матчинг ниже по
конвейеру работает уже по атомарному ``item_raw``.
"""

from __future__ import annotations

import re

from slovar_matcher.normalize import az_lower, tokens

from .config import (
    CONJUNCTIONS,
    NOISE_WORDS,
    POSITION_WORDS,
    QUANTITY_WORDS,
    SIDE_WORDS,
    VEHICLE_BRANDS,
)
from .retriever import RetrieverV2, content_tokens
from .types import L0_EMPTY, L0_FALLBACK, L0_OK, Layer0Item, Layer0Result

# Явные разделители: запятая, точка с запятой, плюс, амперсанд, перевод строки,
# маркеры списка. Слэш НЕ разделитель — он встречается внутри имён деталей.
_SPLIT_RE = re.compile(r"[,\n;+&]|(?:^|\s)[-–—•*]\s")
_YEAR_RE = re.compile(r"^(19[5-9]\d|20[0-4]\d)$")
_CHASSIS_RE = re.compile(r"^[a-z]{1,2}\d{2,3}$")

_ATTRIBUTE_WORDS = SIDE_WORDS | POSITION_WORDS | QUANTITY_WORDS
_DROPPABLE = _ATTRIBUTE_WORDS | NOISE_WORDS | CONJUNCTIONS


def _is_vehicle_token(tok: str, retriever: RetrieverV2) -> bool:
    """Токен описывает автомобиль, а не деталь.

    Страховка от ложных срабатываний: если слово есть в словаре запчастей, оно
    НЕ вырезается, чем бы оно ни выглядело.
    """
    if tok in retriever._postings:
        return False
    return (tok in VEHICLE_BRANDS
            or bool(_YEAR_RE.match(tok))
            or bool(_CHASSIS_RE.match(tok)))


class Layer0:
    def __init__(self, retriever: RetrieverV2):
        self.retriever = retriever

    # ── публичный API ───────────────────────────────────────────────────────
    def segment(self, original_text: str) -> Layer0Result:
        text = (original_text or "").strip()
        if not text:
            return Layer0Result(status=L0_EMPTY, reason="пустое сообщение")

        raw_fragments = self._split(text)
        vehicle_words: list[str] = []
        cleaned: list[str] = []
        for frag in raw_fragments:
            kept, vehicle = self._strip_vehicle(frag)
            vehicle_words.extend(vehicle)
            cleaned.append(kept)

        fragments = [f for f in cleaned if f.strip()]
        if not fragments:
            # В сообщении не осталось ничего, кроме контекста авто/шума —
            # честнее отдать весь текст одним предметом, чем выдумывать разбор.
            return Layer0Result(
                items=[Layer0Item(item_index=0, item_raw=text, status=L0_FALLBACK,
                                  reason="в сообщении нет распознаваемого названия детали",
                                  source_fragments=[text])],
                status=L0_FALLBACK,
                reason="фрагменты не содержат значимых слов",
                vehicle_context=" ".join(dict.fromkeys(vehicle_words)),
                raw_fragments=raw_fragments,
            )

        units = self._attach_attribute_only(fragments)
        units = self._expand_ellipsis(units)
        units = self._merge_same_head(units)

        items: list[Layer0Item] = []
        for i, unit in enumerate(units):
            items.append(Layer0Item(
                item_index=i,
                item_raw=unit["text"],
                status=L0_OK,
                reason=unit["reason"],
                side_hint=unit["side"],
                position_hint=unit["position"],
                source_fragments=unit["fragments"],
            ))

        return Layer0Result(
            items=items,
            status=L0_OK,
            reason=f"{len(items)} атомарн. предмет(ов) из {len(raw_fragments)} фрагмент(ов)",
            vehicle_context=" ".join(dict.fromkeys(vehicle_words)),
            raw_fragments=raw_fragments,
        )

    # ── шаги ────────────────────────────────────────────────────────────────
    def _split(self, text: str) -> list[str]:
        """Разрезать по пунктуации и союзам, сохранив порядок фрагментов."""
        chunks = [c.strip() for c in _SPLIT_RE.split(text) if c and c.strip()]
        out: list[str] = []
        for chunk in chunks:
            current: list[str] = []
            for word in chunk.split():
                if az_lower(word.strip(".!?:()")) in CONJUNCTIONS:
                    if current:
                        out.append(" ".join(current))
                        current = []
                    continue
                current.append(word)
            if current:
                out.append(" ".join(current))
        return out or [text.strip()]

    def _strip_vehicle(self, fragment: str) -> tuple[str, list[str]]:
        kept: list[str] = []
        vehicle: list[str] = []
        for word in fragment.split():
            tok = az_lower(word.strip(".,!?:()"))
            if tok and _is_vehicle_token(tok, self.retriever):
                vehicle.append(word)
            else:
                kept.append(word)
        return " ".join(kept), vehicle

    def _attach_attribute_only(self, fragments: list[str]) -> list[dict]:
        """Фрагмент из одних атрибутов не предмет — он прилипает к соседнему."""
        units: list[dict] = []
        pending: list[str] = []

        def new_unit(text: str, fragments_: list[str], reason: str) -> dict:
            return {"text": text, "fragments": list(fragments_),
                    "side": None, "position": None, "reason": reason}

        for frag in fragments:
            toks = [t for t in tokens(frag)]
            meaningful = [t for t in toks if t not in _DROPPABLE and not t.isdigit()]
            if not meaningful:
                pending.append(frag)          # «ön», «sol», «2 ədəd» — сами по себе не предмет
                continue
            text = " ".join(pending + [frag]) if pending else frag
            reason = "attribute_prefix_merged" if pending else "split"
            units.append(new_unit(text, pending + [frag], reason))
            pending = []

        if pending:
            if units:                          # хвостовые атрибуты — к последнему предмету
                units[-1]["text"] = units[-1]["text"] + " " + " ".join(pending)
                units[-1]["fragments"].extend(pending)
                units[-1]["reason"] = "attribute_suffix_merged"
            else:
                units.append(new_unit(" ".join(pending), pending, "attributes_only"))
        return units

    def _expand_ellipsis(self, units: list[dict]) -> list[dict]:
        """«mühərrik və <...> yastıqları» → «mühərrik yastıqları».

        Расширяем короткий фрагмент хвостом следующего ТОЛЬКО если результат —
        реально существующий ключ словаря. Это гарантирует, что мы не
        придумываем деталь, а лишь восстанавливаем опущенное слово.
        """
        for i in range(len(units) - 1):
            left, right = units[i], units[i + 1]
            left_tokens = content_tokens(left["text"])
            right_tokens = content_tokens(right["text"])
            if not left_tokens or len(left_tokens) > 2 or len(right_tokens) < 2:
                continue
            # Единственная защита от выдумывания — сама проверка на точный ключ:
            # расширение принимается, только если такая запись в словаре есть.
            # Самый короткий хвост берём первым: «mühərrik» + «yastıqları».
            for k in range(len(right_tokens) - 1, 0, -1):
                candidate = " ".join(left_tokens + right_tokens[k:])
                if self.retriever.is_exact_key(candidate):
                    left["text"] = candidate
                    left["reason"] = "ellipsis_expanded"
                    break
        return units

    def _merge_same_head(self, units: list[dict]) -> list[dict]:
        """Слить предметы, которые описывают одну и ту же каноническую деталь.

        Это правило «перед + зад одного бампера — один предмет»: дробим по типу
        детали, а не по позициям, в которых её просят. Сливаем, если

        * фрагменты совпадают дословно после удаления слов стороны/позиции/
          количества («ön bufer» ≡ «arxa bufer» → оба «bufer»), либо
        * у обоих одна и та же непустая словарная «голова» («bufer» ≡ «бампер»).

        Разные детали при этом не склеиваются: «ön fara» и «arxa fanar» после
        очистки дают «fara» и «fanar» — разные слова, разные головы.
        """
        merged: list[dict] = []
        signatures: list[tuple[tuple[str, ...], frozenset[str] | None]] = []

        for unit in units:
            stripped = tuple(_strip_attributes(unit["text"]))
            head = self.retriever.head_part_ids(unit["text"])
            signature = (stripped, frozenset(head) if head else None)

            target = None
            for j, (other_stripped, other_head) in enumerate(signatures):
                same_words = bool(stripped) and stripped == other_stripped
                same_head = (signature[1] is not None
                             and signature[1] == other_head)
                if same_words or same_head:
                    target = j
                    break

            if target is None:
                merged.append(unit)
                signatures.append(signature)
                continue

            host = merged[target]
            host["fragments"].extend(unit["fragments"])
            host["text"] = host["text"] + " " + unit["text"]
            host["reason"] = "same_head_merged"

        for unit in merged:
            unit["text"] = _trim_noise(unit["text"]) or unit["text"]
            unit["side"] = _detect_words(unit["text"], SIDE_WORDS)
            unit["position"] = _detect_words(unit["text"], POSITION_WORDS)
        return merged


def _strip_attributes(text: str) -> list[str]:
    """Токены фразы без слов стороны/позиции/количества и шума."""
    return [t for t in tokens(text)
            if t not in _DROPPABLE and not t.isdigit()]


def _trim_noise(text: str) -> str:
    """Срезать вежливость по краям («Salam, radiator var?» → «radiator»).

    Только по краям и только слова-шум: слова стороны и позиции информативны и
    остаются внутри ``item_raw``, а середина фразы не трогается вовсе.
    """
    words = text.split()
    while words and az_lower(words[0].strip(".,!?:()")) in NOISE_WORDS:
        words.pop(0)
    while words and az_lower(words[-1].strip(".,!?:()")) in NOISE_WORDS:
        words.pop()
    return " ".join(words).strip()


def _detect_words(text: str, vocabulary: set[str]) -> str | None:
    found = [t for t in tokens(text) if t in vocabulary]
    return ",".join(dict.fromkeys(found)) if found else None


def segment(original_text: str, retriever: RetrieverV2 | None = None) -> Layer0Result:
    """Разовый вызов: собрать сегментатор по умолчанию и разобрать сообщение."""
    return Layer0(retriever or RetrieverV2.from_file()).segment(original_text)
