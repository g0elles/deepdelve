"""W7 blind labelling sheet (PLAN_W7.md): every added/removed pool row (w7_rows.jsonl) + 40 baseline rows from today's single-country
facet pools (w7_before.json), shuffled (seed 7), no change tag. Writes w7_sheet.jsonl (id, pool, facet, query, url, text) and
w7_key.json (id -> added|removed|baseline). Repo root: ~/.venvs/deepdelve/bin/python eval/writer_defects/w7_sheet.py"""
import json, os, random, sys
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(HERE + "/../..")
sys.path.insert(0, ROOT + "/src")
from pipeline.run import _country_key, facet_entities  # noqa: E402

rows = [json.loads(x) for x in open(os.path.join(HERE, "w7_rows.jsonl"))]
before = json.load(open(os.path.join(HERE, "w7_before.json")))
single = []
for pool, meta in before["facets"].items():
    d, fid = pool.rsplit(" ", 1)
    ev = json.load(open(os.path.join(ROOT, d, "evidence.json")))
    mine = facet_entities(ev["facets"], meta["query"]).get(fid, set())
    if len(mine) == 1 and _country_key(next(iter(mine))):
        single += [{"pool": pool, "change": "baseline", "url": u, "text": t, **meta} for u, t in before["pools"].get(pool, [])]
rng = random.Random(7)
seen = {(r["pool"], r["text"]) for r in rows}
base = rng.sample([r for r in single if (r["pool"], r["text"]) not in seen], min(40, len(single)))
allr = rows + base
rng.shuffle(allr)
key, sheet = {}, []
for i, r in enumerate(allr):
    key[i] = r["change"]
    sheet.append({"id": i, "pool": r["pool"], "facet": r["name"], "questions": r["questions"], "query": r["query"], "url": r["url"], "text": r["text"]})
with open(os.path.join(HERE, "w7_sheet.jsonl"), "w") as fh:
    fh.writelines(json.dumps(s, ensure_ascii=False) + "\n" for s in sheet)
json.dump(key, open(os.path.join(HERE, "w7_key.json"), "w"))
print("rows", len(rows), "baseline pool", len(single), "sheet", len(sheet))
