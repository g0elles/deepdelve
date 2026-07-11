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
    from utils.run_state import RunState, run_state_ctx, record_search_health, get_search_health

    def _health_scenario():
        rs = RunState.__new__(RunState)
        rs.data = {}
        run_state_ctx.set(rs)
        record_search_health(ok=True)
        record_search_health(ok=False)
        record_search_health(ok=False)
        assert get_search_health() == {"calls": 3, "failures": 2}, get_search_health()

    contextvars.copy_context().run(_health_scenario)  # isolated so the ctx var doesn't leak
    assert get_search_health() == {"calls": 0, "failures": 0}  # no run state -> zeros, no crash

    print("All structural-check assertions passed.")


if __name__ == "__main__":
    main()
