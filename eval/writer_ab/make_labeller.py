"""Builds the human labelling tool for the writer A/B (wiki: Human Labelling Guide).
Usage (repo root): python eval/writer_ab/make_labeller.py [per_cell=2]
Picks a stratified, seeded sample of the already-labelled seed 0 reports (every model x arm cell, plus every report with a severity 3 label),
hides model and arm behind new ids H01.., splits each report into units (one sentence, bullet or row each), and writes ONE self-contained HTML file:
    eval/writer_ab/out/human_labeller.html     (open it in a browser; no server, no network; progress is kept in the browser and exported as JSON)
    eval/writer_ab/out/human_units.json        (the units shown, so the comparison uses the same segmentation)
    eval/writer_ab/out/human_map.json          (PRIVATE: H id -> original id, model, arm; do not show the labeller)
The labeller's export is compared with the first labeller's unit labels by human_compare.py."""
import json, os, random, re, sys
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = HERE + "/out"
sys.path.insert(0, HERE)

CITE = r"(?:\[[\d,\s]+\])*"
TYPES = [
    ("C1", "Not supported by the findings", "A figure, name, fact or expansion of an abbreviation that no finding states, or that a finding states differently."),
    ("C2", "Wrong citation number", "The sentence is supported by a different finding than the one it cites."),
    ("C3", "Overstated or hedge dropped", "Stronger or broader than the findings: a hedge ('the authors infer', 'not significant') lost, a cause stated as fact, a cited result presented as the study's own, scope widened."),
    ("C4", "Mismatched comparison", "Two quantities that are not comparable placed side by side (for example an odds ratio against a hazard ratio) without saying so."),
    ("C5", "Contradiction or false gap", "Contradicts a finding or another sentence, or calls something unanswered that a finding answers (or the reverse)."),
    ("S2", "Padding or opinion", "Background, praise, advice, evaluation ('robust', 'compelling', 'critical') or an aim that no finding contains."),
    ("F1", "Format or meta", "Talks about 'the provided quotes/text', repeats a section, or leaks pipeline wording."),
    ("OTHER", "Other", "Anything else that would mislead a reader. Describe it in the note."),
]


def units_of(body: str) -> list[dict]:
    """[{'k': 'h'|'u', 't': text}]: headings are shown but not labelled; every sentence, bullet or table row is one unit."""
    out = []
    for line in body.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("#") or re.fullmatch(r"\*\*.*\*\*", s) or set(s) <= set("-| :"):
            out.append({"k": "h", "t": s.lstrip("# ").strip("*")})
            continue
        parts = [m.group(0).strip() for m in re.finditer(rf".+?(?:[.!?]{CITE}(?=\s|$)|$)", s) if m.group(0).strip()]
        merged = []
        for p in parts:                      # 'et al.' and 'e.g.' end no sentence
            if merged and re.search(r"(?:\bet al\.|\be\.g\.|\bi\.e\.|\bvs\.)$", merged[-1]):
                merged[-1] += " " + p
            else:
                merged.append(p)
        # a finding is stored as "question answer": the question line is a label, shown for context and not judged
        out += [{"k": "q" if p.endswith("?") else "u", "t": p} for p in merged]
    # a heading with no unit under it (its content was code-rendered and removed) would only confuse the labeller
    return [x for i, x in enumerate(out) if x["k"] != "h" or (i + 1 < len(out) and out[i + 1]["k"] != "h")]


def load_first():
    labels, maps = {}, {}
    for suffix in ("", "_b"):
        mp = f"{OUT}/blind_map{suffix}.json"
        if os.path.exists(mp):
            maps.update({i["id"]: i for i in json.load(open(mp))})
            labels.update(json.load(open(f"{OUT}/labels_seed0{suffix}.json")))
    return maps, labels


def sample(maps, labels, per_cell):
    rng = random.Random(11)
    cells = {}
    for rid in sorted(labels):
        cells.setdefault((maps[rid]["model"], maps[rid]["arm"]), []).append(rid)
    pick = []
    for key in sorted(cells):
        pick += rng.sample(cells[key], min(per_cell, len(cells[key])))
    pick += [r for r, l in labels.items() if l.get("sev3") and r not in pick]      # every severity 3 report is double-checked
    rng.shuffle(pick)
    return pick


def main():
    per_cell = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    maps, labels = load_first()
    chosen = sample(maps, labels, per_cell)
    reports, private, units_json = [], [], {}
    for n, rid in enumerate(chosen, 1):
        hid = f"H{n:02d}"
        pkt = open(f"{OUT}/blind/{rid}.md").read()
        fs, rep = pkt.split("\n\nREPORT\n", 1)
        findings = [ln for ln in fs.splitlines()[1:] if ln.strip()]
        us = units_of(rep)
        reports.append({"id": hid, "findings": findings, "units": us})
        units_json[hid] = us
        private.append({"id": hid, "orig": rid, "model": maps[rid]["model"], "arm": maps[rid]["arm"], "paper": maps[rid]["paper"]})
    json.dump(units_json, open(f"{OUT}/human_units.json", "w"), indent=1)
    json.dump(private, open(f"{OUT}/human_map.json", "w"), indent=1)
    data = json.dumps({"reports": reports, "types": TYPES}, ensure_ascii=False).replace("</", "<\\/")
    open(f"{OUT}/human_labeller.html", "w").write(PAGE.replace("__DATA__", data))
    print(len(chosen), "reports,", sum(1 for r in reports for u in r["units"] if u["k"] == "u"), "units ->", f"{OUT}/human_labeller.html")


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Report labelling tool</title>
<style>
:root{--bg:#fafaf8;--fg:#1d1d1b;--mut:#6b6b66;--card:#fff;--line:#dcdcd4;--acc:#1f5fbf;--bad:#b3261e;--ok:#1e7a3c;--hl:#fff4c9}
@media (prefers-color-scheme:dark){:root{--bg:#171715;--fg:#ecece6;--mut:#a3a39b;--card:#1f1f1c;--line:#37372f;--acc:#7aa7ff;--bad:#ff8a80;--ok:#7fd89a;--hl:#3a3418}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif}
header{position:sticky;top:0;z-index:5;background:var(--card);border-bottom:1px solid var(--line);padding:8px 16px;display:flex;gap:12px;align-items:center;flex-wrap:wrap}
header h1{font-size:16px;margin:0}button,select,input,textarea{font:inherit;color:inherit;background:var(--card);border:1px solid var(--line);border-radius:6px;padding:5px 9px}
button{cursor:pointer}button.pri{background:var(--acc);color:#fff;border-color:var(--acc)}.mut{color:var(--mut)}
main{display:grid;grid-template-columns:minmax(260px,36%) 1fr;gap:16px;padding:16px;max-width:1500px;margin:auto}
@media(max-width:860px){main{grid-template-columns:1fr}}
.pane{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 16px}
#find{position:sticky;top:64px;max-height:calc(100vh - 84px);overflow:auto;align-self:start}
#find p{margin:.4em 0;font-size:14px}.fn{font-weight:600;color:var(--acc)}.gap{color:var(--mut);font-style:italic}
.h{font-weight:700;margin:14px 0 4px}.u{border-left:3px solid var(--line);padding:6px 10px;margin:6px 0;border-radius:0 6px 6px 0}
.u.d{border-left-color:var(--bad);background:var(--hl)}.u.k{border-left-color:var(--ok)}
.row{display:flex;gap:8px;flex-wrap:wrap;margin-top:6px;align-items:center}
.u textarea{width:100%;min-height:34px}.nav{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0}
.nav button.done{border-color:var(--ok);color:var(--ok)}.nav button.cur{outline:2px solid var(--acc)}
details{margin:8px 0}summary{cursor:pointer;font-weight:600}table{border-collapse:collapse;width:100%}td,th{border-top:1px solid var(--line);padding:4px 6px;text-align:left;vertical-align:top;font-size:14px}
</style></head><body>
<header><h1>Report labelling</h1>
<label>Your name or initials <input id="who" size="10"></label>
<span id="prog" class="mut"></span><span id="saved" class="mut"></span>
<button id="exp" class="pri">Save labels file</button>
<button id="clr">Clear all my labels</button><button id="imp">Load a saved file</button><input id="file" type="file" accept=".json" hidden>
</header>
<main>
<div id="find" class="pane"></div>
<div>
<div class="pane"><details open><summary>Steps (read once)</summary><ol>
<li>Type your name or initials above. Your work is saved in this browser after every click, so you can close the page and come back later by opening the <b>same file</b> in the <b>same browser</b> (not a private window; do not clear site data). Also press <b>Save labels file</b> at the end of every sitting: that file is your backup, and <b>Load a saved file</b> restores it anywhere.</li>
<li>Pick a report with the numbered buttons. On the left are the <b>findings</b>: every one is true. "Gap" entries say what the source does not state.</li>
<li>Read the report on the right, one unit (sentence, bullet or row) at a time. Judge each unit <b>only against the findings</b>, not against what you know.</li>
<li>Greyed question lines are only labels of a finding: do not judge them. If a section repeats an earlier one word for word, mark the repeated copy <b>F1</b>. Click <b>OK</b> or <b>Defect</b>. For a defect pick a type, a severity and, if the reason is not obvious, add a short note. A unit with two problems is still one defect: choose the worse.</li>
<li>When every unit is done, tick <b>Report finished</b>. Skip nothing; a unit left unmarked counts as not yet reviewed.</li>
<li>Send the saved file back. Do not discuss the reports with anyone until the file is sent.</li></ol>
<details><summary>Defect types and severities</summary><table id="key"></table>
<p><b>Severity:</b> 3 = misleading: a reader acting on it would be wrong. 2 = weakens trust or needs rework. 1 = minor or cosmetic.</p>
<p class="mut">Citation numbers like [3] point at the finding with that number. A sentence that only restates a finding in other words is OK. A unit that quotes a finding word for word is OK. Headings are not labelled. The model name, the settings and which version wrote a report are hidden from you on purpose.</p></details></details></div>
<div class="nav" id="nav"></div>
<div class="pane" id="rep"></div>
<div class="row"><label><input type="checkbox" id="fin"> Report finished</label>
<button id="prev">Previous report</button><button id="next">Next report</button></div>
</div></main>
<script id="data" type="application/json">__DATA__</script>
<script>
const D=JSON.parse(document.getElementById('data').textContent);
const KEY='labeller_'+D.reports.map(r=>r.id+r.units.length).join('').replace(/\D/g,'').slice(0,40)+'_'+D.reports.reduce((a,r)=>a+r.units.reduce((b,u)=>b+u.t.length,0),0);
let S={who:'',labels:{},done:{},cur:0},cur=0;
try{const s=localStorage.getItem(KEY);if(s)S=JSON.parse(s)}catch(e){}
cur=Math.min(S.cur||0,D.reports.length-1);
const save=()=>{S.cur=cur;try{localStorage.setItem(KEY,JSON.stringify(S))}catch(e){}};
const $=id=>document.getElementById(id);
const esc=t=>t.replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
$('key').innerHTML='<tr><th>Code</th><th>Meaning</th><th>Example</th></tr>'+D.types.map(t=>`<tr><td><b>${t[0]}</b></td><td>${t[1]}</td><td>${t[2]}</td></tr>`).join('');
function lab(rid,i){return (S.labels[rid]||{})[i]}
function setLab(rid,i,v){(S.labels[rid]=S.labels[rid]||{})[i]=v;save();render()}
function counts(r){let n=0,m=0;r.units.forEach((u,i)=>{if(u.k==='u'){n++;if(lab(r.id,i))m++}});return[n,m]}
function render(){
 $('who').value=S.who||'';
 const r=D.reports[cur];
 $('nav').innerHTML=D.reports.map((x,i)=>{const[n,m]=counts(x);return`<button class="${S.done[x.id]?'done':''} ${i===cur?'cur':''}" onclick="go(${i})">${i+1}${S.done[x.id]?' ✓':''}</button>`}).join('');
 let tot=0,got=0;D.reports.forEach(x=>{const[n,m]=counts(x);tot+=n;got+=m});
 $('saved').textContent=' · autosaved in this browser';$('prog').textContent=`${got} of ${tot} units marked, ${Object.values(S.done).filter(Boolean).length} of ${D.reports.length} reports finished`;
 $('find').innerHTML='<b>Findings (true)</b>'+r.findings.map(f=>{const m=f.match(/^\[(\d+)\]\s*(.*)$/);return`<p class="${/Gap:/.test(f)?'gap':''}"><span class="fn">[${m?m[1]:''}]</span> ${esc(m?m[2]:f)}</p>`}).join('');
 let h=`<b>Report ${cur+1} of ${D.reports.length}</b>`;
 r.units.forEach((u,i)=>{
  if(u.k==='h'){h+=`<div class="h">${esc(u.t)}</div>`;return}
  if(u.k==='q'){h+=`<div class="u" style="opacity:.6;font-style:italic">${esc(u.t)} <span class="mut">(question line: shown for context, nothing to judge)</span></div>`;return}
  const v=lab(r.id,i),cls=v?(v.d?'d':'k'):'';
  h+=`<div class="u ${cls}"><div>${esc(u.t)}</div><div class="row">
   <button onclick="mark(${i},false)" ${v&&!v.d?'class="pri"':''}>OK</button><button onclick="mark(${i},true)" ${v&&v.d?'class="pri"':''}>Defect</button>`;
  if(v&&v.d){h+=`<select onchange="upd(${i},'t',this.value)"><option value="">type…</option>${D.types.map(t=>`<option value="${t[0]}" ${v.t===t[0]?'selected':''}>${t[0]} ${t[1]}</option>`).join('')}</select>
   <select onchange="upd(${i},'s',+this.value)"><option value="0">severity…</option>${[1,2,3].map(s=>`<option ${v.s===s?'selected':''}>${s}</option>`).join('')}</select>
   </div><textarea placeholder="Note (optional): what is wrong" oninput="note(${i},this.value)">${esc(v.n||'')}</textarea>`}else h+='</div>';
  h+='</div>'});
 $('rep').innerHTML=h;$('fin').checked=!!S.done[r.id];
}
function mark(i,d){const r=D.reports[cur];const o=lab(r.id,i)||{};setLab(r.id,i,{d:d,t:d?o.t||'':'',s:d?o.s||0:0,n:d?o.n||'':''})}
function note(i,v){lab(D.reports[cur].id,i).n=v;save()}
function upd(i,k,v){const r=D.reports[cur];const o=lab(r.id,i);o[k]=v;save();render()}
function go(i){cur=Math.max(0,Math.min(D.reports.length-1,i));save();render();window.scrollTo(0,0)}
$('prev').onclick=()=>go(cur-1);$('next').onclick=()=>go(cur+1);
$('fin').onchange=e=>{S.done[D.reports[cur].id]=e.target.checked;save();render()};
$('who').onchange=e=>{S.who=e.target.value;save()};
$('exp').onclick=()=>{
 const miss=[];const out={labeller:S.who,exported:new Date().toISOString(),reports:{}};
 D.reports.forEach(r=>{const L={};let n=0,dn=0,s3=0;r.units.forEach((u,i)=>{if(u.k!=='u')return;n++;const v=lab(r.id,i);if(!v){return}if(v.d){dn++;if(v.s===3)s3++;if(!v.t||!v.s)miss.push(`report ${D.reports.indexOf(r)+1}: a defect has no type or severity`)}L[i]=v});
  const un=n-Object.keys(L).length;if(un)miss.push(`report ${D.reports.indexOf(r)+1}: ${un} units not marked`);
  out.reports[r.id]={finished:!!S.done[r.id],units:n,marked:Object.keys(L).length,defect_units:dn,sev3:s3,labels:L}});
 if(!S.who){alert('Type your name or initials first.');return}
 if(miss.length&&!confirm('Not complete yet:\n'+miss.slice(0,12).join('\n')+'\n\nSave anyway (you can load the file later and continue)?'))return;
 const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(out,null,1)],{type:'application/json'}));a.download=`labels_${S.who.replace(/\W+/g,'_')}.json`;a.click()};
$('imp').onclick=()=>$('file').click();
$('clr').onclick=()=>{if(confirm('Delete ALL labels saved in this browser and start from zero? Save a labels file first if you want a backup.')){try{localStorage.removeItem(KEY)}catch(e){}location.reload()}};
$('file').onchange=async e=>{const f=e.target.files[0];if(!f)return;const j=JSON.parse(await f.text());S.who=j.labeller||S.who;for(const[id,r]of Object.entries(j.reports)){S.labels[id]=r.labels;S.done[id]=r.finished}save();render()};
render();
</script></body></html>
"""

if __name__ == "__main__":
    main()
