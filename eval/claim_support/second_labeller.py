"""Machine second labeller for the claim-support pairs (ROADMAP: single labeller so far). Blind: it sees only the claim and the source text, never the
first labeller's label. Windows of WIN chars (max over windows, as MiniCheck does for long documents). A different model family from the first labeller
(Claude) but still a model, so agreement here is a sanity check, not the independent HUMAN labeller the ROADMAP asks for.
Usage (from src/): python ../eval/claim_support/second_labeller.py [--limit N]"""
import json, sys, os, argparse
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) + "/src")
from pipeline.llm import call_json

SCHEMA = {"type": "object", "required": ["supported"], "properties": {"supported": {"type": "boolean"}}}
WIN, STEP = 9000, 8000
HERE = os.path.dirname(os.path.abspath(__file__))


def windows(t):
    return [t[i:i + WIN] for i in range(0, max(len(t), 1), STEP)]


def label(model, claim, text):
    for w in windows(text):
        p = (f"Source text:\n{w}\n\nClaim: {claim}\n\nIs the claim fully supported by the source text above? Answer true only if the source text itself states "
             f"the facts the claim asserts (same numbers, names, dates and entities). A claim that is true in general but not stated in this text is NOT supported.")
        if call_json(model, p, SCHEMA, think="low", timeout=300)["supported"]:
            return True
    return False


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--model", default="deepdelve-gpt-oss:latest"); ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    rows = [r for r in map(json.loads, open(f"{HERE}/pairs_all.jsonl")) if r["kind"] == "natural"]  # cross pairs are negatives by construction
    rows = rows[:a.limit] if a.limit else rows
    out = open(f"{HERE}/second_labels.jsonl", "w")
    for r in rows:
        try:
            s = label(a.model, r["claim"], r["text"])
        except Exception as e:
            s = None
            print("ERR", r["run"], r["n"], str(e)[:80], flush=True)
        out.write(json.dumps({"run": r["run"], "n": r["n"], "first": r["label"], "second": s}) + "\n"); out.flush()
    L = [json.loads(l) for l in open(f"{HERE}/second_labels.jsonl")]
    L = [x for x in L if x["second"] is not None]
    agree = sum(int(x["first"]) == int(x["second"]) for x in L)
    tp = sum(x["first"] == 1 and x["second"] for x in L); tn = sum(x["first"] == 0 and not x["second"] for x in L)
    fp = sum(x["first"] == 0 and x["second"] for x in L); fn = sum(x["first"] == 1 and not x["second"] for x in L)
    n = len(L); po = agree / n; p1 = (tp + fn) / n; p2 = (tp + fp) / n; pe = p1 * p2 + (1 - p1) * (1 - p2)
    print(json.dumps({"n": n, "agree": agree, "tp": tp, "tn": tn, "fp": fp, "fn": fn, "kappa": round((po - pe) / (1 - pe), 3) if pe < 1 else None}))


if __name__ == "__main__":
    main()
