"""Smallest thing that fails if the structural-check heuristics break.
Run: venv/Scripts/python test_structural_checks.py (no framework needed).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from engine.orchestrator import _extract_excluded_topics, _lacks_concrete_subject
from utils.grounding import find_non_url_citations, fully_ungrounded
from utils.run_state import record_fetched_url, reset_fetched_urls


def main():
    # --- excluded-topic extraction (live case: 4 excluded sectors researched anyway) ---
    topics = _extract_excluded_topics(
        "Do a market research of neglected markets in Colombia, excluding Agritech, "
        "HealthTech, EdTech and VR/AR-Education sectors."
    )
    assert "agritech" in topics and "healthtech" in topics and "edtech" in topics, topics
    assert any("vr/ar-education" in t for t in topics), topics
    assert _extract_excluded_topics("How to avoid mosquito bites in the tropics") == set()
    assert _extract_excluded_topics("Compare React and Vue") == set()

    # --- unresolved-referent detection (live case: 'its' resolved to Microsoft, not Python) ---
    assert _lacks_concrete_subject("Summarize its headline feature.")
    assert not _lacks_concrete_subject("Summarize Python 3.14's headline feature.")
    assert not _lacks_concrete_subject("Find the current stable version of Python.")
    assert not _lacks_concrete_subject("Find studies about coffee and how it affects sleep in Colombia.")
    # Long instructions are never flagged, whatever pronouns they use.
    assert not _lacks_concrete_subject("evaluate it against " + "criteria " * 30)

    # --- findings.md wholesale-fabrication gate ---
    reset_fetched_urls()
    assert fully_ungrounded("Findings: lots of prose, zero sources.") == "no_urls"
    assert fully_ungrounded("- claim (https://fake.example.com/x)") == "all_cited_urls_unverified"
    record_fetched_url("https://real.example.com/page", filename="real_page.md")
    # One real citation grounds the file, even alongside an unfetched snippet URL.
    assert fully_ungrounded(
        "- claim (https://real.example.com/page)\n- extra (https://never-fetched.example.com/y)"
    ) is None
    reset_fetched_urls()

    # --- non-URL citation label: pseudo-citations flagged, prose/headers not ---
    # (live case 2026-07-11: a heading with the word "Source" quarantined a grounded report)
    assert find_non_url_citations("Source: Expert opinion from a facility manager in Colombia")
    assert find_non_url_citations("- **Fuente:** Ministerio de Salud, informe interno")
    assert not find_non_url_citations("## Methodology & Source Quality Notes")
    assert not find_non_url_citations("No claims were made without source attribution.")
    assert not find_non_url_citations("**Sources:**\n- **[Title](https://x.org/a)**")

    # --- search-health counter (persists into _run_state.json via RunState) ---
    import contextvars
    import json
    import tempfile
    from utils.run_state import RunState, run_state_ctx, record_search_health, get_search_health

    with tempfile.TemporaryDirectory() as tmpdir:
        def _health_scenario():
            rs = RunState(tmpdir)
            rs.set_query("q")
            run_state_ctx.set(rs)
            record_search_health(ok=True)
            record_search_health(ok=False)
            record_search_health(ok=False)
            assert get_search_health() == {"calls": 3, "failures": 2}, get_search_health()
            # A fetch mid-run persists state immediately (crash forensics), atomically (no .tmp left)
            record_fetched_url("https://real.example.com/page", filename="real_page.md")

        contextvars.copy_context().run(_health_scenario)  # isolated so the ctx var doesn't leak
        assert get_search_health() == {"calls": 0, "failures": 0}  # no run state -> zeros, no crash

        state_path = os.path.join(tmpdir, "_run_state.json")
        assert os.path.exists(state_path), "state not persisted before run end"
        assert not os.path.exists(state_path + ".tmp"), "atomic-write temp file left behind"
        with open(state_path, encoding="utf-8") as f:
            persisted = json.load(f)
        assert persisted["search_health"] == {"calls": 3, "failures": 2}, persisted
        assert persisted["fetched_urls"][0]["url"] == "https://real.example.com/page", persisted
    reset_fetched_urls()

    # --- exclusion gate must not fire on a task's own restated exclusion clause ---
    # (live case 2026-07-11: a discovery task quoting "Exclude fintech, last-mile delivery..."
    # was skipped twice, burning delegate_tasks quota and turns)
    from engine.orchestrator import _EXCLUSION_CUE_RE
    excluded = _extract_excluded_topics("Find niches. Exclude fintech, last-mile delivery and legaltech.")
    assert "fintech" in excluded, excluded
    restated = _EXCLUSION_CUE_RE.sub(" ", "discover regulated niches in colombia. exclude fintech, last-mile delivery and legaltech.")
    assert not any(t in restated for t in excluded), restated
    on_topic = _EXCLUSION_CUE_RE.sub(" ", "research fintech opportunities for gig workers in colombia")
    assert any(t in on_topic for t in excluded), on_topic

    # --- bare-origin fetch must not prefix-ground fabricated deep links ---
    # (live case 2026-07-11, qwen3.6: fetching mercadolibre.com's root waved a fully fabricated
    # findings.md through fully_ungrounded via one reconstructed deep URL on that domain)
    record_fetched_url("https://www.mercadolibre.com/", filename="root.md")
    assert fully_ungrounded(
        "- claim (https://www.mercadolibre.com/mercado-software-b2b-colombia-2025-tamano-inversion/)"
    ) == "all_cited_urls_unverified"
    # A real deep-URL fetch still prefix-grounds its variants (query string, stripped chars).
    record_fetched_url("https://example.com/report/2026", filename="r.md")
    assert fully_ungrounded("- claim (https://example.com/report/2026?utm_source=x)") is None
    reset_fetched_urls()

    # --- quota refund on environmental failure ---
    from tools.core import refund_quota

    def _refund_scenario():
        from tools.core import tool_quotas_ctx as q_ctx, check_quota
        q_ctx.set({"web_search": {"used": 0, "limit": 2}})
        check_quota("web_search")
        refund_quota("web_search")
        assert q_ctx.get()["web_search"]["used"] == 0
        refund_quota("web_search")  # never goes negative
        assert q_ctx.get()["web_search"]["used"] == 0

    contextvars.copy_context().run(_refund_scenario)

    # --- malformed-tool-call recovery predicate (live case: gpt-oss bad escape -> Ollama 500) ---
    from engine.orchestrator import malformed_tool_call_nudge
    assert malformed_tool_call_nudge(Exception(
        "Error code: 500 - {'error': {'message': 'error parsing tool call: raw=...'}}"))
    assert malformed_tool_call_nudge(Exception("Connection error.")) is None

    # --- fetched files live under sources/ and carry their true URL as line 1 ---
    from tools.web import _fetched_filename, _save_fetched
    from tools.fs import _IN_MEMORY_FS
    import config as _config
    assert _fetched_filename("foo") == "sources/foo.md"
    assert _fetched_filename("sources/foo.md") == "sources/foo.md"
    _orig_ws = _config.cfg.get("settings", {}).get("workspace")
    _config.cfg.setdefault("settings", {})["workspace"] = {"type": "memory"}
    try:
        reset_fetched_urls()
        _save_fetched(["https://example.com/page"], "foo", "body text")
        assert _IN_MEMORY_FS["sources/foo.md"].startswith("Source-URL: https://example.com/page\n\n")
        from utils.run_state import get_fetched_urls
        assert get_fetched_urls()[0]["filename"] == "sources/foo.md"
    finally:
        if _orig_ws is None:
            _config.cfg["settings"].pop("workspace", None)
        else:
            _config.cfg["settings"]["workspace"] = _orig_ws
        reset_fetched_urls()

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

    print("All structural-check assertions passed.")


if __name__ == "__main__":
    main()
