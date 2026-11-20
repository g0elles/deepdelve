"""Dump the candidate lists the relevance judge would see, per facet, for saved pipeline evidence folders (no fetch, no model).

Rebuilds `seen` from the folder's saved src_*.md pages (URLs from evidence.json `titles`), runs pipeline.run.select_ce with a recorder in
place of `judge`, so each list is the JUDGE_N-long ranking the judge receives. The lists approximate the live run's (round order and the
cross-facet owner state differ); both judge prompts see the same lists, which is what the A/B needs. Appends one row per candidate to
eval/judge_scope/candidates.jsonl with an empty `label`, to be filled in|out|general BEFORE any model run.

Usage (repo root): python eval/judge_scope/dump_candidates.py <evidence folder> [...]"""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "src"))
import pipeline.run as R                                 # noqa: E402
from pipeline.bridge import source_filename              # noqa: E402


def dump(folder: str) -> list[dict]:
    ev = json.load(open(os.path.join(folder, "evidence.json")))
    query = json.load(open(os.path.join(folder, "metrics.json")))["query"]
    seen = {}
    for url in ev.get("titles") or {}:
        p = os.path.join(folder, source_filename(url))
        if os.path.exists(p):
            seen[url] = open(p, errors="ignore").read()
    keyed_by_file = not seen
    if keyed_by_file:  # folders saved before evidence.json had `titles`: pages keyed by file name; a URL-based domain check sees no domain
        import glob
        seen = {"file://" + os.path.basename(p): open(p, errors="ignore").read() for p in sorted(glob.glob(os.path.join(folder, "src_*.md")))}
    rec = []
    real = R.judge
    R.judge = lambda model, facet, cands: (rec.append((facet, [t for _, t in cands])), None)[1]
    try:
        R.select_ce(query, ev["facets"], seen, list(seen), {f["id"]: {} for f in ev["facets"]}, {}, {}, cfg={"judge": "recorder"})
    finally:
        R.judge = real
    return [{"folder": os.path.relpath(folder, ROOT), "query": query, "facet": f["name"], "questions": f["questions"], "text": t, "label": "", "keyed_by_file": keyed_by_file}
            for f, texts in rec for t in texts]


if __name__ == "__main__":
    out = os.path.join(HERE, "candidates.jsonl")
    done = {json.loads(line)["folder"] for line in open(out)} if os.path.exists(out) else set()
    for folder in sys.argv[1:]:
        if os.path.relpath(folder, ROOT) in done:
            print("skip (already dumped)", folder)
            continue
        rows = dump(folder)
        with open(out, "a") as fh:
            fh.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
        print(folder, len(rows), "candidates")
