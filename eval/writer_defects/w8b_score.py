"""W8b scores (PLAN_W8b.md "Measures"), written before any W8b run. (a) distinct peer-reviewed papers cited, (c) unsupported figures,
(d) pipeline seconds, (e) G-report scholarly sources listed with their citing sentences for the on-topic rule.
    ~/.venvs/deepdelve/bin/python eval/writer_defects/w8b_score.py"""
import functools, hashlib, json, os, re, statistics, sys
from math import comb
from urllib.parse import unquote, urlparse
ROOT = os.path.abspath(os.path.dirname(os.path.abspath(__file__)) + "/../..")
sys.path[:0] = [ROOT + "/scripts", ROOT + "/src", os.path.dirname(os.path.abspath(__file__))]
import evaluate_run as er  # noqa: E402
import w8_score  # noqa: E402

PEER = tuple(h for h in w8_score.PEER if h != "doi.org") + (
    "ehp.niehs.nih.gov", "neurology.org", "atsjournals.org", "jcsm.aasm.org", "ersjournals.com", "ahajournals.org", "pnas.org",
    "science.org", "aeaweb.org")
PREPRINT = re.compile(r"biorxiv|medrxiv|research square|ssrn|preprints|arxiv", re.I)
DOI = re.compile(r"10\.\d{4,9}/[^\s\"'<>)\]|?#&]+", re.I)
S_Q = ("acad", "c2", "vitd", "cbti", "pm25", "minwage")


def src_text(ev, u):
    try:
        return open(f"{ev}/src_{hashlib.sha1(u.encode()).hexdigest()[:8]}.md", errors="ignore").read()
    except OSError:
        return ""


def peer(u, ev, titles):
    h = urlparse(u).netloc.lower()
    if "doi.org" in h:  # only a scholarly source whose record names a non-preprint journal
        journal = (titles.get(u) or "").rsplit(". ", 1)[-1] if ". " in (titles.get(u) or "") else ""
        return src_text(ev, u).startswith("Content-Level:") and bool(journal) and not PREPRINT.search(journal)
    return any(p in h for p in PEER)


@functools.lru_cache(maxsize=None)
def pmc_doi(pmcid):
    from tools.scholar import search_europepmc
    try:
        recs = search_europepmc(f"PMCID:{pmcid}", 1)
        return (recs[0].get("doi") or "").lower() or None if recs else None
    except Exception:
        return None


def key(u):
    m = DOI.search(unquote(u))
    if m:
        return m.group(0).lower().rstrip(".")
    m = re.search(r"(PMC\d+)", u, re.I)
    if m:
        return pmc_doi(m.group(1).upper()) or m.group(1).upper()
    return u


def body_and_cited(rep):
    m = re.search(r"^#{1,3} (?:Sources|References)\b", rep, re.M)
    if not m:
        return rep, {}
    body, lst = rep[:m.start()], rep[m.end():]
    used = set(re.findall(r"\[(\d+)\]", body))
    return body, {n: u for n, u in re.findall(r"^\s*(\d+)\.\s.*?\((https?://[^)\s]+)\)", lst, re.M) if n in used}


def score(row):
    q, arm, k, ev, rep = row[:5]
    e = json.load(open(os.path.join(ROOT, ev, "evidence.json")))
    text = er._strip_notes(open(os.path.join(ROOT, rep, "final_report.md")).read())
    body, cited = body_and_cited(text)
    papers = {key(u) for u in cited.values() if peer(u, os.path.join(ROOT, ev), e.get("titles", {}))}
    sch = [(n, u) for n, u in cited.items() if src_text(os.path.join(ROOT, ev), u).startswith("Content-Level:")]
    sents = [(n, u, [s for s in re.split(r"(?<=[.!?])\s+", body) if f"[{n}]" in s]) for n, u in sch]
    return {"papers": len(papers), "figs": len(er.score(os.path.join(ROOT, rep))["figs_unsupported"] or []), "secs": int(row[5]),
            "scholarly_cited": sents}


def sign_p(wins, n):
    return sum(comb(n, i) for i in range(wins, n + 1)) / 2 ** n if n else 1.0


def main():
    rows = [ln.split("\t") for ln in open(os.path.join(ROOT, "eval/writer_defects/w8b_runs.tsv")).read().splitlines()]
    sc = {tuple(r[:3]): score(r) for r in rows if r[4]}
    for r in rows:
        if not r[4]:
            print("NO REPORT", r[:3])
    print("q\tk\tpapers ON\tOFF\tfigs ON\tOFF\tsecs ON\tOFF")
    diffs, aa = [], []
    for q in dict.fromkeys(r[0] for r in rows):
        for k in ("1", "2"):
            on, off = sc.get((q, "on", k)), sc.get((q, "off", k))
            if on and off:
                print(q, k, on["papers"], off["papers"], on["figs"], off["figs"], on["secs"], off["secs"], sep="\t")
                if q in S_Q:
                    diffs.append(on["papers"] - off["papers"])
        a, b = sc.get((q, "aa1", "3")), sc.get((q, "aa2", "3"))
        if a and b:
            aa.append(abs(a["papers"] - b["papers"]))
            print(q, "A/A", a["papers"], b["papers"], sep="\t")
    untied = [d for d in diffs if d]
    wins = sum(d > 0 for d in untied)
    print(f"S pairs {len(diffs)}, untied {len(untied)}, ON wins {wins}, sign-test p={sign_p(wins, len(untied)):.4f}; "
          f"median diff {statistics.median(diffs) if diffs else 'n/a'} vs max A/A {max(aa) if aa else 'n/a'}")
    on_s = [v["secs"] for (q, a, k), v in sc.items() if a == "on"]
    off_s = [v["secs"] for (q, a, k), v in sc.items() if a == "off"]
    if on_s and off_s:
        print(f"median secs ON {statistics.median(on_s)} OFF {statistics.median(off_s)}")
    print("figures unsupported in ON reports:", sum(v["figs"] for (q, a, k), v in sc.items() if a == "on"))
    for (q, a, k), v in sc.items():
        if q in ("leave", "heat") and v["scholarly_cited"]:
            for n, u, ss in v["scholarly_cited"]:
                print(f"G-check {q} {a} r{k} [{n}] {u}\n    " + "\n    ".join(s[:300] for s in ss))


if __name__ == "__main__":
    main()
