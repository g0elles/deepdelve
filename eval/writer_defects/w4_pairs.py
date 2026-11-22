"""W4 Step A dump (offline, no model): every quote of the 18 v2 comparison evidence sets, set id hidden behind a hash, shuffled within
query so model and run are not visible while labelling. Repo root: ~/.venvs/deepdelve/bin/python eval/writer_defects/w4_pairs.py
-> w4_quotes.jsonl (to label), w4_key.json (hash -> run; not opened while labelling)."""
import hashlib, json, os, random
ROOT = os.path.abspath(os.path.dirname(os.path.abspath(__file__)) + "/../..")
HERE = os.path.dirname(os.path.abspath(__file__))
rows, key = [], {}
for line in open(ROOT + "/eval/comparison_runs/runs.tsv"):
    c = line.rstrip("\n").split("\t")
    if len(c) < 7:
        continue
    sid = hashlib.sha256(c[3].encode()).hexdigest()[:8]
    key[sid] = {"query": c[0], "model": c[1], "run": c[2], "evidence": c[3]}
    ev = json.load(open(os.path.join(ROOT, c[3], "evidence.json")))
    names = {f["id"]: f["name"] for f in ev["facets"]}
    for fid, by in ev["evidence"].items():
        for url, quotes in by.items():
            for q in quotes:
                rows.append({"query": c[0], "set": sid, "facet": fid, "facet_name": names.get(fid, ""), "url": url, "quote": q})
random.Random(0).shuffle(rows)
rows.sort(key=lambda r: r["query"])  # stable: shuffled within query
for i, r in enumerate(rows):
    r["id"] = i
with open(os.path.join(HERE, "w4_quotes.jsonl"), "w") as f:
    f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
json.dump(key, open(os.path.join(HERE, "w4_key.json"), "w"), indent=1)
print(len(rows), "quotes;", {q: sum(r["query"] == q for r in rows) for q in ("c2", "c3", "c4")})
