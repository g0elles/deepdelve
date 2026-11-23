"""W8 live A/B (PLAN_W8.md "Acceptance"): ON = scholarly search in round 1, OFF = pr.scholar_search patched to return [].
Per run: pipeline.run.run() with the CLI defaults (topk 3, ce, judge), then app.py --pipeline-evidence --writer-mode compiled with the
config copy naming ornith (same as the 2026-11-22 academic run); Q-acad also --style academic. GPU + fetching. Repo root:
    DEEPDELVE_FORCE_VIRTUAL_DISPLAY=1 ~/.venvs/deepdelve/bin/python eval/writer_defects/w8_ab.py <config_ornith.yaml>
Resumable: rows already in w8_runs.tsv are skipped."""
import os, subprocess, sys, time
from pathlib import Path
ROOT = os.path.abspath(os.path.dirname(os.path.abspath(__file__)) + "/../..")
sys.path.insert(0, ROOT + "/src")
from pipeline import run as pr  # noqa: E402

MODEL = "ornith-1.5:9b"
QUERY = {"acad": "What does peer-reviewed research report about the effects of Chile's 2016 food labelling and advertising law and Mexico's "
                 "2020 front-of-package warning labels on purchases of sugary drinks, and how do the two effects compare?",
         "c2": "Does semaglutide reduce alcohol consumption in people with alcohol use disorder, and how does the evidence compare with naltrexone?",
         "leave": "What are the legal requirements for minimum paid annual leave in France, Brazil and South Korea, including how many days and "
                  "how pay is calculated?"}
LOG = Path(ROOT) / "eval/writer_defects/w8_runs.tsv"
_scholar = pr.scholar_search


def one(q, arm, k, cfg):
    ev = f"research_output/w8_{q}_{arm}_r{k}"
    pr.scholar_search = _scholar if arm == "on" else (lambda question, k_: [])
    t0 = time.time()
    m = pr.run(QUERY[q], MODEL, Path(ROOT) / ev, 3, "ce", True)
    secs = round(time.time() - t0)
    before = set(os.listdir(Path(ROOT) / "research_output"))
    cmd = [sys.executable, "src/app.py", "--config", cfg, "--prompt", QUERY[q], "--pipeline-evidence", ev, "--writer-mode", "compiled",
           "--auto-approve"] + (["--style", "academic"] if q == "acad" else [])
    with open(Path(ROOT) / f"{ev}.app.log", "w") as log:
        rc = subprocess.run(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT).returncode
    new = sorted(set(os.listdir(Path(ROOT) / "research_output")) - before - {Path(ev).name})
    rep = next((f"research_output/{d}" for d in new if (Path(ROOT) / "research_output" / d / "final_report.md").exists()), "")
    g = m["gather"]
    with open(LOG, "a") as f:
        f.write(f"{q}\t{arm}\t{k}\t{ev}\t{rep}\t{secs}\tapp_rc={rc}\tsearches={g.get('searches', 0)} empty={g.get('empty_searches', 0)} "
                f"scholar={g.get('scholar_sources', 0)}/{g.get('scholar_searches', 0)} full={g.get('scholar_fulltext', 0)} "
                f"fail={g.get('scholar_fail', 0)}\n")
    print(q, arm, k, secs, "s", rep or "NO REPORT", g, flush=True)


def main():
    assert os.environ.get("DEEPDELVE_FORCE_VIRTUAL_DISPLAY") == "1", "set DEEPDELVE_FORCE_VIRTUAL_DISPLAY=1 (headed browser popups)"
    cfg = os.path.abspath(sys.argv[1])
    os.environ.pop("DISPLAY", None)
    os.environ.pop("WAYLAND_DISPLAY", None)
    done = {tuple(line.split("\t")[:3]) for line in LOG.read_text().splitlines()} if LOG.exists() else set()
    try:
        for k in (1, 2):
            for q in QUERY:
                for arm in (("on", "off") if k == 1 else ("off", "on")):  # ON/OFF order alternated per pair
                    if (q, arm, str(k)) not in done:
                        one(q, arm, k, cfg)
    finally:
        subprocess.run(["ollama", "stop", MODEL], capture_output=True)


if __name__ == "__main__":
    main()
