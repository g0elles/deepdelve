"""Score W7 blind labels against the key (PLAN_W7.md bar). Repo root: ~/.venvs/deepdelve/bin/python eval/writer_defects/w7_score.py"""
import collections, json, math, os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from w7_labels import L  # noqa: E402

key = json.load(open(os.path.join(HERE, "w7_key.json")))
sheet = [json.loads(x) for x in open(os.path.join(HERE, "w7_sheet.jsonl"))]
assert len(L) == len(sheet) == len(key)
by = collections.defaultdict(collections.Counter)
for s in sheet:
    by[key[str(s["id"])]][L[s["id"]]] += 1


def wilson(k, n, z=1.96):
    if not n:
        return (0, 0)
    p = k / n; d = 1 + z * z / n; c = p + z * z / (2 * n); h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - h) / d, (c + h) / d)


for g in ("added", "removed", "baseline"):
    c, n = by[g], sum(by[g].values())
    lo, hi = wilson(c["O"], n)
    print(f"{g:8} n={n:3}  ON {c['O']:3}  OFF {c['F']:2}  NOISE {c['N']:2}  UNCLEAR {c['U']:2}  ON rate {c['O'] / max(n, 1):.3f}  95% CI [{lo:.3f}, {hi:.3f}]")
a, r = by["added"], by["removed"]
print("bar (a) ON/added >= 0.80 with >= 15 added:", sum(a.values()) >= 15 and a["O"] / sum(a.values()) >= 0.80)
print("bar (b) ON added >= ON removed:", a["O"], ">=", r["O"], a["O"] >= r["O"])
t = [s for s in sheet if "46,400 tonnes" in s["text"]]
print("bar (c) c4 UK sugar sentence:", [(s["pool"], key[str(s["id"])]) for s in t])
