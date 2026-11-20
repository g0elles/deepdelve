"""Scope probe (design session_status/egm/answer_quality/answer_quality_design.md rev 4, Part 2): does a separate scope question flag
out-of-scope candidates in the judge-kept set without flagging much else? Two designs (pointwise, listwise), per-row output, pre-registered
grades. No production code depends on this file.

Usage (repo root):
  python eval/judge_scope/probe_scope.py --review                       -> probe_runs/out_review.md (user label check)
  python eval/judge_scope/probe_scope.py --show-prompts                 -> both templates + SHA-256 (user approval)
  python eval/judge_scope/probe_scope.py --selftest
  python eval/judge_scope/probe_scope.py --design pointwise|listwise --model ornith-1.5:9b"""
import argparse, collections, hashlib, json, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "src"))
OUT = os.path.join(HERE, "probe_runs")
DISPUTED = os.path.join(OUT, "disputed.json")
ENUM = ("same", "different", "general")

RULES = """Compare what each sentence reports on with what the request and the facet questions name, on five dimensions: the thing or product, the place, the period, the climate or condition, and the population or group.
- request_scope: the five dimensions as the request and facet questions name them; write "none" for a dimension they do not name.
- sentence_scope: the five dimensions as the sentence names them; write "none" for a dimension it does not name.
- differs_in: each dimension where the sentence names something different from what the request or facet questions name, and how; empty if none.
- scope: "different" if differs_in is not empty; "general" if the sentence names nothing beyond the thing the request asks about; otherwise "same".
A dimension the request and facet questions do not name cannot differ."""
POINTWISE = "Request: {query}\nFacet: {facet}\nFacet questions: {questions}\n\nSentence: {text}\n\n" + RULES
LISTWISE = ("Request: {query}\nFacet: {facet}\nFacet questions: {questions}\n\nCandidate sentences:\n{numbered}\n\n" + RULES
            + "\nReturn one label per candidate sentence, with n set to its number.")
FIELDS = {"request_scope": {"type": "string"}, "sentence_scope": {"type": "string"}, "differs_in": {"type": "string"}, "scope": {"type": "string", "enum": list(ENUM)}}
SCHEMA = {"pointwise": {"type": "object", "properties": FIELDS, "required": list(FIELDS)},
          "listwise": {"type": "object", "properties": {"labels": {"type": "array", "items": {
              "type": "object", "properties": {"n": {"type": "integer"}, **FIELDS}, "required": ["n", *FIELDS]}}}, "required": ["labels"]}}
TIMEOUT = {"pointwise": 120, "listwise": 300}
SHA = hashlib.sha256((POINTWISE + "\n---\n" + LISTWISE).encode()).hexdigest()
EXPECTED_KEPT = {"ornith-1.5:9b": (32, 366), "gemma4:12b": (25, 308)}   # (out, not-out) rows with current true, design rev 4


def tag(model: str) -> str:
    return model.replace(":", "_").replace("/", "_")


def groups(rows: list[dict]) -> dict:
    g = collections.defaultdict(list)
    for r in rows:
        g[(r["query"], r["facet"])].append(r)
    return g


def grade(out_flag: int, out_n: int, nonout_flag: int, nonout_n: int, invalid: int, scored: int) -> str:
    if invalid * 100 > 5 * scored:
        return "invalid"
    if out_flag * 3 < 2 * out_n:
        return "fail"
    if nonout_flag * 100 <= 5 * nonout_n:
        return "drop-grade"
    return "label-grade" if nonout_flag * 100 <= 15 * nonout_n else "fail"


def parse_listwise(ans, size: int) -> list[dict | None]:
    """One entry per row; None = invalid (missing, duplicate or out-of-range n, value outside the enum)."""
    labels = (ans or {}).get("labels") if isinstance(ans, dict) else None
    if not isinstance(labels, list):
        return [None] * size
    seen = collections.Counter(x.get("n") for x in labels if isinstance(x, dict))
    res = [None] * size
    for x in labels:
        n = x.get("n") if isinstance(x, dict) else None
        if isinstance(n, int) and 0 <= n < size and seen[n] == 1 and x.get("scope") in ENUM:
            res[n] = x
    return res


def load(model: str) -> list[dict]:
    return [json.loads(line) for line in open(os.path.join(HERE, "ab_runs", f"rows_{tag(model)}.jsonl"))]


def selftest(rows: list[dict] | None = None):
    from pipeline.run import _is_spanish
    rows = rows if rows is not None else load("ornith-1.5:9b")
    assert len(rows) == 542, len(rows)
    g = groups(rows)
    assert len(g) == 29 and max(map(len, g.values())) <= 20, (len(g), max(map(len, g.values())))
    assert sum(_is_spanish(r["query"]) for r in rows) == 100
    for m, (o, no) in EXPECTED_KEPT.items():
        k = [r for r in load(m) if r["current"]]
        assert (sum(r["label"] == "out" for r in k), sum(r["label"] != "out" for r in k)) == (o, no), m
    assert grade(22, 32, 18, 366, 0, 398) == "drop-grade"
    assert grade(22, 32, 19, 366, 0, 398) == "label-grade"
    assert grade(22, 32, 54, 366, 0, 398) == "label-grade"
    assert grade(22, 32, 55, 366, 0, 398) == "fail"
    assert grade(21, 32, 0, 366, 0, 398) == "fail"
    assert grade(22, 32, 0, 366, 15, 300) == "drop-grade" and grade(22, 32, 0, 366, 16, 300) == "invalid"
    ok = {"request_scope": "", "sentence_scope": "", "differs_in": "", "scope": "same"}
    p = parse_listwise({"labels": [{"n": 0, **ok}, {"n": 0, **ok}, {"n": 2, **ok, "scope": "maybe"}, {"n": 20, **ok}]}, 20)
    assert p[0] is None and p[1] is None and p[2] is None and all(x is None for x in p), "duplicate, missing, enum, range"
    assert parse_listwise({"labels": [{"n": 1, **ok}]}, 2) == [None, {"n": 1, **ok}]
    print("selftest OK")


def review():
    rows = load("ornith-1.5:9b")
    by = collections.defaultdict(list)
    for r in rows:
        if r["label"] == "out":
            by[r["text"]].append(f'{r["query"]} || facet: {r["facet"]}')
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "out_review.md"), "w") as fh:
        fh.write(f"# Out-of-scope labels to check ({len(by)} distinct texts)\n\nMark any number you find debatable; those are excluded from the bar.\n\n")
        for i, (t, where) in enumerate(by.items(), 1):
            fh.write(f"{i}. {t}\n" + "".join(f"   - {w}\n" for w in sorted(set(where))) + "\n")
    print(os.path.join(OUT, "out_review.md"), len(by))


def run(design: str, model: str):
    from pipeline.llm import call_json
    if not os.path.exists(DISPUTED):
        sys.exit("probe_runs/disputed.json missing: the user's label check comes first")
    disputed = set(json.load(open(DISPUTED)))
    rows = load(model)
    selftest()
    print(f"prompt sha256 {SHA}\n{POINTWISE if design == 'pointwise' else LISTWISE}\n")
    ans = {}  # id(row) -> (labels dict or None, latency, group size)

    def call(prompt):
        t0 = time.time()
        try:
            a = call_json(model, prompt, SCHEMA[design], think="minimal", timeout=TIMEOUT[design], retries=1, temperature=0)
        except Exception as e:
            a = {"_error": str(e)[:200]}
        return a, round(time.time() - t0, 2)

    for (q, f), rs in groups(rows).items():
        ctx = dict(query=q, facet=f, questions="; ".join(rs[0].get("questions") or [f]))
        if design == "pointwise":
            for r in rs:
                a, dt = call(POINTWISE.format(text=r["text"], **ctx))
                ans[id(r)] = (a if isinstance(a, dict) and a.get("scope") in ENUM else None, dt, 1)
        else:
            a, dt = call(LISTWISE.format(numbered="\n".join(f"{i}. {r['text']}" for i, r in enumerate(rs)), **ctx))
            for r, x in zip(rs, parse_listwise(a, len(rs))):
                ans[id(r)] = (x, dt, len(rs))
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, f"{design}_{tag(model)}.jsonl"), "w") as fh:
        for r in rows:
            x, dt, n = ans[id(r)]
            fh.write(json.dumps({"text": r["text"], "query": r["query"], "facet": r["facet"], "label": r["label"], "disputed": r["text"] in disputed,
                                 "current": r["current"], **{k: (x or {}).get(k) for k in FIELDS}, "invalid": x is None, "latency_s": dt,
                                 "group_size": n, "prompt_sha256": SHA}, ensure_ascii=False) + "\n")
    report(design, model)


def report(design: str, model: str):
    from pipeline.run import _is_spanish
    rows = [json.loads(line) for line in open(os.path.join(OUT, f"{design}_{tag(model)}.jsonl"))]
    assert len(rows) == 542 and {r["prompt_sha256"] for r in rows} == {SHA}

    def rates(rs):
        rs = [r for r in rs if not r["disputed"]]
        out = [r for r in rs if r["label"] == "out"]
        no = [r for r in rs if r["label"] != "out"]
        fl = lambda xs: sum(r["scope"] == "different" and not r["invalid"] for r in xs)
        return dict(out_flag=fl(out), out_n=len(out), nonout_flag=fl(no), nonout_n=len(no), invalid=sum(r["invalid"] for r in rs), scored=len(rs))
    kept = [r for r in rows if r["current"]]
    k = rates(kept)
    print(json.dumps({"design": design, "model": model, "judge_kept": k, "grade": grade(**k), "all_rows": rates(rows),
                      "kept_en": rates([r for r in kept if not _is_spanish(r["query"])]), "kept_es": rates([r for r in kept if _is_spanish(r["query"])]),
                      "disputed_kept": sum(r["disputed"] for r in kept)}))
    sens = os.path.join(OUT, "sensitivity_excluded.json")   # pre-registered before the run; reported only, never changes the grade
    if os.path.exists(sens):
        ex = set(json.load(open(sens)))
        k2 = rates([r for r in kept if r["text"] not in ex])
        print(json.dumps({"sensitivity_without_6_debatable": k2, "grade_if_excluded": grade(**k2)}))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--review", action="store_true")
    ap.add_argument("--show-prompts", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--design", choices=["pointwise", "listwise"])
    ap.add_argument("--model")
    a = ap.parse_args()
    if a.review:
        review()
    elif a.show_prompts:
        print(f"SHA-256 {SHA}\n\n--- pointwise ---\n{POINTWISE}\n\n--- listwise ---\n{LISTWISE}")
    elif a.selftest:
        selftest()
    elif a.design and a.model:
        run(a.design, a.model)
    else:
        ap.print_help()
