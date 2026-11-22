"""W4 B-retrieval labels (PLAN_W4.md Amendment 1, frozen sha256 c88a3039...; Step A rubric unchanged). Every quote in w4_ret_quotes.jsonl was
read blind; only quotes stating a LISTED measure are recorded, all others are `other`. Then the strict bar per query x arm x run.
Repo root: ~/.venvs/deepdelve/bin/python eval/writer_defects/w4_labels_ret.py"""
import json, os
HERE = os.path.dirname(os.path.abspath(__file__))
L = {}
def lab(ids, side, measure, form, note=""):
    for i in ids:
        L[i] = (side, measure, form, note)
# c2: A = semaglutide, B = naltrexone. form: fig (figure) / dir (direction + size word) / stat (model coefficient, not a % reduction)
lab([1], "A", "total_consumption", "fig", "1550 vs 1026 g/30 days vs placebo (Lancet)")
lab([45, 52, 96], "A", "heavy_drinking_days", "fig", "41.1 vs 26.4 pp, 13.7 pp difference (Lancet)")
lab([116, 121], "A", "drinks_per_drinking_day", "fig", "'40-50% reductions in drinks per drinking day' vs placebo (secondary web page)")
lab([44, 48, 63, 129], "A", "drinks_per_drinking_day", "stat", "beta -0.41")
lab([98, 128], "A", "heavy_drinking_days", "stat", "beta 0.84 treatment-by-time")
lab([74], "B", "heavy_drinking_days", "fig", "injectable naltrexone WMD -4.7 % heavy drinking days vs placebo (meta-analysis)")
lab([3, 114, 99], "B", "heavy_drinking_nnt", "fig", "NNT 11 / 12 return to heavy drinking (borderline, as Step A)")
lab([28], "both", "binge_episodes", "fig", "'head-to-head trials, semaglutide -70% binge episodes, outpacing naltrexone': no naltrexone figure; no such trial exists")
lab([16, 66, 92], "both", "mixed", "fig", "semaglutide consumption -40% vs naltrexone relapse 5%: different sub-measures")
# c4: A = Mexico, B = UK
lab([154, 165, 171, 176, 185, 187, 188, 193, 195, 208, 214, 224, 232, 182], "A", "a_drink_volume", "fig", "Mexico purchases/consumption after 2014")
# UK: no quote states a drink-volume, sugar-from-drinks or total-sugar change attributed to the tax (obesity 157/169/162/202/230,
# reformulation 163, levy rates 183, supermarket study 167 without a figure).

rows = [json.loads(x) for x in open(os.path.join(HERE, "w4_ret_quotes.jsonl"))]
key = json.load(open(os.path.join(HERE, "w4_ret_key.json")))   # opened only after the labels above were written
by = {}
for r in rows:
    if r["id"] in L:
        by.setdefault(r["set"], []).append(r)

def pairs(sid, strict=True):
    out = []
    for a in by.get(sid, []):
        for b in by.get(sid, []):
            sa, ma, fa, _ = L[a["id"]]; sb, mb, fb, _ = L[b["id"]]
            if sa == "A" and sb == "B" and ma == mb and fa == fb != "stat" and not (strict and ma == "heavy_drinking_nnt"):
                out.append((a["id"], b["id"]))
    return out

search = [json.loads(x) for x in open(os.path.join(HERE, "w4_ret_search.jsonl"))]
res = {}
for sid, m in sorted(key.items(), key=lambda kv: (kv[1]["query"], kv[1]["model"], kv[1]["run"])):
    run = os.path.relpath(m["evidence"])
    s = [x for x in search if x["run"] == m["evidence"]]
    orig = sum(x["origin"] != "other" for x in s)
    p, pl = pairs(sid), pairs(sid, False)
    res[(m["query"], m["model"], m["run"])] = bool(p)
    print(f"{sid} {m['query']} {m['model']:8s} r{m['run']}  strict {len(p)}  lenient {len(pl)}  searches {len(s)} (original/q0+name {orig})")
for q in ("c2", "c4"):
    new_ok = all(res.get((q, "new", k)) for k in ("1", "2"))
    cur_none = not any(res.get((q, "current", k)) for k in ("1", "2"))
    print(q, "new arm PAIR in both runs:", new_ok, "| current arm none:", cur_none, "-> query passes:", new_ok and cur_none)
