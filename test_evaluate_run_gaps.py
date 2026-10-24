import json, os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts"))
from evaluate_run import score, verdict

GAPS = [{"facet": "Estonia citizen adoption of digital services"}, {"facet": "Finland citizen adoption of digital services"}]


def _run(report, gaps):
    d = tempfile.mkdtemp()
    json.dump({"data": {"evidence_gaps": gaps, "query": "q", "started_at": 0}}, open(os.path.join(d, "_run_state.json"), "w"))
    open(os.path.join(d, "final_report.md"), "w").write(report)
    return verdict(score(d))


pad = "word " * 600
flags = _run(pad + "\nFinland citizen adoption of digital services could not be found in available sources.\n", GAPS)
assert any("1/2 evidence gap" in f and "Estonia" in f for f in flags), flags
flags = _run(pad + "\nDigital services adoption: not found, see the findings file.\n## Sources\n1. Just a moment...\n", GAPS)
assert any("2/2 evidence gap" in f for f in flags) and any("meta-reference" in f for f in flags) and any("bot-wall" in f for f in flags), flags
flags = _run(pad + "\n## Evidence gaps and data limitations\n- A specific figure for Estonia citizen adoption of digital services could not be confirmed in the available sources.\n"
             "- A specific figure for Finland citizen adoption of digital services could not be confirmed in the available sources.\n", GAPS)
assert not any("evidence gap" in f for f in flags), flags
assert any("never labels a stand-in" in f for f in flags), flags
assert not any("never labels" in f for f in _run(pad + " Closest available: 96% online interaction (proxy).\n- A specific figure for Estonia citizen adoption of digital services could not be confirmed in the available sources.\n"
                                                  "- A specific figure for Finland citizen adoption of digital services could not be confirmed in the available sources.\n", GAPS))
assert any("recorded no evidence gaps" in f for f in _run(pad + "\n## Data limitations\nx\n", []))
def _attempts(problems, minutes):
    d = tempfile.mkdtemp()
    att = [{"problem": p, "timestamp": minutes * 60} for p in problems]
    json.dump({"data": {"completion_check_attempts": att, "query": "q", "started_at": 0}}, open(os.path.join(d, "_run_state.json"), "w"))
    open(os.path.join(d, "final_report.md"), "w").write(pad)
    return verdict(score(d))


flags = _attempts(["missing_artifact", "claim_unsupported"], 88)
assert any("unresolved completion problem (claim_unsupported)" in f for f in flags) and any("exceeds max_run_minutes" in f for f in flags), flags
flags = _attempts(["missing_artifact", None], 20)
assert not any("unresolved" in f or "max_run_minutes" in f for f in flags), flags
_body = pad + " A claim [1]. Another claim [2][3]. A range [4, 5]."
assert not any("never cited" in f for f in _run(_body + "\n## Sources\n1. **[a](u)**\n2. **[b](u)**\n3. **[c](u)**\n4. **[d](u)**\n5. **[e](u)**\n", []))
assert any("[6]" in f and "never cited" in f for f in _run(_body + "\n## Sources\n1. **[a](u)**\n2. **[b](u)**\n3. **[c](u)**\n4. **[d](u)**\n5. **[e](u)**\n6. **[f](u)**\n", []))
_d = tempfile.mkdtemp()
json.dump({"data": {"budget_exhausted": "wall_clock", "query": "q", "started_at": 0}}, open(os.path.join(_d, "_run_state.json"), "w"))
open(os.path.join(_d, "final_report.md"), "w").write(pad)
assert any("HARD max_run_minutes cap" in f for f in verdict(score(_d))), "a budget-exhausted run must be flagged"
print("test_evaluate_run_gaps OK")
