"""AUROC + operating points of each scores_all_*.json against pairs.jsonl labels, split by negative kind."""
import json, glob, sys
rows=[json.loads(l) for l in open("eval/claim_support/pairs_all.jsonl")]
def auroc(pos,neg):
    if not pos or not neg: return float('nan')
    return sum((p>n)+0.5*(p==n) for p in pos for n in neg)/(len(pos)*len(neg))
for f in sorted(glob.glob("eval/claim_support/scores_all_*.json")):
    s=json.load(open(f)); j=f.split("scores_all_")[1][:-5]
    if len(s)!=len(rows): print(j,"incomplete"); continue
    pos=[x for x,r in zip(s,rows) if r["label"]==1]
    for kind in ("natural","cross"):
        neg=[x for x,r in zip(s,rows) if r["label"]==0 and r["kind"]==kind]
        print(f"{j:10s} vs {kind:8s} negatives n={len(neg):2d}: AUROC={auroc(pos,neg):.2f}")
    allneg=[x for x,r in zip(s,rows) if r["label"]==0]
    print(f"{j:10s} positives n={len(pos)} score median={sorted(pos)[len(pos)//2]:.2f} min={min(pos):.2f}; negatives median={sorted(allneg)[len(allneg)//2]:.2f} max={max(allneg):.2f}")
    for th in (0.1,0.25,0.5,0.75):
        print(f"   flag if score<{th}: positives flagged {sum(x<th for x in pos)}/{len(pos)}   natural-bad caught {sum(x<th for x,r in zip(s,rows) if r['label']==0 and r['kind']=='natural')}/{sum(1 for r in rows if r['label']==0 and r['kind']=='natural')}   cross caught {sum(x<th for x,r in zip(s,rows) if r['kind']=='cross')}/{sum(1 for r in rows if r['kind']=='cross')}")
