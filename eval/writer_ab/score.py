"""Stage 1 scoring (protocol: sections 4 and 5). Usage: python eval/writer_ab/score.py   (needs out/blind_map.json and out/labels_seed0.json)
labels_seed0.json: {"R001": {"units": 12, "defect_units": 2, "sev3": 0, "defects": ["...", ...]}, ...}, written by the labeller before the map is read.
Prints per (model, arm): labelled units, defect rate with Wilson 95% interval, severity 3 per report, deterministic invalid ids and absent figures over
ALL seeds, and the stage 1 verdict per the pre-registered rules."""
import collections, json, math, os
HERE = os.path.dirname(os.path.abspath(__file__))
THRESHOLD = 0.154        # lowest rate the shipped writer reached in the diagnostic (gpt-oss default)


def wilson(k, n, z=1.96):
    if not n:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - h) / d, (c + h) / d)


def main():
    amap, labels = {}, {}
    for suffix in ("", "_b", "_s1", "_s2"):      # "_b": post-hoc arms A3b/A4b; "_s1", "_s2": later seeds of the cells that needed them
        if os.path.exists(f"{HERE}/out/blind_map{suffix}.json"):
            amap.update({i["id"]: i for i in json.load(open(f"{HERE}/out/blind_map{suffix}.json"))})
            labels.update(json.load(open(f"{HERE}/out/labels_seed0{suffix}.json")))
    cell = collections.defaultdict(lambda: {"units": 0, "defects": 0, "sev3": 0, "reports": 0})
    for rid, lab in labels.items():
        it = amap[rid]
        c = cell[(it["model"], it["arm"])]
        c["units"] += lab["units"]; c["defects"] += lab["defect_units"]; c["sev3"] += lab.get("sev3", 0); c["reports"] += 1
    det = collections.defaultdict(lambda: {"reports": 0, "invalid_ids": 0, "reports_with_absent_figure": 0, "fallback_share": []})
    for mdir in os.listdir(f"{HERE}/out"):
        mp = f"{HERE}/out/{mdir}/metrics.jsonl"
        if os.path.exists(mp):
            for line in open(mp):
                r = json.loads(line)
                d = det[(mdir, r["arm"])]            # mdir is the model tag the blind map also uses
                d["reports"] += 1; d["invalid_ids"] += r.get("invalid_ids", 0); d["reports_with_absent_figure"] += bool(r.get("figures_absent"))
                if r.get("units"):
                    d["fallback_share"].append(r.get("extractive_units", 0) / r["units"])
    print(f"{'model':16s} {'arm':4s} {'units':>6s} {'defect%':>8s} {'95% interval':>14s} {'sev3/rep':>9s} {'invalid ids':>11s} {'rep w/ absent fig':>18s} {'fallback':>9s}")
    for (m, a), c in sorted(cell.items()):
        lo, hi = wilson(c["defects"], c["units"])
        d = det.get((m, a), {"invalid_ids": "-", "reports_with_absent_figure": "-", "fallback_share": []})
        fb = f"{100 * sum(d['fallback_share']) / len(d['fallback_share']):.0f}%" if d["fallback_share"] else "-"
        print(f"{m:16s} {a:4s} {c['units']:6d} {100 * c['defects'] / max(1, c['units']):7.1f}% {lo * 100:6.1f}-{hi * 100:5.1f}% {c['sev3'] / c['reports']:9.2f} {d['invalid_ids']!s:>11s} {d['reports_with_absent_figure']!s:>18s} {fb:>9s}")
    print(f"\nPass rule: rate below {THRESHOLD:.1%} and below the same model's A0, zero invalid ids and zero absent figures, severity 3 per report not above A0, coverage no drop.")
    for m in sorted({m for m, _ in cell}):
        a0 = cell.get((m, "A0"))
        for a in ("A1", "A2", "A3", "A4"):
            c = cell.get((m, a))
            if not c or not a0:
                continue
            rate, rate0 = c["defects"] / c["units"], a0["defects"] / a0["units"]
            lo, hi = wilson(c["defects"], c["units"])
            print(f"{m} {a}: rate {rate:.1%} (A0 {rate0:.1%}); upper bound {hi:.1%}; rule 1 {'met' if rate < THRESHOLD and rate < rate0 else 'not met'}; clearly below threshold: {hi < THRESHOLD}")


if __name__ == "__main__":
    main()
