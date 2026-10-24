"""Interleaved A/B of two arms over the same fault cases: for each job (base, fault, rep) in a seeded order, run BOTH arms back to back in a random
order, so slow drift (server state, thermal) is not confounded with the arm. Arm A = current behaviour; arm B = A plus the --b-ablation overrides.
Prints a summary table at the end and after every job. Usage: see run_arm.py."""
import argparse, json, os, random, statistics, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from run_arm import arm_config, parse_ablation, run_job  # noqa: E402


def summarize(rows: list) -> str:
    out = []
    for arm in sorted({r["arm"] for r in rows}):
        rs = [r for r in rows if r["arm"] == arm]
        mins = [r["wall_min"] for r in rs]
        out.append(f"{arm}: n={len(rs)} clean={sum(r['ended_clean'] for r in rs)} one_pass={sum(r['fixed_in_one_pass'] for r in rs)} "
                   f"budget/timeout={sum(bool(r.get('budget_exhausted')) or r['timed_out'] for r in rs)} median_min={statistics.median(mins):.1f} "
                   f"words_lost_med={statistics.median(r['words_lost'] for r in rs)} cites_lost_med={statistics.median(r['cites_lost'] for r in rs)}")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name"); ap.add_argument("--bases", nargs="+", required=True); ap.add_argument("--faults", nargs="+", required=True)
    ap.add_argument("--reps", type=int, default=3); ap.add_argument("--a-ablation", nargs="*", default=[]); ap.add_argument("--b-ablation", nargs="*", required=True)
    ap.add_argument("--seed", type=int, default=0); ap.add_argument("--run-min", type=float, default=40); ap.add_argument("--cap-min", type=float, default=45)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    arms = {}
    for label, abl in (("A", parse_ablation(a.a_ablation)), ("B", parse_ablation(a.b_ablation))):
        ws = os.path.join(HERE, "workspaces", f"{a.name}_{label}")
        os.makedirs(ws, exist_ok=True)
        arms[label] = (ws, arm_config(os.path.abspath(ws), abl, a.run_min))
    rng = random.Random(a.seed)
    jobs = [(b, f, r) for b in a.bases for f in a.faults for r in range(a.reps)]
    rng.shuffle(jobs)
    print(f"A/B {a.name}: {len(jobs)} jobs x 2 arms; B = {parse_ablation(a.b_ablation)}; run {a.run_min}m / cap {a.cap_min}m", flush=True)
    rows = []
    for b, f, r in jobs:
        order = ["A", "B"]
        rng.shuffle(order)
        for label in order:
            if a.dry_run:
                print(f"  would run [{label}] {os.path.basename(b.rstrip('/'))} {f} r{r}")
                continue
            ws, cfg = arms[label]
            try:
                rows.append(run_job(f"{a.name}_{label}", ws, cfg, b, f, r, a.cap_min))
            except Exception as e:  # one bad case must not end an unattended batch
                print(f"  ERROR [{label}] {b} {f} r{r}: {type(e).__name__}: {e}", flush=True)
        if rows:
            print(summarize(rows), flush=True)
    if rows:
        json.dump(rows, open(os.path.join(HERE, "workspaces", f"{a.name}_summary.json"), "w"))


if __name__ == "__main__":
    main()
