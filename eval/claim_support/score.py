"""Scores eval/claim_support/pairs_all.jsonl with candidate claim-support judges; writes scores_<judge>.json. Usage: score.py minicheck|nli"""
import sys, json, os, re, time
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
sys.path.insert(0, "src")
judge = sys.argv[1]
def norm(c):
    """claim cleanup: drop 'Row label:' prefix, spaced percent/thousands/punctuation (writer emits narrow no-break spaces)"""
    c = re.sub(r"^[^:.]{3,80}\(?[^:.]{0,30}\)?:\s+(?=[A-Z0-9])", "", c)
    c = re.sub(r"(?<=\d)[\s\u202f\u00a0](?=\d{3}\b)", "", c)
    c = re.sub(r"(?<=\d)\s+%", "%", c)
    return re.sub(r"\s+([.,;:])", r"\1", c).strip()
rows = [json.loads(l) for l in open("eval/claim_support/pairs_all.jsonl")]
t0 = time.time()
if judge in ("minicheck", "minicheck_norm"):
    if judge == "minicheck_norm":
        for r in rows: r["claim"] = norm(r["claim"])
    from minicheck.minicheck import MiniCheck
    mc = MiniCheck(model_name="deberta-v3-large")
    out = []
    for i in range(0, len(rows), 8):
        b = rows[i:i+8]
        _, p, _, _ = mc.score(docs=[r["text"] for r in b], claims=[r["claim"] for r in b])
        out += [float(x) for x in p]; print(i, round(time.time()-t0), flush=True)
elif judge == "nli":
    import numpy as np
    from utils import grounding as G
    m = G._get_nli_model(); out = []
    for r in rows:
        terms = {t for t in G.extract_salient_terms(r["claim"]) if not re.fullmatch(r"(?:19|20)\d\d", t)}
        w = G._select_relevant_window(terms, r["text"]) if terms else None
        if w is None: out.append(0.0); continue   # no window: current code skips the pair; counted as 'not supported' here
        s = m.predict([(w, r["claim"])])[0]; e = np.exp(s - s.max()); out.append(float((e / e.sum())[1]))
json.dump(out, open(f"eval/claim_support/scores_all_{judge}.json", "w"))
print("done", round(time.time()-t0))
