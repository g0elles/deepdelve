"""Decision-model probe (PREREG.md). Repo root: python eval/decision_probe/probe.py --model pplx-decider-flash [--task S|A] ; --score"""
import argparse, json, os, urllib.error, urllib.request
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
    for fn in sorted(f for f in os.listdir(RUNS) if f.endswith(("_S.jsonl", "_A.jsonl"))):
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


# ---- Round 3 (PREREG Amendment 3, plan eval/decision_probe/PLAN_v3_draft.md): scope only, one short question with criteria ----
import sys  # noqa: E402
sys.path.insert(0, os.path.join(ROOT, "src"))
RUNS_V3 = os.path.join(ROOT, "eval/decision_probe/runs_v3")
Q3 = {"type": "noul",
      "instructions": ("Is the sentence mainly about a different subject, place, time period, climate or condition, or group of people than "
                       "the request and facet questions name?"),
      "criteria": {
          "true": ("The sentence is mainly about a subject, place, period, climate or condition, or group that differs from one of the same "
                   "kind the request or facet questions name."),
          "false": ("The sentence is mainly about what the request or facet questions name, a narrower or broader version of it, or something "
                    "of a kind they do not name.")}}
GATE = [  # (request, sentence, expected) -- hand-written, not from the labelled data or its topics
    ("How much do bike lanes cost to build in Copenhagen?", "In Amsterdam, a kilometre of bike lane costs about 1 million euros.", True),
    ("How much do bike lanes cost to build in Copenhagen?", "In Copenhagen, a kilometre of protected bike lane costs about 1.5 million euros.", False),
    ("What are wheat yields in Kansas?", "Corn yields in Kansas averaged 130 bushels per acre.", True),
    ("What were coffee prices in 2010?", "Coffee prices rose 20 percent in 1994 after a frost.", True),
    ("How warm do houses stay with wool insulation?", "In Scotland, wool-insulated houses kept 18 degrees indoors.", False),
    ("How do e-bikes affect commuting in Utrecht?", "E-bike commuting rose 30 percent in Utrecht, more than in Rotterdam.", False),
    ("What do dog vaccines cost in Texas?", "In Austin, a rabies vaccine for a dog costs about 25 dollars.", False),
    ("How effective is aspirin for adults over 65?", "In children under 12, aspirin raises the risk of Reye's syndrome.", True),
    ("How much water do rice farms use in the dry season?", "During the monsoon, rice paddies need little irrigation.", True),
    ("What did the 2019 study find on sleep and memory in students?",
     "According to a 2024 summary, the 2019 study found students who slept 8 hours recalled 20 percent more words.", False),
    ("How does the bus network in Lyon work?", "Across France, city bus networks are run by regional transport authorities.", False),
    ("How does the metro in Lisbon work?", "Buses from Porto to Faro run 12 times a day.", True),
    ("¿Cuánto cuesta el pasaje del metro de Santiago?", "En Lima, el pasaje del metro cuesta 1,5 soles.", True)]
GATE_MUST = [i for i, g in enumerate(GATE) if not g[2]]   # items only the criteria decide: all must be correct


class Outage(Exception):
    pass


def body3(model, state, q=Q3):
    return {"model": model, "state": state, "questions": {"q": q}}


def ask3(model, state, q=Q3):
    try:
        r = urllib.request.urlopen(urllib.request.Request("http://localhost:11434/v1/systemone", json.dumps(body3(model, state, q)).encode(),
                                                          {"Content-Type": "application/json"}), timeout=120)
    except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
        raise Outage(str(e)) from e
    p = json.load(r)["answers"]["q"]["noul"]
    if not isinstance(p, (int, float)) or isinstance(p, bool) or not 0 <= p <= 1:
        raise ValueError(f"non-numeric noul {p!r}")
    return float(p)


def gate_path(model):
    return os.path.join(RUNS_V3, "gate", f"{model.replace(':', '_')}.jsonl")


def gate_verdict(model):
    """Stored verdict (last line) or None if the gate never ran. The gate runs once per model, never re-rolled."""
    p = gate_path(model)
    if not os.path.exists(p):
        return None
    return json.loads(open(p).read().splitlines()[-1])["gate_pass"]


def gate(model):
    if gate_verdict(model) is not None:
        print(f"GATE {model}: already run, stored verdict {'PASS' if gate_verdict(model) else 'FAIL'} (not re-run)")
        return gate_verdict(model)
    os.makedirs(os.path.dirname(gate_path(model)), exist_ok=True)
    swapped = {**Q3, "criteria": {"true": Q3["criteria"]["false"], "false": Q3["criteria"]["true"]}}
    bare = {k: v for k, v in Q3.items() if k != "criteria"}

    def one(st, q):
        try:
            return ask3(model, st, q)
        except Exception:
            return None
    rows_out, side, swap, bare_ok, must_ok = [], 0, 0, 0, True
    for i, (req, sent, exp) in enumerate(GATE):
        st = {"request": req, "facet": req, "facet_questions": [req], "sentence": sent}
        a, c, b = one(st, Q3), one(st, swapped), one(st, bare)
        ok_side = a is not None and (a >= .5) == exp          # an error in arm (a) counts as wrong
        ok_swap = a is not None and c is not None and (a - c if exp else c - a) > .3
        side += ok_side; swap += ok_swap; bare_ok += b is not None and (b >= .5) == exp
        if i in GATE_MUST and not ok_side:
            must_ok = False
        rows_out.append({"item": i + 1, "expected": exp, "a": a, "swapped": c, "no_criteria": b, "side_ok": ok_side, "swap_ok": ok_swap})
    passed = side >= len(GATE) - 2 and must_ok
    with open(gate_path(model), "x") as out:   # never overwritten
        for r in rows_out:
            out.write(json.dumps(r) + "\n")
        out.write(json.dumps({"gate_pass": passed, "side": side, "must_all_correct": must_ok, "swap_diag": swap, "no_criteria_correct": bare_ok}) + "\n")
    print(f"GATE {model}: arm a {side}/{len(GATE)}, criteria-only items all correct {must_ok}, swap (diagnostic) {swap}/{len(GATE)}, "
          f"no-criteria (reported) {bare_ok}/{len(GATE)} -> {'PASS' if passed else 'FAIL'}", flush=True)
    return passed


def run3(model):
    os.makedirs(RUNS_V3, exist_ok=True)
    path = os.path.join(RUNS_V3, f"{model.replace(':', '_')}_S.jsonl")
    done = {json.loads(line)["id"] for line in open(path)} if os.path.exists(path) else set()
    streak = 0
    with open(path, "a") as out:
        for rid, state, gold, r in rows("S"):
            if rid in done:
                continue
            try:
                p = ask3(model, state); streak = 0
            except Outage as e:
                print("OUTAGE, stopping without writing this row (resume later):", str(e)[:120], flush=True)
                return False
            except Exception as e:
                p = None; streak += 1; print("ERR", rid, type(e).__name__, str(e)[:120], flush=True)
                if streak >= 3:
                    print("3 errors in a row, stopping; errored rows stay written (one try per row)", flush=True)
                    return False
            out.write(json.dumps({"id": rid, "p": p, "gold": gold, "label": r["label"], "query": r["query"]}) + "\n"); out.flush()
    return True


def _is_spanish(text):  # the pipeline's own detector, applied to the request text
    from pipeline.run import _is_spanish as f
    return f(text)


def wilson(k, n, z=1.96):
    if not n:
        return (float("nan"), float("nan"))
    ph = k / n; d = 1 + z * z / n; c = (ph + z * z / (2 * n)) / d; h = z * ((ph * (1 - ph) / n + z * z / (4 * n * n)) ** .5) / d
    return (c - h, c + h)


def score3(runs_dir=RUNS_V3):
    gold = {rid: g for rid, _, g, _ in rows("S")}
    n_out, n_not = sum(gold.values()), len(gold) - sum(gold.values())
    assert (n_out, n_not) == (44, 498), (n_out, n_not)
    for fn in sorted(f for f in os.listdir(runs_dir) if f.endswith("_S.jsonl")):
        model = fn[:-len("_S.jsonl")]
        rs = [json.loads(line) for line in open(os.path.join(runs_dir, fn))]
        ids = [r["id"] for r in rs]
        if len(ids) != len(set(ids)):
            print(f"{fn}: DUPLICATE ids, no grade"); continue
        by = {r["id"]: r for r in rs}
        missing = [i for i in gold if i not in by]
        p = {i: (by[i]["p"] if i in by else None) for i in gold}         # missing = errored (worst case)
        err = [i for i in gold if p[i] is None]
        tp = sum(1 for i in gold if gold[i] and p[i] is not None and p[i] >= .5)          # errored out row = not flagged
        fp = sum(1 for i in gold if not gold[i] and (p[i] is None or p[i] >= .5))         # errored not-out row = flagged
        grade = "drop" if tp >= 30 and fp <= 24 else "label" if tp >= 30 and fp <= 74 else "fail"
        if len(err) >= 11:
            grade = "NO VERDICT (11 or more errored or missing rows)"
        gv = gate_verdict(next((m for m in (model, model.replace("_", ":", 1)) if gate_verdict(m) is not None), model))
        if gv is not True:
            grade = f"NO VERDICT (gate {'FAIL' if gv is False else 'not run'})"
        ok = [i for i in gold if p[i] is not None]
        ps, gs = [p[i] for i in ok], [gold[i] for i in ok]
        wa, wb = wilson(tp, n_out), wilson(fp, n_not)
        print(f"{fn}: rows {len(rs)}/542, missing {len(missing)}, errored {len(err) - len(missing)} | AUROC {auroc(ps, gs):.3f} (on {len(ok)} answered rows) | "
              f"out {tp}/{n_out} = {tp / n_out:.2f} [{wa[0]:.2f}, {wa[1]:.2f}], not-out {fp}/{n_not} = {fp / n_not:.3f} [{wb[0]:.3f}, {wb[1]:.3f}] -> {grade}")
        prev = os.path.join(RUNS, fn)
        if os.path.exists(prev):
            old = {r["id"]: r for r in map(json.loads, open(prev))}
            oa = [i for i in gold if old.get(i, {}).get("p") is not None]
            print(f"   round 2 (runs/{fn}): AUROC {auroc([old[i]['p'] for i in oa], [gold[i] for i in oa]):.3f}")
            for cls, want in (("out", True), ("not-out", False)):
                cell = {(a, b): 0 for a in (True, False) for b in (True, False)}
                for i in gold:
                    if gold[i] != want or i not in old:
                        continue
                    new_flag = p[i] >= .5 if p[i] is not None else (not want)     # worst case for errored rows
                    old_flag = (old[i]["p"] or 0) >= .5
                    cell[(old_flag == want, new_flag == want)] += 1
                print(f"   paired 2x2 {cls} (round2 correct, round3 correct): right->right {cell[(True, True)]}, right->wrong {cell[(True, False)]}, "
                      f"wrong->right {cell[(False, True)]}, wrong->wrong {cell[(False, False)]}")
        best = None   # exploratory: the highest cut-off flagging >= 30/44 out rows (same rule as notes_round12_sweep.txt)
        for th in sorted(set(ps), reverse=True):
            t = sum(q >= th and g for q, g in zip(ps, gs)); f = sum(q >= th and not g for q, g in zip(ps, gs))
            if t >= 30:
                best = (th, t, f); break
        print(f"   exploratory best cut-off {best[0]:.3f}: out {best[1]}/{n_out}, not-out {best[2]}/{n_not} = {best[2] / n_not:.3f}" if best
              else "   exploratory: no cut-off flags 30/44 out rows")
        labels = {rid: r["label"] for rid, _, _, r in rows("S")}
        queries = {rid: r["query"] for rid, _, _, r in rows("S")}
        gen = [i for i in ok if labels[i] == "general"]
        print(f"   general rows flagged {sum(p[i] >= .5 for i in gen)}/{len(gen)} (answered rows only)")
        for lang, f in (("en", lambda q: not _is_spanish(q)), ("es", _is_spanish)):
            sub = [i for i in ok if f(queries[i])]
            o = [i for i in sub if gold[i]]; no = [i for i in sub if not gold[i]]
            print(f"   {lang}: out {sum(p[i] >= .5 for i in o)}/{len(o)}, not-out {sum(p[i] >= .5 for i in no)}/{len(no)} (answered rows only)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--model"); ap.add_argument("--task", default="SA"); ap.add_argument("--score", action="store_true")
    ap.add_argument("--v3", action="store_true", help="round 3 (Amendment 3): scope only, criteria question")
    ap.add_argument("--gate", action="store_true", help="round 3 step 0 criteria gate only")
    ap.add_argument("--dry", action="store_true", help="print the row-0 and gate-1 request bodies, no model call")
    a = ap.parse_args()
    if a.dry:
        rid, st, _, _ = next(rows("S"))
        print(json.dumps(body3("<m>", st)))
        print(json.dumps(body3("<m>", {"request": GATE[0][0], "facet": GATE[0][0], "facet_questions": [GATE[0][0]], "sentence": GATE[0][1]})))
    elif a.v3 or a.gate:
        if a.model and gate(a.model) and not a.gate:
            run3(a.model)
        if a.score:
            score3()
    else:
        if a.model:
            for t in a.task:
                run(a.model, t)
        if a.score:
            score()
