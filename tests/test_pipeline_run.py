"""Characterization tests for pipeline.run.run() itself (the I/O-driven orchestration: gather rounds, gap rounds,
the one answer-gap re-search round, evidence.json/metrics.json). Until 2026-10-21 only its pure helpers were tested,
which is why run()'s nested closures were left undecomposed (see the comment at the top of run()). Every network/LLM
seam is a module-level name in pipeline.run, so each is replaced here; no model, no network."""
import contextlib
import json
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, "src")
import pipeline.run as pr

URLS = {
    "fi": "https://stats.example.fi/digital",
    "fi2": "https://news.example.fi/eservices",
    "ee": "https://stats.example.ee/digital",
    "dead": "https://dead.example.org/gone",
}
TEXT = {
    URLS["fi"]: "Finland digital service adoption reached 85 percent of citizens in 2023 according to the national statistics office. "
                "The Finnish government publishes yearly figures for online public service usage among adults.",
    URLS["fi2"]: "Finland digital service adoption among citizens grew steadily after the 2019 reform of public eservices. "
                 "Officials in Finland credit strong digital identity coverage for the increase in usage.",
    URLS["ee"]: "Estonia issued more than one hundred thousand eresidency permits to foreign applicants since the programme began in 2014. "
                "The eresidency programme lets foreign entrepreneurs found companies remotely without moving to Estonia.",
}
FACETS = [
    {"id": "f1", "name": "Finland digital service adoption", "entity": "Finland",
     "questions": ["What share of citizens in Finland use digital public services?"]},
    {"id": "f2", "name": "Estonia eresidency permits", "entity": "Estonia",
     "questions": ["How many eresidency permits has Estonia issued to foreign applicants?"]},
]
QUERY = "Compare Finland digital service adoption with Estonia eresidency permits"


class Seams:
    """Records every call made through the patched seams so tests can pin the call pattern, not just the output."""
    def __init__(self, unanswered_first=None, judge_keep_all=True):
        self.searches, self.fetches, self.judges, self.reformulated, self.unans_calls = [], [], [], [], 0
        self.unanswered_first = unanswered_first or {}  # facet id -> question idx list, returned on the FIRST facet_unanswered pass only
        self.judge_keep_all = judge_keep_all
        self.unans_seen = {}

    def plan(self, model, query):
        return json.loads(json.dumps(FACETS))

    def search(self, question, k, stats=None):
        self.searches.append(question)
        if stats is not None:
            stats["searches"] = stats.get("searches", 0) + 1
        if "Finland" in question:
            return [URLS["fi"], URLS["fi2"], URLS["dead"]][:k + 1]
        return [URLS["ee"], URLS["dead"]]

    def fetch(self, url):
        self.fetches.append(url)
        txt = TEXT.get(url)
        return (txt, "Title of " + url.split("/")[2], "2024-03-05") if txt else (None, "", None)

    def judge(self, model, facet, cands, query=""):
        self.judges.append((facet["id"], len(cands)))
        return list(range(len(cands))) if self.judge_keep_all else []

    def facet_unanswered(self, model, query, facet, quotes):
        n = self.unans_seen.get(facet["id"], 0)
        self.unans_seen[facet["id"]] = n + 1
        return self.unanswered_first.get(facet["id"], []) if n == 0 else []

    def scholar_search(self, question, k):  # W8 seam: off by default so no test reaches Europe PMC / OpenAlex
        return []

    def scholar_text(self, rec):
        return None

    def reformulate(self, model, facet_name, question):
        self.reformulated.append(question)
        return ["alt: " + question]


@contextlib.contextmanager
def patched(seams, **extra):
    names = {"plan": seams.plan, "search": seams.search, "fetch": seams.fetch, "judge": seams.judge,
             "facet_unanswered": seams.facet_unanswered, "reformulate_question": seams.reformulate,
             "scholar_search": seams.scholar_search, "scholar_text": seams.scholar_text, **extra}
    old = {k: getattr(pr, k) for k in names}
    try:
        for k, v in names.items():
            setattr(pr, k, v)
        yield
    finally:
        for k, v in old.items():
            setattr(pr, k, v)


def run_once(seams, extractor="bm25", **extra):
    with tempfile.TemporaryDirectory() as td, patched(seams, **extra):
        out = Path(td) / "run"
        metrics = pr.run(QUERY, "fake-model", out, 2, extractor, True)
        ev = json.loads((out / "evidence.json").read_text())
        on_disk = json.loads((out / "metrics.json").read_text())
        srcs = sorted(p.name for p in out.glob("src_*.md"))
    return metrics, ev, on_disk, srcs


def test_bm25_happy_path_files_and_metrics():
    s = Seams()
    m, ev, on_disk, srcs = run_once(s)
    assert m == on_disk, "returned metrics must equal metrics.json"
    assert ev["facets"] == FACETS and set(ev["evidence"]) == {"f1", "f2"}
    # Finland facet got its two real sources, Estonia only one (the other result URL is dead): so the gap round runs for f2 only.
    # (Facet vocabularies are disjoint on purpose: the bm25 extractor pools EVERY fetched URL against EVERY facet with no entity
    # filter, so a shared vocabulary leaks one country's sentences into the other facet. Only the default "ce" path filters by entity.)
    assert set(ev["evidence"]["f1"]) == {URLS["fi"], URLS["fi2"]}, ev["evidence"]["f1"]
    assert set(ev["evidence"]["f2"]) == {URLS["ee"]}, ev["evidence"]["f2"]
    assert len(srcs) == 3, srcs  # one src_*.md per fetched page that had text, none for the dead URL
    assert {"uncovered", "comparison", "dates", "spans"} <= set(ev), sorted(ev)
    assert set(ev["dates"].values()) == {"2024-03-05"}
    assert set(ev["spans"]) == {"f1", "f2"} and m["quotes_total"] >= m["quotes_located"] > 0
    assert m["sources_fetched"] == 3 and m["gather"]["fetch_fail"] >= 1 and m["gather"]["fetch_ok"] == 3
    assert m["facets"] == 2 and m["facets_covered_2src"] == 1 and m["coverage"] == 0.5
    assert m["sources_per_facet"] == {"f1": 2, "f2": 1}
    assert 0 < m["quotes_kept"] <= m["quotes_emitted"] and m["quote_match_rate"] == m["quotes_kept"] / m["quotes_emitted"]
    assert ev["titles"][URLS["fi"]] == "Title of stats.example.fi"
    for k in ("wall_s", "funnel", "gather", "extract_calls", "extract_failed", "facets_answered", "unanswered_facets"):
        assert k in m, k


def test_every_quote_is_literal_source_text():
    s = Seams()
    _, ev, _, _ = run_once(s)
    for per_url in ev["evidence"].values():
        for u, quotes in per_url.items():
            for q in quotes:
                assert pr.quote_in_source(q, TEXT[u]), (u, q)


def test_gap_rounds_only_requery_facets_with_fewer_than_two_sources():
    s = Seams()
    run_once(s)
    # round 1: every facet question + the "question name" doubled variant (2 per facet, 2 facets = 4 searches).
    # gap rounds (<=2): only f2 is short, and a gap round never re-queries f1.
    assert len(s.searches) >= 5, s.searches
    gap_queries = s.searches[4:]
    assert gap_queries and all("Estonia" in q for q in gap_queries), gap_queries
    assert len(gap_queries) <= 2, "at most 2 gap rounds, one question each"


def test_no_gaps_means_no_extra_rounds():
    s = Seams()
    # make every facet reach 2 sources in round 1 by giving Estonia a second live page
    TEXT_EXTRA = "https://extra.example.ee/more"
    TEXT[TEXT_EXTRA] = "Estonia eresidency permits are held by entrepreneurs from more than one hundred countries worldwide. " \
                       "Foreign applicants for Estonia eresidency permits must collect their digital identity card in person."
    orig = s.search
    def search(q, k, stats=None):
        if "Estonia" in q:
            s.searches.append(q)
            return [URLS["ee"], TEXT_EXTRA]
        return orig(q, k, stats)
    s.search = search
    try:
        m, _, _, _ = run_once(s)
    finally:
        del TEXT[TEXT_EXTRA]
    assert m["facets_covered_2src"] == 2 and len(s.searches) == 4, s.searches


def test_answer_gap_round_runs_once_and_carries_final_state():
    s = Seams(unanswered_first={"f1": [0]})
    m, ev, _, _ = run_once(s)
    assert s.reformulated == [FACETS[0]["questions"][0]], s.reformulated  # only the unanswered question is reformulated
    assert any(q.startswith("alt:") for q in s.searches), "the re-search round must use the reformulated query"
    assert s.unans_seen == {"f1": 2, "f2": 2}, s.unans_seen  # one pass before the re-search, one after, never more
    assert m["unanswered_facets"] == {} and m["facets_answered"] == 2 and ev["unanswered_facets"] == {}


def test_still_unanswered_after_re_search_is_disclosed():
    s = Seams()
    s.facet_unanswered = lambda model, query, facet, quotes: [0] if facet["id"] == "f1" else []
    # Seams.facet_unanswered is bound at patch time via names dict, so rebuild with an override instead:
    with tempfile.TemporaryDirectory() as td, patched(s, facet_unanswered=s.facet_unanswered):
        out = Path(td) / "run"
        m = pr.run(QUERY, "fake-model", out, 2, "bm25", True)
        ev = json.loads((out / "evidence.json").read_text())
    assert m["unanswered_facets"] == {"f1": [FACETS[0]["questions"][0]]}, m["unanswered_facets"]
    assert m["facets_answered"] == 1 and ev["unanswered_facets"] == m["unanswered_facets"]


def test_failed_judge_keeps_nothing_on_bm25_path():
    s = Seams()
    s.judge = lambda model, facet, cands, query="": None
    with tempfile.TemporaryDirectory() as td, patched(s, judge=s.judge):
        m = pr.run(QUERY, "fake-model", Path(td) / "run", 2, "bm25", True)
    assert m["quotes_kept"] == 0 and m["facets_covered_2src"] == 0 and m["quote_match_rate"] == 0.0


def test_llm_extractor_drops_quotes_not_in_source_and_unknown_facets():
    s = Seams()
    good = TEXT[URLS["fi"]].split(". ")[0] + "."

    def call_json(model, prompt, schema, **kw):
        return {"spans": [{"facet_id": "f1", "quote": good},
                          {"facet_id": "f1", "quote": "A sentence the page never contained anywhere at all."},
                          {"facet_id": "zz", "quote": good}]}
    m, ev, _, _ = run_once(s, "llm", call_json=call_json)
    assert m["extractor"] == "llm" and m["quotes_emitted"] >= 3
    for u, quotes in ev["evidence"]["f1"].items():
        assert all(pr.quote_in_source(q, TEXT[u]) for q in quotes)
    assert "zz" not in ev["evidence"], "a span for an unknown facet id must be ignored"
    assert m["quotes_kept"] < m["quotes_emitted"]


def test_ce_extractor_runs_with_stub_scorer_and_records_funnel():
    s = Seams()
    stub = types.ModuleType("pipeline.tune")
    stub.ce_score = lambda q, t, model: 1.0
    saved = sys.modules.get("pipeline.tune")
    sys.modules["pipeline.tune"] = stub
    try:
        m, ev, _, _ = run_once(s, "ce")
    finally:
        if saved is None:
            del sys.modules["pipeline.tune"]
        else:
            sys.modules["pipeline.tune"] = saved
    assert m["extractor"] == "ce" and set(m["funnel"]) <= {"f1", "f2"} and m["funnel"], m["funnel"]
    assert m["quotes_kept"] >= 1 and any(ev["evidence"][f] for f in ev["evidence"])
    for fid, st in m["funnel"].items():
        assert st["kept"] <= st["juris"] <= st["entity"] <= st["ce_pos"] <= st["cands"] or st["kept"] <= st["cands"], (fid, st)


def test_plan_covered_replans_for_dropped_entity_and_writer_discloses_the_rest():
    """h25 class: a named entity with no facet triggers one re-plan; a plan that still misses it reaches the report as a Not covered line."""
    import pipeline.run as R
    from writer import compiled as wc
    q = "What were Portugal's, Vietnam's and Ghana's fertility rates?"
    f = lambda *cs: [{"id": f"f{i}", "name": f"{c} fertility rate", "entity": c, "questions": [f"{c} fertility rate"]} for i, c in enumerate(cs)]
    calls, real = [], R._plan
    try:
        R._plan = lambda m, qq, extra="": (calls.append(extra), f("Portugal", "Vietnam") if len(calls) == 1 else f("Portugal", "Vietnam", "Ghana"))[1]
        assert [x["entity"] for x in R._plan_covered("m", q)] == ["Portugal", "Vietnam", "Ghana"] and "ghana" in calls[1], calls
        calls.clear()
        R._plan = lambda m, qq, extra="": (calls.append(extra), f("Portugal", "Vietnam"))[1]
        facets = R._plan_covered("m", q)
        assert len(calls) == 2 and R._uncovered(facets, q) == ["ghana"]
    finally:
        R._plan = real
    c = wc.Compiled(q, [wc.Facet("f0", "Portugal fertility rate", [wc.Quote(1, "Portugal's fertility rate is 1.41.", "f0")])], [(1, "https://a.example", "A")],
                    uncovered=R._uncovered(facets, q))
    assert "No facet covered these words of the request: ghana." in wc.render(c, {})


def test_scholar_round_one_only_and_sources():
    # W8 (eval/writer_defects/PLAN_W8.md): one scholarly search per facet in round 1, records become sources, failures never stop the run
    calls = []
    rec_oa = {"doi": "10.1/oa", "pmcid": "PMC1", "oa": True, "abstract": "x", "title": "Open paper", "authors": "A, B, C, D", "journal": "J", "year": 2020}
    rec_abs = {"doi": "10.1/abs", "pmcid": "", "oa": False, "abstract": "Finland digital service adoption reached 82 percent of citizens in 2023 according to the survey.",
               "title": "Paywalled paper", "authors": "E", "journal": "K", "year": 2021}
    rec_none = {"doi": "", "pmcid": "", "oa": False, "abstract": "orphan abstract text", "title": "No id"}

    def ss(q, k):
        calls.append(q)
        return [rec_oa, rec_abs, rec_none]
    real_text = pr.scholar_text
    import tools.scholar as ts
    old_full = ts._full_text_epmc
    ts._full_text_epmc = lambda pmcid: "# Open paper\n\nEstonia eresidency permits issued to foreign applicants reached 100,000 by 2024 in the register."
    try:
        m, ev, disk, srcs = run_once(Seams(), "bm25", scholar_search=ss, scholar_text=real_text)
        assert real_text(rec_oa)[2].startswith("A, B, C et al. Open paper. J")
        t = real_text(rec_abs)[1]
        assert real_text(rec_none) is None
    finally:
        ts._full_text_epmc = old_full
    assert len(calls) == 2, calls                                         # round 1 only, one per facet (2 facets)
    g = m["gather"]
    assert g["scholar_searches"] == 2 and g["scholar_sources"] == 2 and g["scholar_fulltext"] == 1, g  # no-id record skipped, dup URLs once
    assert "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC1/" in ev["titles"] or any("PMC1" in u for u in ev["titles"]), ev["titles"].keys()
    assert "https://doi.org/10.1/abs" in ev["titles"] and ev["titles"]["https://doi.org/10.1/abs"] == "E. Paywalled paper. K"
    assert t.startswith("Content-Level: ABSTRACT ONLY\nDOI: 10.1/abs")
    assert all("Content-Level" not in x and "Paywalled paper" not in x for x in pr.sentences(t)), pr.sentences(t)  # header/title never quoted


def test_scholar_same_paper_kept_once():
    # the 2026-11-22 A/B: Taillie 2020 cited twice (doi.org abstract + journals.plos.org page). Same paper by DOI or PMCID in the URL -> once.
    assert pr.same_paper("https://journals.plos.org/plosmedicine/article?id=10.1371/journal.pmed.1003015", {"10.1371/journal.pmed.1003015"})
    assert pr.same_paper("https://pmc.ncbi.nlm.nih.gov/articles/PMC13262067/", {"10.5888/pcd23.250422", "pmc13262067"})
    assert pr.same_paper("https://link.springer.com/article/10.1186%2Fs12966-024-01609-3", {"10.1186/s12966-024-01609-3"})
    assert not pr.same_paper("https://news.example/story-about-10-1371", {"10.1371/journal.pmed.1003015"})
    rec = {"doi": "10.1/web", "pmcid": "", "oa": False, "abstract": "x", "title": "T", "authors": "A", "journal": "J", "year": 2020}
    s = Seams()
    web = "https://journal.example/article/10.1/web"
    s.search = lambda q, k, stats=None: [web]
    s.fetch = lambda u: ("Finland digital service adoption reached 82 percent of citizens in 2023 according to the survey.", "Web copy", None)
    m, ev, disk, srcs = run_once(s, "bm25", scholar_search=lambda q, k: [rec], scholar_text=pr.scholar_text)
    assert m["gather"].get("scholar_sources", 0) == 0 and m["gather"]["scholar_dup"] >= 1, m["gather"]   # web copy kept, abstract skipped
    assert "https://doi.org/10.1/web" not in ev["titles"], ev["titles"].keys()


def test_scholar_failure_keeps_run():
    def boom(q, k):
        raise RuntimeError("api down")
    m, ev, disk, srcs = run_once(Seams(), "bm25", scholar_search=boom)
    assert m["gather"]["scholar_fail"] == 2 and m["gather"]["scholar_searches"] == 2 and m["sources_fetched"] == 3, m["gather"]


if __name__ == "__main__":
    _tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    assert _tests, "no tests found"
    for _n, _f in _tests:
        _f()
    print(f"test_pipeline_run OK ({len(_tests)} tests)")
