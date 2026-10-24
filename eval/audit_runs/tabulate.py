"""Pools eval/audit_runs/labels/*.jsonl: defects per type, severity, stage and per report, with Wilson 95% intervals on the share of reports affected.
Severity 0 rows are positives/corrections and are excluded. Repo root: python eval/audit_runs/tabulate.py"""
import glob, json, math, collections, os

def wilson(k, n, z=1.96):
    if n == 0: return (0, 0)
    p = k / n; d = 1 + z * z / n; c = p + z * z / (2 * n); h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0, (c - h) / d), min(1, (c + h) / d))

rows = [json.loads(l) for f in sorted(glob.glob(os.environ.get("AUDIT_LABELS", "eval/audit_runs/labels") + "/*.jsonl")) if "check_firings" not in f for l in open(f)]
rows = [r for r in rows if r["sev"] > 0]
runs = sorted({r["run"] for r in rows}); N = len(runs)
print(f"reports: {N}; defects: {len(rows)}; severity 3: {sum(r['sev'] == 3 for r in rows)}\n")
print("| type | defects | sev3 | reports affected (of %d) | 95%% interval | stages |" % N); print("|---|---|---|---|---|---|")
for t in sorted({r["type"] for r in rows}):
    rr = [r for r in rows if r["type"] == t]; k = len({r["run"] for r in rr}); lo, hi = wilson(k, N)
    st = collections.Counter(r["stage"] for r in rr)
    print(f"| {t} | {len(rr)} | {sum(r['sev'] == 3 for r in rr)} | {k} | {lo:.0%}-{hi:.0%} | {dict(st)} |")
print("\n| report | defects | sev3 | sev2 | sev1 |\n|---|---|---|---|---|")
for run in runs:
    rr = [r for r in rows if r["run"] == run]
    print(f"| {run} | {len(rr)} | {sum(r['sev'] == 3 for r in rr)} | {sum(r['sev'] == 2 for r in rr)} | {sum(r['sev'] == 1 for r in rr)} |")
k3 = len({r["run"] for r in rows if r["sev"] == 3}); lo, hi = wilson(k3, N)
print(f"\nreports with at least one severity-3 defect: {k3}/{N} ({lo:.0%}-{hi:.0%})")
