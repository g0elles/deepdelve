"""W4 survey (offline, no model): per v2 comparison run, comparison flag as saved and as today's COMPARISON_RE sets it, sides, bridging
quotes, and whether the report carries the "not verified" line. Repo root: ~/.venvs/deepdelve/bin/python eval/writer_defects/w4_survey.py"""
import json, os, sys
ROOT = os.path.abspath(os.path.dirname(os.path.abspath(__file__)) + "/../..")
sys.path.insert(0, ROOT + "/src")
from writer import compiled as wc  # noqa: E402
from pipeline.run import COMPARISON_RE  # noqa: E402

QUERY = {}
for line in open(ROOT + "/eval/comparison_runs/run_all.sh"):
    if line.strip().startswith("[c"):
        k, v = line.strip().split("]=", 1)
        QUERY[k.strip("[ ")] = v.strip().strip(')"').strip('"')
tot = {"sets": 0, "two_sides": 0, "no_bridging": 0, "line": 0}
for line in open(ROOT + "/eval/comparison_runs/runs.tsv"):
    c = line.rstrip("\n").split("\t")
    if len(c) < 7:
        continue
    ev = json.load(open(os.path.join(ROOT, c[3], "evidence.json")))
    rep = os.path.join(ROOT, c[6].rstrip("/"), "final_report.md")
    text = open(rep).read() if os.path.exists(rep) else ""
    C = wc.build(ev, QUERY[c[0]])
    C.comparison = bool(COMPARISON_RE.search(QUERY[c[0]]))
    sides = wc._sides(C)
    br = sum(wc._bridging(q, sides) for f in C.facets for q in f.quotes) if sides else None
    line_ = "no like-for-like comparison" in text
    tot["sets"] += 1; tot["two_sides"] += bool(sides); tot["no_bridging"] += br == 0; tot["line"] += line_
    print(c[0], c[1], c[2], "saved_comparison", ev.get("comparison"), "now", C.comparison, "sides", [e for e, _ in sides], "bridging", br, "line", line_)
print(tot)
