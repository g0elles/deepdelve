"""Skeleton fixpoint replay (plan phase 1b, no GPU): compile a report from every saved pipeline evidence directory with a model that always
fails, so every unit falls back to its verbatim quote (the worst-case, fully deterministic report), then run the model-free completion
checks on it. A check that fires on the skeleton is either a compiler gap or a false positive of the checker. Repo root:
    python eval/writer_ab/skeleton_replay.py [dir ...]      -> table on stdout, eval/writer_ab/skeleton_matrix.json
With DD_MODEL=<ollama model> the units are written by that model instead (the real-model replay; DD_DRAFTS drafts per unit, default 1), output
eval/writer_ab/replay_<model>.json. GPU: one model call per unit."""
import glob, json, os, re, sys
ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, ROOT + "/src")
from writer import compiled as wc                                    # noqa: E402
from pipeline.bridge import source_filename                          # noqa: E402
from utils import grounding, run_state                               # noqa: E402
from engine import completion_checks_grounding as cg, completion_checks_structural as cs  # noqa: E402

DIRS = sorted(glob.glob(ROOT + "/eval/heldout3_runs/*/evidence.json") + glob.glob(ROOT + "/eval/heldout4_runs/*/evidence.json")
              + glob.glob(ROOT + "/research_output/cmp_*/evidence.json") + glob.glob(ROOT + "/research_output/pipeline_*/evidence.json"))


def down(*a):
    raise RuntimeError("skeleton: no model")


MODEL = os.environ.get("DD_MODEL", "")


def real_llm(prompt, schema, temperature, seed):
    from pipeline.llm import call_json
    return call_json(MODEL, prompt, schema, think="minimal", timeout=180, temperature=temperature, seed=seed)


def run_one(evpath: str) -> dict:
    d = os.path.dirname(evpath)
    ev = json.load(open(evpath))
    files = {os.path.basename(f): open(f, errors="ignore").read() for f in glob.glob(d + "/src_*.md")}
    if not files:
        return {"skip": "no saved sources"}
    mp = d + "/metrics.json"
    query = json.load(open(mp))["query"] if os.path.exists(mp) else "research request"
    c = wc.build(ev, query)
    written, stats = wc.compose(c, real_llm if MODEL else down, int(os.environ.get("DD_DRAFTS", 1)))
    report = wc.render(c, written)
    grounding.get_workspace_file_content = lambda fn: files.get(os.path.basename(fn))
    titles = ev.get("titles") or {}
    fetched = [{"url": u, "filename": source_filename(u), "title": titles.get(u, "")} for _, u, _ in c.sources]
    run_state.fetched_urls_ctx.set(fetched)
    if wc.add_disagreements(c, grounding.cross_source_hits(report)):
        report = wc.render(c, written)
    if os.environ.get("DD_SAVE"):
        os.makedirs(os.environ["DD_SAVE"], exist_ok=True)
        open(os.path.join(os.environ["DD_SAVE"], os.path.relpath(d, ROOT).replace("/", "__") + ".md"), "w").write(report)
    fired = {}
    for p in grounding.cheap_grounding_problems(report, {}, fetched):
        fired[p.split(":")[0]] = p[:160]
    if grounding.find_cross_source_contradictions(report):
        fired["cross_source_contradiction"] = grounding.find_cross_source_contradictions(report)[0][:160]
    if cg.find_duplicate_report_sections(report) or cg.find_duplicate_heading_text(report):
        fired["duplicate_report_sections"] = "yes"
    m = cs._REPORT_META_RE.search(report)
    if m:
        fired["report_meta_language"] = m.group(0)
    gaps = [{"facet": f.name, "questions": f.unanswered} for f in c.facets if f.unanswered]
    if gaps and cs.undisclosed_gap_facets(report, gaps):
        fired["undisclosed_evidence_gap"] = str(cs.undisclosed_gap_facets(report, gaps))[:160]
    return {"query": query[:70], "facets": len(c.facets), "sources": len(c.sources), "words": len(report.split()), "gaps": len(gaps), "fired": fired, "stats": stats, "extractive_units": sum(1 for u in written.values() for x in u if x.get("extractive")), "units": len(written)}


def main():
    todo = [os.path.join(os.path.abspath(a), "evidence.json") for a in sys.argv[1:]] or DIRS
    out, counts = {}, {}
    for ep in todo:
        name = os.path.relpath(os.path.dirname(ep), ROOT)
        try:
            r = run_one(ep)
        except Exception as e:  # keep going: one broken directory must not hide the others
            r = {"error": f"{type(e).__name__}: {e}"[:200]}
        out[name] = r
        for k in r.get("fired", {}):
            counts[k] = counts.get(k, 0) + 1
        print(f"{name:55s}", r.get("skip") or r.get("error") or f"words={r['words']:5d} src={r['sources']:3d} fired={sorted(r['fired'])}")
    ok = [r for r in out.values() if "fired" in r]
    print(f"\n{len(ok)} reports compiled; checks fired on the skeleton (count of reports):", json.dumps(counts, indent=1))
    print("reports with NO check firing:", sum(1 for r in ok if not r["fired"]), "of", len(ok))
    tag = "replay_" + re.sub(r"\W+", "_", MODEL) if MODEL else "skeleton_matrix"
    json.dump(out, open(ROOT + f"/eval/writer_ab/{tag}.json", "w"), indent=1)
    if MODEL:
        print("units", sum(r["units"] for r in ok), "fallbacks to verbatim", sum(r["extractive_units"] for r in ok), "repairs", sum(r["stats"].get("repairs", 0) for r in ok))


if __name__ == "__main__":
    main()
