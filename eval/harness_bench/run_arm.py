"""Run one experimental ARM of the harness bench: for each (base run, fault, repetition) build a fault case, resume it headless under the arm's settings, collect.

An arm = the live ~/.deepdelve/config.yaml plus `--ablation key=value` overrides under settings.ablation (see config_template.yaml). Cases run sequentially
(16 GB VRAM) in a seeded random order so drift (thermal, server state) is not confounded with the arm. Results append to <workspace>/results.jsonl.
GPU-heavy: check `ollama ps` / nothing else running first.

Usage: python eval/harness_bench/run_arm.py ARM --bases DIR [DIR ...] --faults F [F ...] [--reps 3] [--ablation disable_review_fix=true] [--seed 0] [--dry-run]"""
import argparse, json, os, random, subprocess, sys, time

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
from collect import collect  # noqa: E402
from make_case import build_case  # noqa: E402


def arm_config(ws: str, ablation: dict) -> str:
    cfg = yaml.safe_load(open(os.path.expanduser("~/.deepdelve/config.yaml")))
    st = cfg.setdefault("settings", {})
    assert not st.get("enable_thinking"), "enable_thinking must stay false (user rule): fix the live config first"
    st["workspace"] = {**st.get("workspace", {}), "type": "disk", "dir": ws, "session_isolation": True}
    st["ablation"] = {**(st.get("ablation") or {}), **ablation}
    path = os.path.join(ws, "_arm_config.yaml")
    yaml.safe_dump(cfg, open(path, "w"))
    return path


def parse_ablation(items: list[str]) -> dict:
    out = {}
    for it in items or []:
        k, v = it.split("=", 1)
        out[k] = v.lower() in ("1", "true", "yes")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("arm"); ap.add_argument("--bases", nargs="+", required=True); ap.add_argument("--faults", nargs="+", required=True)
    ap.add_argument("--reps", type=int, default=3); ap.add_argument("--ablation", nargs="*", default=[]); ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    ws = os.path.join(HERE, "workspaces", a.arm)
    os.makedirs(ws, exist_ok=True)
    cfg = arm_config(os.path.abspath(ws), parse_ablation(a.ablation))
    jobs = [(b, f, r) for b in a.bases for f in a.faults for r in range(a.reps)]
    random.Random(a.seed).shuffle(jobs)
    print(f"arm {a.arm}: {len(jobs)} cases, ablation {parse_ablation(a.ablation) or 'none'}", flush=True)
    for b, f, r in jobs:
        name = f"{os.path.basename(b.rstrip('/'))}__{f}__r{r}"
        if a.dry_run:
            print("  would run", name)
            continue
        case = build_case(b, f, os.path.abspath(ws), name)
        t0 = time.time()
        env = {**os.environ, "DEEPDELVE_FORCE_VIRTUAL_DISPLAY": "1"}
        with open(os.path.join(case, "_run.log"), "w") as log:
            subprocess.run([sys.executable, os.path.join(ROOT, "src", "app.py"), "--config", cfg, "--auto-approve", "--resume-run", name],
                           cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        res = {**collect(case), "arm": a.arm, "wall_min": round((time.time() - t0) / 60, 1)}
        open(os.path.join(ws, "results.jsonl"), "a").write(json.dumps(res) + "\n")
        print(f"  {name}: detected={res['detected']} clean={res['ended_clean']} checks={res['checks']} one_pass={res['fixed_in_one_pass']} wall={res['wall_min']}m", flush=True)


if __name__ == "__main__":
    main()
