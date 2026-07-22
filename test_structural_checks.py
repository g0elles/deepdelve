"""Smallest thing that fails if the structural-check heuristics break.
Run: venv/Scripts/python test_structural_checks.py (no framework needed).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from engine.orchestrator import (
    _extract_excluded_topics, _lacks_concrete_subject, _extract_follow_up_directions,
    _ring_fenced_deadline,
)
from engine.completion import (
    _CUTOFF_ONLY_SUMMARY_RE, _reorder_findings_for_position_bias, _find_propagated_bad_content,
    _is_citable_finding, _build_findings_source_material,
)
from utils.grounding import find_non_url_citations, fully_ungrounded, partially_ungrounded, find_uncited_claim_lines, extract_cited_urls
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

    # --- FOLLOW-UP DIRECTIONS extraction (2026-07-19, engine-driven iterative deepening,
    # ROADMAP item 10) — matches WEB_SEARCHER_INSTRUCTIONS/ACADEMIC_SEARCHER_INSTRUCTIONS'
    # mandated trailing section format. ---
    summary_with_directions = (
        "- **[Rust 1.97](https://example.com/rust)**: current stable release.\n\n"
        "FOLLOW-UP DIRECTIONS:\n"
        "- Chase the async runtime RFC mentioned but not fetched.\n"
        "- Corroborate the release date against the official blog.\n"
    )
    directions = _extract_follow_up_directions(summary_with_directions)
    assert directions == [
        "Chase the async runtime RFC mentioned but not fetched.",
        "Corroborate the release date against the official blog.",
    ], directions
    assert _extract_follow_up_directions("no such section here") == []
    assert _extract_follow_up_directions("") == []
    # Case-insensitive header, per re.IGNORECASE — a model that varies casing must still be caught.
    assert _extract_follow_up_directions("stuff\nfollow-up directions:\n- one lead") == ["one lead"]

    # --- task_deadline ring-fence math (2026-07-21, "4th synthesis-vanishing mechanism" fix 1) ---
    # Normal case: extends by a full second sub_agent_timeout_minutes when the SDK ceiling has room.
    assert _ring_fenced_deadline(
        task_start=0, task_deadline=600, sub_agent_timeout_minutes=10, sdk_timeout_ceiling_seconds=3600,
    ) == 1200
    # The one real bug risk this whole fix carries: the extension must never be pushed past the
    # SDK client's own blunt timeout, or the previously-fixed "SDK wins the race" bug (see
    # _build_client's sdk_timeout comment) comes back. Confirm the cap actually bites.
    assert _ring_fenced_deadline(
        task_start=0, task_deadline=600, sub_agent_timeout_minutes=10, sdk_timeout_ceiling_seconds=700,
    ) == 640  # capped at sdk_timeout_ceiling_seconds - 60, not the full 1200
    # No room left at all under the SDK ceiling -> don't extend (caller falls through to cutoff).
    assert _ring_fenced_deadline(
        task_start=0, task_deadline=600, sub_agent_timeout_minutes=10, sdk_timeout_ceiling_seconds=650,
    ) is None

    # --- cutoff-only summary detection (2026-07-21, "4th synthesis-vanishing mechanism" fix 3) ---
    # Must match orchestrator.py's task_deadline marker text EXACTLY, both variants, and ONLY
    # when it's the entire summary -- real content followed by the marker must NOT match (a
    # partial synthesis is still worth showing to FindingsWriter).
    assert _CUTOFF_ONLY_SUMMARY_RE.match(
        "\n\n[SYSTEM: task 'top_heuristics' cut short -- sub_agent_timeout_minutes (10) exceeded.]"
    )
    assert _CUTOFF_ONLY_SUMMARY_RE.match(
        "\n\n[SYSTEM: task 'top_heuristics' cut short -- sub_agent_timeout_minutes (10) exceeded "
        "(stream produced no update before the deadline).]"
    )
    assert not _CUTOFF_ONLY_SUMMARY_RE.match(
        "N-BEATS and TFT are the two leading architectures.\n\n"
        "[SYSTEM: task 'top_heuristics' cut short -- sub_agent_timeout_minutes (10) exceeded.]"
    )
    assert not _CUTOFF_ONLY_SUMMARY_RE.match("Rust's current stable release is 1.97.1.")

    # --- findings-ordering positional-bias reorder (2026-07-22, "Lost in the Middle" +
    # PING's "Anchor Effect") --- pure positional zigzag, no value signal.
    entries6 = [(f"T{i}", f"E{i}") for i in range(6)]
    reordered = _reorder_findings_for_position_bias(entries6)
    assert reordered == [entries6[i] for i in (0, 5, 1, 4, 2, 3)], reordered
    # Invariants that must hold regardless of length -- nothing lost, nothing duplicated.
    for n in (0, 1, 2, 5, 7):
        sample = [(f"T{i}", f"E{i}") for i in range(n)]
        result = _reorder_findings_for_position_bias(sample)
        assert len(result) == n and set(result) == set(sample), (n, result)

    # --- propagation-aware hallucination check (2026-07-22, PING taxonomy) --- narrowed to the
    # documented split-brain pattern (ROADMAP History, 20260718_141225): the task-name-fallback
    # (uncited) sibling carries the genuinely detailed real content, a LATER redispatch produces a
    # different real URL whose summary closely paraphrases that same content without having
    # independently re-verified it against its own claimed source -- suspicious regardless of
    # which sibling has more detail, since a citable entry that just repeats an uncited sibling's
    # content likely didn't come from its own fetch.
    propagated_findings = [
        {"task_name": "background_heuristics", "source_url": "background_heuristics",
         "summary": "The Temporal Fusion Transformer improved forecast accuracy by 23% over "
                     "classical baselines in a 2024 benchmark study of Time Series Models."},
        {"task_name": "background_heuristics", "source_url": "https://forecastio.ai/other",
         "summary": "The Temporal Fusion Transformer improved forecast accuracy by 23% over "
                     "classical baselines in a 2024 benchmark study of Time Series Models."},
    ]
    flagged = _find_propagated_bad_content(
        propagated_findings, ["background_heuristics"]
    )
    assert flagged == ["background_heuristics"], flagged
    # Control: no term overlap between the uncited sibling and the citable one -- must NOT flag.
    control_findings = [
        {"task_name": "colombia_holidays", "source_url": "colombia_holidays",
         "summary": "The Dual Granularity Memory pattern was proposed in a 2026 paper on agentic "
                     "systems, unrelated to payroll timing."},
        {"task_name": "colombia_holidays", "source_url": "https://adp.com/payroll-calendar",
         "summary": "Payroll cycles in Colombia typically run twice monthly, aligned with the "
                     "Minimum Wage Disbursement Rules published in 2024."},
    ]
    assert _find_propagated_bad_content(control_findings, ["colombia_holidays"]) == []

    # --- _is_citable_finding excludes relevance-flagged findings (2026-07-21, live-caught): a
    # finding confirmed off-topic by orchestrator.py's scope-relevance check carries a real http
    # URL and a non-cutoff summary, so it used to pass as an ordinary citable entry even though
    # it has zero real value for the query -- live case was a Colombia-holidays task that fetched
    # a New Zealand page. Scoped to the RELEVANCE marker only, not VERIFICATION (a narrower
    # citation-mismatch flag that may still coexist with real, usable content). ---
    relevance_flagged = {
        "task_name": "colombia_holidays", "source_url": "https://publicholiday.co.nz/",
        "summary": ("I was unable to complete the research because I exceeded the web-search "
                    "quota. The only page fetched was a New Zealand holiday calendar.\n\n"
                    "[SYSTEM RELEVANCE WARNING: none of the sources fetched for this task "
                    "actually mention Colombia, despite the task instructions requiring it.]"),
    }
    assert _is_citable_finding(relevance_flagged) is False
    real_finding = {
        "task_name": "background", "source_url": "https://arxiv.org/abs/1234.5678",
        "summary": "N-BEATS improved forecast accuracy by 23% in a 2024 benchmark.",
    }
    assert _is_citable_finding(real_finding) is True
    # A VERIFICATION warning (citation-mismatch, not off-topic) must NOT be excluded -- real
    # content may still coexist with a narrower citation flag.
    verification_flagged = {
        "task_name": "background", "source_url": "https://arxiv.org/abs/1234.5678",
        "summary": ("N-BEATS improved forecast accuracy by 23% in a 2024 benchmark.\n\n"
                    "[SYSTEM VERIFICATION WARNING: this summary attributes a claim to a source "
                    "that does not match anything actually fetched this run.]"),
    }
    assert _is_citable_finding(verification_flagged) is True

    # --- _build_findings_source_material renders a real title when available (2026-07-21,
    # closing the format gap with FINDINGS_WRITER_INSTRUCTIONS' own required output shape), falls
    # back to the plain "### Source: url" shape when no title was extracted. ---
    def _title_rendering_scenario():
        import tempfile
        from utils.run_state import RunState, run_state_ctx
        reset_fetched_urls()
        with tempfile.TemporaryDirectory() as tmpdir:
            rs = RunState(tmpdir)
            run_state_ctx.set(rs)
            record_fetched_url("https://arxiv.org/abs/1234.5678", filename="sources/arxiv.md",
                                title="N-BEATS: A Neural Basis Expansion Approach")
            record_fetched_url("https://example.com/no-title", filename="sources/notitle.md")
            rs.add_finding("https://arxiv.org/abs/1234.5678", "Real finding with a title.", task_name="t1")
            rs.add_finding("https://example.com/no-title", "Real finding without a title.", task_name="t2")
            material = _build_findings_source_material(rs)
            assert "### [N-BEATS: A Neural Basis Expansion Approach](https://arxiv.org/abs/1234.5678)" in material, material
            assert "### Source: https://example.com/no-title" in material, material
        reset_fetched_urls()

    import contextvars
    contextvars.copy_context().run(_title_rendering_scenario)

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

    # --- findings.md per-citation gate (partially_ungrounded, added 2026-07-19, widened same
    # day) ---
    # Live case 1: findings.md 40% fabricated (6/15 entries) passed fully_ungrounded cleanly (9/15
    # real satisfied its "at least one" bar), then poisoned Builder's rewrite so badly it kept
    # almost no real content.
    # Live case 2 (same day, v1 -> v2): v1 only checked each entry's own '### [Title](URL)'
    # heading, assuming a body-text URL was always a legitimate incidental mention. Proven wrong
    # live within the same session -- FindingsWriter just as often writes '### Source: <task
    # name>' as the heading with the REAL citation as a body bullet, and v1 silently missed 4
    # genuinely fabricated citations shaped that way. v2 checks every distinct citation, matching
    # real_grounding_problem/check_not_grounded's own already-proven strict standard.
    record_fetched_url("https://real.example.com/a", filename="a.md")
    assert partially_ungrounded("no citations here at all, just prose") is None, (
        "no citations -- fully_ungrounded's own no_urls case already covers this")
    assert partially_ungrounded(
        "### [Real Finding](https://real.example.com/a) [PRIMARY]\n- Key Findings: real stuff."
    ) is None, "a single real citation must not be flagged"
    mixed = (
        "### [Real Finding](https://real.example.com/a) [PRIMARY]\n- Key Findings: real stuff.\n\n"
        "### [Fake Finding](https://fake.example.com/b) [SECONDARY]\n- Key Findings: invented."
    )
    problem = partially_ungrounded(mixed)
    assert problem and problem.startswith("unverified_entry_sources:") and "fake.example.com" in problem, problem
    # The exact real live shape that broke v1: heading is a plain task name (no markdown link at
    # all), the actual load-bearing citation is a body bullet -- must still be caught.
    task_name_heading = (
        "### Source: Research some topic\n"
        "- **[Real Finding](https://real.example.com/a)**: real stuff.\n\n"
        "### Source: Research another topic\n"
        "- **[Fake Finding](https://fake.example.com/b)**: invented."
    )
    problem2 = partially_ungrounded(task_name_heading)
    assert problem2 and "fake.example.com" in problem2, (
        "a citation in a body bullet under a plain-task-name heading (the real live-observed "
        "FindingsWriter format v1 missed) must still be caught", problem2)
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

    # --- quota ring-fence, per-task rescue (2026-07-19 QA audit, ROADMAP's tracked open angle
    # (a): a shared cumulative pool can starve a task that already showed real progress. The
    # original fix only rescued the FIRST task per tool/run to hit the wall (a single `_rescued`
    # bool on the shared entry) -- a second/third task with its own real progress got no rescue at
    # all. Now tracked per-task via task_id_ctx, so every distinct task showing real progress gets
    # exactly one rescue, and the SAME task hitting the wall twice does not get rescued twice.
    # CLOSED 2026-07-21: the task_fetched_urls_ctx requirement was dropped (see check_quota's own
    # comment) -- a task blocked on its own FIRST web_search call can never have fetched anything
    # yet, so requiring proof of progress made the rescue unreachable for exactly the tasks that
    # need it most (four sibling comparison tasks starved by one heavily-redispatched sibling,
    # confirmed live). Every distinct task_id now gets one grace top-up regardless of progress. ---
    def _quota_ring_fence_scenario():
        from tools.core import tool_quotas_ctx as q_ctx, check_quota
        from utils.run_state import task_fetched_urls_ctx, task_id_ctx

        q_ctx.set({"web_search": {"used": 2, "limit": 2}})

        # Task A: real progress (a non-empty task_fetched_urls_ctx) -> rescued once, limit grows
        # by 2 (used 2 -> 3, limit 2 -> 4), giving one genuine extra call of headroom.
        task_fetched_urls_ctx.set([{"url": "https://a.example.com"}])
        task_id_ctx.set(101)
        assert check_quota("web_search") is None, "task A's first over-quota call must be rescued"
        assert q_ctx.get()["web_search"]["limit"] == 4
        assert q_ctx.get()["web_search"]["used"] == 3

        # Consume the one extra unit the rescue actually granted (used 3 -> 4, at the new limit).
        assert check_quota("web_search") is None, "the rescued headroom unit must still work normally"
        assert q_ctx.get()["web_search"]["used"] == 4

        # Task A again, now genuinely back over the (already-rescued) limit: same task_id, already
        # rescued once -> must NOT be rescued a second time.
        err = check_quota("web_search")
        assert err and "Quota reached" in err, (
            "the SAME task must not be rescued twice", err)

        # Task B: a DIFFERENT task, also showing real progress -> must get its OWN rescue, the
        # exact fairness gap the single-bool version had (only the first task/run ever rescued).
        q_ctx.set({"web_search": {"used": 4, "limit": 4}})
        task_fetched_urls_ctx.set([{"url": "https://b.example.com"}])
        task_id_ctx.set(102)
        assert check_quota("web_search") is None, (
            "a SECOND task with its own real progress must also get rescued -- this is the "
            "exact per-task fairness fix, not a repeat of task A's single rescue")
        assert q_ctx.get()["web_search"]["limit"] == 6

        # Task C: over quota with NO real progress (empty task_fetched_urls_ctx) -> STILL gets its
        # one grace rescue (2026-07-21 fix) -- a task blocked on its own first web_search call has
        # never had the chance to populate task_fetched_urls_ctx at all, so this is exactly the
        # case the fix exists for, not an exception to it.
        q_ctx.set({"web_search": {"used": 2, "limit": 2}})
        task_fetched_urls_ctx.set([])
        task_id_ctx.set(103)
        assert check_quota("web_search") is None, (
            "a task with no real progress yet must still get its one grace rescue")
        assert q_ctx.get()["web_search"]["limit"] == 4
        assert q_ctx.get()["web_search"]["used"] == 3

        # Consume the rescued headroom unit (used 3 -> 4, at the new limit), same as Task A above.
        assert check_quota("web_search") is None
        assert q_ctx.get()["web_search"]["used"] == 4

        # Task C again, genuinely back over the already-rescued limit -> must NOT be rescued a
        # second time, same bound as every other task_id.
        err = check_quota("web_search")
        assert err and "Quota reached" in err, (
            "the SAME task must not be rescued twice even with no proven progress", err)

    contextvars.copy_context().run(_quota_ring_fence_scenario)

    # --- delegate_tasks batch pre-reservation (ROADMAP's tracked open angle (c), 2026-07-21):
    # live-confirmed a heavily-redispatched sibling can structurally starve later-listed siblings
    # in the same batch before they ever get a turn, even across completion-check topups. Pure
    # arithmetic, pulled out of delegate_tasks's own closure for direct testability. ---
    from engine.orchestrator import _reserve_batch_quota_headroom

    def _batch_reservation_scenario():
        # Pool already mostly drained (the exact shape seen live: an earlier sibling ate most of
        # web_search's budget) -> a 5-task batch must get topped up to guarantee 2 calls each.
        pool = {"web_search": {"used": 13, "limit": 15}, "fetch_url_to_workspace": {"used": 2, "limit": 15}}
        _reserve_batch_quota_headroom(pool, batch_size=5)
        assert pool["web_search"]["limit"] == 23, pool  # 13 used + need 10 headroom (2*5) -> limit 23
        assert pool["fetch_url_to_workspace"]["limit"] == 15, pool  # already has 13 headroom >= 1*5

        # Plenty of headroom already -> no-op, must not shrink or otherwise touch the limit.
        pool2 = {"web_search": {"used": 0, "limit": 15}}
        _reserve_batch_quota_headroom(pool2, batch_size=3)
        assert pool2["web_search"]["limit"] == 15, pool2

        # A single-task "batch" can't starve a sibling -> no-op regardless of pool state.
        pool3 = {"web_search": {"used": 15, "limit": 15}}
        _reserve_batch_quota_headroom(pool3, batch_size=1)
        assert pool3["web_search"]["limit"] == 15, pool3

        # An untracked tool (not in the pool dict at all, e.g. quota disabled for it) must be
        # skipped silently, not raise.
        pool4 = {"web_search": {"used": 15, "limit": 15}}
        _reserve_batch_quota_headroom(pool4, batch_size=4)  # touches fetch_url_to_workspace too, absent here
        assert pool4["web_search"]["limit"] == 23, pool4  # 15 used + need 8 (2*4) -> limit 23

    _batch_reservation_scenario()

    # --- malformed-tool-call recovery predicate (live case: gpt-oss bad escape -> Ollama 500) ---
    from engine.orchestrator import malformed_tool_call_nudge
    assert malformed_tool_call_nudge(Exception(
        "Error code: 500 - {'error': {'message': 'error parsing tool call: raw=...'}}"))
    assert malformed_tool_call_nudge(Exception("Connection error.")) is None

    # --- in-band tool-error recovery predicate (live case 2026-07-13/14: a SubAgent_BuilderFix
    # hallucinated a delegate_tasks call, a separate sub-agent called a malformed grep_workspace?,
    # PeerReviewer tried reading a nonexistent workspace.txt — none of these raise, they come back
    # as ordinary successful function_result content, so malformed_tool_call_nudge above never
    # sees them). Exact strings pulled from agent_framework/_tools.py, not guessed. ---
    from engine.orchestrator import tool_result_error_nudge
    assert tool_result_error_nudge('Error: Requested function "grep_workspace?" not found.')
    assert tool_result_error_nudge('Error: Requested function "delegate_tasks" not found.')
    assert tool_result_error_nudge("Error: Argument parsing failed.")
    assert tool_result_error_nudge(
        "Error: Argument parsing failed. Exception: 1 validation error for query")
    assert tool_result_error_nudge("Error: 'workspace.txt' not found.")
    # Must NOT false-positive on a real success or an unrelated (already-handled-elsewhere) error —
    # a blind retry on either would waste a turn instead of fixing anything.
    assert tool_result_error_nudge("Fetched URL successfully to 'sources/foo.md' on disk.") is None
    assert tool_result_error_nudge(
        "Search failed: timed out after 20s with no response — the search layer appears to be "
        "hanging, not just slow.") is None
    assert tool_result_error_nudge("") is None

    # --- fetched files live under sources/ and carry their true URL as line 1 ---
    from tools.web import _fetched_filename, _save_fetched, _slugify_for_filename, fetch_url_to_workspace
    from tools.fs import _IN_MEMORY_FS
    import config as _config
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
    finally:
        if _orig_ws is None:
            _config.cfg["settings"].pop("workspace", None)
        else:
            _config.cfg["settings"]["workspace"] = _orig_ws
        reset_fetched_urls()

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

    # --- run-resume helpers (--resume-run: reattach to an interrupted run instead of restarting) ---
    from engine.tui import load_resume_state, build_resume_input, _clarify_verdict
    from tools.fs import session_dir_ctx

    with tempfile.TemporaryDirectory() as tmpdir:
        run_dir = os.path.join(tmpdir, "my_interrupted_run")
        os.makedirs(os.path.join(run_dir, "sources"))
        prior = {
            "query": "Research X in Colombia",
            "fetched_urls": [{"url": "https://real.example.com/a", "filename": "sources/a.md", "timestamp": 1.0}],
            "findings": [],
        }
        with open(os.path.join(run_dir, "_run_state.json"), "w", encoding="utf-8") as f:
            json.dump(prior, f)
        with open(os.path.join(run_dir, "_todos.md"), "w", encoding="utf-8") as f:
            f.write("- [x] background\n- [ ] verification")
        with open(os.path.join(run_dir, "findings.md"), "w", encoding="utf-8") as f:
            f.write("## Findings so far\n- claim (https://real.example.com/a)")

        _orig_ws2 = _config.cfg.get("settings", {}).get("workspace")
        _config.cfg["settings"]["workspace"] = {"type": "disk", "dir": tmpdir, "session_isolation": True}
        try:
            name, state = load_resume_state(run_dir)  # full path accepted
            assert name == "my_interrupted_run" and state["query"] == "Research X in Colombia"
            name2, _ = load_resume_state("my_interrupted_run")  # bare folder name accepted
            assert name2 == name

            def _resume_scenario():
                session_dir_ctx.set(name)
                text = build_resume_input(state["query"], state)
                assert "RESUMED RUN" in text
                assert "Research X in Colombia" in text
                assert "https://real.example.com/a" in text
                assert "verification" in text          # _todos.md injected
                assert "Findings so far" in text       # findings.md injected

            contextvars.copy_context().run(_resume_scenario)
        finally:
            if _orig_ws2 is None:
                _config.cfg["settings"].pop("workspace", None)
            else:
                _config.cfg["settings"]["workspace"] = _orig_ws2

    # --- /resume-run TUI wiring: --resume-run existed in the headless CLI for a full session
    # before the TUI had any equivalent at all (caught live, 2026-07-12) ---
    import asyncio as _asyncio_tui

    async def _resume_run_tui_scenario():
        with tempfile.TemporaryDirectory() as tmpdir2:
            run_dir2 = os.path.join(tmpdir2, "my_interrupted_run")
            os.makedirs(os.path.join(run_dir2, "sources"))
            with open(os.path.join(run_dir2, "_run_state.json"), "w", encoding="utf-8") as f:
                json.dump({
                    "query": "Research X",
                    "fetched_urls": [{"url": "https://real.example.com/a", "filename": "sources/a.md", "timestamp": 1.0}],
                    "findings": [{"source_url": "https://real.example.com/a", "summary": "s"}],
                }, f)

            _orig_ws9 = _config.cfg.get("settings", {}).get("workspace")
            _config.cfg["settings"]["workspace"] = {"type": "disk", "dir": tmpdir2, "session_isolation": True}
            try:
                class _FakeBuilder:
                    name = "Planner"
                    instructions = "test"
                    tools = []
                    sub_agents = []

                app = BasicTuiAgent(_FakeBuilder())
                resume_calls = []

                async def _fake_run_agent(query, mount_user=True):
                    resume_calls.append({
                        "mount_user": mount_user,
                        "active_run_dir": app._active_run_dir,
                        "resuming_run": app._resuming_run,  # must be True DURING the call
                        "conv_fetched": list(app._conv_fetched or []),
                        "conv_run_state_query": app._conv_run_state.data.get("query") if app._conv_run_state else None,
                    })
                app.run_agent = _fake_run_agent

                async with app.run_test():
                    app._show_run_picker()
                    assert app._run_picker_active
                    assert app._filtered_cmds and app._filtered_cmds[0][0] == "my_interrupted_run"

                    await app._open_selected_run("my_interrupted_run")
                    assert len(resume_calls) == 1, resume_calls
                    call = resume_calls[0]
                    assert call["mount_user"] is False  # no giant resume-preamble bubble
                    assert call["active_run_dir"] == "my_interrupted_run"
                    assert call["resuming_run"] is True  # skip_completion_check's Q&A shortcut must be disarmed
                    assert call["conv_fetched"] == [
                        {"url": "https://real.example.com/a", "filename": "sources/a.md", "timestamp": 1.0}
                    ]
                    assert call["conv_run_state_query"] == "Research X"
                    # Reset back to False once run_agent returns (the `finally` in _resume_run).
                    assert app._resuming_run is False
            finally:
                if _orig_ws9 is None:
                    _config.cfg["settings"].pop("workspace", None)
                else:
                    _config.cfg["settings"]["workspace"] = _orig_ws9

    from engine.tui import BasicTuiAgent
    _asyncio_tui.run(_resume_run_tui_scenario())

    # --- B5: session log write throttling (2026-07-12) — _write_log serializes and rewrites the
    # WHOLE _session_events list every call; log_stream_content used to call it after EVERY
    # streamed event, so an N-event run paid O(N) per write summed over N writes = O(n²) total
    # (confirmed live: a 370-event killed run produced a 565KB session file). Throttled to at
    # most once per _LOG_WRITE_THROTTLE_SECONDS by default; force=True (every genuine checkpoint:
    # turn end, run end, before sys.exit) always writes regardless. ---
    def _write_log_throttle_scenario():
        # Local import, not the enclosing main()'s `time` (imported later, line ~987 as of this
        # writing) — Python's static per-function scoping would make a bare `time.sleep` here
        # resolve to that not-yet-assigned enclosing local and raise UnboundLocalError, the exact
        # bug class caught and fixed elsewhere in this file earlier this session.
        import time
        import engine.tui as _tui_mod
        with tempfile.TemporaryDirectory() as home_dir:
            _orig_home = os.environ.get("HOME")
            os.environ["HOME"] = home_dir
            _orig_persist = _config.cfg["settings"].get("enable_session_persistence")
            _config.cfg["settings"]["enable_session_persistence"] = True
            _orig_sid = _tui_mod._current_session_id
            _orig_events = _tui_mod._session_events
            _orig_last_write = _tui_mod._last_log_write
            try:
                _tui_mod._current_session_id = "throttle_test"
                _tui_mod._session_events = [{"a": 1}]
                _tui_mod._last_log_write = 0.0
                log_file = os.path.join(home_dir, f".{_config.APP_NAME}", "sessions", "session_throttle_test.json")

                _tui_mod._write_log()  # first call always writes (last_write starts at 0)
                assert os.path.exists(log_file), "first call must write"
                mtime1 = os.path.getmtime(log_file)

                time.sleep(0.05)
                _tui_mod._write_log()  # well within the throttle window
                assert os.path.getmtime(log_file) == mtime1, "throttled call must not rewrite"

                _tui_mod._write_log(force=True)  # bypasses the throttle unconditionally
                assert os.path.getmtime(log_file) > mtime1, "force=True must always write"
            finally:
                if _orig_home is None:
                    os.environ.pop("HOME", None)
                else:
                    os.environ["HOME"] = _orig_home
                if _orig_persist is None:
                    _config.cfg["settings"].pop("enable_session_persistence", None)
                else:
                    _config.cfg["settings"]["enable_session_persistence"] = _orig_persist
                _tui_mod._current_session_id = _orig_sid
                _tui_mod._session_events = _orig_events
                _tui_mod._last_log_write = _orig_last_write

    _write_log_throttle_scenario()

    # --- depth presets (--depth): quick/deep touch quotas+search_mode+retries, standard is a no-op ---
    from engine.tui import apply_depth_preset

    cfg = {"settings": {"quotas": {"web_search": 15, "read_workspace_file": {"limit": 30, "rules": {}}},
                        "search_mode": "light", "max_completion_check_attempts": 3}}
    apply_depth_preset(cfg, "standard")
    assert cfg["settings"]["quotas"]["web_search"] == 15  # untouched
    apply_depth_preset(cfg, "deep")
    assert cfg["settings"]["quotas"]["web_search"] == 30
    assert cfg["settings"]["search_mode"] == "heavy"
    assert cfg["settings"]["max_completion_check_attempts"] == 4
    apply_depth_preset(cfg, "quick")
    assert cfg["settings"]["quotas"]["web_search"] == 8
    assert cfg["settings"]["quotas"]["read_workspace_file"]["limit"] == 30  # dict quotas untouched

    # --- verdict matrix: one row per completion-check problem type, asserting the RECORDED
    # problem name AND a phrase distinctive to that branch's corrective nudge. This is the pin
    # against the swallowed-elif bug class (bd307f4, run 13) that motivated engine/completion.py:
    # a verdict carrying the right detail under the wrong label/nudge fails its row instantly.
    # Live-case rows: missing_findings (runs 10/11), regulation_unsupported (runs 12/13). ---
    import asyncio as _asyncio
    from engine.tui import run_completion_check

    _SRC = "https://gov.example.co/page"
    _STUB_SRC = "https://news.example.co/paywalled-article"
    _SOURCE_TEXT = ("Source-URL: " + _SRC + "\n\n"
                    + "Estrategia nacional de seguridad digital para infraestructura y sectores productivos. " * 3)
    _FINDINGS_OK = f"- hallado ({_SRC})"

    matrix = [
        # (row, delegated, workspace files, expected recorded problem, distinctive nudge phrase)
        ("not_delegated", False, {"final_report.md": f"- x [g]({_SRC})"},
         "not_delegated", "No `delegate_tasks` call was ever made"),
        ("findings_ungrounded", True, {"findings.md": "- todo de memoria, sin fuente alguna"},
         "findings_ungrounded", "fails the grounding check"),
        # Live case 2026-07-19: findings.md 40% fabricated (6/15 entries) passed the wholesale
        # fully_ungrounded gate cleanly (some real entries existed), then poisoned Builder's
        # rewrite. partially_ungrounded's per-entry-heading check catches the mix even though at
        # least one entry (_SRC) is genuinely real.
        ("findings_partially_ungrounded", True, {
            "findings.md": (f"### [Real Finding]({_SRC}) [PRIMARY]\n- Key Findings: real.\n\n"
                             "### [Fake Finding](https://never-fetched.example.com/fake) [SECONDARY]\n"
                             "- Key Findings: invented.")},
         "findings_ungrounded", "unverified_entry_sources"),
        ("missing_findings", True, {"final_report.md": f"- x [g]({_SRC})"},
         "missing_findings", "was never written — the two-pass discipline was skipped"),
        ("missing_artifact", True, {"findings.md": _FINDINGS_OK},
         "missing_artifact", "is missing from the workspace"),
        ("claim_unsupported", True, {"findings.md": _FINDINGS_OK,
          "final_report.md": f"- Colombia exporto USD 3.5 mil millones en 2024 [gov]({_SRC})"},
         "claim_unsupported", "don't appear to come from that source's actual content"),
        ("no_urls", True, {"findings.md": _FINDINGS_OK,
          "final_report.md": "# Informe\nSin enlaces aqui."},
         "not_grounded", "zero hyperlinked sources"),
        ("regulation_unsupported", True, {"findings.md": _FINDINGS_OK,
          "final_report.md": f"| Ley 1906 de 2021 | [gov]({_SRC}) |"},
         "regulation_unsupported", "never mentions that regulation's number"),
        ("non_url_citation", True, {"findings.md": _FINDINGS_OK,
          "final_report.md": f"- dato uno [gov]({_SRC})\n- **Fuente:** Ministerio de Salud, informe interno"},
         "non_url_citation", "isn't a real URL"),
        # Live case run 14: citation to a really-fetched URL whose fetch was a 200 soft-404
        # (paywall shell) — hollow even though the URL gate sees a real fetch.
        ("stub_source", True, {"findings.md": _FINDINGS_OK,
          "final_report.md": f"- dato [news]({_STUB_SRC})"},
         "stub_source", "paywall/not-found stub"),
        # Live case run 14 (format half): claims as a figure table + detached Source URLs list —
        # every line-scoped check passes vacuously; nothing ties any figure to any source.
        ("uncited_claims", True, {"findings.md": _FINDINGS_OK,
          "final_report.md": ("| Sector | Valor estimado |\n"
                              "| Fintech | USD 3.5 mil millones en el mercado local en 2024 |\n"
                              "| Agro | 12% de crecimiento anual en exportaciones regionales |\n"
                              "| Salud | 2.300 empresas registradas en el sector durante 2023 |\n"
                              f"\n### Source URLs\n- {_SRC}\n")},
         "uncited_claims", "carry no citation of their own"),
        ("not_grounded", True, {"findings.md": _FINDINGS_OK,
          "final_report.md": "- x [g](https://never-fetched.example.com/y)"},
         "not_grounded", "was never actually fetched this run"),
        # Live case (ROADMAP "Findings from live testing"): delegate_tasks already skips
        # DISPATCHING a task on an explicitly-excluded topic, but nothing previously stopped that
        # topic showing up as its own section in the final report anyway — confirmed live twice.
        # Heading-scoped: the excluded topic ("agritech") appears as its own "## Sector Agritech"
        # section, not just mentioned in passing prose.
        ("excluded_topic_present", True, {"findings.md": _FINDINGS_OK,
          "final_report.md": (f"- el pais avanza de forma sostenida segun cifras oficiales [gov]({_SRC})\n\n"
                               f"## Sector Agritech\n- el sector agritech crecio de forma notable segun analistas [gov]({_SRC})\n")},
         "excluded_topic_present", "explicitly excluded",
         "Do a market research of Colombia, excluding Agritech."),
        # Live-motivated case (ROADMAP Phase 2, FEVER-style): the report's own citation genuinely
        # supports its claim (12%, from fintech_a), but a DIFFERENT fetched source (fintech_b,
        # never cited on this line) reports a conflicting figure (18%) for the SAME subject, and
        # the report never surfaces that disagreement anywhere.
        ("cross_source_contradiction", True, {"findings.md": _FINDINGS_OK,
          "final_report.md": "- Sector Fintech grew 12% in 2024 [gov](https://gov.example.co/fintech-a)"},
         "cross_source_contradiction", "a DIFFERENT fetched source",
         "", [
             ("https://gov.example.co/fintech-a", "sources/fintech_a.md",
              "Source-URL: https://gov.example.co/fintech-a\n\nSector Fintech grew 12% in 2024 according to official figures."),
             ("https://gov.example.co/fintech-b", "sources/fintech_b.md",
              "Source-URL: https://gov.example.co/fintech-b\n\nSector Fintech grew 18% in 2024 according to a different analysis."),
         ]),
        # Clean pass: grounded findings, report cites the fetched source, no checkable claim
        # contradicting it -> no problem recorded, no retry.
        ("clean_pass", True, {"findings.md": _FINDINGS_OK,
          "final_report.md": f"- el pais avanza de forma sostenida segun cifras oficiales [gov]({_SRC})"},
         None, None),
        # Propagation-aware check (2026-07-22, PING taxonomy): run_state.data["findings"] carries
        # a task-name-fallback (uncited) sibling with real detailed content, and a citable sibling
        # (real URL) whose summary is near-identical -- the report cites the real URL with content
        # that textually matches its own fetched source (so no earlier check fires first), but the
        # propagation check flags it because the SAME content also exists under an uncited sibling
        # for the same task_name, suspicious regardless of which one has "more" detail.
        ("propagated_ungrounded", True, {"findings.md": _FINDINGS_OK,
          "final_report.md": (f"- el pais avanza de forma sostenida segun cifras oficiales [gov]({_SRC})\n"
                               "- The Temporal Fusion Transformer improved forecast accuracy by 23% "
                               "over classical baselines in a 2024 benchmark study of Time Series "
                               "Models. [source](https://forecastio.ai/other)")},
         "propagated_ungrounded", "without independent verification", "", [
             ("https://forecastio.ai/other", "sources/other.md",
              "Source-URL: https://forecastio.ai/other\n\nThe Temporal Fusion Transformer improved "
              "forecast accuracy by 23% over classical baselines in a 2024 benchmark study of Time "
              "Series Models."),
         ], [
             {"task_name": "background_heuristics", "source_url": "background_heuristics",
              "summary": "The Temporal Fusion Transformer improved forecast accuracy by 23% over "
                         "classical baselines in a 2024 benchmark study of Time Series Models."},
             {"task_name": "background_heuristics", "source_url": "https://forecastio.ai/other",
              "summary": "The Temporal Fusion Transformer improved forecast accuracy by 23% over "
                         "classical baselines in a 2024 benchmark study of Time Series Models."},
         ]),
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        for _row_name, _delegated, _files, _expected, _phrase, *_rest in matrix:
            _query = _rest[0] if _rest else ""
            _extra_fetches = _rest[1] if len(_rest) > 1 else []
            _extra_findings = _rest[2] if len(_rest) > 2 else []

            def _matrix_row():
                from tools.fs import _IN_MEMORY_FS
                from tools.core import tool_quotas_ctx as q_ctx
                _orig_ws3 = _config.cfg.get("settings", {}).get("workspace")
                _config.cfg["settings"]["workspace"] = {"type": "memory", "required_artifact": "final_report.md"}
                # This matrix isn't testing NLI wiring (that's _nli_verify_scenario below, with a
                # mocked model) -- without disabling it here, any row whose content_level_check
                # passes would fall through to a REAL (unmocked) nli_unsupported_problem call,
                # silently loading the actual HuggingFace model and making this "fast structural
                # suite" depend on network access. Confirmed live: exactly this happened before
                # this line was added.
                _orig_gc3 = _config.cfg.get("settings", {}).get("grounding_check")
                _config.cfg["settings"]["grounding_check"] = {"nli_verify": False, "topical_relevance_check": False}
                saved_fs = dict(_IN_MEMORY_FS)
                try:
                    _IN_MEMORY_FS.clear()
                    reset_fetched_urls()
                    record_fetched_url(_SRC, filename="sources/page.md")
                    _IN_MEMORY_FS["sources/page.md"] = _SOURCE_TEXT
                    # A stub fetch is on record in EVERY row (rows that don't cite it must not
                    # trip over its mere existence); only the stub_source row cites it.
                    record_fetched_url(_STUB_SRC, filename="sources/stub.md", stub="paywall marker")
                    _IN_MEMORY_FS["sources/stub.md"] = "Source-URL: " + _STUB_SRC + "\n\nSUSCRÍBETE"
                    for _url, _fn, _content in _extra_fetches:
                        record_fetched_url(_url, filename=_fn)
                        _IN_MEMORY_FS[_fn] = _content
                    _IN_MEMORY_FS.update(_files)
                    q_ctx.set({"delegate_tasks": {"used": 1 if _delegated else 0, "limit": 5}})
                    rs = RunState(tmpdir)
                    rs.set_query(_query)
                    run_state_ctx.set(rs)
                    for _finding_kwargs in _extra_findings:
                        rs.add_finding(**_finding_kwargs)
                    msgs = []
                    should_retry, _ = _asyncio.run(run_completion_check(
                        query="q", current_input="q", run_state=rs, notify=msgs.append))
                    recorded = rs.data["completion_check_attempts"][-1]["problem"]
                    assert recorded == _expected, (_row_name, recorded, msgs)
                    assert should_retry == (_expected is not None), (_row_name, should_retry, msgs)
                    if _phrase:
                        assert _phrase in msgs[-1], (_row_name, _phrase, msgs)
                finally:
                    _IN_MEMORY_FS.clear()
                    _IN_MEMORY_FS.update(saved_fs)
                    reset_fetched_urls()
                    if _orig_ws3 is None:
                        _config.cfg["settings"].pop("workspace", None)
                    else:
                        _config.cfg["settings"]["workspace"] = _orig_ws3
                    if _orig_gc3 is None:
                        _config.cfg["settings"].pop("grounding_check", None)
                    else:
                        _config.cfg["settings"]["grounding_check"] = _orig_gc3

            contextvars.copy_context().run(_matrix_row)

    # --- find_cross_source_contradictions: citation-only lines must never be treated as claims.
    # Live-confirmed false positive (2026-07-14, real Iceland-population TUI run): an agency name
    # ("Statistics Iceland") appearing ONLY inside a `- Source: [Title - Statistics Iceland](url)`
    # citation attribution in the report, and dozens of times across a long fetched Wikipedia
    # article as bare source attribution / image captions / reference-list entries, got paired
    # with unrelated nearby years by _extract_figure_claims's nearest-figure heuristic -- firing a
    # phantom cross_source_contradiction on every single Builder rewrite (report never actually
    # said anything wrong), an unfixable, non-converging retry loop. ---
    def _cross_source_citation_line_scenario():
        from utils.grounding import find_cross_source_contradictions, _is_citation_only_line

        assert _is_citation_only_line(
            "- Source: [The population on 1 January 2025 - Statistics Iceland]"
            "(https://statice.is/publications/news-archive/inhabitants/the-population-on-1-january-2025/)"
        )
        assert _is_citation_only_line('2. [↑](#cite_ref-2) ["Population by origin"](https://example.com).')
        # Genuine prose must NOT be classified as citation-only, even with a link or a subject
        # name inside it -- only bibliographic/attribution-only lines are excluded.
        assert not _is_citation_only_line(
            "The population of Iceland from 1703 to 2017, using data from Statistics Iceland."
        )
        assert not _is_citation_only_line(
            "There is a slight discrepancy between the annual growth rate indicated by the "
            "primary Statistics Iceland data (~1.5%) and the trajectory suggested by the "
            "Wikipedia projection (~394,530)."
        )

        def _fake_scenario():
            from tools.fs import _IN_MEMORY_FS
            saved_fs = dict(_IN_MEMORY_FS)
            try:
                _IN_MEMORY_FS.clear()
                reset_fetched_urls()
                report = (
                    "As of January 1, 2025, the official population of Iceland was **389,444**.\n"
                    "- Source: [The population on 1 January 2025 - Statistics Iceland]"
                    "(https://statice.is/pop-2025)\n"
                )
                record_fetched_url("https://statice.is/pop-2025", filename="sources/statice.md")
                _IN_MEMORY_FS["sources/statice.md"] = (
                    "Source-URL: https://statice.is/pop-2025\n\n"
                    "The population on 1 January 2025 was 389,444."
                )
                record_fetched_url("https://en.wikipedia.org/wiki/Demographics_of_Iceland", filename="sources/wiki.md")
                # Real-shape reproduction: "Statistics Iceland" as bare attribution in a caption
                # (2017, unrelated to any population figure) plus a numbered reference-list entry
                # citing "Statistics Iceland" again (2024) -- neither is a genuine competing claim.
                _IN_MEMORY_FS["sources/wiki.md"] = (
                    "Source-URL: https://en.wikipedia.org/wiki/Demographics_of_Iceland\n\n"
                    "The population of Iceland from 1703 to 2017, using data from Statistics Iceland.\n\n"
                    '2. [↑](#cite_ref-2) ["Population by origin"](https://example.com). '
                    "*Statistics Iceland*. Retrieved 2024-01-01."
                )
                hits = find_cross_source_contradictions(report)
                assert hits == [], hits
            finally:
                _IN_MEMORY_FS.clear()
                _IN_MEMORY_FS.update(saved_fs)
                reset_fetched_urls()

        _orig_ws_csc = _config.cfg.get("settings", {}).get("workspace")
        _config.cfg["settings"]["workspace"] = {"type": "memory", "required_artifact": "final_report.md"}
        try:
            contextvars.copy_context().run(_fake_scenario)
        finally:
            if _orig_ws_csc is None:
                _config.cfg["settings"].pop("workspace", None)
            else:
                _config.cfg["settings"]["workspace"] = _orig_ws_csc

    _cross_source_citation_line_scenario()

    # --- NLI grounding verification (live case 2026-07-12, NVIDIA NIM gpt-oss-20b benchmark run):
    # a citation to a real, fetched source whose claim shares terms with it (passes
    # content_level_check) but is actually contradicted by the source's real content (a paper
    # title quoted with one word swapped). The real NLI model isn't loaded in this fast suite —
    # mocked at utils.grounding._get_nli_model to test WIRING correctness (config toggle ->
    # ordering after content_level_check -> Verdict routing -> quarantine -> nudge phrase), same
    # boundary this project already draws elsewhere (e.g. live_http_verify is a real network call,
    # never exercised by the fast suite either). ---
    def _nli_verify_scenario():
        from tools.fs import _IN_MEMORY_FS
        from tools.core import tool_quotas_ctx as q_ctx
        from unittest.mock import patch
        import utils.grounding as _grounding_mod

        class _FakeScore:
            def __init__(self, idx):
                self._idx = idx
            def argmax(self):
                return self._idx

        class _FakeModel:
            def __init__(self, idx):
                self._idx = idx
            def predict(self, pairs):
                return [_FakeScore(self._idx) for _ in pairs]

        _orig_ws6 = _config.cfg.get("settings", {}).get("workspace")
        _config.cfg["settings"]["workspace"] = {"type": "memory", "required_artifact": "final_report.md"}
        # nli_verify stays on (that's what this scenario tests), but topical_relevance_check must
        # be off -- otherwise the entailment/neutral sub-case below (NLI returns None) falls
        # through to a REAL, unmocked topical-relevance model load, same anti-pattern the matrix's
        # own nli_verify:False guard exists to prevent (see its comment above).
        _orig_gc6 = _config.cfg.get("settings", {}).get("grounding_check")
        _config.cfg["settings"]["grounding_check"] = {"topical_relevance_check": False}
        saved_fs = dict(_IN_MEMORY_FS)
        try:
            _IN_MEMORY_FS.clear()
            reset_fetched_urls()
            # Dedicated source/claim pair, NOT the shared _SRC/_SOURCE_TEXT fixture: that fixture
            # deliberately has zero numbers/capitalized phrases (so claim_grounding_problem's OTHER
            # matrix rows can test "no checkable terms -> skipped" vs. "checkable terms -> zero
            # overlap -> flagged"). This scenario needs a claim that DOES share a term with its
            # source (so content_level_check passes and execution actually reaches
            # nli_unsupported_problem) -- a shared year, "2020".
            _nli_src = "https://gov.example.co/nli-test-page"
            _nli_source_text = ("Source-URL: " + _nli_src + "\n\n"
                                 + "The National Cyber Strategy was formally adopted in 2020 "
                                   "following extensive review. " * 3)
            record_fetched_url(_nli_src, filename="sources/nli_page.md")
            _IN_MEMORY_FS["sources/nli_page.md"] = _nli_source_text
            _IN_MEMORY_FS["findings.md"] = f"- hallado ({_nli_src})"
            claim_line = f"- The strategy launched in 2020 under a different name [gov]({_nli_src})"
            _IN_MEMORY_FS["final_report.md"] = claim_line
            q_ctx.set({"delegate_tasks": {"used": 1, "limit": 5}})

            # Contradiction mocked -> nli_unsupported verdict, quarantined, distinctive nudge.
            with patch.object(_grounding_mod, "_get_nli_model", return_value=_FakeModel(0)):
                with tempfile.TemporaryDirectory() as tmpdir5:
                    rs = RunState(tmpdir5)
                    run_state_ctx.set(rs)
                    msgs = []
                    should_retry, _ = _asyncio.run(run_completion_check(
                        query="q", current_input="q", run_state=rs, notify=msgs.append))
                    recorded = rs.data["completion_check_attempts"][-1]["problem"]
                    assert recorded == "nli_unsupported", (recorded, msgs)
                    assert should_retry
                    assert "isn't actually entailed" in msgs[-1] or "NOT actually supported" in msgs[-1], msgs

            # Entailment/neutral mocked (never contradiction) -> clean pass, confirming the new
            # check doesn't regress the existing clean-pass path once wired in.
            with patch.object(_grounding_mod, "_get_nli_model", return_value=_FakeModel(2)):
                with tempfile.TemporaryDirectory() as tmpdir6:
                    rs = RunState(tmpdir6)
                    run_state_ctx.set(rs)
                    msgs = []
                    should_retry, _ = _asyncio.run(run_completion_check(
                        query="q", current_input="q", run_state=rs, notify=msgs.append))
                    recorded = rs.data["completion_check_attempts"][-1]["problem"]
                    assert recorded is None, (recorded, msgs)
                    assert not should_retry
        finally:
            _IN_MEMORY_FS.clear()
            _IN_MEMORY_FS.update(saved_fs)
            reset_fetched_urls()
            if _orig_ws6 is None:
                _config.cfg["settings"].pop("workspace", None)
            else:
                _config.cfg["settings"]["workspace"] = _orig_ws6
            if _orig_gc6 is None:
                _config.cfg["settings"].pop("grounding_check", None)
            else:
                _config.cfg["settings"]["grounding_check"] = _orig_gc6

    contextvars.copy_context().run(_nli_verify_scenario)

    # --- ROADMAP Phase 4: topical-relevance cross-encoder reranker (the GOA-algorithm vs.
    # Goa-the-Indian-state acronym collision from ROADMAP "Findings from live testing" — term
    # overlap passes ('2024' shared) and NLI wouldn't contradict it (an EV-policy sentence doesn't
    # CONTRADICT an algorithm claim, it's just unrelated), so only a topical-relevance judgment
    # catches it. Real BAAI/bge-reranker-v2-m3 isn't loaded in this fast suite -- mocked at
    # utils.grounding._get_topical_relevance_model to test WIRING correctness, same boundary as
    # _nli_verify_scenario above. ---
    def _topical_relevance_scenario():
        from tools.fs import _IN_MEMORY_FS
        from tools.core import tool_quotas_ctx as q_ctx
        from unittest.mock import patch
        import utils.grounding as _grounding_mod

        class _FakeRerankerModel:
            def __init__(self, score):
                self._score = score
            def predict(self, pairs):
                return [self._score for _ in pairs]

        _orig_ws7 = _config.cfg.get("settings", {}).get("workspace")
        _config.cfg["settings"]["workspace"] = {"type": "memory", "required_artifact": "final_report.md"}
        # nli_verify off (this scenario isn't testing NLI wiring, and leaving it on would call the
        # real NLI model unmocked -- same anti-pattern the matrix's own guard exists to prevent).
        _orig_gc7 = _config.cfg.get("settings", {}).get("grounding_check")
        _config.cfg["settings"]["grounding_check"] = {"nli_verify": False}
        saved_fs = dict(_IN_MEMORY_FS)
        try:
            _IN_MEMORY_FS.clear()
            reset_fetched_urls()
            _goa_src = "https://goa.example.co/ev-policy"
            _goa_source_text = ("Source-URL: " + _goa_src + "\n\n"
                                 "Goa announced new electric vehicle incentives for residents "
                                 "in 2024, part of the state's broader transport policy. " * 3)
            record_fetched_url(_goa_src, filename="sources/goa_page.md")
            _IN_MEMORY_FS["sources/goa_page.md"] = _goa_source_text
            _IN_MEMORY_FS["findings.md"] = f"- hallado ({_goa_src})"
            # Shares the checkable term '2024' with the source, so claim_grounding_problem's
            # term-overlap passes outright -- exactly the failure shape this check exists for.
            claim_line = f"- The GOA algorithm improved convergence results in 2024 [source]({_goa_src})"
            _IN_MEMORY_FS["final_report.md"] = claim_line
            q_ctx.set({"delegate_tasks": {"used": 1, "limit": 5}})

            # Low relevance score mocked -> topical_mismatch verdict, quarantined, distinctive nudge.
            with patch.object(_grounding_mod, "_get_topical_relevance_model", return_value=_FakeRerankerModel(0.01)):
                with tempfile.TemporaryDirectory() as tmpdir7a:
                    rs = RunState(tmpdir7a)
                    run_state_ctx.set(rs)
                    msgs = []
                    should_retry, _ = _asyncio.run(run_completion_check(
                        query="q", current_input="q", run_state=rs, notify=msgs.append))
                    recorded = rs.data["completion_check_attempts"][-1]["problem"]
                    assert recorded == "topical_mismatch", (recorded, msgs)
                    assert should_retry
                    assert "different subject" in msgs[-1] or "DIFFERENT SUBJECT" in msgs[-1], msgs

            # High relevance score mocked -> clean pass, confirming the new check doesn't regress
            # the existing clean-pass path once wired in.
            with patch.object(_grounding_mod, "_get_topical_relevance_model", return_value=_FakeRerankerModel(0.95)):
                with tempfile.TemporaryDirectory() as tmpdir7b:
                    rs = RunState(tmpdir7b)
                    run_state_ctx.set(rs)
                    msgs = []
                    should_retry, _ = _asyncio.run(run_completion_check(
                        query="q", current_input="q", run_state=rs, notify=msgs.append))
                    recorded = rs.data["completion_check_attempts"][-1]["problem"]
                    assert recorded is None, (recorded, msgs)
                    assert not should_retry
        finally:
            _IN_MEMORY_FS.clear()
            _IN_MEMORY_FS.update(saved_fs)
            reset_fetched_urls()
            if _orig_ws7 is None:
                _config.cfg["settings"].pop("workspace", None)
            else:
                _config.cfg["settings"]["workspace"] = _orig_ws7
            if _orig_gc7 is None:
                _config.cfg["settings"].pop("grounding_check", None)
            else:
                _config.cfg["settings"]["grounding_check"] = _orig_gc7

    contextvars.copy_context().run(_topical_relevance_scenario)

    # --- ROADMAP Phase 5: coverage accounting (RunState.coverage(), pure-function, no fetched-fs
    # dependency) ---
    def _coverage_scenario():
        from utils.run_state import RunState
        with tempfile.TemporaryDirectory() as tmpdir:
            # No findings at all -> vacuously "fully covered" (ratio 1.0, total 0) -- an empty run
            # must never look like a coverage FAILURE, that's missing_findings/not_delegated's job.
            rs = RunState(tmpdir)
            cov = rs.coverage()
            assert cov == {"total": 0, "covered": 0, "ratio": 1.0, "uncovered_task_names": []}, cov

            # A single top-level task with a real fetched URL -> fully covered.
            rs2 = RunState(tmpdir)
            rs2.add_finding("https://a.example.co/x", "summary", task_name="Background", depth=1)
            cov2 = rs2.coverage()
            assert cov2 == {"total": 1, "covered": 1, "ratio": 1.0, "uncovered_task_names": []}, cov2

            # Nested Analyzer-tier (depth=2) findings with no URL of their own must NOT count
            # against coverage -- that's expected, not a gap (see coverage()'s own docstring).
            rs3 = RunState(tmpdir)
            rs3.add_finding("https://a.example.co/x", "summary", task_name="Background", depth=1)
            rs3.add_finding("Background", "analyzer summary, no new URL", task_name="Analyze x", depth=2)
            cov3 = rs3.coverage()
            assert cov3 == {"total": 1, "covered": 1, "ratio": 1.0, "uncovered_task_names": []}, cov3

            # Three top-level tasks, only one with a real URL -> thin (ratio 1/3).
            rs4 = RunState(tmpdir)
            rs4.add_finding("https://a.example.co/x", "summary", task_name="Background", depth=1)
            rs4.add_finding("Comparison A", "found nothing usable", task_name="Comparison A", depth=1)
            rs4.add_finding("Comparison B", "found nothing usable", task_name="Comparison B", depth=1)
            cov4 = rs4.coverage()
            assert cov4["total"] == 3 and cov4["covered"] == 1, cov4
            assert abs(cov4["ratio"] - 1 / 3) < 1e-9, cov4
            assert set(cov4["uncovered_task_names"]) == {"Comparison A", "Comparison B"}, cov4

    _coverage_scenario()

    # --- ROADMAP Phase 5 follow-up (2026-07-14, live-caught): Builder/FindingsWriter/PeerReviewer
    # dispatches must never feed RunState.add_finding's coverage bookkeeping -- they land at
    # delegation_depth_ctx==1 exactly like a genuine Planner-delegated research task (see
    # orchestrator.py's _NON_RESEARCH_DISPATCH_ROLES comment) and none of them can ever have a
    # real source URL. Pins the exact role set the add_finding call site excludes. ---
    def _non_research_dispatch_roles_scenario():
        from engine.orchestrator import _NON_RESEARCH_DISPATCH_ROLES
        assert _NON_RESEARCH_DISPATCH_ROLES == {"Builder", "FindingsWriter", "PeerReviewer"}, (
            _NON_RESEARCH_DISPATCH_ROLES)

    _non_research_dispatch_roles_scenario()

    # --- ROADMAP "B4": run_cli/run_agent's duplicated malformed-tool-call retry logic extracted
    # into classify_malformed_retry (engine/orchestrator.py) -- pure decision logic, no event loop,
    # no I/O, unit-testable directly with a fake exception. ---
    def _malformed_retry_scenario():
        from engine.orchestrator import classify_malformed_retry

        class _FakeMalformedError(Exception):
            def __str__(self):
                return "error parsing tool call: bad escape"

        class _FakeOtherError(Exception):
            def __str__(self):
                return "some unrelated failure"

        # 1. Recognized class, under retry budget -> should_retry True, nudge appended, counter bumped.
        r = classify_malformed_retry(_FakeMalformedError(), malformed_retries=0, current_input="query")
        assert r.should_retry and not r.reraise and not r.force_final_verdict
        assert r.new_malformed_retries == 1
        assert isinstance(r.new_current_input, list) and len(r.new_current_input) == 2
        assert r.new_current_input[0] == "query"

        # 2. Recognized class, list current_input -> appended, not replaced/wrapped again.
        r2 = classify_malformed_retry(_FakeMalformedError(), malformed_retries=0, current_input=["a", "b"])
        assert r2.new_current_input == ["a", "b", r2.new_current_input[-1]]

        # 3. Recognized class, retry budget exhausted (== max_retries) -> force_final_verdict, no retry.
        r3 = classify_malformed_retry(_FakeMalformedError(), malformed_retries=2, current_input="q")
        assert not r3.should_retry and r3.force_final_verdict and not r3.reraise
        assert r3.new_malformed_retries == 2  # unchanged when not retrying

        # 4. Unrecognized exception class -> reraise True, no retry, no final-verdict force, counter
        #    unchanged, current_input echoed back untouched.
        r4 = classify_malformed_retry(_FakeOtherError(), malformed_retries=0, current_input="q")
        assert r4.reraise and not r4.should_retry and not r4.force_final_verdict
        assert r4.new_current_input == "q"
        assert r4.new_malformed_retries == 0

        # 5. Boundary: exactly max_retries-1 still retries (last allowed retry).
        r5 = classify_malformed_retry(_FakeMalformedError(), malformed_retries=1, current_input="q")
        assert r5.should_retry and r5.new_malformed_retries == 2

    _malformed_retry_scenario()

    # --- ROADMAP "B4": run_cli's deadline-racing stream iteration (previously inline, manually
    # driving stream.__aiter__() + asyncio.wait_for) extracted into iter_agent_stream
    # (engine/orchestrator.py), now shared with run_agent (TUI, deadline=None). ---
    async def _iter_agent_stream_scenario():
        import asyncio as _asyncio
        import time as _time
        from engine.orchestrator import iter_agent_stream

        class _FakeStream:
            def __init__(self, items, delays=None):
                self._items = list(items)
                self._delays = delays or [0] * len(self._items)
            def __aiter__(self):
                return self._gen()
            async def _gen(self):
                for item, delay in zip(self._items, self._delays):
                    if delay:
                        await _asyncio.sleep(delay)
                    yield item

        # deadline=None: unbounded, yields everything, no exception.
        out = [u async for u in iter_agent_stream(_FakeStream(["a", "b", "c"]), None)]
        assert out == ["a", "b", "c"]

        # deadline far in the future: same as unbounded for a fast fake stream.
        out2 = [u async for u in iter_agent_stream(_FakeStream(["x"]), _time.monotonic() + 5)]
        assert out2 == ["x"]

        # deadline already passed: first __anext__ should raise asyncio.TimeoutError immediately,
        # yielding nothing.
        got_timeout = False
        try:
            async for _ in iter_agent_stream(_FakeStream(["a"]), _time.monotonic() - 1):
                pass
        except _asyncio.TimeoutError:
            got_timeout = True
        assert got_timeout

        # deadline that expires mid-stream (between item 1 and item 2, via an injected delay) ->
        # partial yield then TimeoutError, not silently truncated/swallowed.
        seen = []
        got_timeout2 = False
        try:
            async for u in iter_agent_stream(_FakeStream(["a", "b"], delays=[0, 0.3]), _time.monotonic() + 0.1):
                seen.append(u)
        except _asyncio.TimeoutError:
            got_timeout2 = True
        assert seen == ["a"] and got_timeout2

    import asyncio as _asyncio_b4
    _asyncio_b4.run(_iter_agent_stream_scenario())

    # --- check_thin_coverage wiring (mirrors _line_claim_scenario's directness -- pure RunState
    # setup, no fetched-fs dependency needed since this check doesn't read workspace content) ---
    def _thin_coverage_wiring_scenario():
        from tools.fs import _IN_MEMORY_FS
        from tools.core import tool_quotas_ctx as q_ctx
        from utils.run_state import RunState

        _orig_ws10 = _config.cfg.get("settings", {}).get("workspace")
        _config.cfg["settings"]["workspace"] = {"type": "memory", "required_artifact": "final_report.md"}
        saved_fs = dict(_IN_MEMORY_FS)
        try:
            _IN_MEMORY_FS.clear()
            reset_fetched_urls()
            q_ctx.set({"delegate_tasks": {"used": 1, "limit": 5}})

            # (a) 1/3 top-level tasks covered -> thin_coverage fires BEFORE missing_findings even
            # gets a chance to (COMPLETION_CHECKS order), current_input grows via the classic path
            # (not Builder/FindingsWriter-fixable -- this needs new delegation, not a rewrite).
            with tempfile.TemporaryDirectory() as tmpdir_a:
                rs = RunState(tmpdir_a)
                rs.add_finding("https://a.example.co/x", "summary", task_name="Background", depth=1)
                rs.add_finding("Comparison A", "found nothing usable", task_name="Comparison A", depth=1)
                rs.add_finding("Comparison B", "found nothing usable", task_name="Comparison B", depth=1)
                run_state_ctx.set(rs)
                msgs = []
                should_retry, new_input = _asyncio.run(run_completion_check(
                    query="q", current_input="q", run_state=rs, notify=msgs.append))
                recorded = rs.data["completion_check_attempts"][-1]["problem"]
                assert recorded == "thin_coverage", (recorded, msgs)
                assert should_retry
                assert isinstance(new_input, list) and len(new_input) == 2, new_input
                assert "Comparison A" in msgs[-1] and "1/3" in msgs[-1], msgs

            # (b) a single-task query that succeeded -> never flagged, regardless of "breadth"
            # (min_tasks gate wouldn't even matter here since ratio is already 1.0).
            with tempfile.TemporaryDirectory() as tmpdir_b:
                rs = RunState(tmpdir_b)
                rs.add_finding("https://a.example.co/x", "summary", task_name="Simple lookup", depth=1)
                run_state_ctx.set(rs)
                msgs = []
                should_retry, _ = _asyncio.run(run_completion_check(
                    query="q", current_input="q", run_state=rs, notify=msgs.append))
                recorded = rs.data["completion_check_attempts"][-1]["problem"]
                assert recorded != "thin_coverage", (recorded, msgs)

            # (c) 1/2 covered, but min_tasks default is 2 so this DOES have enough signal to fire
            # -- confirms the threshold math itself (1/2 == 0.5, NOT below threshold 0.5 -> must
            # NOT fire, since the check is "below threshold", not "at or below").
            with tempfile.TemporaryDirectory() as tmpdir_c:
                rs = RunState(tmpdir_c)
                rs.add_finding("https://a.example.co/x", "summary", task_name="Background", depth=1)
                rs.add_finding("Comparison A", "found nothing usable", task_name="Comparison A", depth=1)
                run_state_ctx.set(rs)
                msgs = []
                should_retry, _ = _asyncio.run(run_completion_check(
                    query="q", current_input="q", run_state=rs, notify=msgs.append))
                recorded = rs.data["completion_check_attempts"][-1]["problem"]
                assert recorded != "thin_coverage", (
                    "ratio exactly AT threshold (0.5) must not fire -- only below it", recorded, msgs)
        finally:
            _IN_MEMORY_FS.clear()
            _IN_MEMORY_FS.update(saved_fs)
            reset_fetched_urls()
            if _orig_ws10 is None:
                _config.cfg["settings"].pop("workspace", None)
            else:
                _config.cfg["settings"]["workspace"] = _orig_ws10

    contextvars.copy_context().run(_thin_coverage_wiring_scenario)

    # --- missing_artifact escalation (live case 2026-07-12: 24 real fetched URLs + a populated
    # findings.md, but the model still got this nudge 5x verbatim and never once attempted
    # write_workspace_file). Two behaviors added: findings.md content quoted directly in the
    # nudge, and wording/attempt-budget escalate once the SAME problem repeats. ---
    def _missing_artifact_escalation_scenario():
        from tools.fs import _IN_MEMORY_FS
        _orig_ws5 = _config.cfg.get("settings", {}).get("workspace")
        _config.cfg["settings"]["workspace"] = {"type": "memory", "required_artifact": "final_report.md"}
        saved_fs = dict(_IN_MEMORY_FS)
        try:
            from tools.core import tool_quotas_ctx as q_ctx
            _IN_MEMORY_FS.clear()
            reset_fetched_urls()
            record_fetched_url(_SRC, filename="sources/page.md")
            _IN_MEMORY_FS["sources/page.md"] = _SOURCE_TEXT
            _IN_MEMORY_FS["findings.md"] = "- Real finding with a real cited URL (" + _SRC + ")"
            q_ctx.set({"delegate_tasks": {"used": 1, "limit": 5}})

            # First occurrence: findings.md content must appear verbatim in the nudge, and the
            # wording must be the fresh (not-yet-escalated) framing.
            with tempfile.TemporaryDirectory() as tmpdir2:
                rs = RunState(tmpdir2)
                run_state_ctx.set(rs)
                msgs = []
                should_retry, new_input = _asyncio.run(run_completion_check(
                    query="q", current_input="q", run_state=rs, notify=msgs.append))
                assert should_retry, "first missing_artifact occurrence must still retry"
                injected = new_input[-1].contents[0].text
                assert "Real finding with a real cited URL" in injected, (
                    "findings.md content must be quoted directly in the missing_artifact nudge", injected)
                assert "STILL missing" not in injected, "first occurrence must use the fresh framing"

            # Second consecutive occurrence: with the threshold at 3, this is the LAST retry
            # nudge that will ever actually be built for this problem (the 3rd occurrence gets
            # cut off before a nudge is constructed at all) — wording must already be the
            # strongest framing, not a middle step implying more chances remain.
            with tempfile.TemporaryDirectory() as tmpdir3:
                rs = RunState(tmpdir3)
                rs.data["completion_check_attempts"] = [
                    {"attempt": 0, "problem": "missing_artifact"},
                ]
                run_state_ctx.set(rs)
                msgs = []
                should_retry, new_input = _asyncio.run(run_completion_check(
                    query="q", current_input="q", run_state=rs, notify=msgs.append))
                assert should_retry, "2nd consecutive occurrence must still retry"
                injected = new_input[-1].contents[0].text
                assert "last realistic chance" in injected, (
                    "2nd consecutive missing_artifact must use the escalated framing", injected)

            # Third consecutive occurrence (2026-07-22, force_whole_rebuild): the escalation
            # threshold now grants exactly ONE extra, differently-framed retry -- a full
            # "reconsider your whole approach" rebuild -- instead of immediately forcing the
            # final-verdict path. This is the one behavior change from the old early-exit-only
            # design (ACM CAIS '26 planning-horizon paper: full-horizon replanning beats
            # single-step patching).
            with tempfile.TemporaryDirectory() as tmpdir4:
                rs = RunState(tmpdir4)
                rs.data["completion_check_attempts"] = [
                    {"attempt": 0, "problem": "missing_artifact"},
                    {"attempt": 1, "problem": "missing_artifact"},
                ]
                run_state_ctx.set(rs)
                msgs = []
                should_retry, new_input = _asyncio.run(run_completion_check(
                    query="q", current_input="q", run_state=rs, notify=msgs.append))
                assert should_retry, (
                    "3rd consecutive missing_artifact must get exactly one whole-approach "
                    "rebuild retry, not stop immediately", msgs)
                injected = new_input[-1].contents[0].text
                assert "reconsider your whole approach" in injected, injected
                assert rs.data["whole_approach_retry_used_for"] == {"missing_artifact": True}, rs.data

            # Fourth consecutive occurrence: the one whole-approach retry for this problem has
            # already been used (whole_approach_retry_used_for), so this now falls through to the
            # pre-existing early-exit behavior unchanged -- bounded to exactly one extra attempt,
            # never an unbounded loop.
            with tempfile.TemporaryDirectory() as tmpdir5:
                rs = RunState(tmpdir5)
                rs.data["completion_check_attempts"] = [
                    {"attempt": 0, "problem": "missing_artifact"},
                    {"attempt": 1, "problem": "missing_artifact"},
                    {"attempt": 2, "problem": "missing_artifact"},
                ]
                rs.data["whole_approach_retry_used_for"] = {"missing_artifact": True}
                run_state_ctx.set(rs)
                msgs = []
                should_retry, _ = _asyncio.run(run_completion_check(
                    query="q", current_input="q", run_state=rs, notify=msgs.append))
                assert not should_retry, (
                    "4th consecutive missing_artifact, whole-approach retry already spent, must "
                    "escalate straight to the final verdict", msgs)
        finally:
            _IN_MEMORY_FS.clear()
            _IN_MEMORY_FS.update(saved_fs)
            reset_fetched_urls()
            if _orig_ws5 is None:
                _config.cfg["settings"].pop("workspace", None)
            else:
                _config.cfg["settings"]["workspace"] = _orig_ws5

    contextvars.copy_context().run(_missing_artifact_escalation_scenario)

    # --- Generalized escalation guard (2026-07-19 QA audit): the early-cutoff threshold used to
    # be hardcoded to problem == "missing_artifact" only. Live-confirmed gap: thin_coverage has no
    # guard at all and burned a full 8-attempt budget on an identical repeated failure. The guard
    # now applies to every problem except the deliberately-excluded missing_findings (see its own
    # scenario above). Reuses scenario (a)'s 1/3-covered fixture from _thin_coverage_wiring_scenario
    # to drive 3 consecutive thin_coverage occurrences. ---
    def _thin_coverage_escalation_scenario():
        from tools.fs import _IN_MEMORY_FS
        from tools.core import tool_quotas_ctx as q_ctx
        _orig_ws11 = _config.cfg.get("settings", {}).get("workspace")
        _config.cfg["settings"]["workspace"] = {"type": "memory", "required_artifact": "final_report.md"}
        saved_fs = dict(_IN_MEMORY_FS)
        try:
            _IN_MEMORY_FS.clear()
            reset_fetched_urls()
            q_ctx.set({"delegate_tasks": {"used": 1, "limit": 5}})

            def _thin_run_state(tmpdir):
                rs = RunState(tmpdir)
                rs.add_finding("https://a.example.co/x", "summary", task_name="Background", depth=1)
                rs.add_finding("Comparison A", "found nothing usable", task_name="Comparison A", depth=1)
                rs.add_finding("Comparison B", "found nothing usable", task_name="Comparison B", depth=1)
                return rs

            # 2nd consecutive occurrence: still under threshold (3), must still retry.
            with tempfile.TemporaryDirectory() as tmpdir9:
                rs = _thin_run_state(tmpdir9)
                rs.data["completion_check_attempts"] = [
                    {"attempt": 0, "problem": "thin_coverage"},
                ]
                run_state_ctx.set(rs)
                msgs = []
                should_retry, _ = _asyncio.run(run_completion_check(
                    query="q", current_input="q", run_state=rs, notify=msgs.append))
                assert should_retry, "2nd consecutive thin_coverage must still retry"

            # 3rd consecutive occurrence (2026-07-22, force_whole_rebuild): the generalized guard
            # grants exactly one extra whole-approach retry, same as missing_artifact's above --
            # not an immediate final-verdict escalation.
            with tempfile.TemporaryDirectory() as tmpdir10:
                rs = _thin_run_state(tmpdir10)
                rs.data["completion_check_attempts"] = [
                    {"attempt": 0, "problem": "thin_coverage"},
                    {"attempt": 1, "problem": "thin_coverage"},
                ]
                run_state_ctx.set(rs)
                msgs = []
                should_retry, new_input = _asyncio.run(run_completion_check(
                    query="q", current_input="q", run_state=rs, notify=msgs.append))
                assert should_retry, (
                    "3rd consecutive thin_coverage must get exactly one whole-approach rebuild "
                    "retry, not stop immediately", msgs)
                injected = new_input[-1].contents[0].text
                assert "reconsider your whole approach" in injected, injected

            # 4th consecutive occurrence: whole-approach retry already spent for this problem ->
            # falls through to the pre-existing early-exit behavior, same as missing_artifact.
            with tempfile.TemporaryDirectory() as tmpdir11:
                rs = _thin_run_state(tmpdir11)
                rs.data["completion_check_attempts"] = [
                    {"attempt": 0, "problem": "thin_coverage"},
                    {"attempt": 1, "problem": "thin_coverage"},
                    {"attempt": 2, "problem": "thin_coverage"},
                ]
                rs.data["whole_approach_retry_used_for"] = {"thin_coverage": True}
                run_state_ctx.set(rs)
                msgs = []
                should_retry, _ = _asyncio.run(run_completion_check(
                    query="q", current_input="q", run_state=rs, notify=msgs.append))
                assert not should_retry, (
                    "4th consecutive thin_coverage, whole-approach retry already spent, must "
                    "escalate straight to the final verdict -- the generalized guard, not just "
                    "missing_artifact-specific", msgs)
        finally:
            _IN_MEMORY_FS.clear()
            _IN_MEMORY_FS.update(saved_fs)
            reset_fetched_urls()
            if _orig_ws11 is None:
                _config.cfg["settings"].pop("workspace", None)
            else:
                _config.cfg["settings"]["workspace"] = _orig_ws11

    contextvars.copy_context().run(_thin_coverage_escalation_scenario)

    # --- check_thin_coverage against REAL captured data, not just hand-written fixtures (2026-07-19
    # QA audit finding: all 16 completion checks were tested with synthetic fixtures only, even
    # though matching real data already exists unused in finetune/data/thin_coverage.jsonl and the
    # research_output/ run directories it was extracted from). Loads the actual persisted
    # _run_state.json from a real run that genuinely tripped thin_coverage at attempt 0, replays
    # only the findings that existed BEFORE that attempt's own timestamp (the file's final state
    # has more findings added afterward), and confirms check_thin_coverage reproduces the exact
    # 1/3 coverage ratio that was actually recorded live at the time. ---
    def _real_thin_coverage_data_scenario():
        import json as _json
        from utils.run_state import RunState
        from engine.completion import Ctx, check_thin_coverage
        run_dir = os.path.join(
            os.path.dirname(__file__), "research_output",
            "compare_the_current_experimental_status_of_two_sea_20260718_171236")
        state_path = os.path.join(run_dir, "_run_state.json")
        if not os.path.exists(state_path):
            # Real run directory not present in this checkout -- skip rather than silently
            # falling back to a synthetic fixture.
            return
        with open(state_path, encoding="utf-8") as f:
            real_data = _json.load(f)
        attempt0 = next(a for a in real_data["completion_check_attempts"] if a["attempt"] == 0)
        assert attempt0["problem"] == "thin_coverage", attempt0
        cutoff = attempt0["timestamp"]
        rs = RunState(run_dir)
        rs.data["findings"] = [f for f in real_data["findings"] if f.get("timestamp", 0) < cutoff]
        rs.data["query"] = real_data["query"]
        ctx = Ctx(req_artifact="final_report.md", attempt=0, max_attempts=8, delegated=True,
                  files=[], content=None, quotas=None, run_state=rs)
        verdict = check_thin_coverage(ctx)
        assert verdict is not None and verdict.problem == "thin_coverage", (
            "replaying the real pre-attempt-0 findings must reproduce the same thin_coverage "
            "verdict actually recorded live", verdict)
        cov = rs.coverage()
        assert "1/3" in attempt0["detail"], attempt0["detail"]
        assert cov["total"] == 3 and cov["covered"] == 1, (
            "replaying the real findings must reproduce the exact 1/3 coverage ratio actually "
            "recorded live in this run's own _run_state.json", cov)

    contextvars.copy_context().run(_real_thin_coverage_data_scenario)

    # --- Engine-driven iterative deepening (2026-07-19, ROADMAP item 10): when thin_coverage fires
    # AND a covered task's real 'FOLLOW-UP DIRECTIONS:' section named a real lead, run_completion_check
    # must dispatch it directly via dispatch_task (bypassing the Planner, same mechanism as Builder/
    # FindingsWriter) instead of injecting the classic Planner nudge -- and fall back to that classic
    # nudge unchanged when there's nothing real to act on, or the round budget is spent. ---
    def _deepening_round_scenario():
        from tools.fs import _IN_MEMORY_FS
        from tools.core import tool_quotas_ctx as q_ctx
        from unittest.mock import AsyncMock
        from engine.completion import _select_deepening_tasks

        _orig_ws13 = _config.cfg.get("settings", {}).get("workspace")
        _config.cfg["settings"]["workspace"] = {"type": "memory", "required_artifact": "final_report.md"}
        saved_fs = dict(_IN_MEMORY_FS)

        def _thin_run_state_with_direction(tmpdir):
            rs = RunState(tmpdir)
            rs.add_finding("https://real.example.com/x", "real covered summary", task_name="Task A",
                            depth=1, follow_up_directions=["Chase the real lead X"], agent_id="AcademicSearcher")
            rs.add_finding("Task B", "no source", task_name="Task B", depth=1)
            rs.add_finding("Task C", "no source", task_name="Task C", depth=1)
            return rs

        try:
            _IN_MEMORY_FS.clear()
            reset_fetched_urls()
            q_ctx.set({"delegate_tasks": {"used": 1, "limit": 5}})

            # (a) direct unit check of the selection logic: only the covered task's direction is a
            # candidate, routed to its OWN recorded agent_id (not a hardcoded default).
            with tempfile.TemporaryDirectory() as tmpdir_sel:
                rs = _thin_run_state_with_direction(tmpdir_sel)
                selected = _select_deepening_tasks(rs)
                assert len(selected) == 1, selected
                assert selected[0]["instructions"] == "Chase the real lead X", selected
                assert selected[0]["agent_id"] == "AcademicSearcher", selected

            # (b) real directions exist -> run_completion_check dispatches the deepening round
            # directly (never touches the Planner's current_input) instead of injecting the classic
            # nudge, and increments deepening_round on RunState.
            with tempfile.TemporaryDirectory() as tmpdir_a:
                rs = _thin_run_state_with_direction(tmpdir_a)
                run_state_ctx.set(rs)
                msgs = []
                dispatch = AsyncMock(return_value="## Result for Follow-up\nfound more\n---")
                orig_input = "q"
                should_retry, new_input = _asyncio.run(run_completion_check(
                    query="q", current_input=orig_input, run_state=rs, notify=msgs.append,
                    dispatch_task=dispatch))
                assert should_retry, "a dispatched deepening round must still retry (more research just happened)"
                # Unlike Builder/FindingsWriter's dispatch (which resolves the SPECIFIC verdict it
                # was fixing, guaranteeing current_input stays unchanged), a deepening round
                # dispatches MORE research -- whether current_input changes afterward depends on
                # whether that new research closes the coverage gap on the next internal check, not
                # a fixed invariant. What matters here is that the FIRST attempt bypassed the
                # Planner entirely (dispatch_task called directly, not an injected message).
                assert dispatch.call_count == 1, dispatch.call_args_list
                assert dispatch.call_args_list[0].args == ("Follow-up: Chase the real lead X", "Chase the real lead X", "AcademicSearcher")
                assert rs.data["deepening_round"] == 1, rs.data
                assert "Chase the real lead X" in rs.data["consumed_directions"], rs.data
                assert "deepening" in msgs[0].lower(), (
                    "the round must be announced as the FIRST message, before any classic nudge "
                    "text a later internal iteration might add", msgs)

            # (c) no real directions on record -> falls through to the classic thin_coverage nudge,
            # unchanged (should_retry True, current_input GROWN via the classic injected-message path).
            with tempfile.TemporaryDirectory() as tmpdir_b:
                rs = RunState(tmpdir_b)
                rs.add_finding("https://real.example.com/x", "real covered summary, no directions",
                                task_name="Task A", depth=1)
                rs.add_finding("Task B", "no source", task_name="Task B", depth=1)
                rs.add_finding("Task C", "no source", task_name="Task C", depth=1)
                run_state_ctx.set(rs)
                msgs = []
                dispatch = AsyncMock()
                should_retry, new_input = _asyncio.run(run_completion_check(
                    query="q", current_input="q", run_state=rs, notify=msgs.append,
                    dispatch_task=dispatch))
                assert should_retry
                assert dispatch.call_count == 0, (
                    "no real directions exist -- dispatch_task must never be called", dispatch.call_args_list)
                assert isinstance(new_input, list), (
                    "must fall back to the classic injected-message path", new_input)
                assert rs.data.get("deepening_round", 0) == 0, rs.data

            # (d) round budget already exhausted -> falls through to the classic nudge even though
            # real directions exist, same as (c).
            with tempfile.TemporaryDirectory() as tmpdir_c:
                rs = _thin_run_state_with_direction(tmpdir_c)
                rs.data["deepening_round"] = 1  # == default max_deepening_rounds
                run_state_ctx.set(rs)
                msgs = []
                dispatch = AsyncMock()
                should_retry, new_input = _asyncio.run(run_completion_check(
                    query="q", current_input="q", run_state=rs, notify=msgs.append,
                    dispatch_task=dispatch))
                assert should_retry
                assert dispatch.call_count == 0, (
                    "round budget already spent -- must not dispatch another round", dispatch.call_args_list)
                assert isinstance(new_input, list)
        finally:
            _IN_MEMORY_FS.clear()
            _IN_MEMORY_FS.update(saved_fs)
            reset_fetched_urls()
            if _orig_ws13 is None:
                _config.cfg["settings"].pop("workspace", None)
            else:
                _config.cfg["settings"]["workspace"] = _orig_ws13

    contextvars.copy_context().run(_deepening_round_scenario)

    # --- Builder Build->Review->Fix dispatch loop (see engine/completion.py's
    # _dispatch_writer_review_fix / _BUILDER_FIXABLE_PROBLEMS): for artifact-authoring problems,
    # run_completion_check must dispatch a fresh-context Builder (+PeerReviewer check) instead of
    # nudging the Planner's own current_input, when a dispatch_task callable is provided AND both
    # roles are registered. The core regression this guards against: current_input must come back
    # UNCHANGED from the input passed in (that's the actual context-growth fix).
    #
    # 2026-07-14 chaining update: a successful dispatch no longer returns immediately — it
    # `continue`s straight into the next completion-check iteration inside the SAME
    # run_completion_check call (see that function's docstring). So these mocks must actually
    # write grounded content into _IN_MEMORY_FS (not just return canned strings) — otherwise the
    # chained re-check sees the identical unresolved problem, tries to dispatch again, and exhausts
    # the mock's side_effect list. Once the mock genuinely fixes the artifact, the chain converges
    # to should_retry=False within this one call (flipped from should_retry=True pre-chaining). ---
    def _builder_dispatch_scenario():
        from tools.fs import _IN_MEMORY_FS
        from tools.core import tool_quotas_ctx as q_ctx
        from unittest.mock import AsyncMock
        from engine.orchestrator import available_sub_agents_ctx

        class _FakeSubAgentConfig:
            def __init__(self, name):
                self.name = name

        _CLEAN_REPORT = f"- el pais avanza de forma sostenida segun cifras oficiales [gov]({_SRC})"

        _orig_ws8 = _config.cfg.get("settings", {}).get("workspace")
        _config.cfg["settings"]["workspace"] = {"type": "memory", "required_artifact": "final_report.md"}
        _orig_gc8 = _config.cfg.get("settings", {}).get("grounding_check")
        _config.cfg["settings"]["grounding_check"] = {"nli_verify": False, "topical_relevance_check": False}
        saved_fs = dict(_IN_MEMORY_FS)
        try:
            _IN_MEMORY_FS.clear()
            reset_fetched_urls()
            record_fetched_url(_SRC, filename="sources/page.md")
            _IN_MEMORY_FS["sources/page.md"] = _SOURCE_TEXT
            _IN_MEMORY_FS["findings.md"] = _FINDINGS_OK
            q_ctx.set({"delegate_tasks": {"used": 1, "limit": 5}})
            available_sub_agents_ctx.set([_FakeSubAgentConfig("Builder"), _FakeSubAgentConfig("PeerReviewer")])

            # (a) PeerReviewer returns REVIEW: CLEAN -> exactly 2 dispatches (Builder, PeerReviewer);
            # Builder's mocked write actually grounds final_report.md, so the chained re-check finds
            # nothing wrong and converges within this call: should_retry=False, current_input
            # unchanged, one completion_check_attempts row recorded.
            with tempfile.TemporaryDirectory() as tmpdir_a:
                _IN_MEMORY_FS.pop("final_report.md", None)
                rs = RunState(tmpdir_a)
                run_state_ctx.set(rs)
                msgs = []

                async def _side_effect_a(name, instructions, role):
                    if role == "Builder":
                        _IN_MEMORY_FS["final_report.md"] = _CLEAN_REPORT
                        return "## Result for BuilderFix_attempt1\nWrote report\n---"
                    return "REVIEW: CLEAN\nNo issues found."

                dispatch = AsyncMock(side_effect=_side_effect_a)
                orig_input = "q"
                should_retry, new_input = _asyncio.run(run_completion_check(
                    query="q", current_input=orig_input, run_state=rs, notify=msgs.append,
                    dispatch_task=dispatch))
                assert not should_retry, (
                    "a Builder dispatch that genuinely fixes the artifact must converge within "
                    "this call instead of returning control to the Planner", msgs)
                assert new_input == orig_input, ("current_input must stay unchanged on Builder-fixable dispatch", new_input)
                assert dispatch.call_count == 2, dispatch.call_args_list
                assert dispatch.call_args_list[0].args[2] == "Builder", dispatch.call_args_list
                assert dispatch.call_args_list[1].args[2] == "PeerReviewer", dispatch.call_args_list
                assert rs.data["completion_check_attempts"][0]["problem"] == "missing_artifact"

            # (b) PeerReviewer returns REVIEW: ISSUES FOUND -> exactly 3 dispatches
            # (Builder, PeerReviewer, Builder again); the corrective Builder pass grounds the
            # report, so the chain still converges to should_retry=False, current_input unchanged.
            with tempfile.TemporaryDirectory() as tmpdir_b:
                _IN_MEMORY_FS.pop("final_report.md", None)
                rs = RunState(tmpdir_b)
                run_state_ctx.set(rs)
                msgs = []

                async def _side_effect_b(name, instructions, role):
                    if role == "Builder":
                        if "_reviewed" in name:
                            _IN_MEMORY_FS["final_report.md"] = _CLEAN_REPORT
                        else:
                            _IN_MEMORY_FS["final_report.md"] = "- some claim with no citation at all"
                        return "## Result for BuilderFix\nWrote report\n---"
                    return "REVIEW: ISSUES FOUND:\n- citation doesn't trace to findings.md"

                dispatch = AsyncMock(side_effect=_side_effect_b)
                orig_input = "q"
                should_retry, new_input = _asyncio.run(run_completion_check(
                    query="q", current_input=orig_input, run_state=rs, notify=msgs.append,
                    dispatch_task=dispatch))
                assert not should_retry, msgs
                assert new_input == orig_input
                assert dispatch.call_count == 3, dispatch.call_args_list
                assert dispatch.call_args_list[2].args[2] == "Builder", dispatch.call_args_list

            # (c) PeerReviewer response missing the REVIEW: sentinel entirely -> conservative
            # fallback treats it as ISSUES FOUND, still 3 dispatches (fail conservative, not
            # silent); the corrective pass still grounds the report so the chain converges.
            with tempfile.TemporaryDirectory() as tmpdir_c:
                _IN_MEMORY_FS.pop("final_report.md", None)
                rs = RunState(tmpdir_c)
                run_state_ctx.set(rs)
                msgs = []

                async def _side_effect_c(name, instructions, role):
                    if role == "Builder":
                        if "_reviewed" in name:
                            _IN_MEMORY_FS["final_report.md"] = _CLEAN_REPORT
                        else:
                            _IN_MEMORY_FS["final_report.md"] = "- some claim with no citation at all"
                        return "## Result for BuilderFix\nWrote report\n---"
                    return "Looks fine to me, no complaints."

                dispatch = AsyncMock(side_effect=_side_effect_c)
                should_retry, new_input = _asyncio.run(run_completion_check(
                    query="q", current_input="q", run_state=rs, notify=msgs.append,
                    dispatch_task=dispatch))
                assert not should_retry, msgs
                assert dispatch.call_count == 3, (
                    "a malformed/missing REVIEW: sentinel must be treated conservatively as "
                    "ISSUES FOUND, not silently accepted", dispatch.call_args_list)

            # (d) Builder/PeerReviewer not both registered -> falls back to classic
            # inject-into-Planner behavior, dispatch_task never called.
            with tempfile.TemporaryDirectory() as tmpdir_d:
                _IN_MEMORY_FS.pop("final_report.md", None)
                rs = RunState(tmpdir_d)
                run_state_ctx.set(rs)
                msgs = []
                available_sub_agents_ctx.set([_FakeSubAgentConfig("Builder")])  # PeerReviewer missing
                dispatch = AsyncMock()
                orig_input = "q"
                should_retry, new_input = _asyncio.run(run_completion_check(
                    query="q", current_input=orig_input, run_state=rs, notify=msgs.append,
                    dispatch_task=dispatch))
                assert should_retry
                dispatch.assert_not_called()
                assert isinstance(new_input, list) and len(new_input) == 2, (
                    "missing-registration fallback must still inject the classic nudge", new_input)
                available_sub_agents_ctx.set([_FakeSubAgentConfig("Builder"), _FakeSubAgentConfig("PeerReviewer")])

            # (e) missing_findings must NEVER dispatch BUILDER, even with a fully-registered
            # Builder+PeerReviewer pair and a working dispatch_task — that problem is
            # FindingsWriter-fixable, not Builder-fixable (see _FINDINGS_WRITER_FIXABLE_PROBLEMS).
            # Only "FindingsWriter" (not registered in THIS scenario) unlocks the dispatch path for
            # it — see _findings_writer_dispatch_scenario below for that path with FindingsWriter
            # actually registered. Here it must still grow current_input via the classic nudge path.
            with tempfile.TemporaryDirectory() as tmpdir_e:
                _IN_MEMORY_FS.pop("findings.md", None)
                rs = RunState(tmpdir_e)
                run_state_ctx.set(rs)
                msgs = []
                dispatch = AsyncMock()
                orig_input = "q"
                should_retry, new_input = _asyncio.run(run_completion_check(
                    query="q", current_input=orig_input, run_state=rs, notify=msgs.append,
                    dispatch_task=dispatch))
                assert should_retry
                dispatch.assert_not_called()
                assert isinstance(new_input, list) and new_input[-1] is not orig_input, (
                    "missing_findings must still use the classic inject-into-Planner path", new_input)
                assert rs.data["completion_check_attempts"][-1]["problem"] == "missing_findings"
                _IN_MEMORY_FS["findings.md"] = _FINDINGS_OK
        finally:
            _IN_MEMORY_FS.clear()
            _IN_MEMORY_FS.update(saved_fs)
            reset_fetched_urls()
            if _orig_ws8 is None:
                _config.cfg["settings"].pop("workspace", None)
            else:
                _config.cfg["settings"]["workspace"] = _orig_ws8
            if _orig_gc8 is None:
                _config.cfg["settings"].pop("grounding_check", None)
            else:
                _config.cfg["settings"]["grounding_check"] = _orig_gc8

    contextvars.copy_context().run(_builder_dispatch_scenario)

    # --- FindingsWriter Write->Review->Fix dispatch loop (2026-07-14 architecture change: the
    # Planner no longer writes findings.md itself — see _FINDINGS_WRITER_FIXABLE_PROBLEMS /
    # _build_findings_source_material / src/prompts.py's FINDINGS_WRITER_INSTRUCTIONS). Mirrors
    # _builder_dispatch_scenario above, one artifact earlier: current_input must come back
    # UNCHANGED (the same context-growth fix, now applied to findings.md too).
    #
    # 2026-07-14 chaining update: since required_artifact is final_report.md, fixing findings.md
    # alone is not enough for the chain to converge — the very next iteration re-checks and (if
    # final_report.md is still missing) finds a fresh missing_artifact problem. So the PRIMARY
    # case here (a) registers Builder too and asserts the full FindingsWriter->Builder chain
    # converges in one run_completion_check call. A narrower variant (d) pins the fallback that
    # still applies once the chain needs a writer role that isn't registered. ---
    def _findings_writer_dispatch_scenario():
        from tools.fs import _IN_MEMORY_FS
        from tools.core import tool_quotas_ctx as q_ctx
        from unittest.mock import AsyncMock
        from engine.orchestrator import available_sub_agents_ctx

        class _FakeSubAgentConfig:
            def __init__(self, name):
                self.name = name

        _CLEAN_REPORT = f"- el pais avanza de forma sostenida segun cifras oficiales [gov]({_SRC})"

        _orig_ws9 = _config.cfg.get("settings", {}).get("workspace")
        _config.cfg["settings"]["workspace"] = {"type": "memory", "required_artifact": "final_report.md"}
        _orig_gc9 = _config.cfg.get("settings", {}).get("grounding_check")
        _config.cfg["settings"]["grounding_check"] = {"nli_verify": False, "topical_relevance_check": False}
        saved_fs = dict(_IN_MEMORY_FS)
        try:
            _IN_MEMORY_FS.clear()
            reset_fetched_urls()
            record_fetched_url(_SRC, filename="sources/page.md")
            _IN_MEMORY_FS["sources/page.md"] = _SOURCE_TEXT
            # No findings.md yet -- the missing_findings shape.
            q_ctx.set({"delegate_tasks": {"used": 1, "limit": 5}})
            available_sub_agents_ctx.set([
                _FakeSubAgentConfig("FindingsWriter"), _FakeSubAgentConfig("PeerReviewer"),
                _FakeSubAgentConfig("Builder"),
            ])

            # (a) [PRIMARY CHAIN] missing_findings, FindingsWriter genuinely writes findings.md,
            # PeerReviewer CLEAN -> the chain immediately re-checks, finds final_report.md still
            # missing (missing_artifact), dispatches Builder, PeerReviewer CLEAN again -> converges
            # to should_retry=False within this ONE call. Exactly 4 dispatches total
            # (FindingsWriter, PeerReviewer, Builder, PeerReviewer), current_input unchanged — the
            # single best proof of the behavior this chaining fix exists for.
            with tempfile.TemporaryDirectory() as tmpdir_a:
                _IN_MEMORY_FS.pop("findings.md", None)
                _IN_MEMORY_FS.pop("final_report.md", None)
                rs = RunState(tmpdir_a)
                rs.add_finding(_SRC, "the real finding a dispatched Searcher actually returned")
                run_state_ctx.set(rs)
                msgs = []

                async def _side_effect_a(name, instructions, role):
                    if role == "FindingsWriter":
                        _IN_MEMORY_FS["findings.md"] = _FINDINGS_OK
                        return "## Result for FindingsWriterFix_attempt1\nWrote findings.md\n---"
                    if role == "Builder":
                        _IN_MEMORY_FS["final_report.md"] = _CLEAN_REPORT
                        return "## Result for BuilderFix_attempt1\nWrote report\n---"
                    return "REVIEW: CLEAN\nNo issues found."

                dispatch = AsyncMock(side_effect=_side_effect_a)
                orig_input = "q"
                should_retry, new_input = _asyncio.run(run_completion_check(
                    query="q", current_input=orig_input, run_state=rs, notify=msgs.append,
                    dispatch_task=dispatch))
                assert not should_retry, (
                    "a FindingsWriter dispatch that genuinely fixes findings.md must chain "
                    "straight into the Builder dispatch for final_report.md, converging within "
                    "this call instead of returning control to the Planner in between", msgs)
                assert new_input == orig_input, ("current_input must stay unchanged on FindingsWriter-fixable dispatch", new_input)
                assert dispatch.call_count == 4, dispatch.call_args_list
                assert dispatch.call_args_list[0].args[2] == "FindingsWriter", dispatch.call_args_list
                assert dispatch.call_args_list[1].args[2] == "PeerReviewer", dispatch.call_args_list
                assert dispatch.call_args_list[2].args[2] == "Builder", dispatch.call_args_list
                assert dispatch.call_args_list[3].args[2] == "PeerReviewer", dispatch.call_args_list
                # The real finding must actually reach FindingsWriter's dispatch instructions —
                # this is the whole point (a fresh context with no Planner conversation still
                # needs the real evidence, via _build_findings_source_material).
                write_instructions = dispatch.call_args_list[0].args[1]
                assert _SRC in write_instructions, "real fetched URL must reach FindingsWriter's instructions"
                assert rs.data["completion_check_attempts"][0]["problem"] == "missing_findings"

            # (b) findings_ungrounded (findings.md exists but cites nothing real), ISSUES FOUND ->
            # exactly 3 dispatches (FindingsWriter, PeerReviewer, FindingsWriter again).
            # final_report.md is pre-seeded with grounded content so the chain converges right
            # after findings.md is fixed, without needing to involve Builder at all here — that
            # combination is covered by (a) above.
            with tempfile.TemporaryDirectory() as tmpdir_b:
                _IN_MEMORY_FS["findings.md"] = "Some claim with no source at all."
                _IN_MEMORY_FS["final_report.md"] = _CLEAN_REPORT
                rs = RunState(tmpdir_b)
                rs.add_finding(_SRC, "the real finding a dispatched Searcher actually returned")
                run_state_ctx.set(rs)
                msgs = []

                async def _side_effect_b(name, instructions, role):
                    if role == "FindingsWriter":
                        _IN_MEMORY_FS["findings.md"] = _FINDINGS_OK
                        return "## Result for FindingsWriterFix\nWrote findings.md\n---"
                    return "REVIEW: ISSUES FOUND:\n- a finding's figure doesn't match its source"

                dispatch = AsyncMock(side_effect=_side_effect_b)
                orig_input = "q"
                should_retry, new_input = _asyncio.run(run_completion_check(
                    query="q", current_input=orig_input, run_state=rs, notify=msgs.append,
                    dispatch_task=dispatch))
                assert not should_retry, msgs
                assert new_input == orig_input
                assert dispatch.call_count == 3, dispatch.call_args_list
                assert dispatch.call_args_list[2].args[2] == "FindingsWriter", dispatch.call_args_list
                assert rs.data["completion_check_attempts"][0]["problem"] == "findings_ungrounded"
                _IN_MEMORY_FS.pop("findings.md", None)
                _IN_MEMORY_FS.pop("final_report.md", None)

            # (c) FindingsWriter/PeerReviewer not both registered -> falls back to the classic
            # inject-into-Planner path, dispatch_task never called.
            with tempfile.TemporaryDirectory() as tmpdir_c:
                rs = RunState(tmpdir_c)
                run_state_ctx.set(rs)
                msgs = []
                available_sub_agents_ctx.set([_FakeSubAgentConfig("FindingsWriter")])  # PeerReviewer missing
                dispatch = AsyncMock()
                orig_input = "q"
                should_retry, new_input = _asyncio.run(run_completion_check(
                    query="q", current_input=orig_input, run_state=rs, notify=msgs.append,
                    dispatch_task=dispatch))
                assert should_retry
                dispatch.assert_not_called()
                assert isinstance(new_input, list) and len(new_input) == 2, (
                    "missing-registration fallback must still inject the classic nudge", new_input)
                # The classic fallback text must never INSTRUCT the Planner to call
                # write_workspace_file -- it has no such tool as of this architecture change (it
                # MAY explain that fact, e.g. "you have no write_workspace_file tool", which is
                # correct — see PLANNER_INSTRUCTIONS).
                injected = new_input[-1].contents[0].text
                assert "call write_workspace_file" not in injected.lower(), (
                    "Planner-facing fallback must not instruct a call to a tool it doesn't have", injected)
                available_sub_agents_ctx.set([
                    _FakeSubAgentConfig("FindingsWriter"), _FakeSubAgentConfig("PeerReviewer"),
                ])

            # (d) [NARROW FALLBACK VARIANT] FindingsWriter+PeerReviewer registered but Builder is
            # NOT -> FindingsWriter genuinely fixes findings.md (2 dispatches, chain continues),
            # the chain's next iteration hits missing_artifact for final_report.md, and since no
            # Builder is registered that class of problem falls back to the classic
            # inject-into-Planner path instead of dispatching further -> should_retry=True,
            # current_input GROWS (the one case in this scenario where it does), dispatch never
            # called a 3rd time. Pins "falls back to classic path once the chain runs out of
            # registered writers" explicitly.
            with tempfile.TemporaryDirectory() as tmpdir_d:
                _IN_MEMORY_FS.pop("findings.md", None)
                _IN_MEMORY_FS.pop("final_report.md", None)
                rs = RunState(tmpdir_d)
                rs.add_finding(_SRC, "the real finding a dispatched Searcher actually returned")
                run_state_ctx.set(rs)
                msgs = []

                async def _side_effect_d(name, instructions, role):
                    if role == "FindingsWriter":
                        _IN_MEMORY_FS["findings.md"] = _FINDINGS_OK
                        return "## Result for FindingsWriterFix\nWrote findings.md\n---"
                    return "REVIEW: CLEAN\nNo issues found."

                dispatch = AsyncMock(side_effect=_side_effect_d)
                orig_input = "q"
                should_retry, new_input = _asyncio.run(run_completion_check(
                    query="q", current_input=orig_input, run_state=rs, notify=msgs.append,
                    dispatch_task=dispatch))
                assert should_retry, (
                    "once findings.md is fixed but final_report.md still needs an unregistered "
                    "Builder, the chain must fall back to the classic Planner nudge, not loop "
                    "forever or silently drop the problem", msgs)
                assert isinstance(new_input, list) and new_input[-1] is not orig_input, (
                    "the classic fallback for the still-unresolved missing_artifact problem must "
                    "still grow current_input", new_input)
                assert dispatch.call_count == 2, dispatch.call_args_list
                assert dispatch.call_args_list[0].args[2] == "FindingsWriter", dispatch.call_args_list
                assert dispatch.call_args_list[1].args[2] == "PeerReviewer", dispatch.call_args_list
                assert rs.data["completion_check_attempts"][-1]["problem"] == "missing_artifact"
                _IN_MEMORY_FS.pop("findings.md", None)
        finally:
            _IN_MEMORY_FS.clear()
            _IN_MEMORY_FS.update(saved_fs)
            reset_fetched_urls()
            if _orig_ws9 is None:
                _config.cfg["settings"].pop("workspace", None)
            else:
                _config.cfg["settings"]["workspace"] = _orig_ws9
            if _orig_gc9 is None:
                _config.cfg["settings"].pop("grounding_check", None)
            else:
                _config.cfg["settings"]["grounding_check"] = _orig_gc9

    contextvars.copy_context().run(_findings_writer_dispatch_scenario)

    # --- _dispatch_writer_review_fix clean-check hardening (Bonsai-8B bake-off finding,
    # 2026-07-14): a model confident enough to fabricate 'REVIEW: CLEAN' without ever calling
    # read_workspace_file used to defeat the review entirely. Now cross-checked against the
    # read_workspace_file quota's used-count delta -- a CLEAN verdict with zero new reads is
    # treated as ISSUES FOUND instead of trusted. Only applies when the quota is actually tracked
    # (pool has the key) -- a config with it untracked must fail OPEN, not distrust every review,
    # which is what every OTHER scenario in this file (none of which populate that quota key)
    # implicitly already relies on staying unaffected. ---
    def _clean_check_read_verification_scenario():
        from tools.fs import _IN_MEMORY_FS
        from tools.core import tool_quotas_ctx as q_ctx
        from unittest.mock import AsyncMock
        from engine.orchestrator import available_sub_agents_ctx

        class _FakeSubAgentConfig:
            def __init__(self, name):
                self.name = name

        _orig_ws10 = _config.cfg.get("settings", {}).get("workspace")
        _config.cfg["settings"]["workspace"] = {"type": "memory", "required_artifact": "findings.md"}
        _orig_gc10 = _config.cfg.get("settings", {}).get("grounding_check")
        _config.cfg["settings"]["grounding_check"] = {"nli_verify": False, "topical_relevance_check": False}
        saved_fs = dict(_IN_MEMORY_FS)
        try:
            available_sub_agents_ctx.set([
                _FakeSubAgentConfig("FindingsWriter"), _FakeSubAgentConfig("PeerReviewer"),
            ])

            # (a) PeerReviewer says CLEAN but never touches read_workspace_file -> must be treated
            # as ISSUES FOUND, forcing a corrective FindingsWriter pass (3 dispatches total).
            with tempfile.TemporaryDirectory() as tmpdir_a:
                _IN_MEMORY_FS.clear()
                reset_fetched_urls()
                record_fetched_url(_SRC, filename="sources/page.md")
                _IN_MEMORY_FS["sources/page.md"] = _SOURCE_TEXT
                rs = RunState(tmpdir_a)
                rs.add_finding(_SRC, "a real finding")
                run_state_ctx.set(rs)
                msgs = []
                q_ctx.set({"delegate_tasks": {"used": 1, "limit": 5},
                           "read_workspace_file": {"used": 0, "limit": 30}})

                async def _side_effect_fabricated(name, instructions, role):
                    if role == "FindingsWriter":
                        _IN_MEMORY_FS["findings.md"] = _FINDINGS_OK
                        return "## Result\nWrote findings.md\n---"
                    # PeerReviewer claims CLEAN without ever calling read_workspace_file (the
                    # pool's 'used' count is never incremented by this fake dispatch).
                    return "REVIEW: CLEAN\nThe file looks well-structured."

                dispatch = AsyncMock(side_effect=_side_effect_fabricated)
                orig_input = "q"
                should_retry, new_input = _asyncio.run(run_completion_check(
                    query="q", current_input=orig_input, run_state=rs, notify=msgs.append,
                    dispatch_task=dispatch))
                assert dispatch.call_count == 3, (
                    "a fabricated CLEAN with zero real reads must trigger the corrective Fix pass, "
                    "not be trusted", dispatch.call_args_list)
                assert dispatch.call_args_list[2].args[2] == "FindingsWriter", dispatch.call_args_list
                assert any("flagged issues" in m for m in msgs), (
                    "must be notified as issues-found, not as a clean pass", msgs)
                assert not any("found no issues" in m for m in msgs), msgs

            # (b) PeerReviewer says CLEAN and DOES increment read_workspace_file's used count ->
            # trusted as before (2 dispatches, converges).
            with tempfile.TemporaryDirectory() as tmpdir_b:
                _IN_MEMORY_FS.clear()
                reset_fetched_urls()
                record_fetched_url(_SRC, filename="sources/page.md")
                _IN_MEMORY_FS["sources/page.md"] = _SOURCE_TEXT
                rs = RunState(tmpdir_b)
                rs.add_finding(_SRC, "a real finding")
                run_state_ctx.set(rs)
                msgs = []
                q_ctx.set({"delegate_tasks": {"used": 1, "limit": 5},
                           "read_workspace_file": {"used": 0, "limit": 30}})

                async def _side_effect_honest(name, instructions, role):
                    if role == "FindingsWriter":
                        _IN_MEMORY_FS["findings.md"] = _FINDINGS_OK
                        return "## Result\nWrote findings.md\n---"
                    q_ctx.get()["read_workspace_file"]["used"] += 1  # simulates a real read
                    return "REVIEW: CLEAN\nThe file looks well-structured."

                dispatch = AsyncMock(side_effect=_side_effect_honest)
                orig_input = "q"
                should_retry, new_input = _asyncio.run(run_completion_check(
                    query="q", current_input=orig_input, run_state=rs, notify=msgs.append,
                    dispatch_task=dispatch))
                assert dispatch.call_count == 2, (
                    "a CLEAN verdict backed by a real read must still be trusted", dispatch.call_args_list)
                assert any("found no issues" in m for m in msgs), msgs
        finally:
            _IN_MEMORY_FS.clear()
            _IN_MEMORY_FS.update(saved_fs)
            reset_fetched_urls()
            if _orig_ws10 is None:
                _config.cfg["settings"].pop("workspace", None)
            else:
                _config.cfg["settings"]["workspace"] = _orig_ws10
            if _orig_gc10 is None:
                _config.cfg["settings"].pop("grounding_check", None)
            else:
                _config.cfg["settings"]["grounding_check"] = _orig_gc10

    contextvars.copy_context().run(_clean_check_read_verification_scenario)

    # --- _dispatch_writer_review_fix think_tool verification (2026-07-22, PIVOT arXiv:2605.11225):
    # both writer prompts already tell the model to use think_tool before finalizing, but this
    # project's own history is skeptical of prompt-only nudges on small local models -- same
    # reads_before/reads_after quota-delta pattern proven above for PeerReviewer, applied to the
    # WRITER's own think_tool use. NOT a hard gate (see completion.py's own comment for why) --
    # only surfaced (a) folded into the Fix-pass instructions when PeerReviewer separately flags
    # real issues, or (b) as a notify()-only note when PeerReviewer says CLEAN. ---
    def _think_tool_skip_scenario():
        from tools.fs import _IN_MEMORY_FS
        from tools.core import tool_quotas_ctx as q_ctx
        from unittest.mock import AsyncMock
        from engine.orchestrator import available_sub_agents_ctx

        class _FakeSubAgentConfig:
            def __init__(self, name):
                self.name = name

        _orig_ws11 = _config.cfg.get("settings", {}).get("workspace")
        _config.cfg["settings"]["workspace"] = {"type": "memory", "required_artifact": "findings.md"}
        _orig_gc11 = _config.cfg.get("settings", {}).get("grounding_check")
        _config.cfg["settings"]["grounding_check"] = {"nli_verify": False, "topical_relevance_check": False}
        saved_fs = dict(_IN_MEMORY_FS)
        try:
            available_sub_agents_ctx.set([
                _FakeSubAgentConfig("FindingsWriter"), _FakeSubAgentConfig("PeerReviewer"),
            ])

            # (a) Writer never touches think_tool during its Write pass, AND PeerReviewer flags
            # real issues -> the corrective Fix-pass instructions must mention the skipped step.
            with tempfile.TemporaryDirectory() as tmpdir_a:
                _IN_MEMORY_FS.clear()
                reset_fetched_urls()
                record_fetched_url(_SRC, filename="sources/page.md")
                _IN_MEMORY_FS["sources/page.md"] = _SOURCE_TEXT
                rs = RunState(tmpdir_a)
                rs.add_finding(_SRC, "a real finding")
                run_state_ctx.set(rs)
                msgs = []
                q_ctx.set({"delegate_tasks": {"used": 1, "limit": 5},
                           "read_workspace_file": {"used": 1, "limit": 30},
                           "think_tool": {"used": 0, "limit": 30}})

                async def _side_effect_no_think(name, instructions, role):
                    if role == "FindingsWriter":
                        _IN_MEMORY_FS["findings.md"] = _FINDINGS_OK
                        return "## Result\nWrote findings.md\n---"
                    q_ctx.get()["read_workspace_file"]["used"] += 1  # a real, honest review read
                    return "REVIEW: ISSUES FOUND: citation formatting is inconsistent."

                dispatch = AsyncMock(side_effect=_side_effect_no_think)
                should_retry, new_input = _asyncio.run(run_completion_check(
                    query="q", current_input="q", run_state=rs, notify=msgs.append,
                    dispatch_task=dispatch))
                fix_call = dispatch.call_args_list[2]
                assert fix_call.args[2] == "FindingsWriter", dispatch.call_args_list
                assert "skipped its own required think_tool" in fix_call.args[1], fix_call.args[1]

            # (b) Same skip, but PeerReviewer says CLEAN -> not a gate, converges at 2 dispatches,
            # only a notify() note.
            with tempfile.TemporaryDirectory() as tmpdir_b:
                _IN_MEMORY_FS.clear()
                reset_fetched_urls()
                record_fetched_url(_SRC, filename="sources/page.md")
                _IN_MEMORY_FS["sources/page.md"] = _SOURCE_TEXT
                rs = RunState(tmpdir_b)
                rs.add_finding(_SRC, "a real finding")
                run_state_ctx.set(rs)
                msgs = []
                q_ctx.set({"delegate_tasks": {"used": 1, "limit": 5},
                           "read_workspace_file": {"used": 1, "limit": 30},
                           "think_tool": {"used": 0, "limit": 30}})

                async def _side_effect_clean_no_think(name, instructions, role):
                    if role == "FindingsWriter":
                        _IN_MEMORY_FS["findings.md"] = _FINDINGS_OK
                        return "## Result\nWrote findings.md\n---"
                    q_ctx.get()["read_workspace_file"]["used"] += 1
                    return "REVIEW: CLEAN\nThe file looks well-structured."

                dispatch = AsyncMock(side_effect=_side_effect_clean_no_think)
                should_retry, new_input = _asyncio.run(run_completion_check(
                    query="q", current_input="q", run_state=rs, notify=msgs.append,
                    dispatch_task=dispatch))
                assert dispatch.call_count == 2, (
                    "a skipped think_tool must not force a retry when PeerReviewer says CLEAN",
                    dispatch.call_args_list)
                assert any("skipped its own required think_tool" in m for m in msgs), msgs

            # (c) Control: think_tool's quota DOES increment during the Write pass, issues found ->
            # the Fix-pass instructions must NOT mention a skip that didn't happen.
            with tempfile.TemporaryDirectory() as tmpdir_c:
                _IN_MEMORY_FS.clear()
                reset_fetched_urls()
                record_fetched_url(_SRC, filename="sources/page.md")
                _IN_MEMORY_FS["sources/page.md"] = _SOURCE_TEXT
                rs = RunState(tmpdir_c)
                rs.add_finding(_SRC, "a real finding")
                run_state_ctx.set(rs)
                msgs = []
                q_ctx.set({"delegate_tasks": {"used": 1, "limit": 5},
                           "read_workspace_file": {"used": 1, "limit": 30},
                           "think_tool": {"used": 0, "limit": 30}})

                async def _side_effect_used_think(name, instructions, role):
                    if role == "FindingsWriter":
                        q_ctx.get()["think_tool"]["used"] += 1  # a real think_tool call
                        _IN_MEMORY_FS["findings.md"] = _FINDINGS_OK
                        return "## Result\nWrote findings.md\n---"
                    q_ctx.get()["read_workspace_file"]["used"] += 1
                    return "REVIEW: ISSUES FOUND: citation formatting is inconsistent."

                dispatch = AsyncMock(side_effect=_side_effect_used_think)
                should_retry, new_input = _asyncio.run(run_completion_check(
                    query="q", current_input="q", run_state=rs, notify=msgs.append,
                    dispatch_task=dispatch))
                fix_call = dispatch.call_args_list[2]
                assert "skipped its own required think_tool" not in fix_call.args[1], fix_call.args[1]
        finally:
            _IN_MEMORY_FS.clear()
            _IN_MEMORY_FS.update(saved_fs)
            reset_fetched_urls()
            if _orig_ws11 is None:
                _config.cfg["settings"].pop("workspace", None)
            else:
                _config.cfg["settings"]["workspace"] = _orig_ws11
            if _orig_gc11 is None:
                _config.cfg["settings"].pop("grounding_check", None)
            else:
                _config.cfg["settings"]["grounding_check"] = _orig_gc11

    contextvars.copy_context().run(_think_tool_skip_scenario)

    # --- force_whole_rebuild dispatched to Builder (2026-07-22, ACM CAIS '26 planning-horizon
    # paper): when dispatch_task IS available and the repeated problem is Builder-fixable, the
    # 3rd consecutive occurrence must dispatch a genuine FULL REBUILD instruction (not the classic
    # reworded-Planner-nudge text, and not the ordinary targeted-fix instruction) -- confirms the
    # "more complete" option (full artifact rebuild, not just wording) actually reaches the writer
    # dispatch, not just the Planner-fallback path exercised above. ---
    def _force_whole_rebuild_dispatch_scenario():
        from tools.fs import _IN_MEMORY_FS
        from tools.core import tool_quotas_ctx as q_ctx
        from unittest.mock import AsyncMock
        from engine.orchestrator import available_sub_agents_ctx

        class _FakeSubAgentConfig:
            def __init__(self, name):
                self.name = name

        _orig_ws12 = _config.cfg.get("settings", {}).get("workspace")
        _config.cfg["settings"]["workspace"] = {"type": "memory", "required_artifact": "final_report.md"}
        _orig_gc12 = _config.cfg.get("settings", {}).get("grounding_check")
        _config.cfg["settings"]["grounding_check"] = {"nli_verify": False, "topical_relevance_check": False}
        saved_fs = dict(_IN_MEMORY_FS)
        try:
            available_sub_agents_ctx.set([
                _FakeSubAgentConfig("Builder"), _FakeSubAgentConfig("PeerReviewer"),
            ])
            _IN_MEMORY_FS.clear()
            reset_fetched_urls()
            record_fetched_url(_SRC, filename="sources/page.md")
            _IN_MEMORY_FS["sources/page.md"] = _SOURCE_TEXT
            _IN_MEMORY_FS["findings.md"] = "- Real finding with a real cited URL (" + _SRC + ")"
            q_ctx.set({"delegate_tasks": {"used": 1, "limit": 5},
                       "read_workspace_file": {"used": 1, "limit": 30},
                       "write_workspace_file": {"used": 0, "limit": 30},
                       "think_tool": {"used": 1, "limit": 30}})

            with tempfile.TemporaryDirectory() as tmpdir:
                rs = RunState(tmpdir)
                rs.data["completion_check_attempts"] = [
                    {"attempt": 0, "problem": "missing_artifact"},
                    {"attempt": 1, "problem": "missing_artifact"},
                ]
                run_state_ctx.set(rs)
                msgs = []

                async def _side_effect(name, instructions, role):
                    if role == "Builder":
                        _IN_MEMORY_FS["final_report.md"] = f"- x [g]({_SRC})"
                        return "## Result\nWrote final_report.md\n---"
                    return "REVIEW: CLEAN\nThe file looks well-structured."

                dispatch = AsyncMock(side_effect=_side_effect)
                # Not asserting should_retry's final value here -- a successful Builder rebuild +
                # clean PeerReview chains straight into the next completion-check iteration (see
                # run_completion_check's own docstring) and may converge to a clean pass
                # (should_retry False) on its own. What this test actually verifies is that the
                # DISPATCH itself used the full-rebuild instruction shape, not the targeted-fix one.
                _asyncio.run(run_completion_check(
                    query="q", current_input="q", run_state=rs, notify=msgs.append,
                    dispatch_task=dispatch))
                assert dispatch.call_count >= 1, "3rd consecutive occurrence must chain into a Builder dispatch, not stop"
                builder_call = dispatch.call_args_list[0]
                assert builder_call.args[2] == "Builder", dispatch.call_args_list
                builder_instructions = builder_call.args[1]
                assert "completely from scratch" in builder_instructions, builder_instructions
                assert "reconsidering your whole approach" in builder_instructions, builder_instructions
                assert "fixing this specific problem" not in builder_instructions, builder_instructions
                assert rs.data["whole_approach_retry_used_for"] == {"missing_artifact": True}, rs.data
        finally:
            _IN_MEMORY_FS.clear()
            _IN_MEMORY_FS.update(saved_fs)
            reset_fetched_urls()
            if _orig_ws12 is None:
                _config.cfg["settings"].pop("workspace", None)
            else:
                _config.cfg["settings"]["workspace"] = _orig_ws12
            if _orig_gc12 is None:
                _config.cfg["settings"].pop("grounding_check", None)
            else:
                _config.cfg["settings"]["grounding_check"] = _orig_gc12

    contextvars.copy_context().run(_force_whole_rebuild_dispatch_scenario)

    # --- _dispatch_writer_review_fix immediate narration salvage (2026-07-18 bake-off finding:
    # qwen2.5:3b-instruct as FindingsWriter narrated a complete findings.md draft as chat text on
    # EVERY attempt, never once calling write_workspace_file, and burned the full 8-attempt retry
    # budget on missing_findings before giving up — same root cause already documented for
    # Bonsai-8B). A writer role "Finishing" its dispatch is not proof it wrote the file; if
    # req_artifact is still missing afterward but the dispatch returned substantial narrated text,
    # that text must be salvaged as the artifact immediately (attempt 1, not after the whole
    # budget is spent). Uses real disk mode (not "memory") since the salvage helpers
    # (_salvage_narrated_report/_restore_quarantined_draft) operate via plain open()/os.path.exists
    # against _get_safe_path's resolved path, bypassing the in-memory FS dict entirely — the same
    # reason _restore_quarantined_draft's own scenario elsewhere in this file uses disk mode. ---
    from engine.completion import _dispatch_writer_review_fix

    with tempfile.TemporaryDirectory() as tmpdir:
        def _immediate_narration_salvage_scenario():
            from unittest.mock import AsyncMock

            _orig_ws11 = _config.cfg.get("settings", {}).get("workspace")
            _config.cfg["settings"]["workspace"] = {"type": "disk", "dir": tmpdir}
            try:
                msgs = []
                narrated_draft = "## Findings\n\n" + ("Real substantive research content. " * 20)

                async def _side_effect_narrates_instead_of_writing(name, instructions, role):
                    if role == "FindingsWriter":
                        # Never calls write_workspace_file — narrates the draft as its response
                        # text instead, exactly like the live failure.
                        return narrated_draft
                    return "REVIEW: CLEAN"

                dispatch = AsyncMock(side_effect=_side_effect_narrates_instead_of_writing)
                _asyncio.run(_dispatch_writer_review_fix(
                    dispatch, "FindingsWriter", "findings.md", "write it now", 0, msgs.append))

                path = os.path.join(tmpdir, "findings.md")
                assert os.path.exists(path), (
                    "narrated content must be salvaged into findings.md immediately, not left "
                    "missing for the caller's retry loop to burn its whole budget on", msgs)
                content = open(path, encoding="utf-8").read()
                assert "Real substantive research content." in content, content
                assert "AUTO-RECOVERED DRAFT" in content, (
                    "salvaged content must be clearly flagged as unverified", content)
                assert any("auto-recovered its own content" in m for m in msgs), (
                    "must notify that a salvage happened", msgs)
                # Write + Review only (PeerReviewer said CLEAN) — no wasted extra dispatch.
                assert dispatch.call_count == 2, dispatch.call_count
            finally:
                if _orig_ws11 is None:
                    _config.cfg["settings"].pop("workspace", None)
                else:
                    _config.cfg["settings"]["workspace"] = _orig_ws11

        contextvars.copy_context().run(_immediate_narration_salvage_scenario)

    # Negative case: a writer role that DOES call write_workspace_file (real content already on
    # disk by the time _dispatch_writer_review_fix checks) must never have its real write
    # clobbered by salvage logic.
    with tempfile.TemporaryDirectory() as tmpdir:
        def _no_salvage_when_real_write_happened_scenario():
            from unittest.mock import AsyncMock
            from tools.fs import write_workspace_file

            _orig_ws12 = _config.cfg.get("settings", {}).get("workspace")
            _config.cfg["settings"]["workspace"] = {"type": "disk", "dir": tmpdir}
            try:
                msgs = []

                async def _side_effect_writes_for_real(name, instructions, role):
                    if role == "FindingsWriter":
                        write_workspace_file("findings.md", "# Real Findings\nActually written.")
                        return "Wrote 'findings.md' to disk."
                    return "REVIEW: CLEAN"

                dispatch = AsyncMock(side_effect=_side_effect_writes_for_real)
                _asyncio.run(_dispatch_writer_review_fix(
                    dispatch, "FindingsWriter", "findings.md", "write it now", 0, msgs.append))

                content = open(os.path.join(tmpdir, "findings.md"), encoding="utf-8").read()
                assert content == "# Real Findings\nActually written.", (
                    "a real write must never be overwritten by salvage logic", content)
                assert not any("auto-recovered" in m for m in msgs), (
                    "must not claim a salvage happened when the writer actually wrote the file", msgs)
            finally:
                if _orig_ws12 is None:
                    _config.cfg["settings"].pop("workspace", None)
                else:
                    _config.cfg["settings"]["workspace"] = _orig_ws12

        contextvars.copy_context().run(_no_salvage_when_real_write_happened_scenario)

    # --- Builder write_workspace_file quota headroom (ROADMAP "Pending": a Build->Review->Fix
    # cycle can burn up to 2 write_workspace_file calls — Builder's initial rewrite plus one
    # corrective Fix pass — against the same shared pool the Planner's own findings.md writes draw
    # from; a low-quota config could starve Builder specifically mid-cycle). ---
    from engine.completion import _ensure_writer_quota_headroom

    # Nearly exhausted (0 headroom) -> topped up to guarantee exactly 2.
    pool_a = {"write_workspace_file": {"used": 5, "limit": 5}}
    _ensure_writer_quota_headroom(pool_a)
    assert pool_a["write_workspace_file"]["limit"] - pool_a["write_workspace_file"]["used"] == 2

    # Already has plenty of headroom -> left untouched, no silent inflation of the shared budget.
    pool_b = {"write_workspace_file": {"used": 1, "limit": 10}}
    _ensure_writer_quota_headroom(pool_b)
    assert pool_b["write_workspace_file"]["limit"] == 10

    # Tool not in this pool at all (e.g. quotas section omits it) -> no-op, no KeyError.
    _ensure_writer_quota_headroom({"delegate_tasks": {"used": 0, "limit": 5}})

    # --- read_workspace_file quota headroom for remediation cycles (found live 2026-07-20, fixed
    # 2026-07-21) — mirror of the write-side helper above. PeerReviewer's own 'REVIEW: CLEAN' is
    # only trusted if it actually called read_workspace_file (see _dispatch_writer_review_fix's
    # reads_before/reads_after gate), and unlike write_workspace_file, read_workspace_file has no
    # entry in settings.retry_quota_topup by default, so nothing replenished it between cycles —
    # a run with several remediation cycles could exhaust it and leave a later Fix pass unable to
    # re-read its own source, silently dropping content instead of erroring. ---
    from engine.completion import _ensure_reader_quota_headroom

    pool_c = {"read_workspace_file": {"used": 30, "limit": 30}}
    _ensure_reader_quota_headroom(pool_c)
    assert pool_c["read_workspace_file"]["limit"] - pool_c["read_workspace_file"]["used"] == 2, (
        "an exhausted read_workspace_file pool must be topped up to guarantee 2 more calls", pool_c)

    pool_d = {"read_workspace_file": {"used": 1, "limit": 10}}
    _ensure_reader_quota_headroom(pool_d)
    assert pool_d["read_workspace_file"]["limit"] == 10, (
        "plenty of existing headroom must be left untouched, no silent inflation", pool_d)

    _ensure_reader_quota_headroom({"write_workspace_file": {"used": 0, "limit": 5}})  # no-op, no KeyError

    # --- missing_findings escalation (live case 2026-07-13): a real run produced literally ZERO
    # content (no tool call, no text) in response to this exact nudge for 6 consecutive attempts,
    # then genuinely self-corrected with real findings.md content on the 7th. Unlike
    # missing_artifact, late recovery is real here -- wording escalates and, on repeat, hands the
    # model its actual fetched URLs as proof material exists, but deliberately does NOT get the
    # aggressive early-cutoff missing_artifact has (that would have killed this run's real
    # recovery at attempt 3). ---
    def _missing_findings_escalation_scenario():
        from tools.fs import _IN_MEMORY_FS
        from tools.core import tool_quotas_ctx as q_ctx
        _orig_ws7 = _config.cfg.get("settings", {}).get("workspace")
        _config.cfg["settings"]["workspace"] = {"type": "memory", "required_artifact": "final_report.md"}
        saved_fs = dict(_IN_MEMORY_FS)
        try:
            _IN_MEMORY_FS.clear()
            reset_fetched_urls()
            record_fetched_url(_SRC, filename="sources/page.md")
            _IN_MEMORY_FS["sources/page.md"] = _SOURCE_TEXT
            # No findings.md and no final_report.md -- the exact "nothing written yet" shape.
            q_ctx.set({"delegate_tasks": {"used": 1, "limit": 5}})

            # First occurrence: fresh framing, no URL list yet (nothing to prove wrong yet).
            with tempfile.TemporaryDirectory() as tmpdir7:
                rs = RunState(tmpdir7)
                run_state_ctx.set(rs)
                msgs = []
                should_retry, new_input = _asyncio.run(run_completion_check(
                    query="q", current_input="q", run_state=rs, notify=msgs.append))
                recorded = rs.data["completion_check_attempts"][-1]["problem"]
                assert recorded == "missing_findings", (recorded, msgs)
                assert should_retry
                injected = new_input[-1].contents[0].text
                assert "STILL missing" not in injected, "first occurrence must use the fresh framing"
                assert _SRC not in injected, "no URL list should be injected on the first occurrence"

            # 6th consecutive occurrence (matching the live case exactly): escalated wording AND
            # the real fetched URL handed back verbatim, but should_retry must still be True --
            # missing_findings must NOT get missing_artifact's early cutoff.
            with tempfile.TemporaryDirectory() as tmpdir8:
                rs = RunState(tmpdir8)
                rs.data["completion_check_attempts"] = [
                    {"attempt": i, "problem": "missing_findings"} for i in range(6)
                ]
                run_state_ctx.set(rs)
                msgs = []
                should_retry, new_input = _asyncio.run(run_completion_check(
                    query="q", current_input="q", run_state=rs, notify=msgs.append))
                assert should_retry, (
                    "missing_findings must keep retrying past 6 consecutive occurrences -- "
                    "late recovery is real for this problem type, confirmed live", msgs)
                injected = new_input[-1].contents[0].text
                assert "STILL missing" in injected, "6th occurrence must use the escalated framing"
                assert _SRC in injected, (
                    "the real fetched URL must be injected verbatim once the problem repeats", injected)
        finally:
            _IN_MEMORY_FS.clear()
            _IN_MEMORY_FS.update(saved_fs)
            reset_fetched_urls()
            if _orig_ws7 is None:
                _config.cfg["settings"].pop("workspace", None)
            else:
                _config.cfg["settings"]["workspace"] = _orig_ws7

    contextvars.copy_context().run(_missing_findings_escalation_scenario)

    # --- regulation-identifier grounding (live case run 12: 'Ley 1906 de 2021' cited to a real
    # fetched page that never mentions 1906 — passed both the URL gate and zero-overlap check) ---
    from utils.grounding import find_unsupported_regulation_ids

    def _regulation_scenario():
        from tools.fs import _IN_MEMORY_FS
        _orig_ws4 = _config.cfg.get("settings", {}).get("workspace")
        _config.cfg["settings"]["workspace"] = {"type": "memory"}
        saved_fs = dict(_IN_MEMORY_FS)
        try:
            _IN_MEMORY_FS.clear()
            reset_fetched_urls()
            record_fetched_url("https://mintic.example.gov.co/article", filename="sources/mintic.md")
            _IN_MEMORY_FS["sources/mintic.md"] = (
                "Source-URL: https://mintic.example.gov.co/article\n\n"
                "La Estrategia Nacional de Seguridad Digital 2025-2027 llega para proteger a Colombia. "
                "El Ministerio TIC presenta el plan de ciberseguridad nacional para infraestructura."
            )
            # Misattributed law number: page never says 1906 -> flagged
            bad = find_unsupported_regulation_ids(
                "| Ley 1906 de 2021 | [Mintic](https://mintic.example.gov.co/article) |")
            assert bad and "1906" in bad[0], bad
            # Supported identifier: page that DOES contain the number -> silent
            _IN_MEMORY_FS["sources/mintic.md"] += "\nTexto oficial de la Ley 1906 de 2021."
            assert find_unsupported_regulation_ids(
                "| Ley 1906 de 2021 | [Mintic](https://mintic.example.gov.co/article) |") == []
            # Identifier with an unfetched/no URL on the line -> other gates' job, silent here
            assert find_unsupported_regulation_ids("Decreto 9999/2015 obliga a todos.") == []
            assert find_unsupported_regulation_ids(
                "Decreto 9999/2015 ([x](https://never-fetched.example.com/y))") == []
            # Run 14's self-grounding case: the regulation number exists ONLY inside our own
            # injected Source-URL header line (the URL slug), not in the page content — the
            # check must strip that header before matching, or it verifies against itself.
            record_fetched_url("https://news.example.co/ley-1819-e-invoicing-dian",
                               filename="sources/eltiempo.md")
            _IN_MEMORY_FS["sources/eltiempo.md"] = (
                "Source-URL: https://news.example.co/ley-1819-e-invoicing-dian\n\n"
                "Suscríbete para leer el contenido completo de nuestras noticias y análisis del día."
            )
            bad2 = find_unsupported_regulation_ids(
                "| Ley 1819 de 2016 | [ET](https://news.example.co/ley-1819-e-invoicing-dian) |")
            assert bad2 and "1819" in bad2[0], (bad2, "Source-URL header slug must not self-ground")
        finally:
            _IN_MEMORY_FS.clear()
            _IN_MEMORY_FS.update(saved_fs)
            reset_fetched_urls()
            if _orig_ws4 is None:
                _config.cfg["settings"].pop("workspace", None)
            else:
                _config.cfg["settings"]["workspace"] = _orig_ws4

    contextvars.copy_context().run(_regulation_scenario)

    # --- academic-style (Author, Year) citation dialect (eval/sales_forecasting_benchmark.md,
    # ROADMAP.md "academic output mode") — same grounding guarantees as the default
    # `- **[Title](URL)**` format, resolved through a parsed References-section map ---
    from utils.grounding import parse_academic_references, real_grounding_problem as _rgp

    def _academic_citation_scenario():
        from tools.fs import _IN_MEMORY_FS
        _orig_ws6 = _config.cfg.get("settings", {}).get("workspace")
        _orig_gc6 = _config.cfg.get("settings", {}).get("grounding_check")
        _config.cfg["settings"]["workspace"] = {"type": "memory"}
        # Not testing NLI-specific behavior here -- the well-formed case below has genuine
        # term-overlap and would otherwise silently load the real HuggingFace model (see the
        # matrix's own nli_verify:False guard above for why that's undesirable in this suite).
        _config.cfg["settings"]["grounding_check"] = {"nli_verify": False, "topical_relevance_check": False}
        saved_fs = dict(_IN_MEMORY_FS)
        try:
            _IN_MEMORY_FS.clear()
            reset_fetched_urls()
            record_fetched_url("https://arxiv.org/abs/2511.00552", filename="sources/tft.md")
            _IN_MEMORY_FS["sources/tft.md"] = (
                "Source-URL: https://arxiv.org/abs/2511.00552\n\n"
                "Temporal Fusion Transformer achieves an R-squared of 0.9875 on 45 Walmart stores, "
                "integrating holiday, CPI, fuel price and temperature signals into a single model."
            )
            well_formed = (
                "## 3. Architectures\n\n"
                "TFT achieved R-squared of 0.9875 on Walmart data (Punati et al., 2025).\n\n"
                "## References\n\n"
                "1. Punati, S. B., et al. (2025). Temporal Fusion Transformer. "
                "https://arxiv.org/abs/2511.00552\n"
            )
            assert parse_academic_references(well_formed) == {
                "punati,2025": "https://arxiv.org/abs/2511.00552"
            }
            # Well-formed academic report: real citation, real fetch, term overlap -> passes clean.
            assert _asyncio.run(_rgp(well_formed)) is None, _asyncio.run(_rgp(well_formed))

            # Fabricated in-text citation with no matching References entry -> non_url_citation,
            # same failure class a bare (Org, Year) pseudo-citation already triggers.
            fabricated = (
                "## 3. Architectures\n\n"
                "A DQN model achieves the highest accuracy in FMCG forecasting (Nobody, 2099).\n\n"
                "## References\n\n"
                "1. Punati, S. B., et al. (2025). Temporal Fusion Transformer. "
                "https://arxiv.org/abs/2511.00552\n"
            )
            problem = _asyncio.run(_rgp(fabricated))
            assert problem and problem.startswith("non_url_citation"), problem

            # A References entry that cites a paper by title/arXiv-ID text alone, no real URL —
            # exactly how the DeepSeek gold reference itself writes some entries — must NOT
            # silently resolve; the in-text citation stays ungrounded until a URL is added.
            no_url_entry = (
                "## 3. Architectures\n\n"
                "GA-DQN raised service level from 61% to 94% (Various Authors, 2025).\n\n"
                "## References\n\n"
                "1. Various Authors. (2025). GA-DQN hybrid. *Supply Chain Analytics Journal*.\n"
            )
            assert parse_academic_references(no_url_entry) == {}
            # No http URL anywhere in the whole report (in-text citation is parenthetical, and
            # the one References entry has none either) -> the hard "no_urls" gate fires first,
            # before non_url_citation_check is ever reached — even more direct than that check.
            problem2 = _asyncio.run(_rgp(no_url_entry))
            assert problem2 == "no_urls", problem2

            # Two citations on one line: an earlier REAL one must not mask a later FABRICATED
            # one — the exact class of bug caught while building this feature (only the first
            # regex match on a line was being resolved before this fix).
            two_on_one_line = (
                "TFT hit R-squared 0.9875 (Punati et al., 2025); a rival model claims higher "
                "accuracy still (Nobody, 2099).\n\n"
                "## References\n\n"
                "1. Punati, S. B., et al. (2025). Temporal Fusion Transformer. "
                "https://arxiv.org/abs/2511.00552\n"
            )
            assert find_non_url_citations(two_on_one_line), "unresolved 2nd citation must be caught"

            # Fresh audit, 2026-07-12: _PARENTHETICAL_CITATION_RE originally required every
            # token before the comma to start with an ASCII capital, so it silently failed to
            # even DETECT "et al."/"&"/"and"/accented-surname citations at all -- not a
            # false-positive, a total miss that broke grounding in both directions (a fabricated
            # multi-author citation went undetected; a genuinely well-formed one was wrongly
            # quarantined). Pin every form the academic-mode prompt actually tells the model to
            # use (prompts.py ACADEMIC_CITATION_FORMAT_INSTRUCTIONS: "et al. for 3+ authors").
            record_fetched_url("https://example.com/drl", filename="sources/drl.md")
            record_fetched_url("https://example.com/pso", filename="sources/pso.md")
            record_fetched_url("https://example.com/rbfnn", filename="sources/rbfnn.md")
            multi_author_forms = (
                "DRL achieves the highest accuracy for FMCG demand forecasting "
                "(Urgenc et al., 2025). PSO cut MAPE by 23 percent versus Transformer "
                "(Smith and Jones, 2024). RBFNN generalization improved significantly "
                "(Chen & Patel, 2020).\n\n"
                "## References\n\n"
                "1. Urgenc, S., et al. (2025). DRL Demand Forecasting. https://example.com/drl\n"
                "2. Smith, J., and Jones, B. (2024). PSO Attention. https://example.com/pso\n"
                "3. Chen, L., & Patel, R. (2020). RBFNN Hybrid. https://example.com/rbfnn\n"
            )
            assert find_non_url_citations(multi_author_forms) == [], (
                "well-formed et al./and/& citations must all resolve, not be flagged")
            assert find_uncited_claim_lines(multi_author_forms) == [], (
                "sections carrying only et al./and/& citations must be exempted, same as http")
            assert _asyncio.run(_rgp(multi_author_forms)) is None, _asyncio.run(_rgp(multi_author_forms))

            # A fabricated multi-author citation with no matching References entry must still be
            # caught now that the detector actually sees "et al." citations at all.
            fabricated_multi_author = (
                "A DQN model achieves the highest accuracy in FMCG forecasting "
                "(Nobody et al., 2099).\n\n"
                "## References\n\n"
                "1. Urgenc, S., et al. (2025). DRL Demand Forecasting. https://example.com/drl\n"
            )
            assert find_non_url_citations(fabricated_multi_author), (
                "fabricated et al. citation with no matching reference must be flagged")

            # Fresh audit, 2026-07-12: _academic_citation_key tried the unanchored in-text regex
            # BEFORE the anchored reference-entry regex, so a numbered reference whose own TITLE
            # happens to contain a (Word, YYYY)-shaped substring got mis-keyed to that inner
            # parenthetical instead of its real leading author/year.
            title_collision = (
                "## References\n\n"
                "1. Urgenc, S., et al. (2025). A study of trends (Preliminary, 1998) in demand "
                "forecasting. https://example.com/drl\n"
            )
            assert parse_academic_references(title_collision) == {
                "urgenc,2025": "https://example.com/drl"
            }, parse_academic_references(title_collision)
        finally:
            _IN_MEMORY_FS.clear()
            _IN_MEMORY_FS.update(saved_fs)
            reset_fetched_urls()
            if _orig_ws6 is None:
                _config.cfg["settings"].pop("workspace", None)
            else:
                _config.cfg["settings"]["workspace"] = _orig_ws6
            if _orig_gc6 is None:
                _config.cfg["settings"].pop("grounding_check", None)
            else:
                _config.cfg["settings"]["grounding_check"] = _orig_gc6

    contextvars.copy_context().run(_academic_citation_scenario)

    # --- answer mode (ROADMAP.md candidate from dzhng/deep-research's writeFinalAnswer,
    # 2026-07-12): a short direct-answer report shape, `(Source: [Title](URL))` inline citations.
    # Deliberately requires ZERO grounding.py changes — the format is just a different PLACEMENT
    # of the same `[Title](URL)` markdown link syntax the standard style already uses, and every
    # check here extracts URLs format-agnostically. This pins that compatibility claim.
    from prompts import PLANNER_INSTRUCTIONS, ANSWER_REPORT_STYLE_INSTRUCTIONS, ANSWER_CITATION_FORMAT_INSTRUCTIONS

    class _AnswerModeSafeDict(dict):
        def __missing__(self, key):
            return '{' + key + '}'

    _rendered = PLANNER_INSTRUCTIONS.format_map(_AnswerModeSafeDict(
        date="2026-07-12", workspace_dir="/tmp/ws", delegation_instructions="[DELEGATION BLOCK]",
        report_style_instructions=ANSWER_REPORT_STYLE_INSTRUCTIONS,
        citation_format_instructions=ANSWER_CITATION_FORMAT_INSTRUCTIONS,
        delegate_tasks_quota=10, write_workspace_file_quota=10, write_todos_quota=5,
    ))
    assert "{report_style_instructions}" not in _rendered
    assert "{citation_format_instructions}" not in _rendered

    _answer_text = (
        "Guido van Rossum created Python in 1991 "
        "(Source: [Wikipedia](https://en.wikipedia.org/wiki/Guido_van_Rossum))."
    )
    assert extract_cited_urls(_answer_text) == ["https://en.wikipedia.org/wiki/Guido_van_Rossum"]
    assert find_non_url_citations(_answer_text) == []
    assert find_uncited_claim_lines(_answer_text) == []

    # --- parenthesized URL extraction (live case 2026-07-12, NIM gpt-oss-20b run): a genuinely
    # fetched Wikipedia disambiguator URL — https://en.wikipedia.org/wiki/Heuristic_(computer_science)
    # — has a literal balanced '(...)' as part of its own path. The old regex excluded ')' entirely
    # from a URL match, truncating this exact citation mid-slug and false-flagging a real fetch as
    # unverified for 3 consecutive completion-check attempts (wasted retry budget chasing a bug in
    # the extractor, not the model). ---
    _paren_url = "https://en.wikipedia.org/wiki/Heuristic_(computer_science)"
    assert extract_cited_urls(f"See [Heuristic]({_paren_url}) for background.") == [_paren_url], (
        "a URL with its own balanced parens must not be truncated at the internal ')'")
    assert extract_cited_urls(f"See ({_paren_url}) for background.") == [_paren_url], (
        "same case without markdown link syntax, just parenthesized prose")
    # A URL with NO internal parens must still have the markdown link's own closing paren stripped.
    assert extract_cited_urls("See [Foo](https://example.com/page) now.") == ["https://example.com/page"]

    # --- trailing '**' stripped (live case 2026-07-13/14: Builder's own citation style is
    # `**[Title](URL)**` — the bold-close asterisks sat right after the URL's own closing ')',
    # which made the old rstrip's endswith(')') check false and left a literal '**' on every
    # extracted URL, so no Builder-written citation could ever match a real fetched URL) ---
    assert extract_cited_urls("- **[Guido van Rossum](https://en.wikipedia.org/wiki/Guido_van_Rossum)**") == [
        "https://en.wikipedia.org/wiki/Guido_van_Rossum"
    ], "trailing '**' from Builder's bold citation style must not survive extraction"
    # Same case, but the URL ALSO has its own internal balanced parens — both the bold '**' and
    # the correct balanced ')' must be handled together, in the right order.
    assert extract_cited_urls(f"- **[Heuristic]({_paren_url})**") == [_paren_url], (
        "bold citation style combined with a URL's own balanced parens must still resolve correctly")

    # --- stub-fetch detection (live case run 14: a model-invented URL answered by a 200
    # soft-404 — 5KB of subscription chrome — was recorded as a real fetch and passed the
    # hard URL gate) ---
    from tools.web import _stub_reason
    from utils.grounding import real_grounding_problem

    chrome = "\n".join(["[SUSCRÍBETE](https://news.example.co/sub)", "Inicia sesión",
                        "Noticias", "Deportes", "Política"] * 20)
    assert _stub_reason(chrome), "paywall chrome must flag as stub"
    assert _stub_reason("") == "empty page"
    assert _stub_reason("Página no encontrada\n\nError 404"), "tiny not-found page must flag"
    assert _stub_reason("Just a title\n\nAnd one short line."), "near-zero prose must flag"
    _para = ("Colombian exporters shipped record volumes of coffee and flowers this quarter "
             "according to the trade ministry figures released on Tuesday, with analysts "
             "noting sustained demand across European and North American markets overall.")
    real_article = "\n\n".join([_para] * 6 + ["Subscribe to our newsletter for updates"])
    assert _stub_reason(real_article) is None, "real prose mentioning 'subscribe' must NOT flag"

    def _stub_gate_scenario():
        from tools.fs import _IN_MEMORY_FS
        _orig_ws5 = _config.cfg.get("settings", {}).get("workspace")
        _orig_gc = _config.cfg.get("settings", {}).get("grounding_check")
        _config.cfg["settings"]["workspace"] = {"type": "memory"}
        _config.cfg["settings"]["grounding_check"] = {"stub_detection": True, "live_http_verify": False}
        saved_fs = dict(_IN_MEMORY_FS)
        try:
            _IN_MEMORY_FS.clear()
            reset_fetched_urls()
            record_fetched_url("https://news.example.co/paywalled", filename="sources/stub.md",
                               stub="paywall marker")
            _IN_MEMORY_FS["sources/stub.md"] = "Source-URL: https://news.example.co/paywalled\n\nSUSCRÍBETE"
            report = "- dato [news](https://news.example.co/paywalled)"
            problem = _asyncio.run(real_grounding_problem(report))
            assert problem and problem.startswith("stub_source"), problem
            # Flag off -> the stub gate stands down (stub content still can't ground claims,
            # via _fetched_url_files' exclusion).
            _config.cfg["settings"]["grounding_check"]["stub_detection"] = False
            assert _asyncio.run(real_grounding_problem(report)) is None
            _config.cfg["settings"]["grounding_check"]["stub_detection"] = True
            # Same URL later fetched for real (retry got the actual page) -> citation valid.
            record_fetched_url("https://news.example.co/paywalled", filename="sources/real.md")
            _IN_MEMORY_FS["sources/real.md"] = "Source-URL: https://news.example.co/paywalled\n\n" + _para
            problem2 = _asyncio.run(real_grounding_problem(report))
            assert not (problem2 or "").startswith("stub_source"), problem2
        finally:
            _IN_MEMORY_FS.clear()
            _IN_MEMORY_FS.update(saved_fs)
            reset_fetched_urls()
            if _orig_ws5 is None:
                _config.cfg["settings"].pop("workspace", None)
            else:
                _config.cfg["settings"]["workspace"] = _orig_ws5
            if _orig_gc is None:
                _config.cfg["settings"].pop("grounding_check", None)
            else:
                _config.cfg["settings"]["grounding_check"] = _orig_gc

    contextvars.copy_context().run(_stub_gate_scenario)

    # --- URL prefix-match boundary (2026-07-12 audit G1: a genuinely fetched .../article
    # grounded an invented .../article-fake-2024 via bare string-prefixing) ---
    from utils.grounding import _urls_prefix_match

    assert not _urls_prefix_match("https://real.com/article-fake-2024", "https://real.com/article")
    assert _urls_prefix_match("https://real.com/article?utm=1", "https://real.com/article")
    assert _urls_prefix_match("https://real.com/article#s2", "https://real.com/article")
    assert _urls_prefix_match("https://real.com/article/annex", "https://real.com/article")
    # Bare-origin rule unchanged: a domain root never prefix-grounds a deep link.
    assert not _urls_prefix_match("https://real.com/deep/link", "https://real.com")

    # --- grounding_check.enabled master switch honored (2026-07-12 audit G2: the template
    # shipped it but nothing read it — an unhonored kill switch) ---
    def _enabled_off_scenario():
        from tools.fs import _IN_MEMORY_FS
        from tools.core import tool_quotas_ctx as q_ctx
        _orig_ws6 = _config.cfg.get("settings", {}).get("workspace")
        _orig_gc2 = _config.cfg.get("settings", {}).get("grounding_check")
        _config.cfg["settings"]["workspace"] = {"type": "memory", "required_artifact": "final_report.md"}
        _config.cfg["settings"]["grounding_check"] = {"enabled": False}
        saved_fs = dict(_IN_MEMORY_FS)
        try:
            _IN_MEMORY_FS.clear()
            reset_fetched_urls()
            # findings.md fabricated AND the report cites a never-fetched URL — with the master
            # switch off, neither grounding gate may fire (structural checks still pass: the
            # run delegated and both artifacts exist).
            _IN_MEMORY_FS["findings.md"] = "- todo de memoria, sin fuente"
            _IN_MEMORY_FS["final_report.md"] = "- x [g](https://never-fetched.example.com/y)"
            q_ctx.set({"delegate_tasks": {"used": 1, "limit": 5}})
            rs = RunState(tempfile.gettempdir())
            run_state_ctx.set(rs)
            msgs = []
            should_retry, _ = _asyncio.run(run_completion_check(
                query="q", current_input="q", run_state=rs, notify=msgs.append))
            recorded = rs.data["completion_check_attempts"][-1]["problem"]
            assert recorded is None and not should_retry, (recorded, should_retry, msgs)
        finally:
            _IN_MEMORY_FS.clear()
            _IN_MEMORY_FS.update(saved_fs)
            reset_fetched_urls()
            if _orig_ws6 is None:
                _config.cfg["settings"].pop("workspace", None)
            else:
                _config.cfg["settings"]["workspace"] = _orig_ws6
            if _orig_gc2 is None:
                _config.cfg["settings"].pop("grounding_check", None)
            else:
                _config.cfg["settings"]["grounding_check"] = _orig_gc2

    contextvars.copy_context().run(_enabled_off_scenario)

    # --- A3 citation-format layer (run 14's format half: table + detached '### Source URLs') ---
    from utils.grounding import split_prose_from_sources

    # The detached-source-section heading variants must be stripped from prose...
    for heading in ("### Source URLs", "## Sources", "**Fuentes:**", "## Fuentes consultadas",
                    "References used:"):
        rep = f"- claim line\n{heading}\nhttps://x.co/a"
        assert "x.co" not in split_prose_from_sources(rep), heading
    # ...but a real content heading that merely starts with the word must NOT be.
    kept = split_prose_from_sources("## Sources of growth in Colombia\nhttps://x.co/a")
    assert "x.co" in kept

    _table_report = ("| Sector | Valor |\n"
                     "| Fintech | USD 3.5 mil millones en el mercado local en 2024 |\n"
                     "| Agro | 12% de crecimiento anual en exportaciones regionales |\n"
                     "| Salud | 2.300 empresas registradas en el sector durante 2023 |\n"
                     "\n### Source URLs\n- https://gov.example.co/page\n")
    assert len(find_uncited_claim_lines(_table_report)) >= 3
    # Properly formatted claim+citation lines never count, nor do short/heading/separator lines.
    _good_report = ("# Informe\n"
                    "- **[Fintech](https://gov.example.co/page)** USD 3.5 mil millones en 2024\n"
                    "- **[Agro](https://gov.example.co/page)** 12% de crecimiento anual\n"
                    "|---|---|\n")
    assert find_uncited_claim_lines(_good_report) == []
    # Run 15's live FALSE POSITIVE (2026-07-12): a per-niche '#### Sources' block under each
    # h3 section ties that section's claims to sources — held a correctly-grounded report
    # through 3 nudges. h4+ blocks must survive split_prose_from_sources, and a section
    # containing a URL exempts its own figure lines.
    _sectioned_report = (
        "## Research Objective\n"
        "Identify B2B technology opportunities in Colombia for 2026 where a small team could generate revenue.\n\n"
        "### 1. Cattle Traceability\n"
        "| **Regulation** | Ley 2585 de 2026 — trazabilidad ganadera obligatoria |\n"
        "| **Compliance Deadline** | Enacted 4 June 2026; implementation required by that date |\n"
        "| **Market Size** | 2.300 productores registrados en el sistema en 2025 |\n"
        "#### Sources\n"
        "- **[Ley 2585 de 2026](https://sidn.example.gov.co/ley_2585)**\n")
    assert "sidn.example.gov.co" in split_prose_from_sources(_sectioned_report)
    _hits = find_uncited_claim_lines(_sectioned_report)
    assert len(_hits) < 3 and not any("2585" in h for h in _hits), _hits

    # --- context-budget guard: stream char accounting (settings.context_budget_chars) ---
    from engine.orchestrator import stream_content_chars, get_context_budget

    class _C:
        def __init__(self, **kw): [setattr(self, k, v) for k, v in kw.items()]
    class _U:
        def __init__(self, contents): self.contents = contents

    assert stream_content_chars(_U([_C(text="abcde")])) == 5
    assert stream_content_chars(_U([_C(arguments='{"q":1}'), _C(result="xyz")])) == 10
    assert stream_content_chars(_U([_C(result=12345)])) == 5   # non-str result stringified
    assert stream_content_chars(_U([])) == 0
    _orig_cb = _config.cfg.get("settings", {}).get("context_budget_chars")
    try:
        _config.cfg["settings"]["context_budget_chars"] = 50000
        assert get_context_budget() == 50000
        _config.cfg["settings"]["context_budget_chars"] = 0
        assert get_context_budget() == 0
        _config.cfg["settings"].pop("context_budget_chars")
        assert get_context_budget() == 0  # absent = off
    finally:
        if _orig_cb is None:
            _config.cfg["settings"].pop("context_budget_chars", None)
        else:
            _config.cfg["settings"]["context_budget_chars"] = _orig_cb

    # --- role-aware budget nudge (2026-07-21, live-caught): SUBAGENT_BUDGET_NUDGE's "do NOT call
    # any more tools, return as your final message" is correct for Searcher/Analyzer roles but
    # directly CAUSES the narrate-instead-of-write bug for writer roles (Builder/FindingsWriter),
    # whose entire success criterion IS calling write_workspace_file. Live case: a 25-finding run
    # pushed FindingsWriter's own generation over budget, the old nudge told it to stop calling
    # tools and narrate instead, and the narration itself then got salvaged as an unverified,
    # truncated draft (3 of 25 real findings) instead of a real file ever being written. ---
    from engine.orchestrator import (
        _select_budget_nudge, SUBAGENT_BUDGET_NUDGE, SUBAGENT_BUDGET_NUDGE_WRITER,
    )

    assert _select_budget_nudge("FindingsWriter") == SUBAGENT_BUDGET_NUDGE_WRITER
    assert _select_budget_nudge("Builder") == SUBAGENT_BUDGET_NUDGE_WRITER
    assert _select_budget_nudge("PeerReviewer") == SUBAGENT_BUDGET_NUDGE
    assert _select_budget_nudge("WebSearcher") == SUBAGENT_BUDGET_NUDGE
    assert _select_budget_nudge(None) == SUBAGENT_BUDGET_NUDGE
    # The writer nudge must actually tell the model to call write_workspace_file, not to narrate —
    # pin the actual behavioral difference, not just object identity.
    assert "write_workspace_file" in SUBAGENT_BUDGET_NUDGE_WRITER
    assert "do not call any more tools" not in SUBAGENT_BUDGET_NUDGE_WRITER.lower()

    # --- C8 charset handling (run 14: the flagship 750KB DIAN law text was saved as mojibake —
    # 'Resolución'/'número' could never string-match, silently gutting every Spanish-term check) ---
    from tools.web import _decode_html_bytes, _strip_boilerplate_html, _meta_declared_encoding

    _latin_body = "<html><body><p>Resolución número 000042 de la DIAN sobre facturación electrónica en Colombia y sus efectos.</p></body></html>"
    # Charset only in the HTTP header
    assert "Resolución número" in _decode_html_bytes(_latin_body.encode("latin-1"), "iso-8859-1")
    # Charset only in the document's own meta tag
    _latin_meta = ('<html><head><meta charset="iso-8859-1"></head><body>'
                   "<p>Resolución número 000042 de la DIAN.</p></body></html>").encode("latin-1")
    assert _meta_declared_encoding(_latin_meta) == "iso-8859-1"
    assert "Resolución número" in _decode_html_bytes(_latin_meta, None)
    # A LYING latin-1 header must not corrupt a UTF-8 page (strict UTF-8 self-validates first)
    assert "Resolución número" in _decode_html_bytes("<p>Resolución número</p>".encode("utf-8"), "iso-8859-1")
    # No declaration anywhere: cp1252 fallback still yields the accents, never mojibake
    assert "Resolución" in _decode_html_bytes("<p>Resolución</p>".encode("latin-1"), None)
    # The stale meta tag must not survive into the cleaned UTF-8 bytes (markitdown would honor
    # it and re-mojibake the content), and the full markitdown round trip must keep the accents.
    _cleaned, _cleaned_meta = _strip_boilerplate_html(_decode_html_bytes(_latin_meta, None))
    assert b"iso-8859-1" not in _cleaned and _cleaned.startswith(b'<meta charset="utf-8">')
    assert _cleaned_meta == {}, _cleaned_meta  # no <title>/meta author/date in this fixture
    from utils.parsers import convert_to_markdown
    with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="wb") as _tmp_html:
        _tmp_html_bytes, _ = _strip_boilerplate_html(_decode_html_bytes(_latin_body.encode("latin-1"), "iso-8859-1"))
        _tmp_html.write(_tmp_html_bytes)
        _tmp_html_path = _tmp_html.name
    try:
        _md = convert_to_markdown(_tmp_html_path)
        assert _md and "Resolución número 000042" in _md, _md
    finally:
        os.unlink(_tmp_html_path)

    # --- line-scoped claim grounding (review #2 item 4): the old WHOLE-report term overlap let
    # generic shared terms mask per-claim fabrication — run 12's flagship figure was absent from
    # its cited source but passed because other lines shared terms with that same source. ---
    from utils.grounding import claim_grounding_problem, decompose_claim_segments

    # --- Phase 1.1/1.2 of the ROADMAP "Claim-level grounding upgrade": decompose_claim_segments
    # (pure segmentation, no fetched-source dependency) ---
    assert decompose_claim_segments("- Cacao: USD 265.1M [gov](https://x.co/a)") == [
        "- Cacao: USD 265.1M [gov](https://x.co/a)"], "single-citation line must decompose to itself unchanged"
    assert decompose_claim_segments("plain text, no citation at all") == ["plain text, no citation at all"]
    _segs = decompose_claim_segments(
        "- Cacao: USD 265.1M [gov](https://x.co/a), mientras Software genero USD 3.5B [tech](https://x.co/b)")
    assert len(_segs) == 2, _segs
    assert _segs[0] == "- Cacao: USD 265.1M [gov](https://x.co/a)", _segs
    assert _segs[1] == ", mientras Software genero USD 3.5B [tech](https://x.co/b)", _segs
    # Trailing uncited text stays attached to the last segment rather than becoming an orphan.
    _segs_trail = decompose_claim_segments(
        "- A [x](https://x.co/a), B [y](https://x.co/b), and an uncited closing remark")
    assert len(_segs_trail) == 2 and _segs_trail[1].endswith("uncited closing remark"), _segs_trail

    def _line_claim_scenario():
        from tools.fs import _IN_MEMORY_FS
        _orig_ws8 = _config.cfg.get("settings", {}).get("workspace")
        _config.cfg["settings"]["workspace"] = {"type": "memory"}
        saved_fs = dict(_IN_MEMORY_FS)
        try:
            _IN_MEMORY_FS.clear()
            reset_fetched_urls()
            record_fetched_url("https://gov.example.co/exportaciones", filename="sources/exp.md")
            _IN_MEMORY_FS["sources/exp.md"] = (
                "Source-URL: https://gov.example.co/exportaciones\n\n"
                "Las exportaciones de cacao de Colombia alcanzaron USD 265.1 millones en 2024, "
                "segun cifras oficiales de la entidad nacional de estadistica.")
            supported_line = "- Cacao: USD 265.1 millones en 2024 [gov](https://gov.example.co/exportaciones)"
            # A line whose own figure appears in its cited source -> silent
            assert claim_grounding_problem(supported_line) is None
            # THE masking case: a supported line + a fabricated figure citing the SAME source.
            # The whole-report version passed this (265.1/2024 overlapped report-wide).
            problem = claim_grounding_problem(
                supported_line
                + "\n- Software: USD 3.5 mil millones en 2023 [gov](https://gov.example.co/exportaciones)")
            assert problem and problem.startswith("claim_unsupported"), problem
            # A line with no checkable terms of its own -> skipped, never flagged
            assert claim_grounding_problem(
                "- el sector crece de forma sostenida [gov](https://gov.example.co/exportaciones)") is None
            # Unfetched citation -> the hard URL gate's job, silent here
            assert claim_grounding_problem(
                "- USD 9.9 mil millones [x](https://never-fetched.example.com/a)") is None

            # THE SAME-LINE citation-sharing/drift case (ROADMAP Phase 1 target): two claims on
            # ONE line, each with its OWN distinct citation. The genuinely-supported cacao claim
            # must not let its citation's overlap "cover for" the second, fabricated claim whose
            # OWN cited source doesn't support it at all -- the exact gap the old whole-line
            # union check had (a shared generic phrase between the two sources would have masked
            # this before decompose_claim_segments existed).
            record_fetched_url("https://gov.example.co/agro", filename="sources/agro.md")
            _IN_MEMORY_FS["sources/agro.md"] = (
                "Source-URL: https://gov.example.co/agro\n\n"
                "El sector agropecuario crecio 8% en el primer trimestre de 2025, impulsado "
                "por la demanda internacional de cafe.")
            same_line = (
                "- Cacao: USD 265.1 millones en 2024 [gov](https://gov.example.co/exportaciones), "
                "mientras Software genero USD 3.5 mil millones en 2023 [tech](https://gov.example.co/agro)")
            problem = claim_grounding_problem(same_line)
            assert problem and problem.startswith("claim_unsupported"), (
                "a same-line second claim citing a source that doesn't support it must be caught "
                "even though the FIRST claim on the same line is genuinely supported", problem)
            assert "agro" in problem, (
                "the flagged citation must be the second claim's OWN (unsupporting) source, not "
                "the first claim's genuinely-supporting one", problem)
            # Same shape, but BOTH claims genuinely supported by their own distinct sources -> silent.
            clean_same_line = (
                "- Cacao: USD 265.1 millones en 2024 [gov](https://gov.example.co/exportaciones), "
                "mientras el agro crecio 8% en 2025 [gov](https://gov.example.co/agro)")
            assert claim_grounding_problem(clean_same_line) is None, (
                "two claims on one line, each genuinely supported by its own distinct citation, "
                "must not be flagged")
        finally:
            _IN_MEMORY_FS.clear()
            _IN_MEMORY_FS.update(saved_fs)
            reset_fetched_urls()
            if _orig_ws8 is None:
                _config.cfg["settings"].pop("workspace", None)
            else:
                _config.cfg["settings"]["workspace"] = _orig_ws8

    contextvars.copy_context().run(_line_claim_scenario)

    # --- claim_grounding_problem must skip citation-only sub-bullets, not treat their own anchor
    # text as a checkable claim (live false positive, 2026-07-14, Eiffel Tower smoke test): a
    # genuinely-grounded report using "claim on one line, '- Source: [Title](url).' on the next"
    # (this project's own Builder output shape, distinct from the inline "[gov](url)" same-line
    # style tested above) burned its ENTIRE retry budget (6 consecutive claim_unsupported verdicts)
    # because extract_salient_terms pulled "Official Eiffel Tower" out of the citation's own
    # editorialized anchor text "[Official Eiffel Tower website](url)" and flagged it for not
    # appearing verbatim in the source -- even though the actual claims (verbatim figures) WERE
    # genuinely present in the fetched source. Root-caused directly against the real failing run's
    # saved report + fetched source (research_output/what_year_was_the_eiffel_tower_completed_
    # 20260714_215912/), reproduced here as a minimal regression case.
    #
    # 2026-07-19 QA audit: this scenario used to hand-retype an approximation of the real report/
    # source pair instead of loading it from disk, even though the real run directory still exists
    # in-repo -- the exact "fixture encodes the same assumption as the fix, not the real data"
    # pattern that shipped the 2026-07-19 findings.md bug. Now loads the ACTUAL final_report.md and
    # ACTUAL fetched source verbatim from that run directory. ---
    def _citation_only_subbullet_scenario():
        from tools.fs import _IN_MEMORY_FS
        _orig_ws12 = _config.cfg.get("settings", {}).get("workspace")
        _config.cfg["settings"]["workspace"] = {"type": "memory"}
        saved_fs = dict(_IN_MEMORY_FS)
        run_dir = os.path.join(
            os.path.dirname(__file__),
            "research_output", "what_year_was_the_eiffel_tower_completed_20260714_215912")
        report_path = os.path.join(run_dir, "final_report.md")
        source_path = os.path.join(run_dir, "sources", "toureiffel_paris_history.md")
        if not (os.path.exists(report_path) and os.path.exists(source_path)):
            # Real run directory not present in this checkout (e.g. a fresh clone without
            # research_output/ committed) -- skip rather than silently fall back to a synthetic
            # fixture, which is exactly the gap this rewrite exists to close.
            return
        try:
            _IN_MEMORY_FS.clear()
            reset_fetched_urls()
            with open(report_path, encoding="utf-8") as f:
                report = f.read()
            with open(source_path, encoding="utf-8") as f:
                source_body = f.read()
            record_fetched_url("https://www.toureiffel.paris/en/the-monument/history", filename="sources/tour.md")
            _IN_MEMORY_FS["sources/tour.md"] = (
                "Source-URL: https://www.toureiffel.paris/en/the-monument/history\n\n" + source_body)
            assert claim_grounding_problem(report) is None, (
                "the REAL final_report.md that live-triggered this bug (2026-07-14) must not be "
                "flagged against its REAL fetched source, now that the citation-only sub-bullet "
                "fix is in place")
            # Same fix, same shape, ported to _grounded_claim_pairs -- must not surface the
            # citation-only sub-bullet as a (window, claim, display) pair either.
            from utils.grounding import _grounded_claim_pairs
            pairs = _grounded_claim_pairs(report)
            for _, claim_text, _display in pairs:
                assert claim_text.strip() != "- Source: [Official Eiffel Tower website]", (
                    "a bare citation sub-bullet must never surface as a claim pair", pairs)
        finally:
            _IN_MEMORY_FS.clear()
            _IN_MEMORY_FS.update(saved_fs)
            reset_fetched_urls()
            if _orig_ws12 is None:
                _config.cfg["settings"].pop("workspace", None)
            else:
                _config.cfg["settings"]["workspace"] = _orig_ws12

    contextvars.copy_context().run(_citation_only_subbullet_scenario)

    # --- _grounded_claim_pairs (shared by nli_unsupported_problem/topical_relevance_problem) must
    # be SEGMENT-scoped via decompose_claim_segments, same fix class already shipped for
    # claim_grounding_problem above (ROADMAP "Residual note" on the Phase 1 claim-level grounding
    # upgrade, closed 2026-07-14) — a same-line multi-claim case must not let one claim's window
    # get attributed to the wrong citation, or one claim's own evidence get diluted by the other
    # claim's terms. Pure-function test (no NLI model load needed; _grounded_claim_pairs itself has
    # no model dependency, only its two callers do). ---
    def _grounded_claim_pairs_scenario():
        from tools.fs import _IN_MEMORY_FS
        from utils.grounding import _grounded_claim_pairs
        _orig_ws11 = _config.cfg.get("settings", {}).get("workspace")
        _config.cfg["settings"]["workspace"] = {"type": "memory"}
        saved_fs = dict(_IN_MEMORY_FS)
        try:
            _IN_MEMORY_FS.clear()
            reset_fetched_urls()
            record_fetched_url("https://gov.example.co/exportaciones", filename="sources/exp.md")
            _IN_MEMORY_FS["sources/exp.md"] = (
                "Source-URL: https://gov.example.co/exportaciones\n\n"
                "Las exportaciones de cacao de Colombia alcanzaron USD 265.1 millones en 2024, "
                "segun cifras oficiales de la entidad nacional de estadistica.")
            record_fetched_url("https://gov.example.co/agro", filename="sources/agro.md")
            _IN_MEMORY_FS["sources/agro.md"] = (
                "Source-URL: https://gov.example.co/agro\n\n"
                "El sector agropecuario crecio 8% en el primer trimestre de 2025, impulsado "
                "por la demanda internacional de cafe.")
            same_line = (
                "- Cacao: USD 265.1 millones en 2024 [gov](https://gov.example.co/exportaciones), "
                "mientras el agro crecio 8% en 2025 [gov](https://gov.example.co/agro)")
            pairs = _grounded_claim_pairs(same_line)
            assert len(pairs) == 2, (
                "a same-line two-claim, two-citation report must yield two separate pairs, not one "
                "merged whole-line pair", pairs)
            by_display = {display: (window, claim) for window, claim, display in pairs}
            assert "https://gov.example.co/exportaciones" in by_display
            assert "https://gov.example.co/agro" in by_display
            cacao_window, cacao_claim = by_display["https://gov.example.co/exportaciones"]
            agro_window, agro_claim = by_display["https://gov.example.co/agro"]
            # Each claim segment's own text must not bleed into the other's -- the cacao claim
            # text must not contain "agro"/"cafe" and vice versa (the exact drift the whole-line
            # version was vulnerable to: one segment's terms diluting or misattributing evidence
            # meant for the other).
            assert "cacao" in cacao_claim.lower() and "agro" not in cacao_claim.lower(), cacao_claim
            assert "agro" in agro_claim.lower() and "cacao" not in agro_claim.lower(), agro_claim
            # Each window must come from ITS OWN cited source, not the other claim's.
            assert "cacao" in cacao_window.lower() or "exportaciones" in cacao_window.lower(), cacao_window
            assert "agropecuario" in agro_window.lower(), agro_window
        finally:
            _IN_MEMORY_FS.clear()
            _IN_MEMORY_FS.update(saved_fs)
            reset_fetched_urls()
            if _orig_ws11 is None:
                _config.cfg["settings"].pop("workspace", None)
            else:
                _config.cfg["settings"]["workspace"] = _orig_ws11

    contextvars.copy_context().run(_grounded_claim_pairs_scenario)

    # --- quarantined-draft restore beats narration salvage (runs 11/13's endgame) ---
    from engine.tui import _restore_quarantined_draft

    with tempfile.TemporaryDirectory() as tmpdir:
        def _restore_scenario():
            _orig_ws6 = _config.cfg.get("settings", {}).get("workspace")
            _config.cfg["settings"]["workspace"] = {"type": "disk", "dir": tmpdir}
            try:
                draft_path = os.path.join(tmpdir, "final_report.md.rejected_attempt_1")
                with open(draft_path, "w", encoding="utf-8") as f:
                    f.write("# Real Report\nActual researched content.")
                assert _restore_quarantined_draft("final_report.md", "regulation_unsupported")
                restored = open(os.path.join(tmpdir, "final_report.md"), encoding="utf-8").read()
                assert "QUARANTINED DRAFT" in restored and "regulation_unsupported" in restored
                assert "Actual researched content." in restored
                # No-op when the artifact already exists (never clobber a real report)
                assert not _restore_quarantined_draft("final_report.md", "x")
            finally:
                if _orig_ws6 is None:
                    _config.cfg["settings"].pop("workspace", None)
                else:
                    _config.cfg["settings"]["workspace"] = _orig_ws6

        contextvars.copy_context().run(_restore_scenario)

    # --- _get_safe_path Windows escape (review #2 finding 1: os.path.join discards the base
    # for drive-qualified/drive-relative names, letting write_workspace_file leave the workspace) ---
    with tempfile.TemporaryDirectory() as tmpdir:
        def _safe_path_scenario():
            from tools.fs import _get_safe_path
            _orig_ws7 = _config.cfg.get("settings", {}).get("workspace")
            _config.cfg["settings"]["workspace"] = {"type": "disk", "dir": tmpdir}
            try:
                assert _get_safe_path("C:\\evil.md") == ""       # drive-qualified
                assert _get_safe_path("C:evil.md") == ""          # drive-relative
                assert _get_safe_path("..\\evil.md") == ""        # traversal (pre-existing guard)
                ok = _get_safe_path("notes/sub.md")
                assert ok and os.path.commonpath([os.path.abspath(tmpdir), ok]) == os.path.abspath(tmpdir), ok
            finally:
                if _orig_ws7 is None:
                    _config.cfg["settings"].pop("workspace", None)
                else:
                    _config.cfg["settings"]["workspace"] = _orig_ws7

        contextvars.copy_context().run(_safe_path_scenario)

    # --- structural eval scorer (review #2 item 5: rubric tier 1 from _run_state.json, which no
    # other scorer reads — an LLM judge only ever sees the report's self-presentation) ---
    import importlib.util as _ilu
    import json as _json
    _spec = _ilu.spec_from_file_location(
        "eval_evaluate", os.path.join(os.path.dirname(os.path.abspath(__file__)), "eval", "evaluate.py"))
    _ev = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_ev)

    with tempfile.TemporaryDirectory() as tmpdir:
        _u = "https://gov.example.co/page"
        def _write_run(report, findings, state):
            for name, content in (("final_report.md", report), ("findings.md", findings)):
                path = os.path.join(tmpdir, name)
                if content is None:
                    if os.path.exists(path): os.remove(path)
                else:
                    with open(path, "w", encoding="utf-8") as f: f.write(content)
            with open(os.path.join(tmpdir, "_run_state.json"), "w", encoding="utf-8") as f:
                _json.dump(state, f)

        good_state = {"fetched_urls": [{"url": _u, "filename": "sources/p.md"}],
                      "completion_check_attempts": [{"attempt": 0, "problem": None}]}
        # Clean run: report + findings both cite the real fetch, no unresolved problem -> 4/4
        _write_run(f"- x [g]({_u})", f"- f ({_u})", good_state)
        assert _ev.score_structural(tmpdir, "final_report.md") == 1.0
        # Salvaged report citing an unfetched URL, no findings, unresolved problem -> 0/4
        _write_run("> **AUTO-RECOVERED DRAFT** —\n- x [g](https://fake.example.com/a)", None,
                   {"fetched_urls": [{"url": _u, "filename": "sources/p.md"}],
                    "completion_check_attempts": [{"attempt": 2, "problem": "missing_artifact"}]})
        assert _ev.score_structural(tmpdir, "final_report.md") == 0.0
        # Honest partial: clean report + grounded findings, but the run ended unresolved -> 3/4
        _write_run(f"- x [g]({_u})", f"- f ({_u})",
                   {"fetched_urls": [{"url": _u, "filename": "sources/p.md"}],
                    "completion_check_attempts": [{"attempt": 3, "problem": "claim_unsupported"}]})
        assert _ev.score_structural(tmpdir, "final_report.md") == 0.75
        assert _ev.score_structural(None, "final_report.md") == 0.0

    # --- intake verdict parsing (fail-open: the clarifier can never block research) ---
    assert _clarify_verdict("CLEAR") is None
    assert _clarify_verdict("  clear\n") is None
    assert _clarify_verdict("") is None
    assert _clarify_verdict(None) is None
    assert _clarify_verdict("x" * 700) is None  # rambling => proceed
    q = "1. Which country?\n2. What timeframe?"
    assert _clarify_verdict(q) == q

    # --- max_run_minutes wall-clock cutoff actually fires even when the stream goes silent ---
    # (live bug, 2026-07-12): the old `async for update in stream: if deadline exceeded: break`
    # only checked the deadline when an update actually arrived. A real run against
    # deepdelve-tongyi blew 6+ minutes past its configured max_run_minutes=60 with the GPU still
    # actively generating one silent multi-minute <think> block and zero cutoff message. Fixed in
    # run_cli (engine/tui.py) by manually driving __anext__() through asyncio.wait_for(...,
    # timeout=remaining) instead of a plain `async for` — this proves that exact mechanism cuts
    # off a stream that goes silent past its deadline, on a real wall-clock timer, independent of
    # whether the stream ever yields again.
    import time

    class _SlowStream:
        def __aiter__(self):
            return self
        async def __anext__(self):
            if not hasattr(self, "_given"):
                self._given = True
                return "first update"
            await _asyncio.sleep(10)  # simulates a long silent <think> block past the deadline
            return "never reached"

    async def _cutoff_scenario():
        deadline = time.monotonic() + 0.2
        it = _SlowStream().__aiter__()
        received = []
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return received, "pre_check"
            try:
                update = await _asyncio.wait_for(it.__anext__(), timeout=remaining)
            except _asyncio.TimeoutError:
                return received, "timeout"
            received.append(update)

    _start = time.monotonic()
    _received, _how = _asyncio.run(_cutoff_scenario())
    _elapsed = time.monotonic() - _start
    assert _received == ["first update"], _received
    assert _how == "timeout", _how
    assert _elapsed < 2, f"cutoff must fire near the 0.2s deadline, not wait for the 10s stall ({_elapsed}s)"

    # --- fuzzy filename fallback: a sub-agent's garbled/reconstructed filename should still
    # resolve if it clearly maps to one real file ---
    # (live bug, 2026-07-12): sub-agents handed a filename second-hand (not the one they fetched
    # themselves) reconstruct it from memory and get it wrong — 'sources/nixtaverse_nixta?',
    # 'sources/arxiv_org_metaheuristic_analysis?', 'sources/Arxiv????' were all observed live,
    # each burning a full turn + quota unit on a doomed read_workspace_file/grep_workspace_file
    # call. Measured on that one run: 16% of read/grep calls failed "not found", 7/12 of those
    # visibly garbled with a literal '?'.
    from tools.fs import resolve_fuzzy_filename, read_workspace_file, grep_workspace_file, _IN_MEMORY_FS

    def _fuzzy_filename_scenario():
        _orig_ws7 = _config.cfg.get("settings", {}).get("workspace")
        _config.cfg["settings"]["workspace"] = {"type": "memory"}
        saved_fs = dict(_IN_MEMORY_FS)
        try:
            _IN_MEMORY_FS.clear()
            _IN_MEMORY_FS["sources/nixtlaverse_nixtla_exogenous_features_a1b2c3d4.md"] = (
                "Source-URL: https://nixtlaverse.nixtla.io/mlforecast/docs/how-to-guides/exogenous_features.html\n\n"
                "Nixtla exogenous features documentation content here."
            )
            _IN_MEMORY_FS["sources/arxiv_org_genetic_algorithm_deep_learning_sales_e5f6a7b8.md"] = (
                "Source-URL: https://arxiv.org/abs/2410.15047\n\nGenetic algorithm paper content."
            )
            # A garbled request with a literal '?' (exactly the observed live shape) resolves to
            # the one real file it clearly maps to.
            assert resolve_fuzzy_filename("sources/nixtaverse_nixta?") == \
                "sources/nixtlaverse_nixtla_exogenous_features_a1b2c3d4.md"
            # '.' vs '_' mismatch (arxiv.org vs the real arxiv_org slug) also resolves.
            assert resolve_fuzzy_filename("sources/arxiv.org_genetic_algorithm_deep_l") == \
                "sources/arxiv_org_genetic_algorithm_deep_learning_sales_e5f6a7b8.md"
            # Too short / no real overlap -> no confident auto-resolve, stays None.
            assert resolve_fuzzy_filename("sources/xyz?") is None
            assert resolve_fuzzy_filename("sources/completely_unrelated_name_here") is None
            # (In real production flow, resolve_fuzzy_filename is only ever called AFTER an exact
            # get_workspace_file_content lookup already missed — the tools never call it for a
            # name that already resolved. Calling it directly with an exact name is just a
            # 1.0-ratio match to itself, which is correct, not a special case to guard against.)

            # End-to-end through the actual tools: a garbled filename still returns real content
            # (and the response shows the corrected filename, not the garbled one).
            result = read_workspace_file("sources/nixtaverse_nixta?")
            assert "Nixtla exogenous features documentation" in result
            assert "sources/nixtlaverse_nixtla_exogenous_features_a1b2c3d4.md" in result
            assert "not found" not in result

            grep_result = grep_workspace_file("sources/arxiv_org_genetic_algo_deep?", "Genetic")
            assert "genetic algorithm" in grep_result.lower() or "Genetic" in grep_result
            assert "not found — searched" in grep_result

            # A genuinely unresolvable filename still fails cleanly, no false-positive resolve.
            assert "not found" in read_workspace_file("sources/nothing_like_this_exists_at_all.md")
            # Too short/generic even after cleaning ('Arxiv????' -> just 'arxiv') to safely
            # auto-resolve on its own — conservative by design, matches this project's posture.
            assert resolve_fuzzy_filename("sources/Arxiv????") is None
        finally:
            _IN_MEMORY_FS.clear()
            _IN_MEMORY_FS.update(saved_fs)
            if _orig_ws7 is None:
                _config.cfg["settings"].pop("workspace", None)
            else:
                _config.cfg["settings"]["workspace"] = _orig_ws7

    contextvars.copy_context().run(_fuzzy_filename_scenario)

    # --- structured run-state diagnostics (2026-07-12): answer "why did each attempt fail",
    # "how many tool calls errored", and "was this task re-delegated" from _run_state.json alone,
    # without hand-parsing the raw session-event JSON ---
    with tempfile.TemporaryDirectory() as _tmpdir2:
        _rs = RunState(_tmpdir2)

        # record_attempt's new `detail` param persists the full verdict text, not just the label.
        _rs.record_attempt(0, "claim_unsupported", 5, detail="claim_unsupported:https://x.org/a")
        assert _rs.data["completion_check_attempts"][-1]["detail"] == "claim_unsupported:https://x.org/a"
        _rs.record_attempt(1, None, 5)  # detail defaults to None, not required
        assert _rs.data["completion_check_attempts"][-1]["detail"] is None

        # record_tool_error counts + samples (capped at 10 so a constantly-erroring run doesn't
        # bloat _run_state.json).
        for i in range(15):
            _rs.record_tool_error(f"[Agent] Error: sample {i}")
        assert _rs.data["tool_error_count"] == 15
        assert len(_rs.data["tool_error_samples"]) == 10

        # next_subagent_label: first dispatch unchanged, repeats get disambiguated.
        assert _rs.next_subagent_label("SubAgent_background") == "SubAgent_background"
        assert _rs.next_subagent_label("SubAgent_background") == "SubAgent_background#2"
        assert _rs.next_subagent_label("SubAgent_background") == "SubAgent_background#3"
        assert _rs.data["subagent_invocations"]["SubAgent_background"] == 3

        # Collision guard: if a task was genuinely (if implausibly) named to match what the
        # auto-disambiguator would generate for a DIFFERENT task, the generator must not silently
        # collide with it — this is the exact scenario raised when reviewing this feature.
        _rs2 = RunState(_tmpdir2)
        assert _rs2.next_subagent_label("SubAgent_x") == "SubAgent_x"                 # 1st real "x"
        assert _rs2.next_subagent_label("SubAgent_x#2") == "SubAgent_x#2"             # a DIFFERENT
        #                                                                                real task
        #                                                                                literally
        #                                                                                named "x#2"
        assert _rs2.next_subagent_label("SubAgent_x") == "SubAgent_x#3", (
            "2nd real dispatch of 'x' must skip the already-claimed '#2' and land on '#3', "
            "not silently collide with the unrelated task literally named 'x#2'"
        )
        assert len({"SubAgent_x", "SubAgent_x#2", "SubAgent_x#3"}) == 3  # all distinct

    # --- TUI tool-call widget: an error RESULT must not render a green success checkmark ---
    # (live bug, 2026-07-12): a read_workspace_file call that failed with 'Error: Requested
    # function "read_workspace..." not found.' still showed a checkmark, because ToolCallWidget's
    # set_result unconditionally used the success marker regardless of what the result text
    # actually said — this project's tools return formatted error strings instead of raising, so
    # "the call returned" and "the call succeeded" are NOT the same thing.
    from engine.tui import _looks_like_tool_error
    assert _looks_like_tool_error('Error: Requested function "read_workspace..." not found.')
    assert _looks_like_tool_error("Error: 'foo.md' not found.")
    assert _looks_like_tool_error("CRITICAL TOOL EXECUTION ERROR: web_search failed internally.")
    assert _looks_like_tool_error("## Error for Analyze paper\nTask forcefully aborted: timeout\n---")
    assert not _looks_like_tool_error("Wrote 'final_report.md' to disk.")
    assert not _looks_like_tool_error("## Result for background\n**Findings**\n\n- real content")
    assert not _looks_like_tool_error("")
    assert not _looks_like_tool_error(None)

    # --- create_local_agent must return a 3-tuple (agent, session, dispatch_task) — a caller
    # still unpacking 2 values is a hard ValueError, not a silent bug, but worth pinning since
    # engine/completion.py's Build->Review->Fix loop depends on the 3rd element being callable
    # and resolving agent_id via the SAME available_sub_agents_ctx the Planner itself uses. ---
    def _create_local_agent_shape_scenario():
        from engine.orchestrator import create_local_agent
        from engine.sdk import AgentBuilder

        builder = AgentBuilder(name="TestPlanner", description="test", instructions="You are a test agent.", tools=[])
        result = create_local_agent(builder=builder)
        assert isinstance(result, tuple) and len(result) == 3, (
            "create_local_agent must return (agent, session, dispatch_task)", result)
        agent, session, dispatch_task = result
        assert callable(dispatch_task), "3rd element (dispatch_task) must be callable"

    contextvars.copy_context().run(_create_local_agent_shape_scenario)

    # --- _build_findings_source_material must dedupe exact (source_url, summary) repeats before
    # serializing into FindingsWriter's prompt (2026-07-14 live finding: every completion-check
    # retry that re-delegates the same task_name re-adds a finding without removing the stale one,
    # so a real run accumulated 25 entries for ~8-10 distinct pieces of research, with some summaries
    # appearing identically 5 times — genuine content was getting diluted/dropped by FindingsWriter
    # under the bloat rather than something Colombia-specific). Distinct summaries for the SAME
    # source_url (a legitimately different retry result) must both survive. ---
    def _findings_dedup_scenario():
        from engine.completion import _build_findings_source_material

        with tempfile.TemporaryDirectory() as tmpdir:
            rs = RunState(tmpdir)
            rs.add_finding(_SRC, "same summary text", task_name="background", depth=1)
            rs.add_finding(_SRC, "same summary text", task_name="background", depth=1)  # exact repeat
            rs.add_finding(_SRC, "same summary text", task_name="background", depth=1)  # exact repeat
            rs.add_finding(_SRC, "a genuinely different summary", task_name="background", depth=1)
            material = _build_findings_source_material(rs)
            assert material.count("same summary text") == 1, (
                "exact-duplicate findings must be collapsed to one entry", material)
            assert "a genuinely different summary" in material

    contextvars.copy_context().run(_findings_dedup_scenario)

    # --- _build_findings_source_material must show each finding's REAL saved filename alongside
    # its URL (2026-07-19, user-proposed extension of the same-day delegate_tasks filename fix) —
    # FINDINGS_WRITER_INSTRUCTIONS' own Workflow step 2 already claimed "path is given alongside
    # its URL", which was FALSE before this fix (only the separate fetched_block cross-reference
    # list at the bottom had it) — this makes that existing prompt claim true. A finding whose
    # source_url isn't a real fetch (the coverage() task_name fallback for an uncovered task) must
    # NOT get a fabricated filename annotation. ---
    def _findings_filename_scenario():
        from engine.completion import _build_findings_source_material

        with tempfile.TemporaryDirectory() as tmpdir:
            rs = RunState(tmpdir)
            rs.data["fetched_urls"] = [{"url": _SRC, "filename": "sources/page.md"}]
            rs.add_finding(_SRC, "a real finding", task_name="t1", depth=1)
            rs.add_finding("uncovered_task", "", task_name="t2", depth=1)  # no real fetch at all
            material = _build_findings_source_material(rs)
            assert f"### Source: {_SRC} (saved as sources/page.md)" in material, material
            # The no-real-fetch entry gets no filename annotation, and (2026-07-21 fabrication
            # fix) no "### Source: ..." heading at all -- see _findings_uncited_fallback_scenario.
            assert "### Source: uncovered_task" not in material, material

    contextvars.copy_context().run(_findings_filename_scenario)

    # --- A finding whose source_url is the add_finding task_name fallback (no real fetched or
    # reference URL at all) must NEVER be rendered as a "### Source: ..." entry -- that shape is
    # what lets the engine treat an entry as citable, and a bare task_name string wearing it was
    # silently cited as a fake URL by a real qwen3:8b run (5/19 findings fabricated,
    # 2026-07-21). It must instead be named in a separate, explicitly non-citable list. ---
    def _findings_uncited_fallback_scenario():
        from engine.completion import _build_findings_source_material

        with tempfile.TemporaryDirectory() as tmpdir:
            rs = RunState(tmpdir)
            rs.add_finding(_SRC, "a real finding", task_name="t1", depth=1)
            rs.add_finding("orphan_task", "some narration with no real source", task_name="orphan_task", depth=2)
            material = _build_findings_source_material(rs)
            assert "### Source: orphan_task" not in material, (
                "a task_name fallback must never be rendered as a citable Source heading", material)
            assert "orphan_task" in material, (
                "the task must still be named so the model can acknowledge the gap", material)
            assert "invent" in material.lower() or "fabricat" in material.lower(), (
                "the model must be explicitly told not to fabricate a source for it", material)

    contextvars.copy_context().run(_findings_uncited_fallback_scenario)

    # --- _build_findings_source_material must cap total size against context_budget_chars instead
    # of handing an unbounded string to a fresh dispatch for the model backend to silently truncate
    # (2026-07-19 QA audit, "real grounded content silently vanishes during synthesis" investigation
    # -- this was a genuinely unguarded injection point, unlike the Planner's stream and a sub-
    # agent's own generation, which both already had a context_budget_chars-style guard). Whole
    # entries only (never truncate one mid-way), earliest-first, and the omitted task names must be
    # named explicitly so the model can acknowledge the gap instead of silently dropping it. ---
    def _findings_budget_scenario():
        from engine.completion import _build_findings_source_material
        _orig_budget = _config.cfg.get("settings", {}).get("context_budget_chars")
        try:
            _config.cfg.setdefault("settings", {})["context_budget_chars"] = 300
            with tempfile.TemporaryDirectory() as tmpdir:
                rs = RunState(tmpdir)
                rs.add_finding("https://a.example.com", "x" * 150, task_name="first_task", depth=1)
                rs.add_finding("https://b.example.com", "y" * 150, task_name="second_task", depth=1)
                rs.add_finding("https://c.example.com", "z" * 150, task_name="third_task", depth=1)
                material = _build_findings_source_material(rs)
                assert "first_task" not in material or "x" * 150 in material, (
                    "an entry must never be truncated mid-way -- it's either whole or fully omitted", material)
                assert "z" * 150 not in material, (
                    "later entries beyond the budget must be omitted, not silently included anyway", material)
                assert "omitted" in material.lower(), (
                    "an omission must be explicitly named to the model, not silent", material)
                assert "third_task" in material, (
                    "the omitted task's name must be named so the model can acknowledge the gap", material)

            # context_budget_chars == 0 (off) must disable the cap entirely, same "0 = off"
            # convention as get_context_budget elsewhere.
            _config.cfg["settings"]["context_budget_chars"] = 0
            with tempfile.TemporaryDirectory() as tmpdir2:
                rs2 = RunState(tmpdir2)
                rs2.add_finding("https://a.example.com", "x" * 150, task_name="first_task", depth=1)
                rs2.add_finding("https://b.example.com", "y" * 150, task_name="second_task", depth=1)
                rs2.add_finding("https://c.example.com", "z" * 150, task_name="third_task", depth=1)
                material2 = _build_findings_source_material(rs2)
                assert "z" * 150 in material2, "context_budget_chars=0 must disable the cap entirely"
        finally:
            if _orig_budget is None:
                _config.cfg["settings"].pop("context_budget_chars", None)
            else:
                _config.cfg["settings"]["context_budget_chars"] = _orig_budget

    contextvars.copy_context().run(_findings_budget_scenario)

    # --- Non-generative routing classifier for delegate_tasks (RESEARCH.md §6, ROADMAP.md
    # "Planned", 2026-07-20) — pins _agent_routing_rejection_reason's pure decision logic (the
    # decided "reject-and-nudge" policy) with a fake prediction, no real classifier artifact or
    # contextvar/async machinery needed. ---
    def _agent_routing_rejection_scenario():
        from engine.orchestrator import _agent_routing_rejection_reason

        caller_roles = frozenset({"WebSearcher", "AcademicSearcher"})

        # 1. No prediction available (classifier disabled, or roster doesn't overlap its known
        # classes) -> must never reject, regardless of what the declared agent_id looks like.
        assert _agent_routing_rejection_reason("WebSearcher", caller_roles, None, 0.6) is None
        assert _agent_routing_rejection_reason("searcher", caller_roles, None, 0.6) is None, (
            "with no prediction, even an obviously-wrong agent_id must not be rejected here -- "
            "that's the pre-existing exact-string check in _run_single_task's own job")

        # 2. Declared agent_id isn't one of the CALLER's real roles at all (the exact real
        # hallucination pattern: "searcher" lowercase, invented role names) -> always rejected,
        # regardless of confidence, since it can never resolve to a real sub-agent anyway.
        reason = _agent_routing_rejection_reason("searcher", caller_roles, ("WebSearcher", 0.66), 0.6)
        assert reason is not None and "not a valid specialist" in reason, reason
        assert "WebSearcher" in reason and "0.66" in reason, reason

        # 3. Declared agent_id IS a real role for this caller, but the classifier strongly
        # disagrees (confidence >= min_confidence, different class) -> rejected as "looks wrong",
        # a different message than the unknown-role case.
        reason = _agent_routing_rejection_reason(
            "AcademicSearcher", caller_roles, ("WebSearcher", 0.8), 0.6)
        assert reason is not None and "looks wrong" in reason, reason

        # 4. Declared agent_id IS a real role and the classifier AGREES -> never rejected (the
        # common case must be a true no-op).
        assert _agent_routing_rejection_reason(
            "WebSearcher", caller_roles, ("WebSearcher", 0.9), 0.6) is None

        # 5. Declared agent_id IS a real role, classifier disagrees but BELOW min_confidence ->
        # must abstain (same "none apply" pattern ATLAS/AdaMAST uses, RESEARCH.md §1) rather than
        # force a low-confidence guess into a rejection.
        assert _agent_routing_rejection_reason(
            "AcademicSearcher", caller_roles, ("WebSearcher", 0.5), 0.6) is None, (
            "a low-confidence disagreement must abstain, not reject")

    _agent_routing_rejection_scenario()

    # --- RAG findings cache (ROADMAP.md "Strategic options" item 5, RESEARCH.md §8, 2026-07-20) —
    # replaces the deleted knowledge_cache/experience_cache (929b987). Two scenarios: the pure
    # write-gate decision logic (no embeddings/network needed), and the real embedding-based
    # lookup/save round-trip (real tiny all-MiniLM-L6-v2 embeddings, same approach
    # agent_routing.py's own self-test uses -- no mock needed, the model is small and local). ---
    def _rag_cache_write_gate_scenario():
        from engine.orchestrator import _should_cache_finding

        real_finding = "- **[Rust Releases](https://releases.rs/)**: Rust 1.97.1 is current stable."
        narration_only = "I'll search for authoritative information about the latest stable version."

        # 1. Disabled -> never cache, regardless of how clean the finding is.
        assert _should_cache_finding("", [{"url": "https://example.com"}], False, real_finding) is False

        # 2. Enabled, clean (no warnings), real new URL, real markdown-link finding -> cache.
        assert _should_cache_finding("", [{"url": "https://example.com"}], True, real_finding) is True

        # 3. Enabled, but a verification/relevance warning is present -> never cache, even with a
        # real new URL. A cache entry must never be less verified than a same-run finding.
        assert _should_cache_finding(
            "\n\n[SYSTEM VERIFICATION WARNING: ...]", [{"url": "https://example.com"}], True, real_finding
        ) is False

        # 4. Enabled, clean, but no real new URL (task-name-only fallback finding) -> never cache,
        # there's no real source to cite.
        assert _should_cache_finding("", [], True, real_finding) is False

        # 5. Enabled, clean, real new URL, but the text is pre-delegation NARRATION rather than a
        # real consolidated finding (no markdown-link citation) -> never cache. Found live 2026-07-20:
        # a Searcher's own "I'll search for..." narration can carry a real new_urls entry and zero
        # verification warnings, yet contain no actual finding at all.
        assert _should_cache_finding("", [{"url": "https://example.com"}], True, narration_only) is False

    _rag_cache_write_gate_scenario()

    def _rag_cache_lookup_scenario():
        import time as _time
        import tempfile as _tempfile
        import config as _config
        from utils import rag_cache as _rag_cache

        with _tempfile.TemporaryDirectory() as tmp_dir:
            cache_path = os.path.join(tmp_dir, "rag_cache_test.json")
            _config.cfg["settings"]["rag_cache"] = {
                "enabled": True, "path": cache_path, "max_age_days": 7,
                "min_similarity": 0.75, "top_k": 3,
            }
            # Force a clean in-memory state -- this module-level singleton persists across scenario
            # functions in the same test process, same caution as agent_routing's own self-test.
            _rag_cache._entries = None
            _rag_cache._matrix = None

            _rag_cache.save(
                "current stable version of Rust programming language",
                "https://releases.rs/", "Rust 1.97.1 is the current stable release.", "test-model",
            )

            # A near-duplicate query must hit the cached entry above the configured threshold.
            hits = _rag_cache.lookup("what is the latest stable Rust release", min_similarity=0.5)
            assert hits, "a semantically similar query must return the cached entry"
            assert hits[0]["source_url"] == "https://releases.rs/"
            assert "1.97.1" in hits[0]["summary"]

            # A genuinely unrelated query must NOT match at the configured threshold.
            no_hits = _rag_cache.lookup(
                "history of the Roman aqueduct system", min_similarity=0.75
            )
            assert no_hits == [], "an unrelated query must not return the Rust finding"

            # A stale entry (older than max_age_days) must be excluded even with a perfect query.
            _rag_cache._entries[0]["timestamp"] = _time.time() - (8 * 86400)
            _rag_cache._matrix = None  # force rebuild so the mutated timestamp is picked up
            stale_hits = _rag_cache.lookup(
                "current stable version of Rust programming language",
                min_similarity=0.5, max_age_days=7,
            )
            assert stale_hits == [], "an entry older than max_age_days must be excluded"

            _rag_cache._entries = None
            _rag_cache._matrix = None

    _rag_cache_lookup_scenario()

    print("All structural-check assertions passed.")


if __name__ == "__main__":
    main()
