"""Decision-model probe (PREREG.md). Repo root: python eval/decision_probe/probe.py --model pplx-decider-flash [--task S|A] ; --score"""
import argparse, json, os, urllib.request
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RUNS = os.path.join(ROOT, "eval/decision_probe/runs")
QS = ("Does the sentence report on a different thing or product, place, period, climate or condition, or population or group than the "
      "request and facet questions name? A dimension the request does not name cannot differ. Answer false if the sentence only names "
      "nothing beyond what the request asks about.")
QA = ("Does the passage state an answer, fully or in part, to the question: it states the attribute the question asks about, for the entity "
      "the question names? Background, a definition, a related but different quantity, or a different entity does NOT answer.")


def ask(model, state, instructions):
    body = json.dumps({"model": model, "state": state, "questions": {"q": {"type": "noul", "instructions": instructions}}}).encode()
    r = urllib.request.urlopen(urllib.request.Request("http://localhost:11434/v1/systemone", body, {"Content-Type": "application/json"}), timeout=120)
    return json.load(r)["answers"]["q"]["noul"]


def rows(task):
    if task == "S":
        disputed = set()
        p = os.path.join(ROOT, "eval/judge_scope/probe_runs/disputed.json")
        if os.path.exists(p):
            disputed = {d if isinstance(d, str) else d.get("text") for d in json.load(open(p))}
        for i, r in enumerate(map(json.loads, open(os.path.join(ROOT, "eval/judge_scope/candidates.jsonl")))):
            if r["text"] not in disputed:
                yield i, {"request": r["query"], "facet": r["facet"], "facet_questions": r["questions"], "sentence": r["text"]}, r["label"] == "out", r
    else:
        lab = json.load(open(os.path.join(ROOT, "eval/coverage_probe/runs/slots_labels.json")))
        items = json.load(open(os.path.join(ROOT, "eval/coverage_probe/runs/slots_to_label.json")))
        for x in items:
            for j, q in enumerate(x["quotes"]):
                yield f"{x['key']}#{j}", {"question": x["facet"], "passage": q}, q in lab[x["key"]], x


def run(model, task):
    os.makedirs(RUNS, exist_ok=True)
    path = os.path.join(RUNS, f"{model.replace(':', '_')}_{task}.jsonl")
    done = {json.loads(line)["id"] for line in open(path)} if os.path.exists(path) else set()
    with open(path, "a") as out:
        for rid, state, gold, _ in rows(task):
            if rid in done:
                continue
            try:
                p = ask(model, state, QS if task == "S" else QA)
            except Exception as e:
                p = None; print("ERR", rid, type(e).__name__, str(e)[:120], flush=True)
            out.write(json.dumps({"id": rid, "p": p, "gold": gold}) + "\n"); out.flush()


def auroc(ps, gs):
    pos = [p for p, g in zip(ps, gs) if g]; neg = [p for p, g in zip(ps, gs) if not g]
    return sum((a > b) + 0.5 * (a == b) for a in pos for b in neg) / (len(pos) * len(neg)) if pos and neg else float("nan")


def score():
    for fn in sorted(os.listdir(RUNS)):
        rs = [json.loads(line) for line in open(os.path.join(RUNS, fn))]
        ok = [r for r in rs if r["p"] is not None]
        ps, gs = [r["p"] for r in ok], [r["gold"] for r in ok]
        tp = sum(p >= .5 and g for p, g in zip(ps, gs)); fp = sum(p >= .5 and not g for p, g in zip(ps, gs)); pos = sum(gs); neg = len(gs) - pos
        line = f"{fn}: n {len(ok)} err {len(rs) - len(ok)} AUROC {auroc(ps, gs):.3f}"
        if fn.endswith("_S.jsonl"):
            a, b = tp / pos, fp / neg
            grade = "drop" if a >= 2 / 3 and b <= .05 else "label" if a >= 2 / 3 and b <= .15 else "fail"
            line += f" | out flagged {tp}/{pos} = {a:.2f}, not-out flagged {fp}/{neg} = {b:.3f} -> {grade}"
        else:
            prec, rec = tp / max(tp + fp, 1), tp / pos
            by = {}
            for r in ok:
                by.setdefault(r["id"].rsplit("#", 1)[0], []).append(r)
            has = [v for v in by.values() if any(r["gold"] for r in v)]
            pick = sum(max(v, key=lambda r: r["p"])["gold"] for v in has) / len(has)
            none = [v for v in by.values() if not any(r["gold"] for r in v)]
            abst = sum(max(r["p"] for r in v) < .5 for v in none)
            line += (f" | precision {prec:.3f} recall {rec:.3f} | pick {pick:.3f} over {len(has)} facets | abstain {abst}/{len(none)}"
                     f" -> {'PASS' if prec >= .85 and rec >= .80 and pick >= .90 else 'fail'}")
        print(line)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--model"); ap.add_argument("--task", default="SA"); ap.add_argument("--score", action="store_true")
    a = ap.parse_args()
    if a.model:
        for t in a.task:
            run(a.model, t)
    if a.score:
        score()
