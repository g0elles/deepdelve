"""Audit worksheet for one run: every cited claim with the best-matching passage from each cited source, plus saved sources the report never cites.
Reuses the live claim decomposition (utils.grounding.decompose_claim_segments). Repo root: python eval/audit_runs/claim_sheet.py <run_dir> > sheet.md"""
import re, glob, os, sys
sys.path.insert(0, "src")
from utils.grounding import decompose_claim_segments

def body(t): return re.sub(r"\s+", " ", t)
def toks(s): return set(re.findall(r"[a-z0-9]{3,}|\d[\d.,%]*", s.lower()))
def window(claim, txt, w=650):
    ct = toks(claim); best = (-1, 0)
    for i in range(0, max(1, len(txt) - w // 2), 120):
        s = len(ct & toks(txt[i:i + w]))
        if s > best[0]: best = (s, i)
    return txt[best[1]:best[1] + w], best[0], len(ct)

d = sys.argv[1].rstrip("/")
fm = open(d + "/findings.md").read() if os.path.exists(d + "/findings.md") else ""
u2f = {}
for m in re.finditer(r"\((https?://[^)\s]+)\) \(saved as (src_\w+\.md)\)", fm):
    u2f[m.group(1).rstrip("/")] = m.group(2)
rep = open(d + "/final_report.md").read(); prose, srcs = (re.split(r"\n#{1,3} Sources", rep, maxsplit=1) + [""])[:2]
nm = {}
for l in srcs.splitlines():
    m = re.match(r"\s*(\d+)\.\s.*?\((https?://[^)\s]+)\)", l)
    if m: nm[m.group(1)] = m.group(2).rstrip("/")
print(f"# Audit sheet: {os.path.basename(d)}\nsaved src files: {len(glob.glob(d + '/src_*.md'))}, report sources: {len(nm)}, mapped to a saved file: {sum(u in u2f for u in nm.values())}\n")
print("## Report\n" + rep + "\n\n## Claims\n")
k = 0; cited_files = set()
for line in prose.splitlines():
    if line.startswith("#"): continue
    for seg in decompose_claim_segments(line):
        cites = re.findall(r"\[(\d+)\]", seg)
        claim = re.sub(r"\[\d+\]", "", seg).strip("-*|. ").replace("**", "").strip()
        if len(claim) < 25: continue
        k += 1
        print(f"### C{k}. {claim}\ncites: {cites or 'NONE'}")
        for n in cites:
            f = u2f.get(nm.get(n))
            if not f: print(f"- [{n}] {nm.get(n)}: NO SAVED FILE"); continue
            cited_files.add(f); w, s, t = window(claim, body(open(f"{d}/{f}").read()))
            print(f"- [{n}] {f} overlap {s}/{t}: {w}")
        print()
print("## Saved sources never cited\n")
for u, f in u2f.items():
    if f not in cited_files: print(f"- {f} {u}  | {body(open(f'{d}/{f}').read())[:160]}")
