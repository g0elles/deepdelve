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
        return (txt, "Title of " + url.split("/")[2]) if txt else (None, "")

    def judge(self, model, facet, cands):
        self.judges.append((facet["id"], len(cands)))
        return list(range(len(cands))) if self.judge_keep_all else []

    def facet_unanswered(self, model, query, facet, quotes):
        n = self.unans_seen.get(facet["id"], 0)
        self.unans_seen[facet["id"]] = n + 1
        return self.unanswered_first.get(facet["id"], []) if n == 0 else []

    def reformulate(self, model, facet_name, question):
        self.reformulated.append(question)
        return ["alt: " + question]


@contextlib.contextmanager
def patched(seams, **extra):
    names = {"plan": seams.plan, "search": seams.search, "fetch": seams.fetch, "judge": seams.judge,
             "facet_unanswered": seams.facet_unanswered, "reformulate_question": seams.reformulate, **extra}
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
    s.judge = lambda model, facet, cands: None
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


if __name__ == "__main__":
    _tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    assert _tests, "no tests found"
    for _n, _f in _tests:
        _f()
    print(f"test_pipeline_run OK ({len(_tests)} tests)")
