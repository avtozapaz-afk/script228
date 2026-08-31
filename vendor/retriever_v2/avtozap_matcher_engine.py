import json
import os
from avtozap_ambiguity_layer import AmbiguityLayer, na
import openpyxl
from rapidfuzz import process, fuzz
from collections import defaultdict

# ЕДИНСТВЕННАЯ правка вендорного кода: путь к словарю разрешается относительно
# репозитория, а не текущего каталога, — иначе импорт зависит от того, откуда
# запущен процесс. Логика движка не менялась.
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DICT = os.environ.get(
    'AVTOZAP_DICT_XLSX',
    os.path.join(_REPO, 'data', 'AVTOZAP_slovar_FINAL_541.xlsx'))
_wb=openpyxl.load_workbook(DICT,read_only=True)
_pr=[r for r in _wb['parts_synonyms'].iter_rows(values_only=True)]; HP=_pr[0]
PARTS={r[0]:dict(zip(HP,r)) for r in _pr[1:] if r[0]}
IDX=defaultdict(set); TERM_SUB={}
for r in [x for x in _wb['terms_normalized'].iter_rows(values_only=True)][1:]:
    if r[0] and r[3]:
        k=na(r[3]); IDX[k].add(r[0])
KEYS=list(IDX)
AL=AmbiguityLayer()
SUB={c:p['category'] for c,p in PARTS.items()}

# Победители в частых столкновениях: подножка (а не электроподножка),
# рулевая тяга (а не шаровой палец), распредвал (а не его датчик),
# втулка развала (а не сайлентблок рычага).
PREFER={'TY-001','SU-002','MU-003','AS-013'}

def phrases(t):
    for L in range(len(t),0,-1):
        for i in range(len(t)-L+1): yield L,' '.join(t[i:i+L])

def match(text, fuzzy_cut=92, fuzzy_gap=3, guard_sub=True):
    toks=na(text).split()
    if not toks: return None,'no_match'
    c,rule=AL.context_rule(text)
    if c: return c,'context_rule'
    hits=[]
    for L,ph in phrases(toks):
        if ph in IDX: hits.append((L/len(toks),L,ph,IDX[ph]))
    if hits:
        top=max(h[:2] for h in hits); codes=set()
        for h in hits:
            if h[:2]==top: codes|=h[3]
        if len(codes)==1: return next(iter(codes)),'exact'
        # При равном совпадении нескольких деталей выбираем ту, которую
        # покупатели просят в подавляющем большинстве случаев. Проверено
        # на живых заявках; узкая деталь берётся только по своему слову.
        pref = PREFER & codes
        if len(pref)==1: return next(iter(pref)),'exact'
        return None,'ambiguous'
    # fuzzy
    for L,ph in phrases(toks):
        if len(ph)<5: continue
        got=process.extract(ph,KEYS,scorer=fuzz.ratio,limit=3,score_cutoff=fuzzy_cut)
        if not got: continue
        best=got[0]; cs=IDX[best[0]]
        if len(cs)!=1: return None,'ambiguous'
        code=next(iter(cs))
        if len(got)>1:
            second=got[1]
            cs2=IDX[second[0]]
            # мал запас и другая подкатегория -> не угадываем
            if best[1]-second[1] < fuzzy_gap:
                if guard_sub and any(SUB.get(x)!=SUB.get(code) for x in cs2):
                    return None,'fuzzy_blocked'
        return code,'fuzzy'
    return None,'no_match'
