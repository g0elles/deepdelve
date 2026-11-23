"""W8b A/B (PLAN_W8b.md): planner output frozen per (query, pair) and shared by both arms; ON vs OFF (pr.scholar_search -> []); plus
2 A/A pairs (OFF vs OFF). Per run: pr.run (CLI defaults) then app.py compiled writer, as w8_ab.py. GPU + fetching. Repo root:
    DEEPDELVE_FORCE_VIRTUAL_DISPLAY=1 ~/.venvs/deepdelve/bin/python eval/writer_defects/w8b_ab.py <config copy naming ornith>
Resumable: rows already in w8b_runs.tsv are skipped."""
import json, os, subprocess, sys, time
from pathlib import Path
ROOT = os.path.abspath(os.path.dirname(os.path.abspath(__file__)) + "/../..")
sys.path.insert(0, ROOT + "/src")
from pipeline import run as pr  # noqa: E402
import w8_ab  # noqa: E402  (same directory: query texts)

MODEL = "ornith-1.5:9b"
QUERY = {"acad": w8_ab.QUERY["acad"], "c2": w8_ab.QUERY["c2"],
         "vitd": "Does vitamin D supplementation reduce the risk of acute respiratory infections, and how strong is the evidence?",
         "cbti": "How effective is cognitive behavioural therapy for insomnia compared with sleep medication, according to clinical trials?",
         "pm25": "What does epidemiological research report about long-term air pollution exposure and dementia risk?",
         "minwage": "What does economic research find about the effect of minimum wage increases on employment?",
         "leave": w8_ab.QUERY["leave"],
         "heat": "How much does it cost to run a heat pump compared with a gas boiler in the UK?"}
LOG = Path(ROOT) / "eval/writer_defects/w8b_runs.tsv"
_scholar, _plan = pr.scholar_search, pr.plan


def frozen_plan(q, k):
    p = Path(ROOT) / f"research_output/w8b_{q}_r{k}.plan.json"
    if not p.exists():
        p.write_text(json.dumps(_plan(MODEL, QUERY[q]), ensure_ascii=False, indent=1))
    return json.loads(p.read_text())


def one(q, arm, k, cfg):
    ev = f"research_output/w8b_{q}_{arm}_r{k}"
    fs = frozen_plan(q, k)
    pr.plan = lambda model, query: [dict(f) for f in fs]
    pr.scholar_search = _scholar if arm == "on" else (lambda question, k_: [])
    t0 = time.time()
    m = pr.run(QUERY[q], MODEL, Path(ROOT) / ev, 3, "ce", True)
    secs = round(time.time() - t0)
    before = set(os.listdir(Path(ROOT) / "research_output"))
    cmd = [sys.executable, "src/app.py", "--config", cfg, "--prompt", QUERY[q], "--pipeline-evidence", ev, "--writer-mode", "compiled",
           "--auto-approve"] + (["--style", "academic"] if q == "acad" else [])
    with open(Path(ROOT) / f"{ev}.app.log", "w") as log:
        rc = subprocess.run(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT).returncode
    new = sorted(set(os.listdir(Path(ROOT) / "research_output")) - before)
    rep = next((f"research_output/{d}" for d in new if (Path(ROOT) / "research_output" / d / "final_report.md").exists()), "")
    g = m["gather"]
    with open(LOG, "a") as f:
        f.write(f"{q}\t{arm}\t{k}\t{ev}\t{rep}\t{secs}\tapp_rc={rc}\tsearches={g.get('searches', 0)} "
                f"scholar={g.get('scholar_sources', 0)}/{g.get('scholar_searches', 0)} dup={g.get('scholar_dup', 0)} "
                f"full={g.get('scholar_fulltext', 0)} fail={g.get('scholar_fail', 0)}\n")
    print(q, arm, k, secs, "s", rep or "NO REPORT", g, flush=True)


def main():
    assert os.environ.get("DEEPDELVE_FORCE_VIRTUAL_DISPLAY") == "1", "set DEEPDELVE_FORCE_VIRTUAL_DISPLAY=1 (headed browser popups)"
    cfg = os.path.abspath(sys.argv[1])
    os.environ.pop("DISPLAY", None)
    os.environ.pop("WAYLAND_DISPLAY", None)
    done = {tuple(line.split("\t")[:3]) for line in LOG.read_text().splitlines()} if LOG.exists() else set()
    jobs = [(q, arm, k) for k in (1, 2) for q in QUERY for arm in (("on", "off") if k == 1 else ("off", "on"))]
    jobs += [(q, arm, 3) for q in ("c2", "pm25") for arm in ("aa1", "aa2")]  # A/A noise floor: both arms OFF, same frozen plan
    try:
        for q, arm, k in jobs:
            if (q, arm, str(k)) not in done:
                one(q, arm, k, cfg)
    finally:
        subprocess.run(["ollama", "stop", MODEL], capture_output=True)


if __name__ == "__main__":
    main()
