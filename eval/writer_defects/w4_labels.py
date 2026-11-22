"""W4 Step A labels (PLAN_W4.md rubric, frozen sha256 7358442a...). Every quote in w4_quotes.jsonl was read; only quotes stating a LISTED
measure are recorded here, all others are measure `other`. Fields: id -> (side, measure, period_ok, note). Then the per-query decision and the
pool diagnostic (were both quotes of a pair shown to one model call in production today). Repo root:
    ~/.venvs/deepdelve/bin/python eval/writer_defects/w4_labels.py"""
import json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(HERE + "/../..")
sys.path.insert(0, ROOT + "/src")

L = {}
def lab(ids, side, measure, ok, note=""):
    for i in ids:
        L[i] = (side, measure, ok, note)
# c3: A = Iceland, B = Ireland
lab([265, 298, 311, 358, 377], "A", "unemp_peak", True, "10.30% June 2009")
lab([220, 226, 321, 330], "B", "unemp_peak", True, "16.1% early 2012")
lab([223, 231, 305, 379, 380], "B", "unemp_peak", True, "15.1% Feb 2012 (conflicts with 16.1%)")
lab([252, 327], "B", "unemp_year", True, "2010 14.8%")
lab([337, 349], "B", "unemp_year", True, "2010 14%")
lab([229], "B", "unemp_year", True, "2015 10.0%")
lab([269], "A", "gdp_growth_2009_2011", True, "2009 -6.7%, 2011 2.9% (annual)")
lab([288, 306], "A", "gdp_growth_q1_2012", True, "Q1 2012 4.5%")
lab([239, 284, 316, 372], "B", "gdp_growth_2014", True, "2014 4.8%")
lab([261, 308], "B", "gdp_growth_q4_2009_q1_2010", True, "Q4 2009 0.3%, Q1 2010 2.7%")
lab([251, 285, 318, 323], "A", "output_loss", True, "'nearly half its economic output', direction+size; no Ireland counterpart")
lab([217], "both", "gdp_growth_span", True, "'much higher GDP growth of Iceland relative to Ireland' since Q4 2007: no figures, not a PAIR by rubric")
# c4: A = Mexico, B = UK
lab([382, 383, 390, 420, 430, 437, 449, 468, 475, 477], "A", "a_drink_volume", True, "consumption of taxed drinks, % after 2014")
lab([474], "B", "b_sugar_from_drinks", True, "'billions of teaspoons of sugar out of the UK diet', absolute, no Mexico (b)")
# c2: A = semaglutide, B = naltrexone (human RCT / MA only)
lab([36, 22, 144], "A", "drinks_per_drinking_day", True, "vs placebo")
lab([165, 118, 171, 185], "A", "heavy_drinking_days", True, "vs placebo")
lab([112, 141], "A", "total_consumption", True, "g/30 days vs placebo")
lab([180], "A", "heavy_drinking_nnt", True, "NNT 4.3, borderline: heavy drinking outcome, not heavy drinking days")
lab([55, 204], "B", "heavy_drinking_nnt", True, "Cochrane NNT 10 (heavy drinking), NNT 25 (daily drinking); borderline as above")
lab([146], "B", "total_consumption", True, "'modest effect sizes' direction+size, no figures; no semaglutide direction+size counterpart")
BORDERLINE = {"heavy_drinking_nnt"}
DIRECTION = {251, 285, 318, 323, 146, 217}  # direction + size word, no figure

rows = [json.loads(x) for x in open(os.path.join(HERE, "w4_quotes.jsonl"))]
key = json.load(open(os.path.join(HERE, "w4_key.json")))
by_set: dict[str, list] = {}
for r in rows:
    if r["id"] in L:
        by_set.setdefault(r["set"], []).append(r)

def pairs(set_id, strict):
    qs = by_set.get(set_id, [])
    out = []
    for a in qs:
        for b in qs:
            sa, ma, oka, _ = L[a["id"]]; sb, mb, okb, _ = L[b["id"]]
            same_form = (a["id"] in DIRECTION) == (b["id"] in DIRECTION)  # rubric 3: both figures, or both directions with a size word
            if sa == "A" and sb == "B" and ma == mb and oka and okb and same_form and not (strict and ma in BORDERLINE):
                out.append((a, b))
    return out

from writer import compiled as wc  # noqa: E402
from pipeline.run import COMPARISON_RE  # noqa: E402
QUERY = {}
for line in open(ROOT + "/eval/comparison_runs/run_all.sh"):
    if line.strip().startswith("[c"):
        k, v = line.strip().split("]=", 1)
        QUERY[k.strip("[ ")] = v.strip().strip(')"').strip('"')

def shown(set_id, prs):
    """Both quotes of some pair in one production unit (overview pool or one facet), after the MAX_QUOTE_CHARS cut."""
    meta = key[set_id]; q = QUERY[meta["query"]]
    ev = json.load(open(os.path.join(ROOT, meta["evidence"], "evidence.json")))
    c = wc.build(ev, q); c.comparison = bool(COMPARISON_RE.search(q))
    def kept(quotes):
        qmap, used = {}, 0
        for x in quotes:
            qmap.setdefault(x.n, "")
            qmap[x.n] += " " + x.text
        out, used = set(), 0
        for n, t in qmap.items():
            if used + len(t) > wc.MAX_QUOTE_CHARS and out:
                break
            out.add(t); used += len(t)
        return " ".join(out)
    units = [kept(wc._overview_pool(c)[0])] + [kept(f.quotes) for f in c.facets]
    norm = lambda s: " ".join(s.split())[:120]
    return any(any(norm(a["quote"]) in " ".join(u.split()) and norm(b["quote"]) in " ".join(u.split()) for u in units) for a, b in prs)

print("set       query model                    run  strict lenient shown")
summary = {}
for sid, meta in sorted(key.items(), key=lambda kv: (kv[1]["query"], kv[1]["model"], kv[1]["run"])):
    s, l = pairs(sid, True), pairs(sid, False)
    sh = shown(sid, l) if l else None
    summary.setdefault(meta["query"], []).append((bool(s), bool(l), sh))
    print(f"{sid}  {meta['query']}    {meta['model']:24s} {meta['run']}    {len(s):3d}    {len(l):3d}    {sh}")
for q, v in sorted(summary.items()):
    ns, nl = sum(x[0] for x in v), sum(x[1] for x in v)
    dec = lambda n: "writer gap (B-writer)" if n >= 2 else "retrieval gap (B-retrieval)"
    print(f"{q}: PAIR strict {ns}/6 -> {dec(ns)}; lenient {nl}/6 -> {dec(nl)}; shown in one unit: {sum(bool(x[2]) for x in v)}/{nl}")
