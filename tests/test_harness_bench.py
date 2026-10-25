"""Pure-logic tests for eval/harness_bench (fault injection, case building, outcome collection). No GPU."""
import json, os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "eval", "harness_bench"))
from faults import FAULTS, unsupported_claim, meta_language, remove_gap_section, strip_citations  # noqa: E402
from make_case import build_case  # noqa: E402
from collect import collect  # noqa: E402

REP = ("# T\n\n## Summary\nA claim about adoption [1]. More text [2][3].\n\n## Body\nSecond cited claim [2].\n\n"
       "## Evidence gaps and data limitations\n- **F:** No source in the available evidence directly states: q.\n\n## Sources\n1. a\n2. b\n3. c\n")


def main():
    r, exp = unsupported_claim(REP, "no figures here 12")
    assert "83.7" in r and "[1]" in r.split("83.7")[1][:16] and "claim_unsupported" in exp
    r, exp = unsupported_claim(REP, "83.7 61.4 are in the corpus")
    assert "47.9" in r
    r, exp = meta_language(REP)
    assert "findings.md" in r and exp == ("report_meta_language",)
    r, exp = remove_gap_section(REP)
    assert "Evidence gaps" not in r and "## Sources" in r and exp == ("undisclosed_evidence_gap",)
    try:
        remove_gap_section("# T\n\n## Summary\nx [1].\n"); raise AssertionError("must refuse without a gaps section")
    except ValueError:
        pass
    r, exp = strip_citations(REP)
    assert r.split("## Body")[0].count("[") == 0 and "[2]" in r.split("## Body")[1]
    assert set(FAULTS) == {"unsupported_claim", "meta_language", "remove_gap_section", "strip_citations"}

    base = tempfile.mkdtemp(); ws = tempfile.mkdtemp()
    open(os.path.join(base, "final_report.md"), "w").write(REP)
    open(os.path.join(base, "src_a.md"), "w").write("source text 12 percent")
    open(os.path.join(base, "final_report.pdf"), "w").write("x")
    json.dump({"data": {"query": "q", "started_at": 1.0, "completion_check_attempts": [{"problem": "old", "timestamp": 5}], "evidence_only": True}}, open(os.path.join(base, "_run_state.json"), "w"))
    case = build_case(base, "meta_language", ws)
    assert not os.path.exists(os.path.join(case, "final_report.pdf")) and "findings.md" in open(os.path.join(case, "final_report.md")).read()
    assert open(os.path.join(case, "final_report.clean.md")).read() == REP
    st = json.load(open(os.path.join(case, "_run_state.json")))["data"]
    assert st["completion_check_attempts"] == [] and st["evidence_only"] is True and st["started_at"] > 1e9

    # collect: faulty check fires at attempt 0, next check clean, one dispatch of each role recorded
    t0 = st["started_at"]
    st["completion_check_attempts"] = [{"problem": "report_meta_language", "timestamp": t0 + 60}, {"problem": None, "timestamp": t0 + 300}]
    st["dispatch_timings"] = [{"name": "SubAgent_BuilderFix_attempt1", "start": t0, "seconds": 120.0}, {"name": "SubAgent_ReviewFix_attempt1", "start": t0, "seconds": 60.0}]
    json.dump({"data": st}, open(os.path.join(case, "_run_state.json"), "w"))
    open(os.path.join(case, "final_report.md"), "w").write(REP)
    c = collect(case)
    assert c["detected"] and c["ended_clean"] and c["fixed_in_one_pass"] and c["checks"] == 2 and c["minutes"] == 5.0, c
    assert c["dispatch_minutes"] == {"BuilderFix": 2.0, "ReviewFix": 1.0} and c["words_lost"] == 0 and c["cites_lost"] == 0 and c["other_problems"] == [], c
    # judge A/B scoring (eval/judge_cases): pure counting, no GPU
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "eval", "judge_cases"))
    from ab_judge import score
    labs = [{"dir": "d", "facet": "f", "q": 0, "label": "gap"}, {"dir": "d", "facet": "f", "q": 1, "label": "answered"}, {"dir": "d", "facet": "f", "q": 2, "label": "answered"},
            {"dir": "d", "facet": "f", "q": 3, "label": "gap"}, {"dir": "d", "facet": "f", "q": 4, "label": "borderline"}]
    assert score(labs, {("d", "f"): {0, 1, 4}}) == {"gap_caught": 1, "false_gap": 1, "answered_ok": 1, "gap_missed": 1, "borderline_flagged": 1}
    lj = json.load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "eval", "judge_cases", "labels.json")))["labels"]
    assert len(lj) == 24 and {x["label"] for x in lj} == {"gap", "answered", "borderline"}
    print("test_harness_bench OK")


if __name__ == "__main__":
    main()
