"""Узкие, подтверждённые человеком правила после ручной проверки etalon-200.

Здесь нет выбора part_id по догадке. Модуль либо требует уточнение там, где
конкретный код опасен, либо чинит очевидную техническую сегментацию.
"""
from __future__ import annotations
import json, os
from functools import lru_cache
from .dictionary import normalize

_DATA=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),"data")
_RULES=os.path.join(_DATA,"no_answer_rules.json")

@lru_cache(maxsize=1)
def _rules():
    if not os.path.exists(_RULES): return []
    with open(_RULES,encoding="utf-8") as fh: return json.load(fh).get("rules",[])

def no_answer_guard(text:str):
    toks=normalize(text or "").split()
    for rule in _rules():
        prefixes=rule.get("all_prefixes",[])
        if prefixes and all(any(t.startswith(p) for t in toks) for p in prefixes):
            return rule.get("id","guard"), rule.get("question","")
    return None

def resplit_known_phrase(text:str):
    n=normalize(text or "")
    # Покупатель перечисляет два фильтра без союза: «Yağ Hava filtiri».
    if n in {"yag hava filtiri","yag hava filtri"}:
        return ["Yağ filtri", "Hava filtiri"]
    # Один живой запрос просит наружные канты сразу в двух зонах: над
    # крыльями/арками и снизу дверей. Это две разные каталожные позиции.
    if "krulolqrin usdu" in n and "qapilarin alti" in n and "qant" in n:
        return ["krulolqrin usdu qara qant", "qapilarin alti qant"]
    # В одном item модель склеила ДХО и трос люка; оба термина самостоятельны.
    if ("gunduz" in n and "isiq" in n and "lyuk" in n and
            any(t.startswith("tros") for t in n.split())):
        return ["gündüz işıqı", "lyuk trosu"]
    return None

def merge_direction_tail(items):
    """Вернуть список items, слив отдельное yuxarı/aşağı в мотор сиденья."""
    if len(items)<2: return items
    out=[]
    for item in items:
        n=normalize(item.item_raw)
        direction_only=(set(n.split()) <= {"yuxari","asagi","asaqi","yuxarı","aşağı"} and bool(n))
        if direction_only and out and "oturacaq" in normalize(out[-1].item_raw) and "mator" in normalize(out[-1].item_raw):
            out[-1].item_raw=(out[-1].item_raw+" "+item.item_raw).strip()
            out[-1].source_fragments.extend(item.source_fragments)
            out[-1].reason += "; review27_direction_tail_merged"
        else:
            out.append(item)
    for i,item in enumerate(out): item.item_index=i
    return out


def confirmed_context_code(text:str):
    """Код только для контекстов, явно подтверждённых при ручной проверке."""
    n=normalize(text or "")
    toks=n.split(); present=set(toks)
    if "prk" in present:
        if any(t.startswith("qalofka") for t in toks): return "MU-078"
        if any(t.startswith("krsk") for t in toks) and any(t.startswith("ust") for t in toks): return "MU-014"
        if any(t.startswith("svec") for t in toks): return "MU-109"
        if any(t.startswith("kollektor") or t.startswith("kalektor") for t in toks): return "MU-087"
        if any(t.startswith("vakum") for t in toks): return "MU-110"
    if any(t.startswith("paxlava") for t in toks) and any(t.startswith("ablisov") or t.startswith("oblisov") for t in toks):
        return "KZ-086"
    if any(t.startswith("elektromexanik") for t in toks) and ("blok" in present or any(t.startswith("beyn") for t in toks)):
        return "SU-020"
    return None
