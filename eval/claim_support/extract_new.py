"""Extracts unlabelled (claim, cited source) pairs from extra [N] reports into new_pairs.jsonl for hand labelling.
Adds a 'window' = the source passage with most claim-token overlap, so labelling reads ~600 chars not a whole page. Repo root."""
import re, glob, json, sys
sys.path.insert(0,'src')
from utils.grounding import decompose_claim_segments
def body(t): return re.sub(r"\s+"," ",t)
def toks(s): return set(re.findall(r"[a-z0-9]{4,}", s.lower()))
def window(claim, txt, w=700):
    ct=toks(claim); best=(-1,0)
    for i in range(0,max(1,len(txt)-w//2),150):
        s=len(ct&toks(txt[i:i+w]))
        if s>best[0]: best=(s,i)
    return txt[best[1]:best[1]+w]
rows=[]
for tag in sys.argv[1:]:
    d=glob.glob(f"research_output/*_{tag}")[0]
    fm=open(d+"/findings.md").read(); u2t={}
    for m in re.finditer(r"\((https?://[^)\s]+)\) \(saved as (src_\w+\.md)\)",fm): u2t[m.group(1).rstrip('/')]=(m.group(2),body(open(f"{d}/{m.group(2)}").read()))
    rep=open(d+"/final_report.md").read(); prose,_,srcs=rep.partition("\n## Sources")
    nm={}
    for l in srcs.splitlines():
        m=re.match(r"\s*(\d+)\.\s.*?\((https?://[^)\s]+)\)",l)
        if m: nm[m.group(1)]=m.group(2).rstrip('/')
    for line in prose.splitlines():
        if line.startswith("#"): continue
        for seg in decompose_claim_segments(line):  # same unit the live checks use: text up to and including its own citation(s)
            cites=re.findall(r"\[(\d+)\]",seg)
            claim=re.sub(r"\[\d+\]","",seg).strip("-*|. ").replace("**","").replace("\u202f"," ").strip()
            if not cites or len(claim)<25: continue
            for n in cites:
                if nm.get(n) in u2t:
                    f,txt=u2t[nm[n]]; rows.append(dict(run=tag,n=n,group=len(cites),claim=claim,src=f,label=None,kind="natural",text=txt,window=window(claim,txt)))
with open("eval/claim_support/new_pairs.jsonl","w") as fh:
    for r in rows: fh.write(json.dumps(r)+"\n")
print(len(rows))
