import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))


# noqa: F401 -- common test-infra names available to every split file below, regardless of
# whether a given file's own retained sections happen to use all of them (ruff prunes genuinely
# unused ones per file). Split 2026-09-07 out of the former single 10,701-line
# test_structural_checks.py (session_status/CURRENT.md carried-forward TODO) -- see
# test_structural_checks.py's own new header for the full split rationale and the file-to-topic
# map. Pure move: every assertion below is byte-identical to its prior body, just regrouped by
# topic into its own main(), all still called in original order from the new thin
# test_structural_checks.py orchestrator.
import asyncio as _asyncio
import config as _config
import contextvars
import tempfile

from tools.fs import _IN_MEMORY_FS
from engine.tui import _clarify_verdict
from utils.run_state import RunState

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

    # --- content-identity escalation (2026-08-17 live incident, "run6" -- 3 byte-identical
    # findings.md.rejected_attempt_N snapshots proved the retry loop was re-offering the SAME
    # content for rejection every time, while the PROBLEM NAME alternated (findings_ungrounded ->
    # untracked_delegation -> findings_ungrounded), so the pre-existing 3-consecutive-same-problem
    # counter never fired even though the run was provably stuck) ---
    from engine.completion import _content_unchanged_since_last_quarantine

    with tempfile.TemporaryDirectory() as tmpdir_ci:
        def _content_identity_scenario():
            _orig_ws_ci = _config.cfg.get("settings", {}).get("workspace")
            _config.cfg["settings"]["workspace"] = {"type": "disk", "dir": tmpdir_ci}
            try:
                # No prior quarantined snapshot at all -> never "unchanged" (nothing to compare).
                assert not _content_unchanged_since_last_quarantine("findings.md", "some content")
                assert not _content_unchanged_since_last_quarantine("findings.md", None)

                path = os.path.join(tmpdir_ci, "findings.md.rejected_attempt_1")
                with open(path, "w", encoding="utf-8") as f:
                    f.write("### Source: x\nreal content")

                # Byte-identical to the most recent rejected snapshot -> stuck.
                assert _content_unchanged_since_last_quarantine("findings.md", "### Source: x\nreal content")
                # Genuinely different content -> not stuck.
                assert not _content_unchanged_since_last_quarantine("findings.md", "### Source: y\ndifferent content")

                # A LATER snapshot must win over an earlier one with different content -- the most
                # RECENT rejection is what matters, not the first.
                path2 = os.path.join(tmpdir_ci, "findings.md.rejected_attempt_2")
                with open(path2, "w", encoding="utf-8") as f:
                    f.write("### Source: z\nnewer rejected content")
                assert _content_unchanged_since_last_quarantine("findings.md", "### Source: z\nnewer rejected content")
                assert not _content_unchanged_since_last_quarantine("findings.md", "### Source: x\nreal content")
            finally:
                if _orig_ws_ci is None:
                    _config.cfg["settings"].pop("workspace", None)
                else:
                    _config.cfg["settings"]["workspace"] = _orig_ws_ci

        contextvars.copy_context().run(_content_identity_scenario)

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

    # --- pass@k / pass^k reliability summary (2026-08-17, RESEARCH.md §18d): a single pass rate
    # conflates "can the agent solve this at all" (pass@k) with "does it solve this every time"
    # (pass^k) -- this project's own completion-check fixes were all validated with n=1 live-run
    # anecdotes until this was added. ---
    with tempfile.TemporaryDirectory() as tmpdir_rel:
        results_path = os.path.join(tmpdir_rel, "results.jsonl")
        rows = [
            {"query": "q1", "score": 1.0, "run_index": 1, "config": {"model": "m1", "hardware": "h1"}},
            {"query": "q1", "score": 0.75, "run_index": 2, "config": {"model": "m1", "hardware": "h1"}},
            {"query": "q1", "score": 1.0, "run_index": 3, "config": {"model": "m1", "hardware": "h1"}},
            {"query": "q2", "score": 1.0, "run_index": 1, "config": {"model": "m1", "hardware": "h1"}},
            {"query": "q2", "score": 1.0, "run_index": 2, "config": {"model": "m1", "hardware": "h1"}},
        ]
        with open(results_path, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(_json.dumps(r) + "\n")
        summary = _ev.compute_reliability_summary(results_path, threshold=1.0)
        q1 = next(r for r in summary if r["query"] == "q1")
        q2 = next(r for r in summary if r["query"] == "q2")
        # q1: 3 runs, one imperfect (0.75) -- solved at least once, but not every time.
        assert q1["k"] == 3 and q1["pass_at_k"] is True and q1["pass_pow_k"] is False, q1
        assert q1["mean_score"] == round((1.0 + 0.75 + 1.0) / 3, 3), q1
        # q2: 2 runs, both perfect -- both metrics pass.
        assert q2["k"] == 2 and q2["pass_at_k"] is True and q2["pass_pow_k"] is True, q2
        # No results file at all -> empty summary, not an error.
        assert _ev.compute_reliability_summary(os.path.join(tmpdir_rel, "missing.jsonl")) == []
        # print_reliability_summary must not raise on an empty summary or a real one.
        _ev.print_reliability_summary([], 1.0)
        _ev.print_reliability_summary(summary, 1.0)

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
    from tools.fs import resolve_fuzzy_filename, read_workspace_file, grep_workspace_file

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
    assert _looks_like_tool_error("## Error for Analyze paper\nTask forcefully aborted: timeout\n---")
    assert not _looks_like_tool_error("Wrote 'final_report.md' to disk.")
    assert not _looks_like_tool_error("## Result for background\n**Findings**\n\n- real content")
    assert not _looks_like_tool_error("")
    assert not _looks_like_tool_error(None)

    # --- Shared tool-error sentinel, 2026-07-29: previously each tool file invented its own
    # crash-path prefix ("CRITICAL TOOL EXECUTION ERROR", "Grep Error:", "Failed:",
    # "Search failed:"), and _looks_like_tool_error had to hand-list all of them (found missing
    # three of them in a 2026-07-19 QA audit). Converged onto tools.core.TOOL_ERROR_PREFIX --
    # pin that every one of these tools' actual crash paths now emits it, not a private prefix. ---
    from tools.core import TOOL_ERROR_PREFIX
    assert _looks_like_tool_error(f"{TOOL_ERROR_PREFIX}web_search failed internally.\n\nException Details:\n...")
    assert _looks_like_tool_error(f"{TOOL_ERROR_PREFIX}Grep failed: boom\n\nTraceback:\n...")
    assert _looks_like_tool_error(f"{TOOL_ERROR_PREFIX}Fetch failed: boom\n\nTraceback:\n...")
    assert _looks_like_tool_error(f"{TOOL_ERROR_PREFIX}Search failed: boom\n\nTraceback:\n...")
    assert _looks_like_tool_error(f"{TOOL_ERROR_PREFIX}Search timed out after 20s with no response.")
    import inspect as _inspect_tool_err
    import tools.core as _core_mod_check
    import tools.fs as _fs_mod_check
    import tools.web as _web_mod_check
    for _mod in (_core_mod_check, _fs_mod_check, _web_mod_check):
        _src = _inspect_tool_err.getsource(_mod)
        assert "CRITICAL TOOL EXECUTION ERROR" not in _src, (
            f"{_mod.__name__}: old crash-path prefix must not be reintroduced, use TOOL_ERROR_PREFIX")
        assert '"Grep Error:' not in _src, f"{_mod.__name__}: use TOOL_ERROR_PREFIX, not a private prefix"

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



if __name__ == "__main__":
    main()
    print("test_salvage_and_infra OK")
