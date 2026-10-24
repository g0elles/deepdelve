"""Random sample of (claim, cited source) pairs from the audited reports for the blind second-labeller check. Writes pairs_sample.jsonl (gitignored: holds source text).
Repo root: python eval/audit_runs/pairs_sample.py [n=40] [seed=0]"""
import re, glob, os, sys, json, random
sys.path.insert(0, "src")
from utils.grounding import decompose_claim_segments
n = int(sys.argv[1]) if len(sys.argv) > 1 else 40; seed = int(sys.argv[2]) if len(sys.argv) > 2 else 0
RUNS = ["research_output/how_do_raft_and_paxos_differ_in_leader_e_0ba009_20260929_224216", "research_output/what_are_the_latest_developments_in_the_bc7ad7_20261001_232456",
        "research_output/compare_finland_s_and_estonia_s_approach_37dce8_20261023_181948"] + sorted(
    glob.glob("research_output/*_20261024_*"))
def body(t): return re.sub(r"\s+", " ", t)
def toks(s): return set(re.findall(r"[a-z0-9]{3,}", s.lower()))
def window(claim, txt, w=700):
    ct = toks(claim); best = (-1, 0)
    for i in range(0, max(1, len(txt) - w // 2), 120):
        s = len(ct & toks(txt[i:i + w]))
        if s > best[0]: best = (s, i)
    return txt[best[1]:best[1] + w]
pairs = []
for d in RUNS:
    if not os.path.exists(d + "/final_report.md") or "psilocybin" in d and False: continue
    fm = open(d + "/findings.md").read(); u2f = {m.group(1).rstrip("/"): m.group(2) for m in re.finditer(r"\((https?://[^)\s]+)\) \(saved as (src_\w+\.md)\)", fm)}
    rep = open(d + "/final_report.md").read(); prose, srcs = (re.split(r"\n#{1,3} Sources", rep, maxsplit=1) + [""])[:2]
    nm = {m.group(1): m.group(2).rstrip("/") for l in srcs.splitlines() if (m := re.match(r"\s*(\d+)\.\s.*?\((https?://[^)\s]+)\)", l))}
    for line in prose.splitlines():
        if line.startswith("#"): continue
        for seg in decompose_claim_segments(line):
            cites = re.findall(r"\[(\d+)\]", seg); claim = re.sub(r"\[\d+\]", "", seg).strip("-*|. ").replace("**", "").strip()
            if len(claim) < 25: continue
            for c in cites:
                f = u2f.get(nm.get(c))
                if f: pairs.append(dict(run=os.path.basename(d)[-30:], n=c, claim=claim, src=f, text=body(open(f"{d}/{f}").read())))
    for p in pairs: p.setdefault("window", None)
random.Random(seed).shuffle(pairs)
out = pairs[:n]
for i, p in enumerate(out): p["id"] = i; p["window"] = window(p["claim"], p["text"])
with open("eval/audit_runs/pairs_sample.jsonl", "w") as fh:
    for p in out: fh.write(json.dumps(p) + "\n")
print(len(pairs), "pairs total;", len(out), "sampled")
