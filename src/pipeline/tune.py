"""Tune the CPU relevance scorer (BM25 + cross-encoder hybrid) on eval/pipeline_relevance.jsonl with
differential evolution (scipy metaheuristic). Train/test split is BY QUERY so tuned params can't
leak. Metric: mean recall@K of positives per source + MRR. Usage: python -m pipeline.tune"""
import json
import zlib
from collections import defaultdict

import numpy as np
from scipy.optimize import differential_evolution

from pipeline.run import bm25_scores

K, POOL = 4, 30
_ces, _cache = {}, {}


def clusters(queries: list[str], thr: float = 0.5) -> dict[str, str]:
    """Union near-duplicate queries (token Jaccard >= thr) so they never straddle train/test."""
    from pipeline.run import toks
    sets = [set(toks(q)) for q in queries]
    parent = list(range(len(queries)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i in range(len(queries)):
        for j in range(i):
            if len(sets[i] & sets[j]) / max(len(sets[i] | sets[j]), 1) >= thr:
                parent[find(i)] = find(j)
    return {q: queries[find(i)] for i, q in enumerate(queries)}


def load(path="eval/pipeline_relevance.jsonl"):
    g = defaultdict(list)
    for l in open(path):
        r = json.loads(l)
        g[(r["run"], r["source"])].append(r)
    return [(v[0]["query"], [r["text"] for r in v], [r["label"] for r in v]) for v in g.values()]


def ce_score(query, text, model="cross-encoder/ms-marco-MiniLM-L-6-v2"):
    if model not in _ces:
        from sentence_transformers import CrossEncoder
        _ces[model] = CrossEncoder(model, device="cpu")
    key = (model, query, text)
    if key not in _cache:
        _cache[key] = float(_ces[model].predict([(query[:300], text[:1200])], show_progress_bar=False)[0])
    return _cache[key]


def metrics(order, labels):
    pos = {i for i, l in enumerate(labels) if l}
    top = order[:K]
    rec = len(pos & set(top)) / min(len(pos), K)
    rr = next((1 / (r + 1) for r, i in enumerate(order) if i in pos), 0.0)
    return rec, rr


def z(x):
    x = np.asarray(x, float)
    return (x - x.mean()) / (x.std() or 1)


def evaluate(groups, k1, b, w):
    """w = weight of BM25 in the hybrid; w=1 -> BM25 only (no CE calls)."""
    recs, rrs = [], []
    for q, texts, labels in groups:
        s = np.array(bm25_scores(texts, q, k1, b))
        order = list(np.argsort(-s))
        if w < 1:
            pool = order[:POOL]
            ce = z([ce_score(q, texts[i]) for i in pool])
            hy = w * z(s[pool]) + (1 - w) * ce
            order = [pool[i] for i in np.argsort(-hy)] + order[POOL:]
        r, rr = metrics(order, labels)
        recs.append(r)
        rrs.append(rr)
    return float(np.mean(recs)), float(np.mean(rrs))


if __name__ == "__main__":
    data = [g for g in load() if any(g[2])]
    queries = sorted({g[0] for g in data})
    cl = clusters(queries)
    test_q = {q for q in queries if zlib.crc32(cl[q].encode()) % 3 == 0}
    train = [g for g in data if g[0] not in test_q]
    test = [g for g in data if g[0] in test_q]
    print(f"groups train={len(train)} test={len(test)} queries={len(queries)} (test queries={len(test_q)})")
    for name, args in [("BM25 default", (1.5, 0.75, 1.0)), ("CE only", (1.5, 0.75, 0.0)), ("hybrid 0.5", (1.5, 0.75, 0.5))]:
        print(f"{name:14s} train recall@{K}/MRR={evaluate(train, *args)}  test={evaluate(test, *args)}")
    res = differential_evolution(lambda p: -evaluate(train, *p)[0], [(0.5, 3.0), (0.0, 1.0), (0.0, 1.0)],
                                 maxiter=8, popsize=6, seed=0, tol=0, polish=False)
    k1, b, w = res.x
    print(f"DE best k1={k1:.2f} b={b:.2f} w_bm25={w:.2f}  train={evaluate(train, k1, b, w)}  test={evaluate(test, k1, b, w)}")
