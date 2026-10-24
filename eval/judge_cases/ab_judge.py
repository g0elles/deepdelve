"""A/B a gap-judge prompt against the hand-labelled questions (eval/judge_cases/labels.json). Required before ANY change to pipeline/run.py::facet_unanswered's
prompt (2026-10-23: an un-A/B'd prompt sentence turned a correctly answered facet into false gaps).

Variants are python callables (model, query, facet, quotes) -> list of unanswered question indexes; 'current' is the shipped judge. Add a variant by adding to VARIANTS.
Needs the evidence dirs on disk (they hold fetched web text, so they are gitignored) and Ollama running. Deterministic at temperature 0, but run twice if in doubt.

Usage (from src/): python ../eval/judge_cases/ab_judge.py [--model deepdelve-gpt-oss:latest]"""
import argparse, collections, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))


def score(labels: list, flagged: dict) -> dict:
    """labels: [{dir, facet, q, label}], flagged: {(dir, facet): set of question indexes the judge called unanswered}. 'borderline' is reported, not scored."""
    c = collections.Counter()
    for L in labels:
        says_gap = L["q"] in flagged.get((L["dir"], L["facet"]), set())
        if L["label"] == "borderline":
            c["borderline_flagged" if says_gap else "borderline_answered"] += 1
        elif L["label"] == "gap":
            c["gap_caught" if says_gap else "gap_missed"] += 1
        else:
            c["false_gap" if says_gap else "answered_ok"] += 1
    return dict(c)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="deepdelve-gpt-oss:latest")
    a = ap.parse_args()
    sys.path.insert(0, os.path.join(ROOT, "src"))
    import pipeline.run as R
    VARIANTS = {"current": R.facet_unanswered}
    labels = json.load(open(os.path.join(HERE, "labels.json")))["labels"]
    groups = sorted({(L["dir"], L["facet"]) for L in labels})
    for name, fn in VARIANTS.items():
        flagged = {}
        for d, fid in groups:
            ev = json.load(open(os.path.join(ROOT, d, "evidence.json")))
            facet = next(f for f in ev["facets"] if f["id"] == fid)
            quotes = [q for qs in ev["evidence"][fid].values() for q in qs]
            query = json.load(open(os.path.join(ROOT, d, "metrics.json")))["query"] if os.path.exists(os.path.join(ROOT, d, "metrics.json")) else ""
            flagged[(d, fid)] = set(fn(a.model, query, facet, quotes))
        print(name, json.dumps(score(labels, flagged), sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
