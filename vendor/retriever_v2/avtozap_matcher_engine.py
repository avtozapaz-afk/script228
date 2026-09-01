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
    os.path.join(_REPO, 'data', 'AVTOZAP_slovar_FINAL_571.xlsx'))
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


# --- Морфология: азербайджанские окончания множественного числа и
# принадлежности. Без этого "amortizatorlar" не находится, хотя
# "amortizator" в словаре есть. Отрезаем по одному суффиксу и проверяем,
# нашлось ли слово; настоящую основу не трогаем.
SUF = ("larinin","lerinin","larini","lerini","lardan","lerden","larin","lerin",
       "lari","leri","lar","ler","nin","nun","in","un","im","si","su","si",
       "sa","se","ni","nu","na","ne","da","de","dan","den","i","u","a","e")

def _stem(w, depth=2):
    """Возвращает варианты слова с отрезанными окончаниями."""
    out=[w]
    cur=w
    for _ in range(depth):
        for suf in SUF:
            if len(cur)>len(suf)+3 and cur.endswith(suf):
                cur=cur[:-len(suf)]
                out.append(cur)
                break
        else:
            break
    return out

def _lookup(ph):
    """Точный поиск фразы; если не нашлось — пробуем отрезать окончания."""
    if ph in IDX: return IDX[ph]
    k=_key(ph)
    if k and k in STEM_IDX: return STEM_IDX[k]
    words=ph.split()
    variants=[[w] + [v for v in _stem(w) if v!=w] for w in words]
    # сначала меняем только последнее слово (главное в азербайджанской фразе)
    for i in range(len(words)-1,-1,-1):
        for v in variants[i][1:]:
            probe=' '.join(words[:i]+[v]+words[i+1:])
            if probe in IDX: return IDX[probe]
    return None

# --- Русская морфология. В словаре русское название заведено только в
# каноничной форме ("Тормозная колодка"), а покупатели пишут как придётся
# ("тормозные колодки"). Отрезаем окончания и сравниваем по основам.
RU_SUF=("ами","ями","ого","ему","ому","ыми","ими","ах","ях","ам","ям","ов","ев",
        "ые","ый","ой","ая","ое","ии","ий","ью","ем","ом","и","ы","а","я","у","ю","е","ь")

def _ru_stem(w):
    for suf in RU_SUF:
        if len(w)>len(suf)+3 and w.endswith(suf):
            return w[:-len(suf)]
    return w

def _key(ph):
    """Ключ по основам слов — работает и для русского, и для латиницы."""
    ws=[w for w in ph.split() if len(w)>2]
    if not ws: return None
    return tuple(sorted(_ru_stem(w) for w in ws))

STEM_IDX={}
for _t,_cs in IDX.items():
    _k=_key(_t)
    if not _k: continue
    STEM_IDX.setdefault(_k,set()).update(_cs)


# Частичное совпадение по основам: покупатель редко пишет полное каноничное
# название. "Защита картера" должно находить "Защита картера двигателя".
SUB_IDX={}
for _k,_cs in STEM_IDX.items():
    if len(_k)<2: continue
    for _i in range(len(_k)):
        _sub=tuple(_k[:_i]+_k[_i+1:])
        if len(_sub)>=2:
            SUB_IDX.setdefault(_sub,set()).update(_cs)


def phrases(t):
    for L in range(len(t),0,-1):
        for i in range(len(t)-L+1): yield L,' '.join(t[i:i+L])


# Мусорные слова: марка, модель, вежливые обороты, количество. Они не
# называют деталь, но мешают: фраза перестаёт совпадать целиком.
STOP=set(['lazimdi', 'lazimdir', 'lazim', 'ucun', 'olan', 'eded', 'edet', 'orginal', 'orijinal', 'original', 'teref', 'terefi', 'var', 'varmi', 'yazin', 'zehmet', 'olmasa', 'salam', 'xais', 'xahis', 'edirem', 'qiymet', 'maşin', 'masin', 'model', 'ili', 'ci', 'il', 'hyundai', 'opel', 'astra', 'kia', 'toyota', 'bmw', 'mercedes', 'ford', 'nissan', 'chevrolet', 'sevralet', 'volkswagen', 'wolswagen', 'audi', 'lexus', 'honda', 'mazda', 'renault', 'skoda', 'elantra', 'sonata', 'accent', 'tucson', 'santafe', 'malibu', 'cruze', 'passat', 'polo', 'focus', 'fokus', 'prius', 'optima', 'sportage', 'cerato', 'serato', 'benzin', 'dizel', 'amerikanka', 'amerkanka', 'yaponka', 'komplekt'])

def _clean(toks):
    out=[t for t in toks if t not in STOP and not t.isdigit()]
    return out or toks

# Односложные заявки: если покупатель написал ровно одно значащее слово и это
# название узла целиком — отдаём узел. В составе фразы ("matorun paduskasi")
# это слово деталью НЕ считается, поэтому в словарь его заводить нельзя.
SOLO={'mator':'MU-001','motor':'MU-001','matoru':'MU-001','motoru':'MU-001',
      'muherrik':'MU-001','dvigatel':'MU-001','двигатель':'MU-001',
      'karopka':'TR-001','karobka':'TR-001','karopkasi':'TR-001','korobka':'TR-001',
      'коробка':'TR-001','suretler qutusu':'TR-001'}

def _solo(toks):
    NOISE={'lazimdi','lazimdir','lazim','ucun','var','varmi','olan','eded','salam',
           'orginal','orijinal','original','нужен','нужна','нужно','sade','zavod'}
    znach=[t for t in toks if len(t)>2 and t not in NOISE]
    if len(znach)==1 and znach[0] in SOLO: return SOLO[znach[0]]
    return None

def match(text, fuzzy_cut=92, fuzzy_gap=3, guard_sub=True):
    toks=na(text).split()
    if not toks: return None,'no_match'
    solo=_solo(toks)
    if solo: return solo,'solo_word'
    c,rule=AL.context_rule(text)
    if c: return c,'context_rule'
    hits=[]
    for L,ph in phrases(toks):
        got=_lookup(ph)
        if got: hits.append((L/len(toks),L,ph,got))
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
        # Несколько разных деталей в одной заявке (перечисление через "ve",
        # запятую и т.п.): берём ту, что упомянута первой, вместо отказа.
        if len(codes)>1:
            best=None; bestpos=10**9
            for h in hits:
                if h[:2]!=top: continue
                if len(h[3])!=1: continue
                pos=na(text).find(h[2])
                if pos>=0 and pos<bestpos: bestpos=pos; best=next(iter(h[3]))
            if best: return best,'exact_first_of_several'
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
