"""W4 Step A dump (offline, no model): every quote of the 18 v2 comparison evidence sets, set id hidden behind a hash, shuffled within
query so model and run are not visible while labelling. Repo root: ~/.venvs/deepdelve/bin/python eval/writer_defects/w4_pairs.py
-> w4_quotes.jsonl (to label), w4_key.json (hash -> run; not opened while labelling).
Optional args: <runs.tsv> <prefix> (e.g. eval/writer_defects/w4_ret_runs.tsv w4_ret) for the B-retrieval sets."""
import hashlib, json, os, random, sys
ROOT = os.path.abspath(os.path.dirname(os.path.abspath(__file__)) + "/../..")
HERE = os.path.dirname(os.path.abspath(__file__))
rows, key = [], {}
TSV = sys.argv[1] if len(sys.argv) > 1 else ROOT + "/eval/comparison_runs/runs.tsv"
PREFIX = sys.argv[2] if len(sys.argv) > 2 else "w4"
for line in open(TSV):
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
with open(os.path.join(HERE, PREFIX + "_quotes.jsonl"), "w") as f:
    f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
json.dump(key, open(os.path.join(HERE, PREFIX + "_key.json"), "w"), indent=1)
print(len(rows), "quotes;", {q: sum(r["query"] == q for r in rows) for q in ("c2", "c3", "c4")})
