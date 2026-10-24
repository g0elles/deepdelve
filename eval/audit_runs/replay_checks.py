"""Replay checks and shadow detectors over the audited reports, offline (no model, no network), and score them against the hand labels.
Each check is a function f(run) -> list[str] of findings; `run` gives report text, source texts, the run's fetched-url list and the evidence facets.
Output: per-run firings, and per defect type: labelled defects, runs where a check of that type fired, matches. Repo root: python eval/audit_runs/replay_checks.py [name ...]"""
import glob, json, os, re, sys
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(HERE + "/../..")
sys.path.insert(0, ROOT + "/src"); sys.path.insert(0, HERE)
from runs import RUNS


class Run:
    def __init__(self, key):
        self.key, self.dir = key, os.path.join(ROOT, RUNS[key])
        self.report = open(self.dir + "/final_report.md", errors="ignore").read()
        st = json.load(open(self.dir + "/_run_state.json")); self.state = st.get("data", st)
        self.fetched = self.state.get("fetched_urls") or []
        self.sources = {os.path.basename(f): open(f, errors="ignore").read() for f in glob.glob(self.dir + "/src_*.md") + glob.glob(self.dir + "/sources/*.md")}
        self.query = self.state.get("query", "")

    def activate(self):
        """Point the grounding module at this run's saved files, as the live run had them."""
        from utils import grounding, run_state
        files = self.sources
        grounding.get_workspace_file_content = lambda fn: files.get(os.path.basename(fn))
        run_state.fetched_urls_ctx.set(list(self.fetched))


def chk_cross_source_contradiction(run):
    from utils.grounding import find_cross_source_contradictions
    return find_cross_source_contradictions(run.report)


def chk_cheap_grounding(run):
    """claim_unsupported / specific_figure / uncited_claims / non_url_citation etc. (the cheap, model-free part of the live grounding gate)."""
    from utils.grounding import cheap_grounding_problems
    return cheap_grounding_problems(run.report, {}, run.fetched)


CHECKS = {"cross_source_contradiction": chk_cross_source_contradiction, "cheap_grounding": chk_cheap_grounding}
# detector name -> defect types it is meant to catch; filled by the shadow detectors in detectors.py
TARGETS = {"cross_source_contradiction": {"C5"}, "cheap_grounding": {"C1", "C2"}}
try:
    from detectors import DETECTORS, DETECTOR_TARGETS
    CHECKS.update(DETECTORS); TARGETS.update(DETECTOR_TARGETS)
except ImportError:
    pass


def labels():
    out = {k: [] for k in RUNS}
    for f in glob.glob(HERE + "/labels/*.jsonl"):
        if "check_firings" in f: continue
        for l in open(f):
            d = json.loads(l)
            if d["sev"] > 0 and d["run"] in out: out[d["run"]].append(d)
    return out


def main(names):
    names = names or list(CHECKS)
    lab = labels(); fired = {n: {} for n in names}
    for key in RUNS:
        run = Run(key); run.activate()
        for n in names:
            try: fired[n][key] = CHECKS[n](run)
            except Exception as e: fired[n][key] = [f"ERROR {type(e).__name__}: {str(e)[:100]}"]
    for n in names:
        print(f"\n## {n}  (targets {sorted(TARGETS.get(n, []))})")
        for key in RUNS:
            f = fired[n][key]
            if f: print(f"- {key}: {len(f)} -> " + " | ".join(x[:140] for x in f[:2]))
        tg = TARGETS.get(n, set())
        if tg:
            pos = [k for k in RUNS if any(d["type"] in tg for d in lab[k])]
            hit = [k for k in pos if fired[n][k] and not str(fired[n][k][0]).startswith("ERROR")]
            fp_runs = [k for k in RUNS if k not in pos and fired[n][k] and not str(fired[n][k][0]).startswith("ERROR")]
            print(f"runs with a labelled {sorted(tg)} defect: {len(pos)}; check fired on {len(hit)} of them; fired on {len(fp_runs)} runs with none ({fp_runs})")


if __name__ == "__main__":
    main(sys.argv[1:])
