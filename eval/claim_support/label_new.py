"""Hand labels (2026-10-02, one labeller = Claude, each checked by regex grep of the cited source text) for new_pairs.jsonl; writes
pairs_all.jsonl = pairs.jsonl + the labelled new pairs. Skipped as ambiguous: 0 2 13 21-25 47 48 (generalising claims / source only partly states them).
label 0 here = the specific claim (figure, date, name, mechanism) is absent from the cited source, even where it is true elsewhere."""
import json
ONE = {1,3,4,5,6,7,8,9,10,11,12,15,17,18,19,20,26,28,29,31,33,36,37,38,40,41,43,44,45,46,49,50,52,55}
ZERO = {14,16,27,30,32,34,35,39,42,51,53,54,56,57}
new = [json.loads(l) for l in open("eval/claim_support/new_pairs.jsonl")]
out = [json.loads(l) for l in open("eval/claim_support/pairs.jsonl")]
for i, r in enumerate(new):
    if i in ONE | ZERO:
        r["label"] = 1 if i in ONE else 0; r.pop("window", None); r["run"] = "n" + r["run"]; out.append(r)
with open("eval/claim_support/pairs_all.jsonl", "w") as fh:
    for r in out: fh.write(json.dumps(r) + "\n")
print(len(out), sum(r["label"] for r in out), "positives")
