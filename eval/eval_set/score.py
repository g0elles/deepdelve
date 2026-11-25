"""E1 evaluation set: blind packets, Phase 0 frame and gold draw (PLAN.md). The test/simulation half is added for Phase 1.

  python eval/eval_set/score.py phase0     # resolve the Phase 0 frame, write packets + blind map + gold draw to _private/
"""
import hashlib
import json
import random
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from evaluate_run import _strip_notes  # noqa: E402
from pipeline.bridge import source_filename  # noqa: E402
from pipeline.run import quote_span  # noqa: E402

HERE = Path(__file__).resolve().parent
PRIV = HERE / "_private"
SEEDS = {"blind": 20261125, "gold": 20261126}
WIN, CAP, NOQ, CTX_CAP = 500, 3000, 1500, 150_000  # PLAN Amendment 1
ANSI = re.compile(r"\x1b\[[0-9;]*m")
QUOTE = re.compile(r"[\"“]([^\"”]{20,})[\"”]")
MARK = re.compile(r"\[(\d+)\]")


def sha(p) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def run_folder(log: Path) -> str | None:
    """report_dir from an app log: everything after 'Run folder: ' to end of line (the repo path has a space)."""
    for line in ANSI.sub("", log.read_text(errors="ignore")).splitlines():
        if line.startswith("Run folder: "):
            return line[len("Run folder: "):].strip()
    return None


def phase0_frame() -> list[dict]:
    """[{key, query, report_dir, evidence_dir}] for the cmp/cmp2/cmp2pres c2/c3 logs and the 36 W8b runs."""
    out = []
    for log in sorted((ROOT / "research_output").glob("cmp*_c[23]_r*.app.log")):
        name = log.name[:-len(".app.log")]
        fam = name.split("_")[0]
        if fam not in ("cmp", "cmp2", "cmp2pres"):
            continue
        rd = run_folder(log)
        if rd and (Path(rd) / "final_report.md").exists():
            out.append({"key": name, "query": name.split("_")[-2], "report_dir": rd, "evidence_dir": str(ROOT / "research_output" / name)})
    for line in (ROOT / "eval/writer_defects/w8b_runs.tsv").read_text().splitlines():
        q, arm, k, ev, rep = line.split("\t")[:5]
        if rep and (ROOT / rep / "final_report.md").exists():
            out.append({"key": f"w8b_{q}_{arm}_r{k}", "query": q, "report_dir": str(ROOT / rep), "evidence_dir": str(ROOT / ev)})
    return out


def strip_report(md: str) -> str:
    md = _strip_notes(md)
    lines = md.splitlines()
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    keep = []
    for line in lines:
        if re.match(r"^#+\s*(Answer in brief|Summary)\s*$", line, re.I):
            continue
        if re.match(r"^#+\s*(References|Sources|Bibliography)\s*$", line, re.I):
            line = "## Sources"
        if re.match(r"^\s*(Generated|Run (id|folder)|Model|Writer|Style)\s*:", line, re.I):
            continue
        keep.append(line)
    return "\n".join(keep).strip() + "\n"


def source_list(md: str) -> dict[int, str]:
    urls, on = {}, False
    for line in md.splitlines():
        if re.match(r"^#+\s*(References|Sources|Bibliography)\s*$", line, re.I):
            on = True
            continue
        if on and line.startswith("#"):
            break
        m = re.match(r"^(\d+)\.\s+\[", line)
        if on and m and "](" in line:
            i = line.rfind("](")
            urls[int(m.group(1))] = line[i + 2:line.rfind(")")]
    return urls


def excerpts(md: str, report_dir: Path, evidence_dir: Path) -> dict[int, str]:
    urls = source_list(md)
    quotes: dict[int, list[str]] = {}
    for line in md.splitlines():
        ns = [int(n) for n in MARK.findall(line)]
        for q in QUOTE.findall(line):
            for n in ns:
                quotes.setdefault(n, []).append(q)
    out = {}
    for n, url in urls.items():
        f = report_dir / source_filename(url)
        if not f.exists():
            f = evidence_dir / source_filename(url)
        if not f.exists():
            continue
        text = f.read_text(errors="ignore")
        spans = sorted(s for s in (quote_span(q, text) for q in quotes.get(n, [])) if s)
        wins = []
        for a, b in spans:
            a, b = max(0, a - WIN), min(len(text), b + WIN)
            if wins and a <= wins[-1][1]:
                wins[-1][1] = max(wins[-1][1], b)
            else:
                wins.append([a, b])
        ex = " [...] ".join(text[a:b] for a, b in wins) if wins else text[:NOQ]  # no located quote: short lead only (amendment 1)
        out[n] = ex[:CAP]
    return out


def packet(rid: str, query: dict, report_dir: Path, evidence_dir: Path) -> str:
    md = (report_dir / "final_report.md").read_text(errors="ignore")
    parts = []
    for p in query["parts"]:
        r = p["ref"]
        parts.append(f"- part_id: {p['id']} ({p['type']})\n  part: {p['text']}\n  reference: {r['statement']}\n"
                     f"  value: {r.get('value')}\n  alt_values: {r.get('alt_values') or []}")
    ex = excerpts(md, report_dir, evidence_dir)
    exs = "\n\n".join(f"[{n}] {t}" for n, t in sorted(ex.items()))
    return (f"=== REPORT {rid} ===\nQUERY: {query['q']}\n\nPARTS TO GRADE:\n" + "\n".join(parts) +
            f"\n\n--- REPORT TEXT ---\n{strip_report(md)}\n--- CITED SOURCE EXCERPTS ---\n{exs}\n=== END {rid} ===\n")


def batches(packets: list[str]) -> list[list[str]]:
    out, cur, size = [], [], 0
    for p in packets:  # a report is never split; one over the cap goes alone
        if cur and size + len(p) > CTX_CAP:
            out.append(cur)
            cur, size = [], 0
        cur.append(p)
        size += len(p)
    return out + ([cur] if cur else [])


def gold_draw(frame: list[dict], queries: dict, rng: random.Random, n=40, n_cmp=15) -> list[tuple[str, str]]:
    """Seeded, stratified by part and query: comparison parts round-robin to n_cmp, other parts round-robin to n - n_cmp.
    At most one report of each cmp2/cmp2pres evidence-sharing pair is eligible."""
    eligible = []
    for r in frame:
        k = r["key"]
        if k.startswith(("cmp2_ornith_", "cmp2pres_ornith_")):
            twin = k.split("_", 1)[1]  # ornith_cX_rY
            keep_pres = random.Random(f"{SEEDS['gold']}:{twin}").random() < 0.5
            if k.startswith("cmp2pres_") != keep_pres:
                continue
        eligible.append(r)
    picked = []
    for want, is_cmp in ((n_cmp, True), (n - n_cmp, False)):
        parts = [(q, p["id"]) for q, qd in queries.items() for p in qd["parts"] if (p["type"] == "comparison") == is_cmp]
        rng.shuffle(parts)
        got, i = 0, 0
        while got < want:
            q, pid = parts[i % len(parts)]
            pool = [r["rid"] for r in eligible if r["query"] == q and (r["rid"], pid) not in picked]
            if pool:
                picked.append((rng.choice(pool), pid))
                got += 1
            i += 1
    return picked


def phase0():
    queries = {q["id"]: q for q in yaml.safe_load((HERE / "phase0/queries_phase0.yaml").read_text())}
    frame = [r for r in phase0_frame() if r["query"] in queries]
    random.Random(SEEDS["blind"]).shuffle(frame)
    for i, r in enumerate(frame, 1):
        r["rid"] = f"P{i:03d}"
    PRIV.mkdir(exist_ok=True)
    (PRIV / "phase0_blind_map.json").write_text(json.dumps(frame, indent=1))
    pk = [packet(r["rid"], queries[r["query"]], Path(r["report_dir"]), Path(r["evidence_dir"])) for r in frame]
    for i, b in enumerate(batches(pk), 1):
        (PRIV / f"phase0_batch{i:02d}.txt").write_text("".join(b))
    gold = gold_draw(frame, queries, random.Random(SEEDS["gold"]))
    (PRIV / "phase0_gold_items.json").write_text(json.dumps([{"report_id": a, "part_id": b} for a, b in gold], indent=1))
    by_q = {}
    for r in frame:
        by_q[r["query"]] = by_q.get(r["query"], 0) + 1
    print(f"frame {len(frame)} reports {by_q}; packets {len(pk)} in {len(batches(pk))} batches "
          f"(max {max(map(len, pk))} chars); gold {len(gold)} ({sum(1 for _, p in gold if '.cmp' in p)} comparison, "
          f"{len({p for _, p in gold if '.cmp' in p})} distinct comparison parts)")



def gold_html():
    """Local, self-contained page for the user's gold labels (no network). Grouped by report; saves in the browser and exports JSONL."""
    import html
    priv = PRIV
    items = json.loads((priv / "phase0_gold_items.json").read_text())
    text = "".join(p.read_text() for p in sorted(priv.glob("phase0_batch*.txt")))
    pk = dict(re.findall(r"=== REPORT (P\d+) ===\n(.*?)=== END \1 ===", text, re.S))
    by_r = {}
    for it in items:
        by_r.setdefault(it["report_id"], []).append(it["part_id"])
    sec = []
    for rid in sorted(by_r):
        body = pk[rid]
        head, rest = body.split("--- REPORT TEXT ---", 1)
        rep, ex = rest.split("--- CITED SOURCE EXCERPTS ---", 1)
        q = head.split("\n", 1)[0].removeprefix("QUERY: ")
        blocks = re.split(r"\n(?=- part_id: )", head.split("PARTS TO GRADE:\n", 1)[1])
        parts = []
        for b in blocks:
            pid = re.match(r"- part_id: (\S+)", b).group(1)
            if pid in by_r[rid]:
                key = f"{rid}|{pid}"
                btns = "".join(f'<label><input type="radio" name="{key}" value="{v}"> {v}</label>' for v in ("ANSWERED", "WRONG", "MISSING"))
                parts.append(f'<div class="part"><pre>{html.escape(b.strip())}</pre><div class="btns">{btns}</div>'
                             f'<input class="note" data-k="{key}" placeholder="optional note / evidence quote"></div>')
        sec.append(f'<section><h2>{rid}</h2><p class="q">{html.escape(q)}</p>{"".join(parts)}'
                   f'<details open><summary>Report</summary><pre class="rep">{html.escape(rep.strip())}</pre></details>'
                   f'<details><summary>Cited source excerpts</summary><pre class="rep">{html.escape(ex.strip())}</pre></details></section>')
    page = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Gold Labels</title><style>
:root{--bg:#fafaf8;--fg:#1d1d1b;--mut:#666;--card:#fff;--line:#ddd;--acc:#2b5fd9}
@media (prefers-color-scheme:dark){:root{--bg:#161616;--fg:#e8e8e6;--mut:#999;--card:#202020;--line:#333;--acc:#7aa2ff}}
body{background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif;margin:0 auto;max-width:980px;padding:16px}
section{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 16px;margin:18px 0}
pre{white-space:pre-wrap;word-wrap:break-word;font:13px/1.45 ui-monospace,monospace;margin:6px 0}
.rep{max-height:60vh;overflow:auto;border-top:1px solid var(--line);padding-top:6px}.q{color:var(--mut)}
.part{border-left:3px solid var(--acc);padding-left:10px;margin:10px 0}.btns label{margin-right:16px;cursor:pointer}
.note{width:100%;box-sizing:border-box;margin-top:6px;padding:4px;background:var(--bg);color:var(--fg);border:1px solid var(--line)}
#bar{position:sticky;top:0;background:var(--bg);padding:8px 0;border-bottom:1px solid var(--line);z-index:1}
button{padding:6px 12px}</style></head><body>
<div id="bar"><b>Gold labels</b> &middot; <span id="n"></span> &middot; <button id="dl">Download labels (JSONL)</button>
<p class="q">Grade each highlighted part by rubric.md: ANSWERED / WRONG / MISSING. Grade before seeing any model labels. Progress is saved in this browser.</p></div>
""" + "".join(sec) + """<script>
const K="e1_gold_v1";let st={};try{st=JSON.parse(localStorage.getItem(K)||"{}")}catch(e){}
function save(){try{localStorage.setItem(K,JSON.stringify(st))}catch(e){}count()}
function count(){const all=document.querySelectorAll('.part').length;const done=Object.values(st).filter(v=>v.label).length;document.getElementById('n').textContent=done+" / "+all+" graded"}
document.querySelectorAll('input[type=radio]').forEach(r=>{const s=st[r.name];if(s&&s.label===r.value)r.checked=true;
 r.addEventListener('change',()=>{st[r.name]=Object.assign(st[r.name]||{},{label:r.value});save()})});
document.querySelectorAll('.note').forEach(n=>{const s=st[n.dataset.k];if(s&&s.note)n.value=s.note;
 n.addEventListener('input',()=>{st[n.dataset.k]=Object.assign(st[n.dataset.k]||{},{note:n.value});save()})});
document.getElementById('dl').onclick=()=>{const lines=Object.entries(st).filter(([k,v])=>v.label).map(([k,v])=>{const[r,p]=k.split('|');
 return JSON.stringify({report_id:r,part_id:p,label:v.label,evidence_quote:v.note||"",pass:"gold"})});
 const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([lines.join("\\n")+"\\n"],{type:"application/json"}));a.download="phase0_gold_labels.jsonl";a.click()};
count();</script></body></html>"""
    (priv / "gold.html").write_text(page)
    print(priv / "gold.html", len(by_r), "reports", len(items), "items")

if __name__ == "__main__":
    {"phase0": phase0, "gold_html": gold_html}[sys.argv[1]]()
