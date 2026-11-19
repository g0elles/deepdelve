"""Pipeline plan coverage (named entities in _uncovered), quote locators (norm_map, quote_span) and fetch date normalisation."""
import sys

sys.path.insert(0, "src")
from pipeline import run as pr  # noqa: E402


def test_uncovered_keeps_entities():
    facets = [{"id": "f1", "name": "inflation", "questions": ["inflation rate"]}]
    assert "kenya" in pr._uncovered(facets, "inflation in Kenya")


def test_plan_covered_keeps_first_when_replan_worse():
    first = [{"id": "f1", "name": "inflation in Kenya", "questions": []}]
    worse = [{"id": "f1", "name": "inflation", "questions": []}]
    calls = []
    old = pr._plan
    pr._plan = lambda model, query, hint="": calls.append(hint) or (first if len(calls) == 1 else worse)
    try:
        assert pr._plan_covered("m", "inflation in Kenya and Ghana") == first
    finally:
        pr._plan = old
    assert len(calls) == 2 and "ghana" in calls[1]  # the re-plan names what the first plan missed


def test_norm_map_equals_norm():
    for s in ["see [the report](https://x.org/a \"t\") here", "growth[[3]](#cite_note-4) was high",
              "Straße und Weg", "a -- b,, c!!! (d)", "  padded text  "]:
        t, m = pr.norm_map(s)
        assert t == pr.norm(s), (s, t)
        assert len(m) == len(t)


def test_quote_span_offsets():
    src = "Intro. Emissions fell by [twelve percent](https://e.org) in 2023 across the sector. End."
    q = "Emissions fell by twelve percent in 2023 across the sector"
    s, e = pr.quote_span(q, src)
    assert pr.norm(src[s:e]) == pr.norm(q)
    assert pr.quote_span("this sentence is nowhere in the page", src) is None
    assert pr.quote_span("too short", src) is None


def test_fetch_date_normalisation():
    import tools.web as web
    old = web._fetch_raw, web._stub_reason
    for pub, want in [("2024-03-05T10:00:00Z", "2024-03-05"), ("March 2024", None)]:
        web._fetch_raw = lambda url, b, pub=pub: ("page text " * 50, None, {"title": "T", "published": pub})
        web._stub_reason = lambda d: None
        try:
            assert pr.fetch("https://x.org") == ("page text " * 50, "T", want)
        finally:
            web._fetch_raw, web._stub_reason = old



def test_comparison_flag_is_whole_word_and_not_for_and_against():
    from pipeline.run import COMPARISON_RE
    assert COMPARISON_RE.search("Compare Norway's and the Netherlands' EV incentives")
    assert COMPARISON_RE.search("How do Raft and Paxos differ in leader election?")
    assert COMPARISON_RE.search("heat pumps versus gas furnaces") and COMPARISON_RE.search("Team A against Team B")
    assert not COMPARISON_RE.search("What does the evidence say for and against congestion pricing?")   # h18
    assert not COMPARISON_RE.search("different approaches to pensions")

if __name__ == "__main__":
    _tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    assert _tests, "no tests found"
    for _n, _f in _tests:
        _f()
    print(f"test_pipeline_plan_coverage OK ({len(_tests)} tests)")
