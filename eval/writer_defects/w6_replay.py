"""W6 offline replay (PLAN_W6.md), no model. Snapshot the country filters' verdicts with the CURRENT code into w6_<tag>.json:
labelled-sheet jurisdiction_ok, per-page source_off_jurisdiction, per-facet select_ce judge pool (judge stubbed to keep all).
Repo root:  ~/.venvs/deepdelve/bin/python eval/writer_defects/w6_replay.py before   (then after the change: ... after; ... --diff)"""
import glob, hashlib, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(HERE + "/../..")
sys.path.insert(0, ROOT + "/src")


def snapshot(tag):
    from pipeline import run as pr
    out = {"sheet": {}, "pages": {}, "pools": {}}
    for i, line in enumerate(open(os.path.join(ROOT, "eval/pipeline_relevance.jsonl"))):
        r = json.loads(line)
        out["sheet"][i] = [r["label"], pr.jurisdiction_ok(r["text"], pr.countries(r["query"]))]
    dirs = sorted({os.path.dirname(p) for pat in ("research_output/cmp2_*/evidence.json", "research_output/w4ret_*/evidence.json",
                   "eval/heldout3_runs/*/evidence.json", "eval/heldout4_runs/*/evidence.json", "eval/heldout4_runs/rerun2/*/evidence.json",
                   "research_output/pipeline_*/evidence.json") for p in glob.glob(os.path.join(ROOT, pat))})
    pr.judge = lambda model, facet, cands: list(range(len(cands)))
    for d in dirs:
        mp = os.path.join(d, "metrics.json")
        if not os.path.exists(mp) or not glob.glob(d + "/src_*.md"):
            continue
        ev, query = json.load(open(os.path.join(d, "evidence.json"))), json.load(open(mp))["query"]
        known = set(ev.get("titles", {})) | set(ev.get("dates", {})) | {u for by in ev["evidence"].values() for u in by}
        byhash = {hashlib.sha1(u.encode()).hexdigest()[:8]: u for u in known}
        seen = {byhash.get(os.path.basename(f)[4:12], "https://unknown.example/" + os.path.basename(f)): open(f, errors="ignore").read()
                for f in glob.glob(d + "/src_*.md")}
        rel, qk = os.path.relpath(d, ROOT), pr.countries(query)
        for u, txt in seen.items():
            out["pages"][rel + " " + u] = pr.source_off_jurisdiction(u, txt, qk)
        evidence, funnel = {f["id"]: {} for f in ev["facets"]}, {}
        pr.select_ce(query, ev["facets"], seen, list(seen), evidence, {}, funnel, {"judge": "stub"})
        for fid, by in evidence.items():
            out["pools"][rel + " " + fid] = sorted(t for ts in by.values() for t in ts)
    json.dump(out, open(os.path.join(HERE, f"w6_{tag}.json"), "w"), ensure_ascii=False)
    print(tag, "sheet", len(out["sheet"]), "pages", len(out["pages"]), "facet pools", len(out["pools"]))


def diff():
    a, b = (json.load(open(os.path.join(HERE, f"w6_{t}.json"))) for t in ("before", "after"))
    flips = [(k, a["sheet"][k], b["sheet"][k]) for k in a["sheet"] if a["sheet"][k][1] != b["sheet"][k][1]]
    print("sheet flips:", len(flips), "| positives newly dropped:", sum(1 for _, x, y in flips if x[0] == 1 and x[1] and not y[1]),
          "| positives newly kept:", sum(1 for _, x, y in flips if x[0] == 1 and y[1] and not x[1]),
          "| negatives newly kept:", sum(1 for _, x, y in flips if x[0] == 0 and y[1] and not x[1]))
    rows = [json.loads(x) for x in open(os.path.join(ROOT, "eval/pipeline_relevance.jsonl"))]
    for k, x, y in flips:
        r = rows[int(k)]
        print(f"  label={x[0]} {'kept' if y[1] else 'dropped'} now | {r['query'][:50]} | {r['text'][:140]}")
    pages = [k for k in a["pages"] if k in b["pages"] and a["pages"][k] != b["pages"][k]]
    print("pages changed (off-jurisdiction verdict):", len(pages))
    for k in pages:
        print("  ", "dropped->kept" if a["pages"][k] else "kept->dropped", k)
    ch = [k for k in a["pools"] if set(a["pools"][k]) != set(b["pools"].get(k, []))]
    print("facet pools changed:", len(ch), "of", len(a["pools"]))
    for k in ch:
        add, rem = set(b["pools"][k]) - set(a["pools"][k]), set(a["pools"][k]) - set(b["pools"][k])
        print(f"  {k}: +{len(add)} -{len(rem)}")
        for t in sorted(add)[:3]:
            print("     +", t[:150])
        for t in sorted(rem)[:3]:
            print("     -", t[:150])


if __name__ == "__main__":
    diff() if sys.argv[1] == "--diff" else snapshot(sys.argv[1])
