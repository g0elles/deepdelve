"""Overview slots replay (PREREG_slots.md). Repo root:
    python eval/coverage_probe/replay_slots.py --model ornith-1.5:9b   -> runs/slots_<model>.json (answering per dir, GPU)
    python eval/coverage_probe/replay_slots.py --to-label              -> runs/slots_to_label.json (blind, per facet)
    python eval/coverage_probe/replay_slots.py --score                 -> hit rates vs runs/slots_labels.json"""
import argparse, json, os, random, re, sys
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "src")); sys.path.insert(0, os.path.join(ROOT, "eval/writer_ab"))
RUNS = os.path.join(ROOT, "eval/coverage_probe/runs")


def dirs():
    import skeleton_replay as SR
    return [os.path.relpath(os.path.dirname(p), ROOT) for p in SR.DIRS]


def load(d):
    ev = json.load(open(os.path.join(ROOT, d, "evidence.json")))
    mp = os.path.join(ROOT, d, "metrics.json")
    return ev, json.load(open(mp))["query"] if os.path.exists(mp) else "research request"


def run(model):
    import pipeline.run as R
    path = os.path.join(RUNS, "slots_" + re.sub(r"\W+", "_", model) + ".json")
    out = json.load(open(path)) if os.path.exists(path) else {}
    for d in dirs():
        if d in out:
            continue
        ev, q = load(d)
        ans: dict = {}
        R._compute_unanswered(model, q, ev["facets"], ev["evidence"], ans)
        out[d] = ans
        json.dump(out, open(path, "w"), indent=1)
        print(d, {k: len(v) for k, v in ans.items()}, flush=True)


def pools(d, answering):
    from writer import compiled as wc
    ev, q = load(d)
    ev = {**ev, "answering": answering}
    ev.pop("comparison", None)
    from pipeline.run import COMPARISON_RE
    ev["comparison"] = bool(COMPARISON_RE.search(q))
    c = wc.build(ev, q)
    pool, _ = wc._overview_pool(c)
    by = {f.id: [] for f in c.facets}
    for p in pool:
        by[p.facet].append(p.text)
    return c, by


def arms():
    """{(dir, fid): {"name", "without": [texts], model: [texts]}}"""
    res = {}
    for fn in sorted(os.listdir(RUNS)):
        if not (fn.startswith("slots_") and fn.endswith(".json")) or fn.startswith("slots_to_label") or fn.startswith("slots_labels"):
            continue
        m = fn[6:-5]
        for d, ans in json.load(open(os.path.join(RUNS, fn))).items():
            c, w = pools(d, {})
            _, a = pools(d, ans)
            for f in c.facets:
                r = res.setdefault(f"{d}|{f.id}", {"name": f.name, "without": w[f.id]})
                r[m] = a[f.id]
    return res


def to_label():
    random.seed(0)
    items = []
    for k, r in arms().items():
        allq = list(dict.fromkeys(t for key, v in r.items() if key != "name" for t in v))
        random.shuffle(allq)
        items.append({"key": k, "facet": r["name"], "quotes": allq})
    json.dump(items, open(os.path.join(RUNS, "slots_to_label.json"), "w"), ensure_ascii=False, indent=1)
    print(len(items), "facets,", sum(len(x["quotes"]) for x in items), "quotes")


def score():
    lab = json.load(open(os.path.join(RUNS, "slots_labels.json")))  # {key: [answering quote texts]}
    res = arms()
    models = sorted({m for r in res.values() for m in r if m not in ("name", "without")})
    for m in models:
        keys = [k for k in res if m in res[k]]
        hit = lambda k, arm: any(t in lab[k] for t in res[k][arm])
        w, a = sum(hit(k, "without") for k in keys), sum(hit(k, m) for k in keys)
        gained = sum(hit(k, m) and not hit(k, "without") for k in keys)
        lost = sum(hit(k, "without") and not hit(k, m) for k in keys)
        print(m, f"facets {len(keys)} without {w / len(keys):.3f} with {a / len(keys):.3f} gained {gained} lost {lost}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model"); ap.add_argument("--to-label", action="store_true"); ap.add_argument("--score", action="store_true")
    a = ap.parse_args()
    if a.model:
        run(a.model)
    if a.to_label:
        to_label()
    if a.score:
        score()
