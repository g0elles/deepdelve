"""Builds eval/claim_support/pairs.jsonl: (claim, source text, label) from the two 2026-10-01 A/B reports.
label 1 = the cited source states the claim (hand-verified 2026-10-01); 0 = it does not.
kind: 'natural' (hand-labelled pair as the report cited it), 'cross' (same claim vs another source of the run that lacks every digit
string of the claim: deterministic negative). Run from the repo root."""
import re, glob, json, random
random.seed(7)
BAD = {("182935","began enforcing","1"),("182935","embedded systems","4"),("182935","Prohibited uses","7"),("182935","Other infringements","7"),
       ("182935","European Data Protection","12"),("182935","SMEs","14"),("182935","Large enterprises","14"),("182935","General","14")}
SKIP = {("182935","standalone systems","2")}   # source only says "phased application across 2025, 2026, 2027": ambiguous, excluded
def body(t): return re.sub(r"\s+"," ",t)
rows=[]
for tag in ("182935","185243"):
    d=glob.glob(f"research_output/*_{tag}")[0]
    fm=open(d+"/findings.md").read(); u2t={}
    for m in re.finditer(r"\((https?://[^)\s]+)\) \(saved as (src_\w+\.md)\)",fm): u2t[m.group(1).rstrip('/')]=(m.group(2),body(open(f"{d}/{m.group(2)}").read()))
    rep=open(d+"/final_report.md").read(); prose,_,srcs=rep.partition("\n## Sources")
    nm={}
    for l in srcs.splitlines():
        m=re.match(r"\s*(\d+)\.\s.*?\((https?://[^)\s]+)\)",l)
        if m: nm[m.group(1)]=m.group(2).rstrip('/')
    for line in prose.splitlines():
        cites=re.findall(r"\[(\d+)\]",line)
        if not cites or line.startswith("#"): continue
        claim=re.sub(r"\[\d+\]","",line).strip("-* ").replace("**","").replace(" "," ").replace("‑","-").strip()
        nums={re.sub(r"\D","",x) for x in re.findall(r"\d[\d,.  ]*\d|\d",claim)}-{""}
        for n in cites:
            if nm.get(n) not in u2t: continue
            f,txt=u2t[nm[n]]
            if any(k[0]==tag and k[1] in line and k[2]==n for k in SKIP): continue
            lab=0 if any(k[0]==tag and k[1] in line and k[2]==n for k in BAD) else 1
            rows.append(dict(run=tag,n=n,claim=claim,src=f,label=lab,kind="natural",text=txt))
        # cross negatives: other sources lacking all long digit strings of the claim
        digs={x for x in nums if len(x)>=2}
        if not digs: continue
        others=[(f,txt) for f,txt in u2t.values() if all(x not in re.sub(r"\D","",txt) and x not in txt.replace(",","") for x in digs)]
        for f,txt in random.sample(others,min(2,len(others))):
            rows.append(dict(run=tag,n="x",claim=claim,src=f,label=0,kind="cross",text=txt))
with open("eval/claim_support/pairs.jsonl","w") as fh:
    for r in rows: fh.write(json.dumps(r)+"\n")
from collections import Counter
print(Counter((r["kind"],r["label"]) for r in rows))
