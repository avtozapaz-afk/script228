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
        "leaf_code": p.leaf_code, "side": p.side_flag, "position": p.position_flag,
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
      <h1>Детерминированный матчер деталей</h1>
      <p class="sub">Точный поиск по словарю AVTOZAP · без LLM · 15 категорий · 86 листьев · 438 деталей</p>
    </div>
    <button class="toggle" id="theme">◐ тема</button>
  </header>

  <div class="card">
    <label for="phrase">Нормализованная фраза <span class="hint">(вход от Seller-модели)</span></label>
    <input type="text" id="phrase" placeholder="напр. yan güzgü" autocomplete="off">
    <div class="examples" id="examples"></div>

    <div style="margin-top:16px">
      <label for="raw">Сырой текст клиента <span class="hint">(необязательно — для side/position)</span></label>
      <input type="text" id="raw" placeholder="напр. sol güzgü" autocomplete="off">
    </div>

    <div class="row">
      <button class="go" id="run">Найти деталь</button>
      <label class="chk"><input type="checkbox" id="fuzzy"> fuzzy-подсказки при no_match (только предложения, не выбор)</label>
    </div>
  </div>

  <div class="card" id="result" style="display:none"></div>

  <details class="card">
    <summary>Как это работает</summary>
    <p class="muted" style="font-size:13.5px">
      <b>Шаг 1.</b> Точное совпадение с именем детали (name_ru / name_az или альтернатива в скобках) —
      высший приоритет, один кандидат. Иначе — совпадение с синонимом группы: синонимы общие на весь лист,
      поэтому возвращаются <i>все</i> ID листа (1 ID → single_match; 2+ → честный ambiguous). Иначе — no_match.<br><br>
      <b>Шаг 2 — уточнение.</b> Только для single_match. У каждой детали есть флаги
      <code>side</code> (сторона: лево/право) и <code>position</code> (позиция: перёд/зад).
      Программа спрашивает <b>только те</b>, что у детали <code>true</code> и не найдены в тексте.
      Если у детали флаг <code>false</code> — про этот атрибут <b>не спрашиваем</b> и не заполняем.<br><br>
      <b>Шаг 3.</b> Категория и подкатегория берутся прямо из структуры словаря.<br><br>
      Никакого угадывания: ambiguous никогда не сворачивается в один вариант.
    </p>
  </details>

  <p class="foot" id="foot"></p>
</div>

<script>
const DATA = __DATA__;
</script>
<script>
/* ---- normalization (mirrors slovar_matcher/normalize.py) ---- */
const AZ_MAP={'İ':'i','I':'ı','Ə':'ə','Ç':'ç',
  'Ş':'ş','Ğ':'ğ','Ö':'ö','Ü':'ü'};
function azLower(s){let o='';for(const ch of s)o+=(AZ_MAP[ch]||ch);return o.toLowerCase();}
function normalize(s){return azLower(s).trim().replace(/\s+/g,' ');}
const TOKEN_RE=/[0-9a-zçğıəöüşЀ-ӿ]+/g;
function tokens(s){return azLower(s).match(TOKEN_RE)||[];}

/* ---- attribute keyword sets (mirrors slovar_matcher/attributes.py) ---- */
const SIDE_LEFT=new Set(["sol","sola","soldan","soldakı","soldaki","left","lh","лево","левый","левая","левое","левых","левого","слева"]);
const SIDE_RIGHT=new Set(["sağ","sag","sağa","sağdan","sagdan","sağdakı","sagdaki","right","rh","право","правый","правая","правое","правых","правого","справа"]);
const POS_FRONT=new Set(["ön","on","öndeki","öndəki","önki","qabaq","qabağ","qabaqdakı","qabaqdaki","qabağdakı","front","fr","перед","передний","передняя","переднее","передних","переднего","спереди","speredi"]);
const POS_REAR=new Set(["arxa","arxadakı","arxadaki","arxadan","arxadaku","rear","back","зад","задний","задняя","заднее","задних","заднего","сзади"]);
function detect(text,L,R,lv,rv){const t=new Set(tokens(text));let l=false,r=false;
  for(const x of t){if(L.has(x))l=true;if(R.has(x))r=true;}
  if(l&&r)return lv+","+rv;if(l)return lv;if(r)return rv;return null;}
const detectSide=t=>detect(t,SIDE_LEFT,SIDE_RIGHT,"sol","sağ");
const detectPosition=t=>detect(t,POS_FRONT,POS_REAR,"ön","arxa");

/* ---- fuzzy (approx difflib) ---- */
function lev(a,b){const m=a.length,n=b.length;if(!m)return n;if(!n)return m;
  let prev=Array.from({length:n+1},(_,i)=>i);
  for(let i=1;i<=m;i++){let cur=[i];for(let j=1;j<=n;j++){
    cur[j]=Math.min(prev[j]+1,cur[j-1]+1,prev[j-1]+(a[i-1]===b[j-1]?0:1));}prev=cur;}
  return prev[n];}
function ratio(a,b){const M=Math.max(a.length,b.length);return M?1-lev(a,b)/M:1;}

/* ---- matcher (mirrors slovar_matcher/matcher.py) ---- */
function match(phrase,raw,fuzzy){
  const key=normalize(phrase);
  let ids=null;
  if(DATA.name_index[key])ids=DATA.name_index[key].slice();
  else if(DATA.synonym_index[key]){ids=[];
    for(const code of DATA.synonym_index[key])
      for(const pid of DATA.groups[code].part_ids) if(!ids.includes(pid)) ids.push(pid);}
  if(ids){return ids.length===1?single(ids[0],phrase,raw):ambiguous(ids);}
  const res={status:"no_match",part_id:null,name_ru:null,name_az:null,category:null,
    subcategory:null,attributes:{side:null,position:null},needs_clarification:[],candidates:[]};
  if(fuzzy)res.fuzzy_suggestions=fuzzySuggest(key);
  return res;
}
function single(pid,phrase,raw){const p=DATA.parts[pid];
  const space=[phrase,raw].filter(Boolean).join(" ");
  const attributes={side:null,position:null};const needs=[];
  if(p.side){const s=detectSide(space);s?attributes.side=s:needs.push("side");}
  if(p.position){const s=detectPosition(space);s?attributes.position=s:needs.push("position");}
  return{status:"single_match",part_id:pid,name_ru:p.name_ru,name_az:p.name_az,
    category:p.category,subcategory:p.subcategory,attributes,needs_clarification:needs,candidates:[]};
}
function ambiguous(ids){return{status:"ambiguous",part_id:null,name_ru:null,name_az:null,
  category:null,subcategory:null,attributes:{side:null,position:null},needs_clarification:[],
  candidates:ids.map(i=>({part_id:i,name_ru:DATA.parts[i].name_ru,name_az:DATA.parts[i].name_az}))};}
function fuzzySuggest(key){const pool={};
  for(const k in DATA.name_index)(pool[k]=pool[k]||[]).push(...DATA.name_index[k]);
  for(const k in DATA.synonym_index)for(const c of DATA.synonym_index[k])
    (pool[k]=pool[k]||[]).push(...DATA.groups[c].part_ids);
  const scored=Object.keys(pool).map(k=>[k,ratio(key,k)]).filter(x=>x[1]>=0.82)
    .sort((a,b)=>b[1]-a[1]).slice(0,5);
  const seen=new Set();const out=[];
  for(const [k] of scored)for(const pid of pool[k]){if(seen.has(pid))continue;seen.add(pid);
    const p=DATA.parts[pid];out.push({part_id:pid,name_ru:p.name_ru,name_az:p.name_az,matched_on:k});}
  return out;}

/* ---- UI ---- */
const $=s=>document.querySelector(s);
const EXAMPLES=[["yan güzgü",""],["yan güzgü","sol güzgü"],
  ["Stupitsa podşipniki","sol qabaq"],["Turbo (nadduv) datçiki",""],
  ["traves",""],["radiator ekran",""]];
const exBox=$("#examples");
EXAMPLES.forEach(([ph,rw])=>{const b=document.createElement("button");
  b.className="ex";b.textContent=rw?(ph+" + «"+rw+"»"):ph;
  b.onclick=()=>{$("#phrase").value=ph;$("#raw").value=rw;run();};exBox.appendChild(b);});

function esc(s){return String(s).replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));}
const Q={side:{q:"С какой стороны?",opts:["левая — sol","правая — sağ"]},
  position:{q:"Какая позиция?",opts:["перёд — ön","зад — arxa"]}};
function askBlock(needs){
  let h='<div class="ask"><h3>❓ Нужно уточнить у клиента:</h3>';
  needs.forEach(n=>{const q=Q[n];h+='<div class="q">'+esc(q.q)+'</div><div>'+
    q.opts.map(o=>'<span class="opt">'+esc(o)+'</span>').join('')+'</div>';});
  h+='</div>';return h;}

function render(r){
  const cls={single_match:"s-single",ambiguous:"s-amb",no_match:"s-no"}[r.status];
  let h='<span class="status-badge '+cls+'">'+r.status+'</span>';
  if(r.status==="single_match"){
    h+='<dl class="kv">'+
      '<dt>part_id</dt><dd class="cand"><span class="pid">'+esc(r.part_id)+'</span></dd>'+
      '<dt>name_ru</dt><dd>'+esc(r.name_ru)+'</dd>'+
      '<dt>name_az</dt><dd>'+esc(r.name_az)+'</dd>'+
      '<dt>category</dt><dd>'+esc(r.category)+'</dd>'+
      '<dt>subcategory</dt><dd>'+esc(r.subcategory)+'</dd>'+
      '<dt>attributes</dt><dd>side: '+(r.attributes.side?esc(r.attributes.side):'<span class=muted>—</span>')+
        ' &nbsp;·&nbsp; position: '+(r.attributes.position?esc(r.attributes.position):'<span class=muted>—</span>')+'</dd>'+
      '</dl>';
    if(r.needs_clarification.length) h+=askBlock(r.needs_clarification);
    else h+='<p class="done">✔ Атрибуты не требуются — вопросов нет.</p>';
  } else if(r.status==="ambiguous"){
    h+='<p class="summary muted">Неоднозначно — '+r.candidates.length+' кандидат(ов). Программа НЕ выбирает один, отдаёт всех:</p>';
    r.candidates.forEach(c=>{h+='<div class="cand"><span class="pid">'+esc(c.part_id)+'</span> — '+
      esc(c.name_ru)+' &nbsp;<span class="muted">| '+esc(c.name_az)+'</span></div>';});
  } else {
    h+='<p class="summary muted">Такой детали в словаре нет (ни в именах, ни в синонимах).</p>';
    if(r.fuzzy_suggestions&&r.fuzzy_suggestions.length){
      h+='<p class="summary muted" style="margin-top:14px">Похожее (только подсказка, не выбор):</p>';
      r.fuzzy_suggestions.forEach(c=>{h+='<div class="cand"><span class="pid">'+esc(c.part_id)+'</span> — '+
        esc(c.name_ru)+' <span class="muted">| '+esc(c.name_az)+' · ≈ «'+esc(c.matched_on)+'»</span></div>';});
    } else if(r.hasOwnProperty("fuzzy_suggestions")){
      h+='<p class="muted" style="font-size:13px">Похожих вариантов не найдено.</p>';
    }
  }
  h+='<pre>'+esc(JSON.stringify(r,null,2))+'</pre>';
  const box=$("#result");box.innerHTML=h;box.style.display="block";
}
function run(){const ph=$("#phrase").value;if(!ph.trim())return;
  render(match(ph,$("#raw").value,$("#fuzzy").checked));}
$("#run").onclick=run;
$("#phrase").addEventListener("keydown",e=>{if(e.key==="Enter")run();});
$("#raw").addEventListener("keydown",e=>{if(e.key==="Enter")run();});
$("#theme").onclick=()=>{const r=document.documentElement;
  const cur=r.getAttribute("data-theme")||(matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light");
  r.setAttribute("data-theme",cur==="dark"?"light":"dark");};
$("#foot").textContent="Автономный офлайн-инструмент. Логика и весь словарь встроены в этот файл — "+
  Object.keys(DATA.parts).length+" деталей, "+Object.keys(DATA.groups).length+
  " листьев. Идентичен Python-программе (slovar_matcher).";
</script>
</body>
</html>'''


def main() -> None:
    out = TEMPLATE.replace("__DATA__", export_data())
    path = os.path.join(HERE, "matcher_tool.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(out)
    print(f"wrote {path}: {os.path.getsize(path)} bytes")


if __name__ == "__main__":
    main()
