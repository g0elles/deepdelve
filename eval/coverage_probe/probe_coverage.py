"""Coverage probe (eval/coverage_probe/PREREG.md): per request part, which kept quote answers it? Arms A (answerability YES/NO), B (existing
pipeline.run.facet_unanswered), C (relevance YES/NO), D (word overlap, no model). Per-row output, resumable.
Repo root:
    python eval/coverage_probe/probe_coverage.py --model ornith-1.5:9b      -> runs/<model>.jsonl (one row per model call or pick)
    python eval/coverage_probe/probe_coverage.py --picks                     -> runs/to_label.json (pooled, shuffled, blind picks per part)
    python eval/coverage_probe/probe_coverage.py --score                     -> hit rates per arm and model vs runs/labels.json"""
import argparse, json, os, random, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "src"))
RUNS = os.path.join(ROOT, "eval/coverage_probe/runs")
PARTS = {
    "research_output/cmp_ornith_c1": ["What is the lifetime cost of heat pumps compared with gas furnaces?",
                                      "What are the carbon emissions of heat pumps compared with gas furnaces?",
                                      "How do heat pumps perform in cold climates?"],
    "eval/heldout3_runs/h11": [f"What are the legal parental leave duration and pay in {c}?" for c in ("Germany", "Sweden", "Japan")],
    "eval/heldout3_runs/h12": ["How do Raft and Paxos differ in leader election?", "How do Raft and Paxos differ in log replication?",
                               "Which production systems use Raft?", "Which production systems use Paxos?"],
    "eval/heldout4_runs/h21": ["How do LSM trees and B-trees differ in write amplification?", "How do LSM trees and B-trees differ in read performance?",
                               "Which production databases use LSM trees?", "Which production databases use B-trees?"],
    "eval/heldout4_runs/h25": [f"What was {c}'s {m} in the most recent reported year?" for c in ("Portugal", "Vietnam", "Ghana")
                               for m in ("GDP per capita", "internet penetration", "fertility rate")],
}
YN = {"type": "object", "required": ["answer"], "properties": {"answer": {"type": "string", "enum": ["YES", "NO"]}}}


def answer_prompt(part: str, quote: str) -> str:
    return ("You are an expert at judging whether a passage answers a question.\n"
            "Does the passage state an answer, fully or in part, to the question? It answers only if it states the attribute the question "
            "asks about, for the entity the question names. Background, a definition, a related but different quantity, or a different "
            "entity or country does NOT answer it.\n\n"
            f"Question: {part}\nPassage: {quote}\n\nRespond with YES or NO.")


def relevance_prompt(query: str, quote: str) -> str:
    return f"Is the passage relevant to the request?\n\nRequest: {query}\nPassage: {quote}\n\nRespond with YES or NO."


def load(d: str) -> tuple[str, list[str]]:
    ev = json.load(open(os.path.join(ROOT, d, "evidence.json")))
    query = json.load(open(os.path.join(ROOT, d, "metrics.json")))["query"]
    return query, list(dict.fromkeys(q for by in ev["evidence"].values() for ql in by.values() for q in ql))


def overlap_pick(cands: list[int], quotes: list[str], text: str) -> int:
    from writer.compiled import _terms
    want = _terms(text)
    return max(cands, key=lambda i: len(_terms(quotes[i]) & want)) if cands else -1


def run(model: str):
    from pipeline.llm import call_json
    import pipeline.run as R
    os.makedirs(RUNS, exist_ok=True)
    path = os.path.join(RUNS, re.sub(r"\W+", "_", model) + ".jsonl")
    done = {(r["arm"], r["dir"], r.get("p"), r.get("i")) for r in map(json.loads, open(path))} if os.path.exists(path) else set()
    out = open(path, "a")
    yn = lambda p: call_json(model, p, YN, think="minimal", timeout=120)["answer"]
    for d, parts in PARTS.items():
        query, quotes = load(d)
        for i, q in enumerate(quotes):  # C: one relevance call per quote
            if ("C", d, None, i) not in done:
                try:
                    a = yn(relevance_prompt(query, q))
                except Exception as e:
                    a = f"ERR {type(e).__name__}"
                out.write(json.dumps({"arm": "C", "dir": d, "i": i, "answer": a}) + "\n"); out.flush()
        for p, part in enumerate(parts):
            for i, q in enumerate(quotes):  # A: one answerability call per (part, quote)
                if ("A", d, p, i) not in done:
                    try:
                        a = yn(answer_prompt(part, q))
                    except Exception as e:
                        a = f"ERR {type(e).__name__}"
                    out.write(json.dumps({"arm": "A", "dir": d, "p": p, "i": i, "answer": a}) + "\n"); out.flush()
        if ("B", d, None, None) not in done:  # B: production facet_unanswered (prompt untouched), chunks of 20, first chunk that answers
            pick = {p: -1 for p in range(len(parts))}
            seen = []
            real = R.call_json
            R.call_json = lambda *x, **k: (lambda r: (seen.append(r), r)[1])(real(*x, **k))
            try:
                for s in range(0, len(quotes), 20):
                    todo = [p for p in pick if pick[p] < 0]
                    if not todo:
                        break
                    seen.clear()
                    R.facet_unanswered(model, query, {"name": query, "questions": [parts[p] for p in todo]}, quotes[s:s + 20])
                    for ans in (seen[0]["answers"] if seen and isinstance(seen[0], dict) else []):
                        k, qi = ans.get("q"), ans.get("quote")
                        if isinstance(k, int) and 0 <= k < len(todo) and isinstance(qi, int) and 0 <= qi < len(quotes[s:s + 20]) and pick[todo[k]] < 0:
                            pick[todo[k]] = s + qi
            finally:
                R.call_json = real
            out.write(json.dumps({"arm": "B", "dir": d, "picks": pick}) + "\n"); out.flush()
    _ = R


def picks() -> dict:
    """{model: {(dir, p): {arm: quote index or -1}}} from the run files; D needs no model."""
    out = {}
    for fn in sorted(os.listdir(RUNS)):
        if not fn.endswith(".jsonl"):
            continue
        rows = [json.loads(line) for line in open(os.path.join(RUNS, fn))]
        m = {}
        for d, parts in PARTS.items():
            query, quotes = load(d)
            rel = [r["i"] for r in rows if r["arm"] == "C" and r["dir"] == d and r["answer"] == "YES"]
            b = next((r["picks"] for r in rows if r["arm"] == "B" and r["dir"] == d), {})
            for p, part in enumerate(parts):
                yes = [r["i"] for r in rows if r["arm"] == "A" and r["dir"] == d and r["p"] == p and r["answer"] == "YES"]
                m[(d, p)] = {"A": overlap_pick(yes, quotes, part), "B": int(b.get(str(p), -1)), "C": overlap_pick(rel, quotes, part),
                             "D": overlap_pick(list(range(len(quotes))), quotes, query + " " + part)}
        out[fn[:-6]] = m
    return out


def write_to_label():
    random.seed(0)
    pk, items = picks(), []
    for d, parts in PARTS.items():
        _, quotes = load(d)
        for p, part in enumerate(parts):
            idx = sorted({v for m in pk.values() for v in m[(d, p)].values() if v >= 0})
            random.shuffle(idx)
            abstained = any(v < 0 for m in pk.values() for v in m[(d, p)].values())
            items.append({"key": f"{d}|{p}", "part": part, "any_arm_abstained": abstained, "cands": [{"i": i, "text": quotes[i]} for i in idx]})
    json.dump(items, open(os.path.join(RUNS, "to_label.json"), "w"), ensure_ascii=False, indent=1)
    print(len(items), "parts,", sum(len(x["cands"]) for x in items), "quotes to label")


def score():
    """labels.json: {"dir|p": {"answers": [quote indexes that answer], "none_exists": bool}}."""
    lab = json.load(open(os.path.join(RUNS, "labels.json")))
    for model, m in picks().items():
        line = {}
        for arm in "ABCD":
            hits = 0
            for (d, p), pk in m.items():
                L, v = lab[f"{d}|{p}"], pk[arm]
                hits += (v in L["answers"]) if v >= 0 else L["none_exists"]
            line[arm] = round(hits / len(m), 3)
        print(model, json.dumps(line))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model")
    ap.add_argument("--picks", action="store_true")
    ap.add_argument("--score", action="store_true")
    a = ap.parse_args()
    if a.model:
        run(a.model)
    if a.picks:
        write_to_label()
    if a.score:
        score()


if __name__ == "__main__":
    main()
