import json, os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
from evaluate_run import score, verdict, advisory

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
# --- citation counting (audit 2026-10-24): "title (url)" source lists, "# Sources" headings, listed-vs-cited ---
_fd = tempfile.mkdtemp()
json.dump({"data": {"fetched_urls": [{"url": "https://a.org/x"}, {"url": "https://b.org/y"}, {"url": "https://c.org/z"}], "query": "q", "started_at": 0}}, open(os.path.join(_fd, "_run_state.json"), "w"))
def _score(report):
    open(os.path.join(_fd, "final_report.md"), "w").write(report)
    return score(_fd)
_r = _score(pad + " Claim one [1]. Claim two [2].\n\n# Sources\n1. **Page A** (https://a.org/x)\n2. **Page B** (https://b.org/y)\n3. **Page C** (https://c.org/z)\n")
assert _r["cited"] == 2 and _r["inline_cites"] == 2 and _r["listed_n"] == 3, _r  # "title (url)" list under "# Sources": 2 of 3 cited, was 0 of 3
assert any("[3]" in f and "never cited" in f for f in verdict(_r)), verdict(_r)
_r = _score(pad + "\n## Sources\n1. **[Page A](https://a.org/x)**\n2. **[Page B](https://b.org/y)**\n3. **[Page C](https://c.org/z)**\n")
assert _r["cited"] == 0 and _r["inline_cites"] == 0, _r  # listed is not cited: used to score 3 of 3
assert any("no inline citations" in f for f in verdict(_r)), verdict(_r)
_r = _score(pad + " See [Page A](https://a.org/x) and [Page B](https://b.org/y).\n")
assert _r["cited"] == 2, _r  # inline markdown links keep working
assert not any("no inline citations" in f for f in verdict(_r))
print("test_evaluate_run_gaps citations OK")
# --- advisory shadow detectors (audit 2026-10-24): hints only, never flags, never fatal ---
_ad = tempfile.mkdtemp()
json.dump({"data": {"started_at": 1790900000, "query": "q"}}, open(os.path.join(_ad, "_run_state.json"), "w"))  # 2026-10-02
open(os.path.join(_ad, "final_report.md"), "w").write(pad + " Topline data from the Phase 3 trial are expected in summer 2024. A claim [1].\n\n## Sources\n1. **[a](https://a.org/x)**\n")
_hints = advisory(_ad)
assert any(h.startswith("stale_status:") and "summer 2024" in h for h in _hints), _hints
assert not any("stale_status" in f for f in verdict(score(_ad))), "advisory hints must not become flags"
_nr = tempfile.mkdtemp()
json.dump({"data": {"query": "q", "started_at": 0}}, open(os.path.join(_nr, "_run_state.json"), "w"))
assert advisory(_nr) == [], "no report, no hints, no crash"
print("test_evaluate_run_gaps advisory OK")
