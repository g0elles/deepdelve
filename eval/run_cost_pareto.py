"""Step 0 of the 2026-10-23 plan (session_status/CURRENT.md): where do completion-check minutes go?

For every saved run, attempt i fired problem P at timestamp t_i; the time until the next attempt's check, t_{i+1} - t_i, is what P cost to fix (a
Builder/FindingsWriter pass plus the next check). The LAST attempt has no successor, so its cost is unknown and is excluded from minutes (counted
in `final_problem` instead). Observational only: runs span months of code changes, so slice with --since and --evidence-only before concluding.

Usage: python eval/run_cost_pareto.py [--since YYYY-MM-DD] [--evidence-only|--free-form] [--max-min 240]"""
import argparse, collections, datetime, glob, json, os, re, statistics

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(path):
    try:
        j = json.load(open(path))
    except Exception:
        return None
    return j.get("data", j)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-10-01")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--evidence-only", action="store_true")
    g.add_argument("--free-form", action="store_true")
    ap.add_argument("--max-min", type=float, default=240, help="drop runs longer than this (resumed runs have gaps in their timestamps)")
    a = ap.parse_args()
    since = datetime.datetime.fromisoformat(a.since).timestamp()
    paths = glob.glob(f"{ROOT}/research_output/*/_run_state.json") + glob.glob(f"{ROOT}/src/research_output/*/_run_state.json")
    cost, count, first, final = collections.defaultdict(float), collections.Counter(), collections.Counter(), collections.Counter()
    runs, per_run_minutes, per_run_attempts, clean_first = 0, [], [], 0
    disp, disp_n, timed_runs = collections.defaultdict(float), collections.Counter(), 0
    for p in paths:
        d = load(p)
        att = (d or {}).get("completion_check_attempts") or []
        st = d.get("started_at") if d else None
        if not att or not st or st < since:
            continue
        if a.evidence_only and not d.get("evidence_only"):
            continue
        if a.free_form and d.get("evidence_only"):
            continue
        ts = [x["timestamp"] for x in att]
        if (ts[-1] - st) / 60 > a.max_min or any(t2 < t1 for t1, t2 in zip(ts, ts[1:])):
            continue
        runs += 1
        dt = d.get("dispatch_timings") or []
        timed_runs += bool(dt)
        for x in dt:  # 'SubAgent_BuilderFix_attempt3_reviewed' -> 'BuilderFix_reviewed'
            k = re.sub(r"_attempt\d+", "", x["name"]).replace("SubAgent_", "")
            disp[k] += x["seconds"] / 60
            disp_n[k] += 1
        per_run_minutes.append((ts[-1] - st) / 60)
        per_run_attempts.append(len(att))
        probs = [x.get("problem") for x in att]
        if probs[0] is None:
            clean_first += 1
        else:
            first[probs[0]] += 1
        for i, x in enumerate(att):
            pr = x.get("problem")
            if pr is None:
                continue
            count[pr] += 1
            if i + 1 < len(att):
                cost[pr] += (ts[i + 1] - ts[i]) / 60
        if probs[-1] is not None:
            final[probs[-1]] += 1
    if not runs:
        print("no runs matched")
        return
    print(f"runs: {runs}  clean on the first check: {clean_first} ({100 * clean_first // runs}%)")
    print(f"minutes to last check: median {statistics.median(per_run_minutes):.0f}, mean {statistics.mean(per_run_minutes):.0f}, max {max(per_run_minutes):.0f}; "
          f"attempts: median {statistics.median(per_run_attempts)}, max {max(per_run_attempts)}")
    tot = sum(cost.values()) or 1
    print(f"\n{'problem':34s} {'fired':>6s} {'first':>6s} {'ended-on':>9s} {'min lost':>9s} {'share':>6s} {'min/fire':>9s}")
    for pr, m in sorted(cost.items(), key=lambda kv: -kv[1]):
        known = max(count[pr] - final[pr], 1)
        print(f"{pr:34s} {count[pr]:6d} {first[pr]:6d} {final[pr]:9d} {m:9.0f} {100 * m / tot:5.0f}% {m / known:9.1f}")
    for pr in count:
        if pr not in cost:
            print(f"{pr:34s} {count[pr]:6d} {first[pr]:6d} {final[pr]:9d}   (cost unknown: only ever the last check)")

    if disp:
        print(f"\nper-dispatch wall time ({timed_runs} runs recorded dispatch_timings since 2026-10-23):")
        for k, m in sorted(disp.items(), key=lambda kv: -kv[1]):
            print(f"  {k:40s} n={disp_n[k]:3d}  {m:7.1f} min  ({m / disp_n[k]:.1f} min each)")
    else:
        print("\nno run has dispatch_timings yet (added 2026-10-23; only newer runs carry it)")


if __name__ == "__main__":
    main()
