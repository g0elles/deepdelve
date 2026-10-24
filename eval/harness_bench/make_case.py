"""Build one fault-injection case: copy a finished, clean run folder, inject one fault into its final_report.md, reset the check-attempt history so
`--resume-run` starts a fresh retry budget on the faulty report. The case folder lives under the arm's workspace dir (resume requires that).

Usage: python eval/harness_bench/make_case.py BASE_RUN_DIR FAULT WORKSPACE_DIR [--name NAME]"""
import argparse, glob, json, os, re, shutil, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from faults import FAULTS  # noqa: E402


def corpus_text(run_dir: str) -> str:
    return " ".join(open(f, errors="ignore").read().lower() for f in glob.glob(os.path.join(run_dir, "src_*.md")) + glob.glob(os.path.join(run_dir, "sources", "*.md")))


def build_case(base_dir: str, fault: str, ws_dir: str, name: str | None = None) -> str:
    base_dir = base_dir.rstrip("/")
    name = name or f"{os.path.basename(base_dir)}__{fault}"
    dst = os.path.join(ws_dir, name)
    if os.path.exists(dst):
        shutil.rmtree(dst)
    shutil.copytree(base_dir, dst, ignore=shutil.ignore_patterns("*.rejected_attempt_*", "final_report.pdf"))
    rep_path = os.path.join(dst, "final_report.md")
    clean = open(rep_path, encoding="utf-8").read()
    faulty, expected = FAULTS[fault](clean, corpus_text(dst))
    assert faulty != clean, "fault left the report unchanged"
    open(os.path.join(dst, "final_report.clean.md"), "w", encoding="utf-8").write(clean)
    open(rep_path, "w", encoding="utf-8").write(faulty)
    sp = os.path.join(dst, "_run_state.json")
    j = json.load(open(sp))
    d = j.get("data", j)
    d["completion_check_attempts"] = []
    d["dispatch_timings"] = []
    d["distinct_problem_bonus_used"] = 0
    d["started_at"] = time.time()
    json.dump(j, open(sp, "w"))
    json.dump({"base": os.path.basename(base_dir), "fault": fault, "expected": list(expected), "clean_words": len(clean.split()),
               "faulty_words": len(faulty.split()), "clean_cites": len(re.findall(r"\[\d+", clean))}, open(os.path.join(dst, "_case.json"), "w"))
    return dst


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("base"); ap.add_argument("fault", choices=sorted(FAULTS)); ap.add_argument("workspace"); ap.add_argument("--name")
    a = ap.parse_args()
    print(build_case(a.base, a.fault, a.workspace, a.name))
