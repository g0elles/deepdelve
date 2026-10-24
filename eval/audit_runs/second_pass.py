"""Blind machine second labeller on the audit's random (claim, cited source) sample. First labels (mine, made from the source text before the second pass) are FIRST below.
Run from src/: python ../eval/audit_runs/second_pass.py. Needs the GPU (gpt-oss:20b), about 40 short calls."""
import json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE + "/../claim_support"); sys.path.insert(0, HERE + "/../../src")
from second_labeller import label
FIRST = {0:0,1:1,2:1,3:1,4:1,5:1,6:0,7:1,8:1,9:0,10:0,11:1,12:0,13:1,14:1,15:1,16:1,17:0,18:1,19:1,20:1,21:1,22:1,23:1,24:1,25:1,26:0,27:1,28:1,29:1,30:1,31:1,32:1,33:0,34:1,35:1,36:0,37:0,38:1,39:1}
rows = [json.loads(l) for l in open(HERE + "/pairs_sample.jsonl")]
res = []
for p in rows:
    try: s = label("deepdelve-gpt-oss:latest", p["claim"], p["text"])
    except Exception as e: s = None; print("ERR", p["id"], str(e)[:80])
    res.append(dict(id=p["id"], first=FIRST[p["id"]], second=s)); print(p["id"], FIRST[p["id"]], s, flush=True)
json.dump(res, open(HERE + "/second_pass_results.json", "w"))
L = [r for r in res if r["second"] is not None]; n = len(L); ag = sum(int(r["first"]) == int(r["second"]) for r in L)
tp = sum(r["first"] == 1 and r["second"] for r in L); tn = sum(r["first"] == 0 and not r["second"] for r in L); fp = sum(r["first"] == 0 and r["second"] for r in L); fn = sum(r["first"] == 1 and not r["second"] for r in L)
p1 = (tp + fn) / n; p2 = (tp + fp) / n; pe = p1 * p2 + (1 - p1) * (1 - p2); k = (ag / n - pe) / (1 - pe) if pe < 1 else None
print(json.dumps(dict(n=n, agree=ag, tp=tp, tn=tn, fp=fp, fn=fn, kappa=round(k, 3) if k is not None else None)))
print("disagreements:", [r["id"] for r in L if int(r["first"]) != int(r["second"])])
