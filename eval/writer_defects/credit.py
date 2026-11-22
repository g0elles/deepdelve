"""W3 offline credit (eval/writer_defects/PLAN.md): old vs new cross_source_hits on 54 saved reports, no model, no GPU, saved files never written.
Repo root: ~/.venvs/deepdelve/bin/python eval/writer_defects/credit.py   -> hits.jsonl, hits_to_label.jsonl, summary on stdout.
After labelling (labels.jsonl): add --score.
Frozen: needs writer.compiled.add_disagreements, removed by W5 (PLAN_W5.md); reproduce at commit 8502167."""
import glob, json, os, re, sys
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(HERE + "/../..")
sys.path.insert(0, ROOT + "/src"); sys.path.insert(0, ROOT + "/eval/audit_runs")
from utils import grounding as g, run_state  # noqa: E402
from utils.reader_notes import strip_block  # noqa: E402
from writer import compiled as wc  # noqa: E402
import runs as audit  # noqa: E402

DIFFER = "## Where sources differ"
LINE_RE = re.compile(r"^- \*\*(.+?):\*\* (.+?) \[(\d+)\] but (.+?) \[(\d+)\];")
SRC_RE = re.compile(r"^(\d+)\. \[(.*)\]\((https?://[^)\s]+)\)(?:, \d{4}-\d\d-\d\d)?$")
RULESETS = {"off": "", "a": "a", "b": "b", "c": "c", "c+": "c", "ca": "ca", "cab": "abc"}


def corpus():
    out = []
    for tsv, name in (("runs.tsv", "v2"), ("runs_v2_ornith_presence1.5.tsv", "pres1.5"), ("runs_v1_16k_t0.tsv", "v1")):
        for line in open(os.path.join(ROOT, "eval/comparison_runs", tsv)):
            cols = line.rstrip("\n").split("\t")
            if len(cols) >= 7 and cols[6].strip():
                out.append((name, cols[6].strip().rstrip("/"), None))
    out += [("audit", d, k) for k, d in audit.RUNS.items()] + [("audit_val", d, k) for k, d in audit.VAL_RUNS.items()]
    return out


def activate(d):
    st = json.load(open(os.path.join(d, "_run_state.json"))); st = st.get("data", st)
    files = {os.path.basename(f): open(f, errors="ignore").read() for f in glob.glob(d + "/src_*.md") + glob.glob(d + "/sources/*.md")}
    g.get_workspace_file_content = lambda fn: files.get(os.path.basename(fn))
    run_state.fetched_urls_ctx.set(list(st.get("fetched_urls") or []))
    return {e["filename"] if "/" not in e.get("filename", "") else os.path.basename(e["filename"]): e["url"] for e in st.get("fetched_urls") or []}


def stripped(report):
    if DIFFER in report:
        a = report.index(DIFFER); nxt = report.find("\n## ", a + len(DIFFER))
        report = report[:a] + (report[nxt + 1:] if nxt >= 0 else "")
    return strip_block(report)


def hits_with(rules, text):
    orig = g._XSRC_RULES
    g._XSRC_RULES = frozenset(rules)
    try:
        return g.cross_source_hits(text)
    finally:
        g._XSRC_RULES = orig


def sanity(report, old):
    """Old-code hits through add_disagreements must give the saved differ lines, compared on (subject, figure, other_figure, n)."""
    sec = strip_block(report).split("\n## Sources", 1)[-1].split("\n## ", 1)[0].split("\n<!--", 1)[0]
    items = re.split(r"\n(?=\d+\. \[)", "\n" + sec.strip())          # a title can wrap onto the next line
    srcs = []
    for it in (x.strip() for x in items if x.strip()):
        m = SRC_RE.match(" ".join(it.split()))
        if not m:
            return "fail: unparseable source item " + it[:80]
        srcs.append((int(m.group(1)), m.group(3), m.group(2)))
    saved = []
    if DIFFER in report:
        sec = report.split(DIFFER, 1)[1].split("\n## ", 1)[0]
        saved = [LINE_RE.match(ln) for ln in sec.splitlines() if ln.startswith("- **")]
        if not all(saved):
            return "fail: unparseable differ line"
    c = wc.Compiled(query="", facets=[], sources=srcs)
    wc.add_disagreements(c, old)
    got = {(d["subject"].lower(), d["figure"], d["other_figure"], d["n"]): d["m"] for d in c.disagreements}
    want = {(m.group(1).lower(), m.group(2), m.group(4), int(m.group(3))): int(m.group(5)) for m in saved}
    if set(got) != set(want):
        return f"fail: saved {sorted(want)} vs recomputed {sorted(got)}"
    return "ok" if all(got[k] == want[k] for k in want) else "ok (order-dependent m)"


def main():
    rows, summary, c5 = [], [], []
    for corp, rel, key in corpus():
        d = os.path.join(ROOT, rel)
        report = open(os.path.join(d, "final_report.md"), errors="ignore").read()
        fn_url = activate(d)
        text = stripped(report)
        res = {name: hits_with(r, text) for name, r in RULESETS.items()}
        keyset = {name: {(h["subject"], h["figure"], h["other_figure"]) for h in hs} for name, hs in res.items()}
        assert keyset["cab"] <= keyset["off"], (rel, keyset["cab"] - keyset["off"])
        san = sanity(report, res["off"]) if corp in ("v2", "pres1.5", "v1") else "n/a"
        union = {}
        for name in ("off", "cab"):
            for h in res[name]:
                union.setdefault(((h["subject"], h["figure"], h["other_figure"]), h["other_file"]), h)
        for (k, of), h in union.items():
            removed_by = next((r for r, nm in (("c", "c+"), ("a", "ca"), ("b", "cab")) if k not in keyset[nm]), None)
            single = [r for r in "abc" if k not in keyset[r]]
            rows.append({"run": rel, "corpus": corp, "sanity": san, "subject": h["subject"], "figure": h["figure"], "other_figure": h["other_figure"],
                         "cited_files": h["cited_files"], "other_file": of, "other_url": fn_url.get(os.path.basename(of), ""),
                         "segment": h.get("segment", ""), "other_line": h.get("other_line", ""), "removed_by": removed_by, "removed_single": single})
        summary.append((corp, rel, san, len(keyset["off"]), len(keyset["cab"])))
        if key:
            c5.append((corp, key, len(keyset["off"]) > 0, len(keyset["cab"]) > 0))
    with open(os.path.join(HERE, "hits.jsonl"), "w") as f:
        f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    with open(os.path.join(HERE, "hits_to_label.jsonl"), "w") as f:   # blind: no removed_by, no corpus, no provenance
        for r in rows:
            f.write(json.dumps({"key": [r["subject"], r["figure"], r["other_figure"]], "other_file": r["other_file"], "other_url": r["other_url"],
                                "segment": r["segment"], "other_line": r["other_line"]}, ensure_ascii=False) + "\n")
    for corp in ("v2", "pres1.5", "v1", "audit", "audit_val"):
        s = [x for x in summary if x[0] == corp]
        print(f"{corp}: reports {len(s)}, sanity fail {sum(not x[2].startswith(('ok', 'n/a')) for x in s)}, hits old {sum(x[3] for x in s)} new {sum(x[4] for x in s)}")
    for x in summary:
        if not x[2].startswith(("ok", "n/a")):
            print("  SANITY", x[1].split("/")[-1], x[2][:200])
    rb = {}
    for r in rows:
        rb[r["removed_by"]] = rb.get(r["removed_by"], 0) + 1
    print("rows by removed_by (cumulative c>a>b):", rb, "| single-rule removals:", {x: sum(x in r["removed_single"] for r in rows) for x in "abc"})
    print("audit runs (key, old fires, new fires):", [(k, o, n) for _, k, o, n in c5 if o])


def score():
    """Join labels.jsonl (key, other_file, label) with hits.jsonl. Acceptance 2 (0 REAL removed, with denominators and 'tested')."""
    lab = {(tuple(d["key"]), d["other_file"]): d["label"] for d in map(json.loads, open(os.path.join(HERE, "labels.jsonl")))}
    rows = [json.loads(l) for l in open(os.path.join(HERE, "hits.jsonl"))]
    miss = [r for r in rows if ((r["subject"], r["figure"], r["other_figure"]), r["other_file"]) not in lab]
    print("unlabelled rows:", len(miss))
    for corp in ("v2", "pres1.5", "v1", "audit", "audit_val", None):
        sub = [r for r in rows if corp is None or r["corpus"] == corp]
        L = lambda r: lab.get(((r["subject"], r["figure"], r["other_figure"]), r["other_file"]))
        real = [r for r in sub if L(r) == "REAL"]; junk = [r for r in sub if L(r) == "JUNK"]
        print(f"{corp or 'ALL'}: REAL {len(real)} (removed {sum(bool(r['removed_by']) for r in real)}), JUNK {len(junk)} (removed "
              f"{sum(bool(r['removed_by']) for r in junk)}), per rule removed REAL/JUNK " +
              ", ".join(f"{x}: {sum(x in r['removed_single'] for r in real)}/{sum(x in r['removed_single'] for r in junk)}" for x in "abc"))


if __name__ == "__main__":
    score() if "--score" in sys.argv else main()
