#!/usr/bin/env python
"""Scorecard for one finished research run (a research_output/<run>/ dir), plus every earlier run of the same query.

    python scripts/evaluate_run.py <run_dir>            # newest run of a query vs its siblings
    python scripts/evaluate_run.py <run_dir> --no-siblings

Reads only files the run already wrote (_run_state.json, final_report.md, sources/). No model, no network.
CLAUDE.md requires this to be run and summarised the moment a run completes, before anything else is said about it.
"""
import argparse
import glob
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from engine.completion_checks_structural import _REPORT_META_RE, undisclosed_gap_facets  # noqa: E402  same rule the completion check uses

STUB_WORDS = 150  # a "source" shorter than this is a stub or an interstitial, not evidence
MAX_RUN_MIN = 60  # settings.max_run_minutes default; a run past it ended on the wall clock, not on a clean check
WALL_MARKERS = ("just a moment", "performing security verification", "checking your browser", "access denied", "enable javascript")
FIG = re.compile(r"\b\d+(?:[.,]\d+)?\s?(?:%|percent|per cent)", re.I)


def run_active() -> bool:
    """True while a DeepDelve run process is alive (the state file only changes between completion
    attempts, minutes apart, so its mtime cannot tell 'running' from 'done')."""
    import subprocess
    out = subprocess.run(["pgrep", "-af", r"app\.cli_main|deepdelve-api|src\.api|pipeline\.run"], capture_output=True, text=True).stdout
    me = str(os.getpid())
    return any(l.split(None, 1)[0] != me and "pgrep" not in l and "run_eval_reminder" not in l for l in out.splitlines() if l.strip())


def load(d):
    j = json.load(open(os.path.join(d, "_run_state.json")))
    return j.get("data", j)


def norm_fig(s):
    """'55 percent' / '55 %' / '55%' / '55,5 per cent' all compare equal."""
    return re.sub(r"\s+", "", re.sub(r"per ?cent", "%", s.lower())).replace(",", ".")


def source_texts(d):
    out = {}
    # free-form runs save fetches under sources/; evidence-only runs seed them as src_*.md in the run dir itself
    for f in glob.glob(os.path.join(d, "sources", "*.md")) + glob.glob(os.path.join(d, "src_*.md")):
        out[os.path.basename(f)] = open(f, errors="ignore").read()
    return out


def score(d):
    s, rep = load(d), ""
    rp = os.path.join(d, "final_report.md")
    if os.path.exists(rp):
        rep = open(rp, errors="ignore").read()
    srcs = source_texts(d)
    fetched = s.get("fetched_urls", [])
    urls = {(u["url"] if isinstance(u, dict) else u) for u in fetched}
    cited = {u.rstrip("/") for u in re.findall(r"\]\((https?://[^)\s]+)\)", rep)}
    cited_n = sum(1 for u in urls if u.rstrip("/") in cited)
    stubs = [n for n, t in srcs.items() if len(t.split()) < STUB_WORDS or any(m in t.lower()[:1500] for m in WALL_MARKERS)]
    corpus_n = norm_fig(" ".join(srcs.values()))
    figs = FIG.findall(rep)
    unsupported = [f for f in figs if norm_fig(f) not in corpus_n]
    att = s.get("completion_check_attempts", [])
    end = att[-1]["timestamp"] if att else os.path.getmtime(os.path.join(d, "_run_state.json"))
    findings = s.get("findings", [])
    errs = s.get("tool_error_samples", [])
    gaps = s.get("evidence_gaps") or s.get("unanswered_facets") or []
    gap_facets = [g for g in (s.get("evidence_gaps") or []) if isinstance(g, dict) and g.get("facet")]
    src_block = rep.split("## Sources", 1)[-1] if "## Sources" in rep else ""
    # A Sources-list entry no [N] in the body points at: "cites 12/12" counted list entries, so an uncited source passed (verify run 2026-10-23).
    body_text = rep.split("## Sources", 1)[0]
    listed = {int(n) for n in re.findall(r"^\s*(\d+)\.\s", src_block, re.M)}
    cited_in_body = {int(n) for grp in re.findall(r"\[(\d+(?:\s*[,\-]\s*\d+)*)\]", body_text) for n in re.findall(r"\d+", grp)}
    uncited_listed = sorted(listed - cited_in_body) if "## Sources" in rep and listed and cited_in_body else []
    gap_heading = bool(re.search(r"^#+ .*(evidence gaps?|data limitations?)", rep, re.I | re.M))
    return {
        "gaps_total": len(gap_facets), "gaps_undisclosed": undisclosed_gap_facets(rep, gap_facets) if rep and gap_facets else [],
        "gap_meta": len(_REPORT_META_RE.findall(rep)), "wall_titles": [m for m in WALL_MARKERS if m in src_block.lower()],
        "unrequested_gap_section": gap_heading and not gap_facets, "uncited_listed": uncited_listed,
        # Advisory only: detecting WHICH figure is an unlabelled stand-in is too fuzzy for a completion check (a false fire costs a Builder pass).
        "gaps_no_proxy_label": bool(gap_facets) and bool(rep) and not re.search(r"\bproxy\b|closest (?:available|related)|related figure|not (?:the same as|a direct)", rep, re.I),
        "dir": os.path.basename(d.rstrip("/")), "evidence_only": bool(s.get("evidence_only")),
        "wall_min": round((end - s.get("started_at", end)) / 60), "attempts": len(att),
        "chain": [a.get("problem") or "clean" for a in att], "words": len(rep.split()),
        "fetched": len(urls), "cited": cited_n, "stubs": len(stubs), "stub_names": stubs,
        "findings": len(findings), "empty_findings": sum(1 for f in findings if len((f.get("summary") or "").strip()) < 20),
        "citable": s.get("findings_written_citable_count"), "tool_errors": s.get("tool_error_count", len(errs)),
        "err_kinds": sorted({re.sub(r"[\d']+|sources/\S+", "", str(e).split("Error:", 1)[-1])[:70].strip() for e in errs}),
        "figs": len(figs), "figs_unsupported": unsupported, "gaps_disclosed": len(gaps) if hasattr(gaps, "__len__") else 0,
        "raw_url_titles": len(re.findall(r"\*\*\[Source: https?://", rep)), "report": bool(rep),
        "started": s.get("started_at"), "query": s.get("query", ""),
    }


def verdict(r):
    flags = []
    if not r["report"]:
        flags.append("NO final_report.md")
    if r["fetched"] and r["cited"] / r["fetched"] < 0.5:
        flags.append(f"cites {r['cited']}/{r['fetched']} fetched sources (evidence unused)")
    if r["stubs"]:
        flags.append(f"{r['stubs']} stub/bot-wall page(s) saved as sources")
    if r["empty_findings"]:
        flags.append(f"{r['empty_findings']}/{r['findings']} findings have an empty summary")
    if r["figs_unsupported"]:
        flags.append(f"{len(r['figs_unsupported'])} report figure(s) not found in any saved source: {r['figs_unsupported'][:4]}")
    if r["raw_url_titles"]:
        flags.append(f"{r['raw_url_titles']} source title(s) are raw 'Source: URL' strings")
    if r["gaps_undisclosed"]:
        flags.append(f"{len(r['gaps_undisclosed'])}/{r['gaps_total']} evidence gap(s) not disclosed in the report text: {r['gaps_undisclosed'][:3]}")
    if r["gap_meta"]:
        flags.append(f"{r['gap_meta']} meta-reference(s) to the findings file / gaps section in the report text")
    if r["uncited_listed"]:
        flags.append(f"Sources list entries never cited in the body: {r['uncited_listed']}")
    if r["gaps_no_proxy_label"]:
        flags.append("gaps recorded but the report never labels a stand-in figure as a proxy: check what fills each gap's place")
    if r["wall_titles"]:
        flags.append(f"bot-wall text in the report's source list: {r['wall_titles']}")
    if r["unrequested_gap_section"]:
        flags.append("report has an evidence-gaps/limitations section but the run recorded no evidence gaps")
    if r["chain"] and r["chain"][-1] != "clean":
        flags.append(f"run ended on an unresolved completion problem ({r['chain'][-1]}): the report was never verified clean")
    if r["chain"] and r["wall_min"] > MAX_RUN_MIN:
        flags.append(f"wall clock {r['wall_min']} min exceeds max_run_minutes ({MAX_RUN_MIN})")
    if r["words"] < 500:
        flags.append(f"short report ({r['words']} words)")
    if r["tool_errors"] > 15:
        flags.append(f"{r['tool_errors']} tool errors")
    if r["evidence_only"] is False and r["gaps_disclosed"] == 0 and r["cited"] < 5:
        flags.append("free-form run with no answer-coverage gate: unanswered parts are not enforced")
    return flags


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("--no-siblings", action="store_true")
    a = ap.parse_args()
    r = score(a.run_dir)
    idle = not run_active()
    if idle:  # silences scripts/run_eval_reminder.py, but only for a run that has stopped writing
        try:
            open(os.path.join(a.run_dir, ".evaluated"), "w").write(time.strftime("%F %T"))
        except OSError:
            pass
    else:
        print("NOTE: a run process is still alive; this scorecard is of an INTERMEDIATE state and the run is NOT marked evaluated.\n")
    print(f"# Run scorecard: {r['dir']}\nquery: {r['query']}\n")
    print(f"path: {'evidence-only (pipeline)' if r['evidence_only'] else 'free-form agent'} | wall {r['wall_min']} min | "
          f"{r['attempts']} completion attempts: {' -> '.join(r['chain'])}")
    print(f"report: {r['words']} words, cites {r['cited']}/{r['fetched']} fetched, {r['figs']} % figures "
          f"({len(r['figs_unsupported'])} not in any source), {r['gaps_total'] - len(r['gaps_undisclosed'])}/{r['gaps_total']} gaps disclosed in text")
    print(f"findings: {r['findings']} ({r['empty_findings']} empty), citable at write time: {r['citable']} | tool errors: {r['tool_errors']}")
    for k in r["err_kinds"][:6]:
        print(f"  - {k}")
    if r["stub_names"]:
        print("stub/bot-wall sources: " + ", ".join(n[:40] for n in r["stub_names"][:6]))
    print("\n## Flags")
    for f in verdict(r) or ["none"]:
        print(f"- {f}")
    if not a.no_siblings and r["query"]:
        sib = []
        for d in sorted(glob.glob(os.path.join(os.path.dirname(a.run_dir.rstrip('/')), "*/"))):
            if os.path.exists(os.path.join(d, "_run_state.json")) and os.path.basename(d.rstrip("/")) != r["dir"]:
                try:
                    x = score(d)
                except Exception:
                    continue
                if x["query"] == r["query"]:
                    sib.append(x)
        if sib:
            print("\n## Same query, earlier runs\n| run | path | wall | attempts | words | cited/fetched | figs |\n|---|---|---|---|---|---|---|")
            for x in sib + [r]:
                print(f"| {x['dir'][-15:]} | {'evidence' if x['evidence_only'] else 'free-form'} | {x['wall_min']}m | {x['attempts']} | "
                      f"{x['words']} | {x['cited']}/{x['fetched']} | {x['figs']} |")


if __name__ == "__main__":
    main()
