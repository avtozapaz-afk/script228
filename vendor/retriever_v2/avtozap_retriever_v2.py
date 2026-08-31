from __future__ import annotations
from collections import defaultdict
from dataclasses import dataclass
from rapidfuzz import process, fuzz
import re

import avtozap_matcher_engine as legacy
from avtozap_ambiguity_layer import na, AmbiguityLayer

# These words carry attributes/context but usually should not dominate part retrieval.
ALIASES={
 'motor':{'mator','muherrik'}, 'motorun':{'mator','muherrik'}, 'mator':{'motor','muherrik'}, 'muherrik':{'mator','motor'},
 'tormuz':{'eylec'}, 'tormoz':{'eylec'}, 'eylec':{'tormuz','tormoz'},
 'remen':{'kemer','qayis'}, 'kemer':{'remen','qayis'}, 'qayis':{'remen','kemer'},
 'karopka':{'karobka','qutu','suretler'}, 'karobka':{'karopka','qutu','suretler'},
 'paduska':{'padusq','yastiq','yastig'}, 'padusq':{'paduska','yastiq','yastig'},
 'naklatka':{'nakladka'}, 'nakladka':{'naklatka'},
}

MODIFIERS={
 'sol','sag','qabaq','on','arxa','alt','ust','ic','col','daxili','xarici','zbor','komplekt','tam',
 'original','orjinal','arginal','lazim','lazimdi','lazimdir','eded','tere','teref','terefi','ucun','ve','ile',
 'birlikde','bir','yerde','sürücü','surucu','sernisin','tərəf','terefden'
}

# Conservative Azerbaijani/Turkic/Russian-style inflection stripping. Candidate generation only.
SUFFIXES=sorted({
 'larinin','lerinin','larina','lerine','lardan','lerden','lari','leri','ların','lərin',
 'unun','ünün','inin','ının','un','ün','in','ın','nin','nın','nun','nün',
 'lari','leri','lar','ler','asi','esi','si','sı','su','sü','i','ı','u','ü',
 'dan','den','da','de','dir','dır','dur','dür','nin','nun','nın','nün'
}, key=len, reverse=True)

def stem_token(tok:str)->str:
    t=tok
    # normalize common typo/phonetic variants useful only for retrieval
    repl=(('sh','s'),('ch','c'))
    for a,b in repl: t=t.replace(a,b)
    for suf in SUFFIXES:
        if len(t)-len(suf) >= 4 and t.endswith(suf):
            t=t[:-len(suf)]
            break
    return t

def compact(s:str)->str:
    return na(s).replace(' ','')

@dataclass
class RHit:
    code:str
    score:float
    reason:str

class RetrieverV2:
    def __init__(self):
        self.parts=legacy.PARTS
        self.context=AmbiguityLayer()
        self.idx=legacy.IDX
        self.keys=legacy.KEYS
        self.term_codes=[]
        self.token_codes=defaultdict(set)
        self.stem_codes=defaultdict(set)
        self.compact_codes=defaultdict(set)
        self.all_tokens=set()
        for term,codes in self.idx.items():
            toks=term.split()
            self.term_codes.append((term,toks,codes))
            self.compact_codes[term.replace(' ','')].update(codes)
            for t in toks:
                if len(t)>=2:
                    self.token_codes[t].update(codes)
                    st=stem_token(t)
                    if len(st)>=3: self.stem_codes[st].update(codes)
                    self.all_tokens.add(t)
        self.all_tokens=list(self.all_tokens)
        self.compact_keys=list(self.compact_codes)

    def retrieve_codes(self,text:str,limit:int=16):
        n=na(text); toks=n.split()
        if not toks:return []
        scores={}; reasons=defaultdict(list); evidence=defaultdict(set)
        def add(code,score,reason,ev=None):
            if code not in self.parts:return
            if score>scores.get(code,0): scores[code]=score
            if ev: evidence[code].add(ev)
            if len(reasons[code])<5 and reason not in reasons[code]: reasons[code].append(reason)

        # 0. Existing context rules are useful as candidate generators, never final truth.
        c,rule=self.context.context_rule(text)
        if c: add(c,0.995,f'context:{rule}')

        # A. Exact phrases/ngrams remain strongest.
        for L,ph in legacy.phrases(toks):
            for c in self.idx.get(ph,()):
                add(c,0.90+0.09*L/max(1,len(toks)),f'exact:{ph}')

        # B. Exact token + morphological-stem retrieval. This is intentionally broad.
        info=[t for t in toks if t not in MODIFIERS and len(t)>=3]
        unresolved_tokens=[]
        for t in info:
            before=set(scores)

            for c in self.token_codes.get(t,()): add(c,0.78,f'token:{t}',t)
            st=stem_token(t)
            for c in self.stem_codes.get(st,()): add(c,0.76,f'stem:{st}',t)
            for av in ALIASES.get(t,set()) | ALIASES.get(st,set()):
                ast=stem_token(av)
                for c in self.token_codes.get(av,()): add(c,0.79,f'alias:{t}->{av}',t)
                for c in self.stem_codes.get(ast,()): add(c,0.78,f'alias_stem:{t}->{ast}',t)
            if set(scores)==before: unresolved_tokens.append(t)

        # C. No full dictionary scan here: inverted token/stem indexes above are the fast recall layer.

        # D. Whole phrase fuzzy only when deterministic retrieval is sparse.
        if len(scores) < 3:
            for term,sc,_ in process.extract(n,self.keys,scorer=fuzz.WRatio,limit=24,score_cutoff=60):
                fsc=.56 + .0038*sc
                for c in self.idx[term]: add(c,fsc,f'fuzzy_phrase:{term}:{sc:.0f}')

        # E. Token typo retrieval only for tokens that deterministic indexes could not explain.
        for q in unresolved_tokens:
            cutoff=72 if len(q)>=6 else 78
            for dt,sc,_ in process.extract(q,self.all_tokens,scorer=fuzz.ratio,limit=10,score_cutoff=cutoff):
                fsc=.58+.0035*sc
                for c in self.token_codes[dt]: add(c,fsc,f'fuzzy_token:{q}->{dt}:{sc:.0f}')

        # F. Joined spelling variants.
        cq=''.join(toks)
        if len(cq)>=5 and len(scores)<7:
            for ct,sc,_ in process.extract(cq,self.compact_keys,scorer=fuzz.WRatio,limit=12,score_cutoff=66):
                fsc=.57+.0035*sc
                for c in self.compact_codes[ct]: add(c,fsc,f'fuzzy_compact:{ct}:{sc:.0f}')

        # Prefer candidates supported by >1 independent signal.
        out=[]
        evid_counts={}
        for c,s in scores.items():
            multi=max(0,len(evidence[c])-1)
            bonus=min(.10, .018*max(0,len(reasons[c])-1) + .055*multi)
            evid_counts[c]=len(evidence[c])
            out.append(RHit(c,min(.999,s+bonus),' | '.join(reasons[c])))
        out.sort(key=lambda x:(-evid_counts.get(x.code,0),-x.score,x.code))
        return out[:limit]

if __name__=='__main__':
    r=RetrieverV2()
    for q in ['Motorun paduskalari','Qabaq amortizatorlar','tormuz disk','Naklatka','İç remen komplekt','Karopka yağı','sol alt şaravoy']:
        print('\n',q)
        for x in r.retrieve_codes(q,12): print(x)
