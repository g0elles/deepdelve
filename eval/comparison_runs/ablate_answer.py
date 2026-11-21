"""Ablation: does ornith's card sampling cause the drop in facets judged answered (v1 12/16 -> v2 7/18)?
Replays ONLY pipeline.run._compute_unanswered (the answer judge) on the saved evidence of all 12 ornith dirs (cmp_ = v1, cmp2_ = v2),
so evidence is held fixed and sampling is the only variable. Arms: v1 (no model_options: num_ctx 16384, T 0), card (settings.model_options),
card_nopres (card without presence_penalty). Random arms x3 reps. Repo root: python eval/comparison_runs/ablate_answer.py -> ablate_answer.jsonl"""
import json, os, sys
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
import config  # noqa: E402
from pipeline import run as R  # noqa: E402
M = "ornith-1.5:9b"
CARD = dict(config.cfg["settings"]["model_options"][M])
ARMS = [("v1", None, 1), ("card", CARD, 3), ("card_nopres", {k: v for k, v in CARD.items() if k != "presence_penalty"}, 3)]
DIRS = [f"research_output/{p}_ornith_{c}_r{r}" for p in ("cmp", "cmp2") for c in ("c2", "c3", "c4") for r in (1, 2)]
out = open(os.path.join(ROOT, "eval/comparison_runs/ablate_answer.jsonl"), "a")
tot = {}
for arm, opts, reps in ARMS:   # arm outermost: one model load per arm (num_ctx differs between arms)
    config.cfg["settings"]["model_options"] = {M: opts} if opts else {}
    for d in DIRS:
        ev = json.load(open(os.path.join(ROOT, d, "evidence.json")))
        query = json.load(open(os.path.join(ROOT, d, "metrics.json")))["query"]
        nq = sum(len(f["questions"]) for f in ev["facets"])
        for rep in range(reps):
            un = R._compute_unanswered(M, query, ev["facets"], ev["evidence"])
            n_un = sum(len(v) for v in un.values())
            fac_ans = sum(1 for f in ev["facets"] if f["id"] not in un)
            out.write(json.dumps({"arm": arm, "dir": d, "rep": rep, "questions": nq, "unanswered_q": n_un, "facets": len(ev["facets"]),
                                  "facets_answered": fac_ans, "unanswered": un}) + "\n"); out.flush()
            t = tot.setdefault(arm, [0, 0, 0, 0]); t[0] += nq - n_un; t[1] += nq; t[2] += fac_ans; t[3] += len(ev["facets"])
    print(f"{arm}: questions answered {tot[arm][0]}/{tot[arm][1]} = {tot[arm][0]/tot[arm][1]:.2f}, facets answered {tot[arm][2]}/{tot[arm][3]}", flush=True)
