"""W4 B-retrieval runner (PLAN_W4.md Amendment 1): production pipeline.run.run() with plan() patched to the saved ornith facets, current vs
hand-written questions, interleaved, 2 runs. GPU + fetching. Repo root:
    DEEPDELVE_FORCE_VIRTUAL_DISPLAY=1 ~/.venvs/deepdelve/bin/python eval/writer_defects/w4_retrieval.py"""
import json, os, subprocess, sys, time
from pathlib import Path
ROOT = os.path.abspath(os.path.dirname(os.path.abspath(__file__)) + "/../..")
sys.path.insert(0, ROOT + "/src")
from pipeline import run as pr  # noqa: E402

MODEL = "ornith-1.5:9b"
QUERY = {"c2": "Does semaglutide reduce alcohol consumption in people with alcohol use disorder, and how does the evidence compare with naltrexone?",
         "c4": "¿Cómo se compara el efecto del impuesto a las bebidas azucaradas en México con el del Reino Unido sobre el consumo de azúcar?"}
BASE = {"c2": "research_output/cmp2_ornith_c2_r1", "c4": "research_output/cmp2_ornith_c4_r1"}
NEW = {"c2": {"f1": ["semaglutide heavy drinking days percent reduction placebo randomized trial", "semaglutide drinks per drinking day reduction placebo trial"],
              "f2": ["naltrexone heavy drinking days percent reduction placebo meta-analysis", "naltrexone drinks per drinking day reduction placebo trial"],
              "f3": ["semaglutide naltrexone heavy drinking days effect size placebo", "naltrexone semaglutide drinks per drinking day placebo trials"]},
       "c4": {"f1": ["reducción compras bebidas azucaradas México impuesto 2014 2015 porcentaje", "reducción azúcar consumida bebidas azucaradas México tras impuesto 2014"],
              "f2": ["reducción compras bebidas azucaradas Reino Unido impuesto 2018 2019 porcentaje", "reducción azúcar consumida bebidas azucaradas Reino Unido tras impuesto 2018"]}}
LOG = Path(ROOT) / "eval/writer_defects/w4_ret_runs.tsv"
SEARCH_LOG = Path(ROOT) / "eval/writer_defects/w4_ret_search.jsonl"
_search = pr.search


def facets(q, arm):
    fs = json.load(open(os.path.join(ROOT, BASE[q], "evidence.json")))["facets"]
    if arm == "new":
        fs = [{**f, "questions": NEW[q][f["id"]]} for f in fs]
    assert all(len(f["questions"]) == 2 for f in fs)
    return fs


def one(q, arm, k, attempt):
    out = Path(ROOT) / f"research_output/w4ret_{q}_{arm}_r{k}" if attempt == 1 else Path(ROOT) / f"research_output/w4ret_{q}_{arm}_r{k}_redo"
    fs = facets(q, arm)
    originals = {qq for f in fs for qq in f["questions"]}
    q0name = {f"{f['questions'][0]} {f['name']}" for f in fs}

    def logged(question, k_, stats=None):
        urls = _search(question, k_, stats)
        origin = "original" if question in originals else "q0+name" if question in q0name else "other"
        with open(SEARCH_LOG, "a") as f:
            f.write(json.dumps({"run": os.path.relpath(out, ROOT), "question": question, "origin": origin, "urls": urls}, ensure_ascii=False) + "\n")
        return urls
    pr.search = logged
    pr.plan = lambda model, query, fs=fs: [dict(f) for f in fs]
    t0 = time.time()
    m = pr.run(QUERY[q], MODEL, out, 3, "ce", True)
    g = m["gather"]
    rel = os.path.relpath(out, ROOT)
    with open(LOG, "a") as f:  # 7 columns like runs.tsv so w4_pairs.py reads it
        f.write(f"{q}\t{arm}\t{k}\t{rel}\t{round(time.time() - t0)}s attempt={attempt}\tsearches={g.get('searches', 0)} empty={g.get('empty_searches', 0)}\t{rel}\n")
    print(q, arm, k, attempt, rel, m["facets_answered"], "/", m["facets"], g, flush=True)
    return g.get("empty_searches", 0) > 0.25 * max(g.get("searches", 0), 1)


def main():
    assert os.environ.get("DEEPDELVE_FORCE_VIRTUAL_DISPLAY") == "1", "set DEEPDELVE_FORCE_VIRTUAL_DISPLAY=1 (headed browser popups)"
    order = [("c2", "current"), ("c2", "new"), ("c4", "current"), ("c4", "new")]
    done = {tuple(line.split("\t")[:3]) for line in LOG.read_text().splitlines()} if LOG.exists() else set()
    try:
        for k in (1, 2):
            for q, arm in (order if k == 1 else order[::-1]):
                if (q, arm, str(k)) in done:
                    continue
                if one(q, arm, k, 1):  # throttled (> 25% empty searches): redo once, both attempts logged
                    one(q, arm, k, 2)
    finally:
        subprocess.run(["ollama", "stop", MODEL], capture_output=True)


if __name__ == "__main__":
    main()
