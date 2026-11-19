"""Replay checks and shadow detectors over the audited reports, offline (no model, no network), and score them against the hand labels.
Each check is a function f(run) -> list[str] of findings; `run` gives report text, source texts, the run's fetched-url list and the evidence facets.
Output: per-run firings, and per defect type: labelled defects, runs where a check of that type fired, matches. Repo root: python eval/audit_runs/replay_checks.py [name ...]"""
import glob, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(HERE + "/../..")
sys.path.insert(0, ROOT + "/src"); sys.path.insert(0, HERE)
import runs as _runs
SET = os.environ.get("AUDIT_SET", "main")  # AUDIT_SET=validation scores the cheap-validation reports against labels_validation/
RUNS = _runs.VAL_RUNS if SET == "validation" else _runs.RUNS
LABEL_DIR = "/labels_validation" if SET == "validation" else "/labels"


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


def claims(report):
    """(C<k>, claim text, [n, ...]) numbered exactly as claim_sheet.py numbers them, plus the report's [n] -> url map."""
    import re
    from utils.grounding import decompose_claim_segments
    prose, srcs = (re.split(r"\n#{1,3} Sources", report, maxsplit=1) + [""])[:2]
    nm = {int(m.group(1)): m.group(2).rstrip("/") for m in (re.match(r"\s*(\d+)\.\s.*?\((https?://[^)\s]+)\)", ln) for ln in srcs.splitlines()) if m}
    out, k = [], 0
    for line in prose.splitlines():
        if line.startswith("#"):
            continue
        for seg in decompose_claim_segments(line):
            claim = re.sub(r"\[\d+\]", "", seg).strip("-*|. ").replace("**", "").strip()
            if len(claim) >= 25:
                k += 1
                out.append((f"C{k}", claim, [int(n) for n in re.findall(r"\[(\d+)\]", seg)]))
    return out, nm


def chk_compiled_verifier(run):
    """The compiled writer's figure_context rule, with each cited source's whole saved page as the quote. Only the figure rule is
    reported: hedge_dropped and stale_year read the whole page as the quote and would fire on any page with "may" or a recent year."""
    from writer.compiled import check
    cl, nm = claims(run.report)
    u2f = {(e.get("url") or "").rstrip("/"): os.path.basename(e.get("filename") or "") for e in run.fetched if isinstance(e, dict)}
    out = []
    for cid, claim, cites in cl:
        pages = {n: run.sources.get(u2f.get(nm.get(n, ""), "")) for n in cites}
        pages = {n: t for n, t in pages.items() if t}
        if pages:
            out += [f"{cid}:{d}" for d in check({"cites": list(pages), "text": claim}, pages, run.query) if d.startswith("figure_context")]
    return out


CHECKS = {"cross_source_contradiction": chk_cross_source_contradiction, "cheap_grounding": chk_cheap_grounding, "compiled_verifier": chk_compiled_verifier}
# detector name -> defect types it is meant to catch; filled by the shadow detectors in detectors.py
TARGETS = {"cross_source_contradiction": {"C5"}, "cheap_grounding": {"C1", "C2"}, "compiled_verifier": {"C1"}}
try:
    from detectors import DETECTORS, DETECTOR_TARGETS
    CHECKS.update(DETECTORS); TARGETS.update(DETECTOR_TARGETS)
except ImportError:
    pass


def unit_ids(unit):
    """"C19-C21" -> {C19, C20, C21}; "C4/C5" -> {C4, C5}; Q* units (question-level) -> none."""
    import re
    ids = set()
    for part in re.split(r"[/,\s]+", unit or ""):
        m = re.fullmatch(r"C(\d+)(?:-C?(\d+))?", part)
        if m:
            ids |= {f"C{i}" for i in range(int(m.group(1)), int(m.group(2) or m.group(1)) + 1)}
    return ids


def labels():
    out = {k: [] for k in RUNS}
    for f in glob.glob(HERE + LABEL_DIR + "/*.jsonl"):
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
            unit_level = [(k, x.split(":", 1)[0]) for k in RUNS for x in fired[n][k] if str(x)[:1] == "C" and ":" in str(x)]
            if unit_level:  # findings named "C<k>:<code>": score per claim (stage-J records ignored)
                any_lab = {k: set().union(*(unit_ids(d["unit"]) for d in lab[k] if d.get("stage") != "J")) for k in RUNS}
                tgt_lab = {k: set().union(*(unit_ids(d["unit"]) for d in lab[k] if d.get("stage") != "J" and d["type"] in tg)) for k in RUNS}
                fu = set(unit_level)
                tp = sum(u in tgt_lab[k] for k, u in fu)
                fa = sum(u not in any_lab[k] for k, u in fu)
                print(f"claim level: fired on {len(fu)} claims; {tp} carry a labelled {sorted(tg)} defect "
                      f"(of {sum(len(v) for v in tgt_lab.values())}); {fa} are labelled-correct claims")


if __name__ == "__main__":
    main(sys.argv[1:])
