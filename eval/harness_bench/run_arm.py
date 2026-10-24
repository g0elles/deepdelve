"""Run harness-bench cases: build a fault case from a clean run, resume it headless under an arm's settings, collect the outcome.

An arm = the live ~/.deepdelve/config.yaml plus `--ablation key=value` overrides under settings.ablation (see config_template.yaml). Each case runs with
settings.max_run_minutes = --run-min (the hard cap now ends the run gracefully through the final path) and a backstop --cap-min: a case still alive then gets
SIGTERM (SIGKILL after 60 s) and is recorded timed_out. Sequential (16 GB VRAM). Results append to <workspace>/results.jsonl.

Single arm:   python eval/harness_bench/run_arm.py ARM --bases DIR [DIR ...] --faults F [F ...] [--reps 3] [--ablation disable_review_fix=true]
A/B, interleaved (use this for comparisons; drift is then not confounded with the arm):
              python eval/harness_bench/run_ab.py NAME --bases ... --faults ... --reps 3 --b-ablation disable_review_fix=true
GPU-heavy: nothing else may be using Ollama."""
import argparse, json, os, random, subprocess, sys, time

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
from collect import collect  # noqa: E402
from make_case import build_case  # noqa: E402


def arm_config(ws: str, ablation: dict, run_min: float | None = None) -> str:
    cfg = yaml.safe_load(open(os.path.expanduser("~/.deepdelve/config.yaml")))
    st = cfg.setdefault("settings", {})
    assert not st.get("enable_thinking"), "enable_thinking must stay false (user rule): fix the live config first"
    st["workspace"] = {**st.get("workspace", {}), "type": "disk", "dir": ws, "session_isolation": True}
    st["ablation"] = {**(st.get("ablation") or {}), **ablation}
    if run_min:
        st["max_run_minutes"] = run_min
    path = os.path.join(ws, "_arm_config.yaml")
    yaml.safe_dump(cfg, open(path, "w"))
    return path


def parse_ablation(items: list[str]) -> dict:
    out = {}
    for it in items or []:
        k, v = it.split("=", 1)
        out[k] = v.lower() in ("1", "true", "yes")
    return out


def run_job(arm: str, ws: str, cfg: str, base: str, fault: str, rep: int, cap_min: float) -> dict:
    name = f"{os.path.basename(base.rstrip('/'))}__{fault}__r{rep}"
    case = build_case(base, fault, os.path.abspath(ws), name)
    t0 = time.time()
    env = {**os.environ, "DEEPDELVE_FORCE_VIRTUAL_DISPLAY": "1"}
    timed_out = False
    with open(os.path.join(case, "_run.log"), "w") as log:
        proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "src", "app.py"), "--config", cfg, "--auto-approve", "--resume-run", name],
                                cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            proc.wait(timeout=cap_min * 60)
        except subprocess.TimeoutExpired:
            timed_out = True
            proc.terminate()  # SIGTERM first, as with other GPU processes in this project
            try:
                proc.wait(timeout=60)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
    res = {**collect(case), "arm": arm, "wall_min": round((time.time() - t0) / 60, 1), "timed_out": timed_out}
    open(os.path.join(ws, "results.jsonl"), "a").write(json.dumps(res) + "\n")
    print(f"  [{arm}] {name}: detected={res['detected']} clean={res['ended_clean']} checks={res['checks']} one_pass={res['fixed_in_one_pass']} "
          f"wall={res['wall_min']}m budget={res.get('budget_exhausted')} timed_out={timed_out}", flush=True)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("arm"); ap.add_argument("--bases", nargs="+", required=True); ap.add_argument("--faults", nargs="+", required=True)
    ap.add_argument("--reps", type=int, default=3); ap.add_argument("--ablation", nargs="*", default=[]); ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--run-min", type=float, default=40); ap.add_argument("--cap-min", type=float, default=45); ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    ws = os.path.join(HERE, "workspaces", a.arm)
    os.makedirs(ws, exist_ok=True)
    cfg = arm_config(os.path.abspath(ws), parse_ablation(a.ablation), a.run_min)
    jobs = [(b, f, r) for b in a.bases for f in a.faults for r in range(a.reps)]
    random.Random(a.seed).shuffle(jobs)
    print(f"arm {a.arm}: {len(jobs)} cases, ablation {parse_ablation(a.ablation) or 'none'}, run {a.run_min}m / cap {a.cap_min}m", flush=True)
    for b, f, r in jobs:
        if a.dry_run:
            print("  would run", os.path.basename(b.rstrip('/')), f, r)
            continue
        try:
            run_job(a.arm, ws, cfg, b, f, r, a.cap_min)
        except Exception as e:  # one bad case must not end an unattended batch
            print(f"  ERROR {b} {f} r{r}: {type(e).__name__}: {e}", flush=True)


if __name__ == "__main__":
    main()
