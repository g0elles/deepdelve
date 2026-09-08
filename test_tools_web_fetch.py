import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from utils.run_state import record_fetched_url, reset_fetched_urls

# noqa: F401 -- common test-infra names available to every split file below, regardless of
# whether a given file's own retained sections happen to use all of them (ruff prunes genuinely
# unused ones per file). Split 2026-09-07 out of the former single 10,701-line
# test_structural_checks.py (session_status/CURRENT.md carried-forward TODO) -- see
# test_structural_checks.py's own new header for the full split rationale and the file-to-topic
# map. Pure move: every assertion below is byte-identical to its prior body, just regrouped by
# topic into its own main(), all still called in original order from the new thin
# test_structural_checks.py orchestrator.
import config as _config
import contextvars

from tools.web import fetch_url_to_workspace, _slugify_for_filename, _save_fetched, _fetched_filename
from tools.fs import _IN_MEMORY_FS

# Literal test fixtures reused across many topic files below (constants, not code) --
# hoisting these verbatim into every file's header is simpler than a separate shared-fixtures
# module for 4 short constants.
_SRC = "https://gov.example.co/page"
_STUB_SRC = "https://news.example.co/paywalled-article"
_SOURCE_TEXT = (
    "Source-URL: " + _SRC + "\n\n"
    "El pais avanza de forma sostenida segun cifras oficiales del gobierno, con datos "
    "verificados por organismos independientes."
)
_FINDINGS_OK = f"- hallado ({_SRC})"

def main():
    # --- fetched files live under sources/ and carry their true URL as line 1 ---
    assert _fetched_filename("foo") == "sources/foo.md"
    assert _fetched_filename("sources/foo.md") == "sources/foo.md"

    # --- fetch_url_to_workspace's filename is optional with an auto-derived default (2026-07-12):
    # confirmed live, 5 separate calls across today's benchmark runs omitted `filename` entirely.
    # Since a missing REQUIRED field is rejected by schema validation before the function body
    # ever runs, there was no way to recover it defensively inside the function -- the call was
    # just lost. Pin that the default actually exists (not just "happens to work by luck") and
    # that the slugify helper it falls back to produces a sane, deterministic, non-empty name. ---
    import inspect
    _fetch_sig = inspect.signature(fetch_url_to_workspace.func if hasattr(fetch_url_to_workspace, "func") else fetch_url_to_workspace)
    assert _fetch_sig.parameters["filename"].default == "", (
        "fetch_url_to_workspace's filename must default to '' (auto-derive), not be required")
    _slug1 = _slugify_for_filename("https://example.com/some/page", "")
    _slug2 = _slugify_for_filename("https://example.com/some/page", "")
    _slug3 = _slugify_for_filename("https://different.com/other", "")
    assert _slug1 and _slug1 == _slug2, "must be deterministic for the same URL"
    assert _slug1 != _slug3, "must differ for a different URL"

    # --- fetch_url_to_workspace rejects a malformed URL containing internal whitespace
    # (2026-08-17, live incident, session_status 2026-08-16 item 3 follow-up): a model tool-call
    # mangled a URL with an embedded space ("...-in-port Portugal" instead of "...-in-portugal").
    # This used to fetch successfully (a 404/stub, no exception) and get stored VERBATIM as
    # "ground truth" -- but extract_cited_urls' citation regex correctly stops at whitespace, so
    # any citation to it forever extracts as a shorter, non-matching prefix: UNGROUNDABLE BY
    # CONSTRUCTION, no matter how FindingsWriter rewords the citation. Confirmed live: this burned
    # 4+ rebuild attempts and nearly the whole run's retry budget on an unwinnable rewrite loop.
    # Must now be rejected before it's ever fetched or recorded, as an immediate, recoverable tool
    # error the model can retry with a clean URL. Tested against the pure helper, not the full
    # network-fetching tool function -- no real HTTP call belongs in this suite. ---
    from tools.web import _reject_malformed_url
    _clean, _err = _reject_malformed_url("https://example.com/post/real-page Extra")
    assert _err and "Malformed URL" in _err and "whitespace" in _err, _err
    # Leading/trailing whitespace (common, harmless) must still be accepted after stripping --
    # only INTERNAL whitespace is a real malformation.
    _clean2, _err2 = _reject_malformed_url("  https://example.com/some/page  ")
    assert _err2 is None and _clean2 == "https://example.com/some/page", (_clean2, _err2)
    _orig_ws = _config.cfg.get("settings", {}).get("workspace")
    _config.cfg.setdefault("settings", {})["workspace"] = {"type": "memory"}
    try:
        reset_fetched_urls()
        _save_fetched(["https://example.com/page"], "foo", "body text")
        assert _IN_MEMORY_FS["sources/foo.md"].startswith("Source-URL: https://example.com/page\n\n")
        from utils.run_state import get_fetched_urls
        assert get_fetched_urls()[0]["filename"] == "sources/foo.md"

        # --- fetch-time metadata extraction (2026-07-12): Title:/Authors:/Published: headers,
        # written only for fields actually present, replacing the "Extract title/authors/abstract"
        # sub-agent-dispatch pattern that recurred identically across multiple live benchmark runs.
        reset_fetched_urls()
        _save_fetched(["https://example.com/paper"], "bar", "body text", metadata={
            "title": "A Real Paper Title", "author": "Jane Doe", "published": "2026-01-15"})
        assert _IN_MEMORY_FS["sources/bar.md"] == (
            "Source-URL: https://example.com/paper\n"
            "Title: A Real Paper Title\n"
            "Authors: Jane Doe\n"
            "Published: 2026-01-15\n"
            "\nbody text"
        ), _IN_MEMORY_FS["sources/bar.md"]
        # 2026-07-21: the extracted title must also reach get_fetched_urls() (previously only
        # written into the saved file's own header, never surfaced to
        # _build_findings_source_material's evidence base).
        assert get_fetched_urls()[-1]["title"] == "A Real Paper Title", get_fetched_urls()[-1]

        # Partial metadata (only title known) -> only that one extra header line, no blank/guessed
        # Authors:/Published: lines for fields extraction didn't find.
        reset_fetched_urls()
        _save_fetched(["https://example.com/partial"], "baz", "body text", metadata={"title": "Only Title Known"})
        assert _IN_MEMORY_FS["sources/baz.md"] == (
            "Source-URL: https://example.com/partial\nTitle: Only Title Known\n\nbody text"
        ), _IN_MEMORY_FS["sources/baz.md"]

        # No metadata at all (PDF/plain-text path, or extraction found nothing) -> unchanged
        # single-line header, exactly today's pre-existing shape.
        reset_fetched_urls()
        _save_fetched(["https://example.com/none"], "qux", "body text", metadata={})
        assert _IN_MEMORY_FS["sources/qux.md"] == "Source-URL: https://example.com/none\n\nbody text"
        # No title extracted -> key absent entirely, same "absent when not present" convention as
        # stub, so a pre-existing _run_state.json / any entry.get("title") reader stays compatible.
        assert "title" not in get_fetched_urls()[-1], get_fetched_urls()[-1]

        # --- fetch_url_to_workspace cross-agent dedup (2026-07-23): confirmed live -- 4
        # different sub-agents independently fetched the exact same URL under 4 different
        # slugified filenames in one run. Must short-circuit BEFORE attempting a real network
        # fetch once get_fetched_urls() already has that exact URL, regardless of which
        # sub-agent/filename originally fetched it. ---
        import asyncio as _asyncio13
        from tools.core import tool_quotas_ctx as _q_ctx13
        record_fetched_url("https://example.com/dup", filename="sources/already_here.md")
        _q_ctx13.set({"fetch_url_to_workspace": {"used": 0, "limit": 5}})
        _dup_result = _asyncio13.run(fetch_url_to_workspace.func(
            url="https://example.com/dup", filename="whatever_new_name"))
        assert "already_here.md" in _dup_result, _dup_result
        assert "already fetched" in _dup_result.lower(), _dup_result
        # A genuinely different URL must not match (no real network call needed to prove this --
        # the dedup loop itself is a pure comparison against get_fetched_urls()).
        assert not any(e.get("url") == "https://example.com/genuinely-new" for e in get_fetched_urls())

        # --- fetch_url_to_workspace per-task fetch cap (2026-08-01, RESEARCH.md Sec.15): once
        # task_fetched_urls_ctx (THIS task's own real fetches) is at the configured cap, a call for
        # a genuinely NEW url must be rejected outright, no real network fetch attempted -- proven
        # here by using a URL that was never dedup-registered (would raise if it actually tried to
        # fetch it, since no network mock is set up in this scenario). A dedup hit (tested above)
        # must never count against this cap -- it's checked first and returns before this gate. ---
        from utils.run_state import task_fetched_urls_ctx as _task_fetched_urls_ctx13
        _orig_cap13 = _config.cfg.get("settings", {}).get("specialist_fetch_cap")
        _config.cfg["settings"]["specialist_fetch_cap"] = 2
        try:
            _task_fetched_urls_ctx13.set([{"url": "https://example.com/one"}, {"url": "https://example.com/two"}])
            _cap_result = _asyncio13.run(fetch_url_to_workspace.func(
                url="https://example.com/genuinely-uncached-and-unfetched"))
            assert "rejected" in _cap_result.lower() and "cap" in _cap_result.lower(), _cap_result
            assert not any(
                e.get("url") == "https://example.com/genuinely-uncached-and-unfetched"
                for e in get_fetched_urls()
            ), "a rejected call must never actually record a fetch"
            # Below the cap -> still rejected requires no network call to prove EITHER way here, so
            # just confirm the pure predicate agrees with the wired-up behavior above.
            from tools.web import _specialist_fetch_over_cap as _cap_fn13
            assert _cap_fn13(current_count=2, cap=2)
            assert not _cap_fn13(current_count=1, cap=2)
        finally:
            _task_fetched_urls_ctx13.set(None)
            if _orig_cap13 is None:
                _config.cfg["settings"].pop("specialist_fetch_cap", None)
            else:
                _config.cfg["settings"]["specialist_fetch_cap"] = _orig_cap13
    finally:
        if _orig_ws is None:
            _config.cfg["settings"].pop("workspace", None)
        else:
            _config.cfg["settings"]["workspace"] = _orig_ws
        reset_fetched_urls()

    # --- fetch_url_to_workspace's in-turn repetition guard (2026-08-20, ROADMAP "finer-grained
    # repetition guard"): a task re-requesting a URL it (or a sibling) already fetched gets a
    # plain redirect the first time, an escalated "stop repeating this" message from the SECOND
    # redirect onward -- scoped per-task (task_dedup_fetch_repeat_ctx) so an unrelated task's own
    # first dedup hit on a DIFFERENT already-fetched URL never escalates. ---
    from utils.run_state import task_dedup_fetch_repeat_ctx as _dedup_repeat_ctx14
    _q_ctx14 = _q_ctx13
    _orig_ws14 = _config.cfg.get("settings", {}).get("workspace")
    _config.cfg.setdefault("settings", {})["workspace"] = {"type": "memory"}
    try:
        reset_fetched_urls()
        record_fetched_url("https://example.com/repeat", filename="sources/repeat.md")
        _q_ctx14.set({"fetch_url_to_workspace": {"used": 0, "limit": 15}})
        _dedup_repeat_ctx14.set({})
        _first14 = _asyncio13.run(fetch_url_to_workspace.func(url="https://example.com/repeat"))
        assert "already fetched" in _first14.lower(), _first14
        assert "You have now been told" not in _first14, _first14
        _second14 = _asyncio13.run(fetch_url_to_workspace.func(url="https://example.com/repeat"))
        assert "You have now been told" in _second14, _second14
        # A DIFFERENT already-fetched URL, same task's own repeat-count dict, must NOT inherit
        # the first URL's escalated count.
        record_fetched_url("https://example.com/repeat-other", filename="sources/other.md")
        _other14 = _asyncio13.run(fetch_url_to_workspace.func(url="https://example.com/repeat-other"))
        assert "You have now been told" not in _other14, _other14
    finally:
        _dedup_repeat_ctx14.set(None)
        _q_ctx14.set(None)
        if _orig_ws14 is None:
            _config.cfg["settings"].pop("workspace", None)
        else:
            _config.cfg["settings"]["workspace"] = _orig_ws14
        reset_fetched_urls()

    # --- read_workspace_file/grep_workspace_file per-dispatch analyzer_read_cap (2026-08-01,
    # RESEARCH.md Sec.16): once task_read_grep_count_ctx (THIS dispatch's own combined read+grep
    # call count) is at the configured cap, a call for either tool must be rejected outright, no
    # real read/grep attempted. The two tools share ONE counter -- a cap hit via grep must also
    # reject a subsequent read, and vice versa. Disabled entirely (no rejection ever, matching
    # Builder/FindingsWriter/PeerReviewer's own exclusion at the orchestrator.py reset site) when
    # the contextvar is left at its None default. ---
    def _analyzer_read_cap_scenario():
        from tools.fs import read_workspace_file as _rwf, grep_workspace_file as _gwf, _IN_MEMORY_FS
        from utils.run_state import task_read_grep_count_ctx as _trgc

        _orig_ws2 = _config.cfg.get("settings", {}).get("workspace")
        _config.cfg["settings"]["workspace"] = {"type": "memory"}
        _orig_cap2 = _config.cfg.get("settings", {}).get("analyzer_read_cap")
        _config.cfg["settings"]["analyzer_read_cap"] = 2
        saved_fs2 = dict(_IN_MEMORY_FS)
        try:
            _IN_MEMORY_FS.clear()
            _IN_MEMORY_FS["sources/test.md"] = "Source-URL: https://example.co/x\n\nSome real content here about the topic."

            # Disabled (contextvar None, the default -- e.g. a Builder/FindingsWriter/PeerReviewer
            # dispatch): no cap enforced no matter how high a count would otherwise be.
            _trgc.set(None)
            for _ in range(5):
                r = _rwf.func(filename="sources/test.md")
                assert "rejected" not in r.lower(), r

            # Active (e.g. an Analyzer dispatch), cap=2: first two calls (either tool, shared
            # counter) succeed, the third (regardless of which tool) is rejected -- no real
            # read/grep performed on the rejected call.
            _trgc.set([0])
            r1 = _gwf.func(filename="sources/test.md", pattern="real")
            assert "rejected" not in r1.lower() and "Match" in r1, r1
            r2 = _rwf.func(filename="sources/test.md")
            assert "rejected" not in r2.lower() and "Source-URL" in r2, r2
            r3 = _rwf.func(filename="sources/test.md")
            assert "rejected" in r3.lower() and "cap" in r3.lower(), r3
            r4 = _gwf.func(filename="sources/test.md", pattern="content")
            assert "rejected" in r4.lower() and "cap" in r4.lower(), (
                "the cap is a SHARED counter across both tools -- grep must also be rejected "
                "once read_workspace_file calls alone already hit it", r4)
        finally:
            _IN_MEMORY_FS.clear()
            _IN_MEMORY_FS.update(saved_fs2)
            _trgc.set(None)
            if _orig_ws2 is None:
                _config.cfg["settings"].pop("workspace", None)
            else:
                _config.cfg["settings"]["workspace"] = _orig_ws2
            if _orig_cap2 is None:
                _config.cfg["settings"].pop("analyzer_read_cap", None)
            else:
                _config.cfg["settings"]["analyzer_read_cap"] = _orig_cap2

    contextvars.copy_context().run(_analyzer_read_cap_scenario)

    # --- _extract_html_metadata: title/author/published from a page's own <head>, best-effort,
    # never fabricated for fields the page doesn't declare ---
    from tools.web import _extract_html_metadata
    from bs4 import BeautifulSoup

    _html_full = BeautifulSoup(
        '<html><head><title>The Real Title</title>'
        '<meta name="author" content="John Smith">'
        '<meta property="article:published_time" content="2026-03-01">'
        '</head><body>ignored</body></html>', "html.parser")
    _meta_full = _extract_html_metadata(_html_full)
    assert _meta_full == {"title": "The Real Title", "author": "John Smith", "published": "2026-03-01"}, _meta_full

    _html_og_fallback = BeautifulSoup(
        '<html><head><meta property="og:title" content="OG Title Fallback"></head>'
        '<body>ignored</body></html>', "html.parser")
    assert _extract_html_metadata(_html_og_fallback) == {"title": "OG Title Fallback"}

    _html_empty = BeautifulSoup('<html><head></head><body>no metadata here</body></html>', "html.parser")
    assert _extract_html_metadata(_html_empty) == {}, "a page with no declared metadata must return an empty dict, never guess"

    # --- prompts.py: the mechanical "Extract title/authors/abstract" delegation worked example
    # must be gone (it's exactly the pattern that fired identically across multiple 2026-07-12
    # benchmark runs, burning a full LLM sub-agent turn each time), replaced by wording pointing
    # at the new fetch-time header fields instead. ---
    from prompts import ACADEMIC_SEARCHER_INSTRUCTIONS, DATA_ANALYZER_INSTRUCTIONS
    assert "Extract title/authors/abstract" not in ACADEMIC_SEARCHER_INSTRUCTIONS, (
        "the old mechanical worked example must be replaced, not just supplemented")
    assert "Title:" in ACADEMIC_SEARCHER_INSTRUCTIONS and "Authors:" in ACADEMIC_SEARCHER_INSTRUCTIONS, (
        "must reference the new fetch-time header fields")
    assert "already in the file" in DATA_ANALYZER_INSTRUCTIONS or "header" in DATA_ANALYZER_INSTRUCTIONS

    # --- PEER_REVIEWER_INSTRUCTIONS: must explicitly tell the model not to guess further filenames
    # after its first successful read (2026-08-17 fix for the hallucinated-filename churn incident —
    # tools/core.py's tool-failure-streak guard now CONTAINS this, but the prompt itself should
    # still say not to do it in the first place). ---
    from prompts import PEER_REVIEWER_INSTRUCTIONS
    assert "do NOT call" in PEER_REVIEWER_INSTRUCTIONS and "different filename" in PEER_REVIEWER_INSTRUCTIONS

    # --- query-level scope warning (live case: Colombia task searching offshore wind turbines) ---
    from tools.web import _scope_warning
    from utils.run_state import scope_entities_ctx

    def _scope_scenario():
        scope_entities_ctx.set({"Colombia"})
        assert "SCOPE WARNING" in _scope_warning("predictive maintenance offshore wind turbine")
        assert _scope_warning("mantenimiento predictivo industrial colombia") == ""
        scope_entities_ctx.set(set())
        assert _scope_warning("anything at all") == ""  # no scope entities -> silent

    contextvars.copy_context().run(_scope_scenario)
    assert _scope_warning("anything") == ""  # outside any task -> silent

    # --- auto-fetch scope gate (2026-08-27 live incident: a Colombia-scoped "background" task's
    # web_search auto-fetched praoto.baby ("Yandex Tante Top Trending... Arab Culture Insights")
    # and hotplayer.ru (a Russian music search page) -- neither on-scope, ddgs's own top ranking
    # was simply wrong. _result_matches_scope is the pre-fetch gate this incident motivated. ---
    from tools.web import _result_matches_scope

    on_scope_result = {"title": "Colombia digital health regulation", "snippet": "Resolucion 1888 de 2025 sets EMR requirements for Colombian hospitals."}
    off_scope_result = {"title": "Yandex Tante Top Trending Global 2025", "snippet": "Gelora Sma indonesia 2025 Membara Di Meja Kerja Arab Culture Insights"}
    assert _result_matches_scope(on_scope_result, {"Colombia"}) is True
    assert _result_matches_scope(off_scope_result, {"Colombia"}) is False
    assert _result_matches_scope(off_scope_result, set()) is True, (
        "no scope entities extracted for this task -- nothing to check against, must not flag")
    assert _result_matches_scope({}, {"Colombia"}) is False, (
        "a result with no title/snippet at all has nothing supporting relevance either")

    # --- pre-run search health probe (patched ddgs, no network) ---
    import ddgs as _ddgs
    from tools.web import probe_search_health

    class _HealthyDDGS:
        def text(self, *a, **k): return [{"href": "https://x", "title": "t", "body": "b"}]

    class _ThrottledDDGS:
        def text(self, *a, **k): raise RuntimeError("202 Ratelimit")

    _real_ddgs = _ddgs.DDGS
    try:
        _ddgs.DDGS = _HealthyDDGS
        assert probe_search_health(retry_delay=0) is None
        _ddgs.DDGS = _ThrottledDDGS
        err = probe_search_health(retry_delay=0)
        assert err and "Ratelimit" in err, err
    finally:
        _ddgs.DDGS = _real_ddgs

    # --- ROADMAP Phase 3: xQuAD-style search-result diversity reranking (pure function, no
    # network) — DDGS's own #1 must stay first (preserve its relevance judgment for the single
    # best result), but a genuinely distinct result buried behind several near-duplicates of the
    # top result must get promoted ahead of them. ---
    from tools.web import _diversity_rerank, _result_aspect_terms

    _dup_results = [
        {"title": "Fintech regulation update Colombia 2024", "snippet": "New rules for fintech lending platforms in Colombia."},
        {"title": "Colombia fintech regulation overview", "snippet": "Fintech lending regulation changes summarized for 2024."},
        {"title": "Fintech regulatory changes Colombia", "snippet": "Colombia updates fintech lending regulation this year."},
        {"title": "Agritech subsidies expand in rural Colombia", "snippet": "Government announces new agritech subsidy program for farmers."},
    ]
    _reranked = _diversity_rerank(_dup_results)
    assert _reranked[0] == _dup_results[0], (
        "DDGS's own #1 result must stay first -- diversity reranking augments its relevance "
        "judgment, it doesn't discard it", _reranked)
    assert _reranked[1]["title"] == _dup_results[3]["title"], (
        "a genuinely distinct result (agritech) must be promoted ahead of near-duplicate "
        "fintech results that add no new aspect coverage", [r["title"] for r in _reranked])
    # Edge cases: must never crash on 0 or 1 results, and must not mutate order when already diverse.
    assert _diversity_rerank([]) == []
    assert _diversity_rerank([_dup_results[0]]) == [_dup_results[0]]
    _distinct_results = [
        {"title": "Fintech sector overview Colombia", "snippet": "Lending platforms and digital banks."},
        {"title": "Agritech subsidies rural Colombia", "snippet": "Farmers receive new government subsidy program."},
        {"title": "Healthtech investment trends Colombia", "snippet": "Telemedicine startups attract venture funding."},
    ]
    assert _diversity_rerank(_distinct_results) == _distinct_results, (
        "already-diverse results (no near-duplicates) must keep their original relevance order")
    # _result_aspect_terms itself: stopwords and short words excluded, real terms kept.
    _terms = _result_aspect_terms({"title": "The Fintech Sector", "snippet": "Grew with new rules"})
    assert "fintech" in _terms and "sector" in _terms and "grew" in _terms, _terms
    assert "the" not in _terms and "with" not in _terms and "new" not in _terms, _terms



if __name__ == "__main__":
    main()
    print("test_tools_web_fetch OK")
