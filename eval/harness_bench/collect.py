"""Collect one case's outcome after a resume run. Outcome metrics: did the expected problem fire, was it cleared, how many checks it took, collateral damage
(words and citations lost vs the clean report). Process metrics: minutes, and minutes per dispatch role from run_state's dispatch_timings.

Usage: python eval/harness_bench/collect.py CASE_DIR [CASE_DIR ...]   (prints one JSON object per case)"""
import collections, json, os, re, sys


def collect(case_dir: str) -> dict:
    meta = json.load(open(os.path.join(case_dir, "_case.json")))
    j = json.load(open(os.path.join(case_dir, "_run_state.json")))
    d = j.get("data", j)
    att = d.get("completion_check_attempts") or []
    chain = [a.get("problem") or "clean" for a in att]
    ts = [a["timestamp"] for a in att]
    rep = open(os.path.join(case_dir, "final_report.md"), encoding="utf-8", errors="ignore").read() if os.path.exists(os.path.join(case_dir, "final_report.md")) else ""
    exp = set(meta["expected"])
    fired = next((i for i, p in enumerate(chain) if p in exp), None)
    roles = collections.defaultdict(float)
    for t in d.get("dispatch_timings") or []:
        roles[re.sub(r"_attempt\d+", "", t["name"]).replace("SubAgent_", "")] += t["seconds"] / 60
    cleared = bool(chain) and chain[-1] == "clean"
    return {
        "case": os.path.basename(case_dir), "fault": meta["fault"], "expected": meta["expected"], "chain": chain,
        "detected": fired is not None, "ended_clean": cleared, "checks": len(chain),
        "fixed_in_one_pass": fired is not None and len(chain) > fired + 1 and chain[fired + 1] == "clean",
        "minutes": round((ts[-1] - d["started_at"]) / 60, 1) if ts and d.get("started_at") else None,
        "dispatch_minutes": {k: round(v, 1) for k, v in sorted(roles.items())},
        "words_lost": meta["clean_words"] - len(rep.split()), "cites_lost": meta["clean_cites"] - len(re.findall(r"\[\d+", rep)),
        "other_problems": sorted({p for p in chain if p not in exp and p != "clean"}), "budget_exhausted": d.get("budget_exhausted"),
    }


if __name__ == "__main__":
    for c in sys.argv[1:]:
        print(json.dumps(collect(c)))
