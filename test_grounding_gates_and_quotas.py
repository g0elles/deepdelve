import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from engine.orchestrator import (
    _extract_excluded_topics,
)
from utils.grounding import find_non_url_citations, fully_ungrounded, partially_ungrounded
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
import json
import tempfile

from utils.run_state import RunState, run_state_ctx

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

    # --- third non-URL pseudo-citation shape, live case 2026-07-29: 【Bracketed Label】-style
    # full-width-bracket markers, real URL living only in a separate "## Sources" list, not on the
    # claim's own line. Confirmed live against the actual saved report that triggered this fix:
    # extract_cited_urls/find_uncited_claim_lines/find_non_url_citations all returned nothing
    # before this addition. ---
    assert find_non_url_citations(
        "Regression remains foundational for sales forecasting【LinkedIn article】.")
    assert find_non_url_citations("The Wikipedia entry defines a heuristic【Wikipedia】.")
    # A markdown link on the SAME line still exempts it, same convention as the other two shapes.
    assert not find_non_url_citations(
        "Regression remains foundational [LinkedIn article](https://linkedin.com/pulse/x)【LinkedIn article】.")
    # Plain ASCII brackets are deliberately NOT flagged (narrower scope than a general "any
    # bracket" rule -- no live evidence yet of that shape causing this failure).
    assert not find_non_url_citations("See the appendix [1] for details.")

    # --- search-health counter (persists into _run_state.json via RunState) ---
    from utils.run_state import record_search_health, get_search_health

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

    # --- excluded_topic_semantic_hit: cross-lingual/paraphrase backstop for check_excluded_topic
    # (2026-08-29 audit finding: the substring-only exclusion check misses a heading covering an
    # excluded topic via translation or same-language paraphrase; this project's own benchmark
    # instructs bilingual search, so this isn't hypothetical) ---
    from utils.grounding import excluded_topic_semantic_hit
    fintech_excluded = {"fintech"}
    assert excluded_topic_semantic_hit(fintech_excluded, "digital payment solutions for smes") == "fintech", (
        "an English paraphrase of an excluded topic must still be caught")
    assert excluded_topic_semantic_hit(fintech_excluded, "soluciones de pago digital para pymes") == "fintech", (
        "a Spanish translation of an excluded topic must still be caught")
    assert excluded_topic_semantic_hit(fintech_excluded, "environmental reporting requirements") is None, (
        "a genuinely unrelated heading must not be flagged"
    )
    assert excluded_topic_semantic_hit(fintech_excluded, "competitor landscape") is None, (
        "a generic, topic-agnostic heading must not be flagged"
    )
    assert excluded_topic_semantic_hit(set(), "digital payment solutions for smes") is None, (
        "no excluded topics at all -- nothing to check"
    )
    assert excluded_topic_semantic_hit(fintech_excluded, "") is None, "empty heading -- nothing to check"

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

    # --- read_workspace_file exact-repeat quota dedup (2026-08-17 live incident, run5
    # investigation): a real FindingsWriter dispatch called read_workspace_file(findings.md, 1,
    # 200) with the IDENTICAL exact arguments 2-3 times in a row -- not re-reading after a change,
    # just re-verifying unchanged state -- and burned its entire quota this way
    # ("Quota reached... used the 'read_workspace_file' tool 41/47 times") before it could finish
    # its actual edit work. Deliberately a tiny opt-in (_DEDUP_ELIGIBLE_TOOLS = {read_workspace_
    # file} only) -- other quota'd tools (web_search, etc.) are untouched. ---
    def _read_dedup_scenario():
        from tools.core import tool_quotas_ctx as q_ctx, check_quota

        q_ctx.set({"read_workspace_file": {"used": 0, "limit": 3}})
        key_a = (("findings.md",), (("end_line", 200), ("start_line", 1)))
        key_b = (("other.md",), (("end_line", 200), ("start_line", 1)))

        assert check_quota("read_workspace_file", key_a) is None
        assert q_ctx.get()["read_workspace_file"]["used"] == 1, "the first call must spend one unit"

        # Exact repeat of the immediately preceding call -- must NOT spend another unit.
        assert check_quota("read_workspace_file", key_a) is None
        assert q_ctx.get()["read_workspace_file"]["used"] == 1, (
            "an exact-repeat call (same filename/start_line/end_line) must be free")
        assert check_quota("read_workspace_file", key_a) is None
        assert q_ctx.get()["read_workspace_file"]["used"] == 1, (
            "repeated dedup hits must stay free, not just the first repeat")

        # A DIFFERENT call must spend its own unit normally -- dedup must never suppress a real,
        # distinct read.
        assert check_quota("read_workspace_file", key_b) is None
        assert q_ctx.get()["read_workspace_file"]["used"] == 2, (
            "a call with different arguments must not be treated as a repeat")

        # Once key_b is the new 'last call', repeating key_a again is a genuinely new call (not
        # consecutive with the earlier key_a run) and must spend its own unit.
        assert check_quota("read_workspace_file", key_a) is None
        assert q_ctx.get()["read_workspace_file"]["used"] == 3, (
            "only CONSECUTIVE identical calls are deduped -- a different call in between breaks "
            "the streak")

        # At/over the limit, dedup must not apply -- the real quota-exhaustion error must still
        # surface normally so the model gets the "you must stop" signal.
        err = check_quota("read_workspace_file", key_a)
        assert err and "Quota reached" in err, (
            "dedup must never mask a genuine quota-exhaustion rejection", err)

        # Other quota'd tools are NOT in _DEDUP_ELIGIBLE_TOOLS -- a call_key passed for one must be
        # a complete no-op, identical behavior to the pre-fix code path.
        q_ctx.set({"web_search": {"used": 0, "limit": 2}})
        assert check_quota("web_search", key_a) is None
        assert q_ctx.get()["web_search"]["used"] == 1
        assert check_quota("web_search", key_a) is None, "web_search has no dedup -- a repeat still spends"
        assert q_ctx.get()["web_search"]["used"] == 2, (
            "an ineligible tool must never dedup, even with an identical call_key")

        # grep_workspace_file (2026-08-17) shares read_workspace_file's idempotent-read shape, so
        # it gets the exact same free-repeat dedup treatment.
        q_ctx.set({"grep_workspace_file": {"used": 0, "limit": 3}})
        assert check_quota("grep_workspace_file", key_a) is None
        assert q_ctx.get()["grep_workspace_file"]["used"] == 1
        assert check_quota("grep_workspace_file", key_a) is None
        assert q_ctx.get()["grep_workspace_file"]["used"] == 1, (
            "grep_workspace_file must dedup an exact-repeat call the same way read_workspace_file does")

    contextvars.copy_context().run(_read_dedup_scenario)

    # --- no-progress guard (2026-08-17, RESEARCH.md §18c): a GENERAL mechanism (every quota'd
    # tool, not an allowlist) -- the same (tool, args) pair failing with the SAME error N times in
    # a row is a no-progress signal regardless of which tool it is. Confirmed live:
    # edit_workspace_file's own "old_string not found"/"appears N times" errors are exactly this
    # shape (a model retrying the identical wrong old_string). ---
    def _no_progress_guard_scenario():
        from tools.core import (
            tool_quotas_ctx as q_ctx, check_quota, _record_call_outcome, TOOL_ERROR_PREFIX,
        )

        q_ctx.set({"edit_workspace_file": {"used": 0, "limit": 10}})
        bad_key = (("findings.md", "wrong old_string", "new"), ())
        good_key = (("findings.md", "correct old_string", "new"), ())

        # 1st failing call: passes through normally, no guard yet.
        assert check_quota("edit_workspace_file", bad_key) is None
        _record_call_outcome("edit_workspace_file", bad_key, f"{TOOL_ERROR_PREFIX}old_string not found")
        # 2nd identical failing call: still passes through (streak reaches 2, the LIMIT, not yet over it).
        assert check_quota("edit_workspace_file", bad_key) is None
        _record_call_outcome("edit_workspace_file", bad_key, f"{TOOL_ERROR_PREFIX}old_string not found")
        # 3rd identical call: the guard fires BEFORE the real function would even run.
        err = check_quota("edit_workspace_file", bad_key)
        assert err and "exact same arguments" in err and "2 times in a row" in err, err
        # The guard-blocked 3rd call must NOT consume a further quota unit itself (only the first
        # two real calls did, 2 total) -- this is a distinct failure from being over quota.
        assert q_ctx.get()["edit_workspace_file"]["used"] == 2, q_ctx.get()

        # A genuinely DIFFERENT call (different args) must never be blocked by another key's streak.
        assert check_quota("edit_workspace_file", good_key) is None

        # A real SUCCESS resets the streak -- the model recovering must not stay guarded forever.
        _record_call_outcome("edit_workspace_file", bad_key, "Edited 'findings.md' (1 replacement).")
        assert check_quota("edit_workspace_file", bad_key) is None, (
            "a successful call with the same key must clear the error streak")

        # An exception-shaped internal failure (not a tool's own returned error string) must ALSO
        # feed the guard -- _record_call_outcome is tool-agnostic, only checks the TOOL_ERROR_PREFIX
        # shape of whatever the wrapped function actually returned.
        q_ctx.set({"read_workspace_file": {"used": 0, "limit": 10}})
        crash_key = (("bad.md",), ())
        _record_call_outcome("read_workspace_file", crash_key, f"{TOOL_ERROR_PREFIX}read_workspace_file failed internally: OSError: boom")
        _record_call_outcome("read_workspace_file", crash_key, f"{TOOL_ERROR_PREFIX}read_workspace_file failed internally: OSError: boom")
        err2 = check_quota("read_workspace_file", crash_key)
        assert err2 and "exact same arguments" in err2, err2

    contextvars.copy_context().run(_no_progress_guard_scenario)

    # --- tool-failure streak guard (2026-08-17, MAST FM-1.3, RESEARCH.md §18f): unlike the
    # no-progress guard above, this fires on N consecutive FAILURES to the same tool regardless of
    # DIFFERING arguments -- confirmed live, PeerReviewer read its real target once then burned 66
    # read/grep calls guessing distinct garbage filenames, never once repeating the same (tool,
    # args) pair and so never tripping the guard above. ---
    def _tool_failure_streak_scenario():
        from tools.core import (
            tool_quotas_ctx as q_ctx, check_quota, _record_call_outcome, TOOL_ERROR_PREFIX,
        )

        q_ctx.set({"read_workspace_file": {"used": 0, "limit": 20}})
        keys = [(("./",), ()), (("*",), ()), (("",), ()), (("ReviewFix_attempt2.md",), ())]

        # 1st real success (the live incident's own first call) must not count toward the streak.
        assert check_quota("read_workspace_file", keys[0]) is None
        _record_call_outcome("read_workspace_file", keys[0], "file content here")

        # 3 DIFFERENT failing calls in a row -- none share a call_key, so the exact-repeat
        # no-progress guard never fires, but the tool-wide streak does.
        for k in keys[1:]:
            assert check_quota("read_workspace_file", k) is None
            _record_call_outcome("read_workspace_file", k, f"{TOOL_ERROR_PREFIX}file not found")

        err = check_quota("read_workspace_file", (("yet_another_guess.md",), ()))
        assert err and "all failed" in err and "different arguments" in err, err

        # A real success anywhere resets the tool-wide streak (not just a matching-key success).
        _record_call_outcome("read_workspace_file", keys[0], "file content here")
        assert check_quota("read_workspace_file", (("one_more_guess.md",), ())) is None, (
            "a success must reset the tool-wide failure streak, regardless of which call_key it used")

    contextvars.copy_context().run(_tool_failure_streak_scenario)

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

    # --- _get_compaction_strategy (2026-07-23): enable_conversational_memory defaults the
    # Planner's AgentSession to accumulate the ENTIRE message history for a run's whole duration
    # with zero compaction -- neither as_agent() call site in orchestrator.py ever passed
    # compaction_strategy despite agent_framework shipping a ready-to-use
    # ContextWindowCompactionStrategy for exactly this. Same 0-is-off convention as
    # get_context_budget(). ---
    def _compaction_strategy_scenario():
        from engine.orchestrator import _get_compaction_strategy
        from agent_framework import ContextWindowCompactionStrategy

        _orig = _config.cfg.get("settings", {}).get("max_context_window_tokens")
        try:
            _config.cfg.setdefault("settings", {})["max_context_window_tokens"] = 0
            assert _get_compaction_strategy() is None, "0 must disable compaction (opt-out)"

            _config.cfg["settings"].pop("max_context_window_tokens", None)
            strat_default = _get_compaction_strategy()
            assert isinstance(strat_default, ContextWindowCompactionStrategy), strat_default
            assert strat_default.max_context_window_tokens == 16384, (
                "default must match this project's current model's real num_ctx", strat_default)

            _config.cfg["settings"]["max_context_window_tokens"] = 32768
            _config.cfg["settings"]["max_output_tokens"] = 8192
            strat_custom = _get_compaction_strategy()
            assert strat_custom.max_context_window_tokens == 32768, strat_custom
            assert strat_custom.max_output_tokens == 8192, strat_custom
        finally:
            if _orig is None:
                _config.cfg["settings"].pop("max_context_window_tokens", None)
            else:
                _config.cfg["settings"]["max_context_window_tokens"] = _orig
            _config.cfg["settings"].pop("max_output_tokens", None)

    _compaction_strategy_scenario()

    # --- _get_default_options / settings.skip_chat_template_kwargs (2026-07-26): vLLM's native
    # Mistral tokenizer mode unconditionally rejects any request containing chat_template_kwargs
    # (confirmed at the vLLM source, confirmed intentional via vLLM PR #26358) -- a real
    # mistral:7b-instruct benchmark run 400'd on its very first request despite passing the
    # isolated tool-call smoke test cleanly. This setting must fully suppress extra_body when set,
    # and leave every other model's behavior (including the reasoning_effort addition from earlier
    # today) completely unchanged when unset/false. ---
    def _default_options_scenario():
        from engine.orchestrator import _get_default_options

        # Isolate from whatever api.backend the LIVE ~/.deepdelve/config.yaml happens to have --
        # this scenario is specifically about the OpenAI-only extra_body shape (api.backend not
        # "ollama"), and must not silently pass/fail based on unrelated live-config state a
        # previous session's manual testing left behind.
        _orig_backend = _config.cfg.get("api", {}).get("backend")
        _config.cfg.setdefault("api", {})["backend"] = "openai"

        _orig = _config.cfg.get("settings", {}).get("skip_chat_template_kwargs")
        try:
            _config.cfg.setdefault("settings", {})["skip_chat_template_kwargs"] = True
            opts = _get_default_options()
            assert "extra_body" not in opts, (
                "skip_chat_template_kwargs=True must omit extra_body entirely (Mistral tokenizer rejects "
                "the request outright if the field is present at all, regardless of value)", opts)

            _config.cfg["settings"]["skip_chat_template_kwargs"] = False
            opts_default = _get_default_options()
            assert "extra_body" in opts_default, (
                "skip_chat_template_kwargs=False must not change existing behavior", opts_default)
            assert "chat_template_kwargs" in opts_default["extra_body"], opts_default

            _config.cfg["settings"].pop("skip_chat_template_kwargs", None)
            opts_unset = _get_default_options()
            assert "extra_body" in opts_unset, (
                "unset (default False) must not change existing behavior", opts_unset)
        finally:
            if _orig is None:
                _config.cfg["settings"].pop("skip_chat_template_kwargs", None)
            else:
                _config.cfg["settings"]["skip_chat_template_kwargs"] = _orig
            if _orig_backend is None:
                _config.cfg["api"].pop("backend", None)
            else:
                _config.cfg["api"]["backend"] = _orig_backend

    _default_options_scenario()

    # --- _compaction_strategy_for_role (2026-07-24): FindingsWriter's whole evidence base is one
    # front-loaded first-turn message -- generic truncation has nothing else to evict once that
    # crosses threshold and deletes it outright (confirmed live: empty findings.md / false "no
    # evidence" claims). Must be excluded from compaction regardless of config; every other role
    # keeps normal compaction. ---
    def _compaction_strategy_for_role_scenario():
        from engine.orchestrator import _compaction_strategy_for_role
        from agent_framework import ContextWindowCompactionStrategy

        assert _compaction_strategy_for_role("FindingsWriter") is None
        assert isinstance(_compaction_strategy_for_role("Builder"), ContextWindowCompactionStrategy)
        assert isinstance(_compaction_strategy_for_role("WebSearcher"), ContextWindowCompactionStrategy)
        assert isinstance(_compaction_strategy_for_role(None), ContextWindowCompactionStrategy)

    _compaction_strategy_for_role_scenario()

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



if __name__ == "__main__":
    main()
    print("test_grounding_gates_and_quotas OK")
