#!/usr/bin/env python3
"""Build the self-contained offline HTML tester (matcher_tool.html).

Exports the parsed dictionary to JSON and inlines it together with a JavaScript
port of the matcher (kept faithful to ``slovar_matcher``). Run after any change
to ``data/SLOVAR_FINAL.txt``::

    python build_html.py
"""

import json
import os

from slovar_matcher.parser import parse_file

HERE = os.path.dirname(os.path.abspath(__file__))


def export_data() -> str:
    d = parse_file(os.path.join(HERE, "data", "SLOVAR_FINAL.txt"))
    parts = {pid: {
        "name_ru": p.name_ru, "name_az": p.name_az,
        "category": p.category, "subcategory": p.subcategory,
        "leaf_code": p.leaf_code, "side": p.side_flag,
        "direction": p.direction_flag, "location": p.location_flag,
    } for pid, p in d.parts.items()}
    groups = {c: {"part_ids": g.part_ids} for c, g in d.groups.items()}
    data = {"parts": parts, "groups": groups,
            "name_index": d.name_index, "synonym_index": d.synonym_index}
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


TEMPLATE = r'''<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AVTOZAP — детерминированный матчер деталей</title>
<style>
  :root{
    --bg:#f5f6f8; --panel:#ffffff; --ink:#1c2330; --muted:#667085;
    --line:#e3e7ee; --accent:#2f6fed; --accent-ink:#fff;
    --ok:#1a7f4b; --ok-bg:#e7f6ee; --amb:#b26a00; --amb-bg:#fdf3e2;
    --no:#b42318; --no-bg:#fdeceb; --code:#0f172a; --code-ink:#e6edf6;
    --chip:#eef2f9; --ask:#8a4bd6; --ask-bg:#f2e9fc;
  }
  @media (prefers-color-scheme: dark){
    :root{
      --bg:#0f1420; --panel:#161c29; --ink:#e7ecf4; --muted:#9aa6b8;
      --line:#26304180; --accent:#5b8cff; --accent-ink:#0b1020;
      --ok:#5ad19a; --ok-bg:#12301f; --amb:#f0b45e; --amb-bg:#33260f;
      --no:#f0857c; --no-bg:#331715; --code:#0b101b; --code-ink:#d5e2f2;
      --chip:#1e2637; --ask:#c79bf2; --ask-bg:#241633;
    }
  }
  :root[data-theme="dark"]{
    --bg:#0f1420; --panel:#161c29; --ink:#e7ecf4; --muted:#9aa6b8;
    --line:#26304180; --accent:#5b8cff; --accent-ink:#0b1020;
    --ok:#5ad19a; --ok-bg:#12301f; --amb:#f0b45e; --amb-bg:#33260f;
    --no:#f0857c; --no-bg:#331715; --code:#0b101b; --code-ink:#d5e2f2;
    --chip:#1e2637; --ask:#c79bf2; --ask-bg:#241633;
  }
  :root[data-theme="light"]{
    --bg:#f5f6f8; --panel:#ffffff; --ink:#1c2330; --muted:#667085;
    --line:#e3e7ee; --accent:#2f6fed; --accent-ink:#fff;
    --ok:#1a7f4b; --ok-bg:#e7f6ee; --amb:#b26a00; --amb-bg:#fdf3e2;
    --no:#b42318; --no-bg:#fdeceb; --code:#0f172a; --code-ink:#e6edf6;
    --chip:#eef2f9; --ask:#8a4bd6; --ask-bg:#f2e9fc;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);
    font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
  .wrap{max-width:920px;margin:0 auto;padding:28px 18px 64px}
  header{display:flex;align-items:baseline;justify-content:space-between;gap:12px;flex-wrap:wrap}
  h1{font-size:20px;margin:0 0 2px}
  .sub{color:var(--muted);font-size:13px;margin:0 0 20px}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:14px;
    padding:18px;margin-bottom:16px}
  label{display:block;font-weight:600;font-size:13px;margin:0 0 6px}
  .hint{font-weight:400;color:var(--muted)}
  input[type=text]{width:100%;padding:11px 12px;border:1px solid var(--line);
    border-radius:10px;background:var(--bg);color:var(--ink);font-size:15px}
  input[type=text]:focus{outline:2px solid var(--accent);outline-offset:1px;border-color:transparent}
  .row{display:flex;gap:12px;flex-wrap:wrap;align-items:center;margin-top:14px}
  button{cursor:pointer;border:0;border-radius:10px;font-size:14px;font-weight:600;padding:11px 18px}
  .go{background:var(--accent);color:var(--accent-ink)}
  .chk{display:flex;align-items:center;gap:7px;color:var(--muted);font-size:13px;font-weight:500}
  .examples{display:flex;gap:8px;flex-wrap:wrap;margin-top:4px}
  .ex{background:var(--chip);color:var(--ink);border:1px solid var(--line);
    padding:7px 11px;border-radius:999px;font-size:12.5px;font-weight:500}
  .status-badge{display:inline-block;padding:4px 12px;border-radius:999px;font-weight:700;font-size:13px;letter-spacing:.2px}
  .s-single{background:var(--ok-bg);color:var(--ok)}
  .s-amb{background:var(--amb-bg);color:var(--amb)}
  .s-no{background:var(--no-bg);color:var(--no)}
  .summary{margin:14px 0 0}
  .kv{display:grid;grid-template-columns:150px 1fr;gap:6px 14px;margin-top:12px}
  .kv dt{color:var(--muted);font-size:13px}
  .kv dd{margin:0;font-weight:600}
  .cand{border:1px solid var(--line);border-radius:10px;padding:10px 12px;margin-top:8px;background:var(--bg)}
  .cand .pid{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--accent);font-weight:700}
  .ask{background:var(--ask-bg);border:1px solid var(--ask);border-radius:12px;padding:14px 16px;margin-top:14px}
  .ask h3{margin:0 0 8px;font-size:14px;color:var(--ask)}
  .ask .q{font-weight:600;margin:4px 0}
  .ask .opt{display:inline-block;background:var(--panel);border:1px solid var(--line);
    border-radius:999px;padding:4px 12px;margin:2px 6px 2px 0;font-size:13px;font-weight:700}
  .done{color:var(--ok);font-weight:600;margin-top:14px}
  .pid{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--accent);font-weight:700}
  .tline{padding:7px 2px;border-bottom:1px solid var(--line);font-size:14px}
  .tline:first-child{padding-top:0}
  .qblock{margin-top:14px}
  .qblock .q{font-weight:600;margin-bottom:10px}
  .chips{display:flex;flex-wrap:wrap;gap:8px}
  .chips.col{flex-direction:column;align-items:stretch}
  .chip{background:var(--chip);color:var(--ink);border:1px solid var(--line);border-radius:999px;
    padding:10px 15px;font-size:13.5px;font-weight:600;cursor:pointer;text-align:left;transition:border-color .12s}
  .chip:hover{border-color:var(--accent)}
  .chip.col{border-radius:12px}
  .final{margin-top:8px}
  .final h3{margin:0 0 12px;color:var(--ok);font-size:16px}
  pre{background:var(--code);color:var(--code-ink);border-radius:12px;padding:14px;overflow-x:auto;
    font:12.5px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;margin:14px 0 0}
  .muted{color:var(--muted)}
  .toggle{background:var(--chip);color:var(--ink);border:1px solid var(--line);padding:7px 12px;font-size:12.5px}
  details summary{cursor:pointer;color:var(--muted);font-size:13px;font-weight:600}
  .foot{color:var(--muted);font-size:12px;margin-top:22px;line-height:1.6}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div>
      <h1>AVTOZAP — воронка подбора детали</h1>
      <p class="sub">Категория → деталь → атрибуты · без LLM · каждый шаг: уверен или ОДИН вопрос кнопками</p>
    </div>
    <button class="toggle" id="theme">◐ тема</button>
  </header>

  <div class="card">
    <label for="phrase">Запрос клиента <span class="hint">(нормализованная фраза от Seller-модели)</span></label>
    <input type="text" id="phrase" placeholder="напр. emblema" autocomplete="off">
    <div class="examples" id="examples"></div>

    <div style="margin-top:16px">
      <label for="raw">Сырой текст клиента <span class="hint">(необязательно — для стороны/позиции)</span></label>
      <input type="text" id="raw" placeholder="напр. sol qabaq" autocomplete="off">
    </div>

    <div class="row">
      <button class="go" id="run">Начать подбор</button>
      <label class="chk">порог:
        <input type="text" id="threshold" value="0.90" style="width:58px;padding:6px 8px" autocomplete="off">
        <span class="hint">(1.0 = только точное; 0.90 = терпит опечатки)</span></label>
    </div>
  </div>

  <div class="card" id="dialog" style="display:none"></div>

  <details class="card">
    <summary>Как это работает</summary>
    <p class="muted" style="font-size:13.5px">
      <b>Слой 1 — категория.</b> По словарю смотрим, в скольких из 15 категорий слово вообще встречается:
      в одной → категория определена; в двух+ → <b>вопрос кнопками</b>; нигде (и near-match &lt; порога) →
      «не определено». Никаких вероятностей — только факт присутствия по словарю.<br><br>
      <b>Слой 2 — деталь.</b> Как категория известна, матчер ищет деталь <b>только внутри неё</b>
      (точное имя → синоним группы → near-match ≥ порога). Одна деталь → дальше; несколько →
      <b>вопрос кнопками</b>; ничего → «не найдено».<br><br>
      <b>Атрибуты.</b> У детали спрашиваются <b>только</b> те флаги (сторона / позиция), что у неё
      <code>true</code> и не найдены в тексте — тоже кнопками. Если флаг <code>false</code> — не спрашиваем.<br><br>
      Никакого угадывания: на каждом шаге либо уверенность, либо ровно один вопрос.
    </p>
  </details>

  <p class="foot" id="foot"></p>
</div>

<script>
const DATA = __DATA__;
</script>
<script>
const CATIDX = __CATIDX__;
</script>
<script>
/* ---- normalization (mirrors slovar_matcher/normalize.py) ---- */
const AZ_MAP={'İ':'i','I':'ı','Ə':'ə','Ç':'ç',
  'Ş':'ş','Ğ':'ğ','Ö':'ö','Ü':'ü'};
function azLower(s){let o='';for(const ch of s)o+=(AZ_MAP[ch]||ch);return o.toLowerCase();}
const TOKEN_RE=/[0-9a-zçğıəöüşЀ-ӿ]+/g;
function tokens(s){return azLower(s).match(TOKEN_RE)||[];}
function normalize(s){return tokens(s).join(' ');}  // drops punctuation/symbols

/* ---- attribute keyword sets (mirrors slovar_matcher/attributes.py) ---- */
const SIDE_LEFT=new Set(["sol","sola","soldan","soldakı","soldaki","left","lh","лево","левый","левая","левое","левых","левого","слева"]);
const SIDE_RIGHT=new Set(["sağ","sag","sağa","sağdan","sagdan","sağdakı","sagdaki","right","rh","право","правый","правая","правое","правых","правого","справа"]);
const DIR_FRONT=new Set(["ön","öndeki","öndəki","önki","qabaq","qabağ","qabaqdakı","qabaqdaki","qabağdakı","front","fr","перед","передний","передняя","переднее","передних","переднего","спереди","speredi"]);
const DIR_REAR=new Set(["arxa","arxadakı","arxadaki","arxadan","arxadaku","rear","back","зад","задний","задняя","заднее","задних","заднего","сзади"]);
const LOC_INNER=new Set(["daxili","daxildəki","daxildaki","daxil","iç","içəri","icheri","içdəki","icdeki","inner","internal","внутренний","внутренняя","внутреннее","внутренних","внутреннего","внутри","vnutrenniy","до","before","əvvəl","əvvəlki","evvel","öncə","once"]);
const LOC_OUTER=new Set(["xarici","xaricdəki","xaricdaki","xaric","çöl","cöl","col","çöldəki","coldeki","outer","external","outside","наружный","наружная","наружное","наружных","наружного","снаружи","naruzhnyy","после","after","sonra","sonrakı","sonraki"]);
function detect(text,L,R,lv,rv){const t=new Set(tokens(text));let l=false,r=false;
  for(const x of t){if(L.has(x))l=true;if(R.has(x))r=true;}
  if(l&&r)return lv+","+rv;if(l)return lv;if(r)return rv;return null;}
const detectSide=t=>detect(t,SIDE_LEFT,SIDE_RIGHT,"sol","sağ");
const detectDirection=t=>detect(t,DIR_FRONT,DIR_REAR,"ön","arxa");
const detectLocation=t=>detect(t,LOC_INNER,LOC_OUTER,"daxili","xarici");

/* ---- similarity (mirrors slovar_matcher/normalize.py) ---- */
function lev(a,b){const m=a.length,n=b.length;if(!m)return n;if(!n)return m;
  let prev=Array.from({length:n+1},(_,i)=>i);
  for(let i=1;i<=m;i++){let cur=[i];for(let j=1;j<=n;j++){
    cur[j]=Math.min(prev[j]+1,cur[j-1]+1,prev[j-1]+(a[i-1]===b[j-1]?0:1));}prev=cur;}
  return prev[n];}
function similarity(a,b){const M=Math.max(a.length,b.length);return M?1-lev(a,b)/M:1;}

/* ---- matcher (mirrors slovar_matcher/matcher.py) ---- */
const DEFAULT_THRESHOLD=0.90,EPS=1e-9;
const round3=x=>Math.round(x*1000)/1000;
function groupIds(codes){const ids=[];for(const c of codes)
  for(const pid of DATA.groups[c].part_ids) if(!ids.includes(pid)) ids.push(pid);return ids;}
function fuzzyRefs(key,index,threshold){let best=0;const hits=[];
  for(const k in index){const s=similarity(key,k);if(s>=threshold){hits.push([s,k]);if(s>best)best=s;}}
  if(!hits.length)return[0,[]];const refs=[];
  for(const [s,k] of hits) if(Math.abs(s-best)<EPS)
    for(const r of index[k]) if(!refs.includes(r)) refs.push(r);
  return[best,refs];}
function noMatch(){return{status:"no_match",part_id:null,name_ru:null,name_az:null,category:null,
  subcategory:null,attributes:{side:null,direction:null,location:null},needs_clarification:[],candidates:[]};}
function match(phrase,raw,threshold,restrict){
  if(threshold==null)threshold=DEFAULT_THRESHOLD;
  const key=normalize(phrase);
  const nameHit=DATA.name_index[key];
  if(nameHit){const ids=nameHit.slice();
    const synHit=DATA.synonym_index[key];  // collision guard: name == other group's synonym
    if(synHit){const owner=new Set(nameHit.map(p=>DATA.parts[p].leaf_code));
      const extra=synHit.filter(c=>!owner.has(c));
      for(const pid of groupIds(extra)) if(!ids.includes(pid)) ids.push(pid);}
    return build(ids,phrase,raw,1.0,restrict);}
  if(DATA.synonym_index[key])return build(groupIds(DATA.synonym_index[key]),phrase,raw,1.0,restrict);
  const [ns,pids]=fuzzyRefs(key,DATA.name_index,threshold);
  if(pids.length)return build(pids,phrase,raw,round3(ns),restrict);
  const [ss,codes]=fuzzyRefs(key,DATA.synonym_index,threshold);
  if(codes.length)return build(groupIds(codes),phrase,raw,round3(ss),restrict);
  return noMatch();
}
function build(ids,phrase,raw,score,restrict){
  if(restrict!=null){ids=ids.filter(i=>DATA.parts[i].category===restrict); if(!ids.length)return noMatch();}
  return ids.length===1?single(ids[0],phrase,raw,score):ambiguous(ids,score);}
function single(pid,phrase,raw,score){const p=DATA.parts[pid];
  const space=[phrase,raw].filter(Boolean).join(" ");
  const attributes={side:null,direction:null,location:null};const needs=[];
  if(p.side){const s=detectSide(space);s?attributes.side=s:needs.push("side");}
  if(p.direction){const s=detectDirection(space);s?attributes.direction=s:needs.push("direction");}
  if(p.location){const s=detectLocation(space);s?attributes.location=s:needs.push("location");}
  return{status:"single_match",part_id:pid,name_ru:p.name_ru,name_az:p.name_az,
    category:p.category,subcategory:p.subcategory,attributes,needs_clarification:needs,
    candidates:[],match_score:score};
}
function ambiguous(ids,score){return{status:"ambiguous",part_id:null,name_ru:null,name_az:null,
  category:null,subcategory:null,attributes:{side:null,direction:null,location:null},needs_clarification:[],
  candidates:ids.map(i=>({part_id:i,name_ru:DATA.parts[i].name_ru,name_az:DATA.parts[i].name_az})),
  match_score:score};}

/* ---- Layer 1: category resolver (mirrors slovar_matcher/category.py) ---- */
function containmentCategories(key){
  if(key.length<3)return[];
  const cats=[];
  for(const k in CATIDX.index) if(k.indexOf(key)>=0)
    for(const c of CATIDX.index[k]) if(!cats.includes(c)) cats.push(c);
  return cats.sort();
}
function resolveCategory(word,raw,threshold){
  if(threshold==null)threshold=DEFAULT_THRESHOLD;
  const passthrough=(raw!=null&&raw!=="")?raw:word;  // forwarded verbatim to Layer 2
  const key=normalize(word);
  let cats=CATIDX.index[key],score=1.0;
  if(!cats){
    let best=0;const hits=[];
    for(const k in CATIDX.index){const s=similarity(key,k);if(s>=threshold){hits.push([s,k]);if(s>best)best=s;}}
    if(!hits.length){
      const fb=containmentCategories(key);   // last resort: every category the word appears in
      if(!fb.length)return{status:"category_unknown",raw_text_passthrough:passthrough};
      return{status:"category_ambiguous",candidates:fb,
        question:"К какой категории относится деталь?",raw_text_passthrough:passthrough,matched_by:"containment"};
    }
    const cs=[];for(const [s,k] of hits) if(Math.abs(s-best)<EPS)
      for(const c of CATIDX.index[k]) if(!cs.includes(c)) cs.push(c);
    cats=cs.sort(); score=round3(best);
  }
  if(cats.length===1)return{status:"category_resolved",category:cats[0],
    raw_text_passthrough:passthrough,match_score:score};
  return{status:"category_ambiguous",candidates:cats.slice(),
    question:"К какой категории относится деталь?",raw_text_passthrough:passthrough,match_score:score};
}

/* ---- UI ---- */
const $=s=>document.querySelector(s);
function esc(s){return String(s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));}
const pct=s=>Math.round(s*100)+"%";

const EXAMPLES=[["emblema",""],["fara",""],["radiator",""],
  ["yan güzgü","sol"],["traves",""],["stupitsa podsipniki",""]];
const exBox=$("#examples");
EXAMPLES.forEach(([ph,rw])=>{const b=document.createElement("button");
  b.className="ex";b.textContent=rw?(ph+" + «"+rw+"»"):ph;
  b.onclick=()=>{$("#phrase").value=ph;$("#raw").value=rw;start();};exBox.appendChild(b);});

/* ---- funnel state machine ---- */
let S=null;
function draw(){const d=$("#dialog");d.innerHTML=S.transcript.join("")+(S.current||"");d.style.display="block";}
function line(html){S.transcript.push('<div class="tline">'+html+'</div>');}
function chip(act,val,label){return '<button class="chip" data-act="'+act+'" data-val="'+esc(val)+'">'+label+'</button>';}

function start(){
  const phrase=$("#phrase").value.trim(); if(!phrase)return;
  let th=parseFloat($("#threshold").value); if(isNaN(th))th=0.90;
  S={phrase,raw:$("#raw").value.trim(),threshold:th,category:null,part:null,
     attributes:{side:null,direction:null,location:null},pendingNeeds:[],transcript:[],current:null};
  line('🔎 Запрос: <b>'+esc(phrase)+'</b>'+(S.raw?' <span class=muted>· сырой текст: «'+esc(S.raw)+'»</span>':''));
  stepCategory();
}

function stepCategory(){
  const r=resolveCategory(S.phrase,S.raw,S.threshold);
  if(r.status==="category_resolved"){
    S.category=r.category;
    line('① Категория: <b>'+esc(r.category)+'</b>'+(r.match_score<0.9999?' <span class=muted>(≈ '+pct(r.match_score)+')</span>':''));
    S.current=null; stepDetail();
  } else if(r.status==="category_ambiguous"){
    let h='<div class="qblock"><div class="q">① '+esc(r.question)+
      ' <span class=muted>('+r.candidates.length+' варианта)</span></div><div class="chips">';
    r.candidates.forEach(c=>h+=chip("cat",c,esc(c)));
    h+=chip("cat-other","","Başqa / Другое")+'</div></div>';
    S.current=h; draw();
  } else {
    line('① Категория <b>не определена</b> — ищу деталь без ограничения по категории.');
    S.category=null; S.current=null; stepDetail();
  }
}

function stepDetail(){
  const r=match(S.phrase,S.raw,S.threshold,S.category);
  if(r.status==="single_match"){ selectPart(r.part_id,r.match_score); }
  else if(r.status==="ambiguous"){
    let h='<div class="qblock"><div class="q">② Уточните деталь <span class=muted>('+
      r.candidates.length+' вариантов, совпадение '+pct(r.match_score)+')</span></div><div class="chips col">';
    r.candidates.forEach(c=>h+='<button class="chip" data-act="part" data-val="'+esc(c.part_id)+'">'+
      '<b>'+esc(c.part_id)+'</b> — '+esc(c.name_ru)+' <span class=muted>| '+esc(c.name_az)+'</span></button>');
    h+=chip("part-other","","Başqa / Другое")+'</div></div>';
    S.current=h; draw();
  } else {
    line('② Деталь <b>не найдена</b>'+(S.category?' в категории «'+esc(S.category)+'»':'')+'.');
    S.current='<div class="qblock"><div class="chips">'+chip("restart","","↻ Заново")+'</div></div>'; draw();
  }
}

function selectPart(pid,score){
  const res=single(pid,S.phrase,S.raw,score==null?1.0:score);
  S.part=res; S.attributes={side:res.attributes.side,direction:res.attributes.direction,location:res.attributes.location};
  S.pendingNeeds=res.needs_clarification.slice();
  line('② Деталь: <span class="pid">'+esc(res.part_id)+'</span> — <b>'+esc(res.name_ru)+'</b>'+
    (res.match_score<0.9999?' <span class=muted>(≈ '+pct(res.match_score)+')</span>':''));
  // auto-filled attributes (found in raw text) reported as facts, not questions
  ["side","direction","location"].forEach(k=>{ if(S.attributes[k]) line('③ '+attrLabel(k)+' найдено в тексте: <b>'+esc(S.attributes[k])+'</b>'); });
  S.current=null; askNext();
}

const attrLabel=k=>k==="side"?"Сторона":k==="direction"?"Позиция":"Расположение";
const attrQuestion=k=>k==="side"?"С какой стороны?":k==="direction"?"Перёд или зад?":"Внутренний или наружный?";
function askNext(){
  if(!S.pendingNeeds.length){ renderFinal(); return; }
  const need=S.pendingNeeds[0];
  let h='<div class="qblock"><div class="q">③ ❓ '+attrQuestion(need)+'</div><div class="chips">';
  if(need==="side"){ h+=chip("side","sol","Sol (лев.)")+chip("side","sağ","Sağ (прав.)")+chip("side","sol,sağ","Hər ikisi (обе)"); }
  else if(need==="direction"){ h+=chip("dir","ön","Ön (перёд)")+chip("dir","arxa","Arxa (зад)"); }
  else { h+=chip("loc","daxili","Daxili (внутр.)")+chip("loc","xarici","Xarici (наружн.)"); }
  h+='</div></div>'; S.current=h; draw();
}
function answerAttr(kind,val){
  S.attributes[kind]=val; S.pendingNeeds=S.pendingNeeds.filter(n=>n!==kind);
  line('③ '+attrLabel(kind)+': <b>'+esc(val)+'</b> <span class=muted>(выбор кнопкой)</span>');
  S.current=null; askNext();
}

function renderFinal(){
  const p=S.part, a=S.attributes;
  const final={category:p.category,subcategory:p.subcategory,part_id:p.part_id,
    name_ru:p.name_ru,name_az:p.name_az,attributes:a,match_score:p.match_score};
  let h='<div class="final"><h3>✔ Деталь собрана</h3><dl class="kv">'+
    '<dt>Категория</dt><dd>'+esc(p.category)+'</dd>'+
    '<dt>Подкатегория</dt><dd>'+esc(p.subcategory)+'</dd>'+
    '<dt>part_id</dt><dd><span class="pid">'+esc(p.part_id)+'</span></dd>'+
    '<dt>Название (RU)</dt><dd>'+esc(p.name_ru)+'</dd>'+
    '<dt>Название (AZ)</dt><dd>'+esc(p.name_az)+'</dd>'+
    '<dt>Сторона</dt><dd>'+(a.side?esc(a.side):'<span class=muted>— (не требуется)</span>')+'</dd>'+
    '<dt>Позиция</dt><dd>'+(a.direction?esc(a.direction):'<span class=muted>— (не требуется)</span>')+'</dd>'+
    '<dt>Расположение</dt><dd>'+(a.location?esc(a.location):'<span class=muted>— (не требуется)</span>')+'</dd>'+
    '</dl><details><summary>JSON</summary><pre>'+esc(JSON.stringify(final,null,2))+'</pre></details>'+
    '<div class="chips" style="margin-top:12px">'+chip("restart","","↻ Новый подбор")+'</div></div>';
  S.current=h; draw();
}

/* ---- click delegation for all chips ---- */
$("#dialog").addEventListener("click",e=>{
  const b=e.target.closest("button[data-act]"); if(!b)return;
  const act=b.dataset.act, val=b.dataset.val;
  if(act==="cat"){ S.category=val; line('① Категория выбрана: <b>'+esc(val)+'</b>'); S.current=null; stepDetail(); }
  else if(act==="cat-other"){ line('① Категория: <b>другое</b> → без ограничения.'); S.category=null; S.current=null; stepDetail(); }
  else if(act==="part"){ S.current=null; selectPart(val,1.0); }
  else if(act==="part-other"){ line('② Другое — уточните название в поле выше и нажмите «Начать».');
    S.current='<div class="qblock"><div class="chips">'+chip("restart","","↻ Заново")+'</div></div>'; draw(); $("#phrase").focus(); }
  else if(act==="side"){ answerAttr("side",val); }
  else if(act==="dir"){ answerAttr("direction",val); }
  else if(act==="loc"){ answerAttr("location",val); }
  else if(act==="restart"){ S=null; $("#dialog").style.display="none"; $("#phrase").focus(); }
});

$("#run").onclick=start;
$("#phrase").addEventListener("keydown",e=>{if(e.key==="Enter")start();});
$("#raw").addEventListener("keydown",e=>{if(e.key==="Enter")start();});
$("#theme").onclick=()=>{const r=document.documentElement;
  const cur=r.getAttribute("data-theme")||(matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light");
  r.setAttribute("data-theme",cur==="dark"?"light":"dark");};
$("#foot").textContent="Автономный офлайн-инструмент. Слой 1 (категория) + Слой 2 (деталь) и весь словарь встроены в файл — "+
  Object.keys(DATA.parts).length+" деталей, "+Object.keys(DATA.groups).length+" листьев, "+
  Object.keys(CATIDX.index).length+" слов индекса. Идентичен Python-библиотеке (slovar_matcher).";
</script>
</body>
</html>'''


def export_category_index() -> str:
    with open(os.path.join(HERE, "data", "category_index.json"), encoding="utf-8") as fh:
        return fh.read()


def main() -> None:
    out = (TEMPLATE
           .replace("__DATA__", export_data())
           .replace("__CATIDX__", export_category_index()))
    path = os.path.join(HERE, "matcher_tool.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(out)
    print(f"wrote {path}: {os.path.getsize(path)} bytes")


if __name__ == "__main__":
    main()
