"""A/B the relevance judge (pipeline/run.py::judge) with and without the request in its prompt. Required before shipping the scope rule
(design session_status/egm/answer_quality/answer_quality_design.md rev 2, Phase 3; bar: scoped keeps at most half the old prompt's `out`
candidates, recall at most 0.05 lower).

Two sets:
  recall  eval/pipeline_relevance_es.json: 168 hand-labelled Spanish candidates (label 1 = relevant), the sheet behind the recorded
          precision 0.67 / recall 0.68. Query ids map to the exact query text in eval/pipeline_queries.yaml.
  scope   eval/judge_scope/candidates.jsonl (eval/judge_scope/dump_candidates.py), labels in|out|general.
Variants: current = judge without the request (byte-identical to the shipped-before prompt), scoped = judge with it.
Needs Ollama running. Deterministic at temperature 0, run twice if in doubt.

Usage (repo root): python eval/judge_cases/ab_relevance.py --model ornith-1.5:9b"""
import argparse, collections, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))


def groups(rows: list[dict], key) -> dict:
    g = collections.defaultdict(list)
    for r in rows:
        g[key(r)].append(r)
    return g


def recall_scores(kept: dict, rows: list[dict]) -> dict:
    """kept: {id(row): bool}. Precision and recall against label 1."""
    tp = sum(1 for r in rows if kept[id(r)] and r["label"] == 1)
    fp = sum(1 for r in rows if kept[id(r)] and r["label"] != 1)
    pos = sum(1 for r in rows if r["label"] == 1)
    return {"kept": tp + fp, "precision": round(tp / max(tp + fp, 1), 3), "recall": round(tp / max(pos, 1), 3)}


def run_judge(judge, model: str, rows: list[dict], facet: dict, query: str) -> dict:
    keep = judge(model, facet, [("", r["text"]) for r in rows], query)
    keep = set(range(len(rows))) if keep is None else set(keep)   # None = failed call: select_ce keeps the CE ranking
    return {id(r): i in keep for i, r in enumerate(rows)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    a = ap.parse_args()
    sys.path.insert(0, os.path.join(ROOT, "src"))
    import yaml
    import pipeline.run as R
    qtext = {q["id"]: q["q"] for q in yaml.safe_load(open(os.path.join(ROOT, "eval/pipeline_queries.yaml")))}
    rec = json.load(open(os.path.join(ROOT, "eval/pipeline_relevance_es.json")))
    scope_path = os.path.join(ROOT, "eval/judge_scope/candidates.jsonl")
    scope = [json.loads(line) for line in open(scope_path)] if os.path.exists(scope_path) else []
    unlabelled = sum(1 for r in scope if r.get("label") not in ("in", "out", "general"))
    if unlabelled:
        sys.exit(f"{unlabelled} scope candidates have no in|out|general label; label them before any model run")
    for name, use_query in (("current", False), ("scoped", True)):
        kept = {}
        for (q, fname), rs in groups(rec, lambda r: (r["q"], r["facet"])).items():
            kept |= run_judge(R.judge, a.model, rs, {"name": fname, "questions": [rs[0]["question"]]}, qtext[q] if use_query else "")
        line = {"variant": name, "recall_set": recall_scores(kept, rec)}
        if scope:
            kept = {}
            for (q, fname), rs in groups(scope, lambda r: (r["query"], r["facet"])).items():
                kept |= run_judge(R.judge, a.model, rs, {"name": fname, "questions": rs[0].get("questions") or [fname]}, q if use_query else "")
            line["scope_set_kept"] = {lab: sum(1 for r in scope if kept[id(r)] and r["label"] == lab) for lab in ("in", "out", "general")}
            line["scope_set_total"] = dict(collections.Counter(r["label"] for r in scope))
        print(json.dumps(line), flush=True)


if __name__ == "__main__":
    main()
