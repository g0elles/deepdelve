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
import config as _config
import contextvars
import inspect as _inspect
import json
import tempfile

from engine.completion import (
    Ctx, check_not_delegated,
)
from tools.fs import session_dir_ctx, _IN_MEMORY_FS
from engine.tui import load_resume_state, build_resume_input
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
    # --- run-resume helpers (--resume-run: reattach to an interrupted run instead of restarting) ---

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
                # Stage-aware directive (2026-07-24 loop fix): findings.md already exists in the
                # workspace, so the resumed Planner must be told the prior run already finished
                # its research phase, not just handed "delegate for the gaps" with no context.
                assert "ALREADY finished its research phase" in text
                assert "plus a draft final report" not in text  # final_report.md doesn't exist yet

            contextvars.copy_context().run(_resume_scenario)
        finally:
            if _orig_ws2 is None:
                _config.cfg["settings"].pop("workspace", None)
            else:
                _config.cfg["settings"]["workspace"] = _orig_ws2

    # --- root-cause fix (2026-08-01, RESEARCH.md Sec.17e): resuming a run whose final_report.md
    # already exists must structurally disable further delegate_tasks, not just discourage it in
    # prose -- live-confirmed a resumed Planner ignored the old prose-only wording, redelegated
    # anyway, and the new tasks it created starved the fix that already existed for the real
    # problem (report_underuses_evidence never got a turn across 24 minutes and 8 attempts). ---
    from utils.run_state import merge_resumed_state, RunState as _RunStateResumeTest

    with tempfile.TemporaryDirectory() as tmpdir3:
        # (a) final_report.md exists -> planner_delegate_rounds pre-set to the cap, flag set.
        with open(os.path.join(tmpdir3, "final_report.md"), "w", encoding="utf-8") as f:
            f.write("# Report\nSome content.")
        rs_a = _RunStateResumeTest(tmpdir3)
        _orig_cap3 = _config.cfg.get("settings", {}).get("max_planner_delegate_rounds")
        _config.cfg["settings"]["max_planner_delegate_rounds"] = 4
        try:
            merge_resumed_state(rs_a, {"query": "q"})
            assert rs_a.data.get("resumed_with_existing_report") is True, rs_a.data
            assert rs_a.data.get("planner_delegate_rounds") == 4, (
                "planner_delegate_rounds must be pre-set to the configured cap so the very "
                "FIRST delegate_tasks call this run is already rejected", rs_a.data)
            from engine.orchestrator import _planner_delegate_over_cap as _pdoc_test
            assert _pdoc_test(rs_a.data["planner_delegate_rounds"], 4), (
                "the pre-set value must actually trip the existing cap predicate")
        finally:
            if _orig_cap3 is None:
                _config.cfg["settings"].pop("max_planner_delegate_rounds", None)
            else:
                _config.cfg["settings"]["max_planner_delegate_rounds"] = _orig_cap3

    with tempfile.TemporaryDirectory() as tmpdir4:
        # (b) no final_report.md (e.g. resumed before the report was ever written) -> untouched,
        # delegation stays available -- this is the "findings.md only" case that legitimately
        # still needs research, must not be blocked.
        rs_b = _RunStateResumeTest(tmpdir4)
        merge_resumed_state(rs_b, {"query": "q"})
        assert not rs_b.data.get("resumed_with_existing_report"), rs_b.data
        assert rs_b.data.get("planner_delegate_rounds", 0) == 0, (
            "no final_report.md on disk -- delegation must remain fully available, same as any "
            "other resume", rs_b.data)

    # build_resume_input's own stage_note wording for the final_report.md-exists case.
    with tempfile.TemporaryDirectory() as tmpdir5:
        with open(os.path.join(tmpdir5, "final_report.md"), "w", encoding="utf-8") as f:
            f.write("# Report\nSome content.")
        with open(os.path.join(tmpdir5, "findings.md"), "w", encoding="utf-8") as f:
            f.write("## Findings\n- x (https://real.example.com/a)")
        _orig_ws3 = _config.cfg.get("settings", {}).get("workspace")
        _config.cfg["settings"]["workspace"] = {"type": "disk", "dir": os.path.dirname(tmpdir5), "session_isolation": True}
        try:
            def _resume_report_exists_scenario():
                session_dir_ctx.set(os.path.basename(tmpdir5))
                text = build_resume_input("q", {"query": "q"})
                assert "DISABLED for this run" in text, text
                assert "any delegate_tasks call will be rejected" in text, text
                # Must NOT contain the old, now-misleading "only delegate for a genuinely missing
                # fact" framing -- the tool doesn't offer that choice anymore in this state.
                assert "genuinely still missing" not in text, text
            contextvars.copy_context().run(_resume_report_exists_scenario)
        finally:
            if _orig_ws3 is None:
                _config.cfg["settings"].pop("workspace", None)
            else:
                _config.cfg["settings"]["workspace"] = _orig_ws3

    # --- _scale_resume_quota_pool (2026-07-24 loop fix): a resumed run previously got a FULL
    # fresh research-volume quota on top of whatever the interrupted run already spent -- confirmed
    # live to let a resumed Planner re-delegate the same angle 40+ times without ever running low
    # enough on budget to be forced toward finishing. ---
    from engine.tui import _scale_resume_quota_pool
    _pool = {
        "delegate_tasks": {"used": 0, "limit": 15},
        "web_search": {"used": 0, "limit": 15},
        "fetch_url_to_workspace": {"used": 0, "limit": 15},
        "think_tool": {"used": 0, "limit": 30},  # untouched -- not a research-volume quota
    }
    _scale_resume_quota_pool(_pool)
    assert _pool["delegate_tasks"]["limit"] == 7, _pool   # int(15 * 0.5) == 7
    assert _pool["web_search"]["limit"] == 7, _pool
    assert _pool["fetch_url_to_workspace"]["limit"] == 7, _pool
    assert _pool["think_tool"]["limit"] == 30, _pool       # untouched
    # Floor: a small `quick`-depth quota must not get scaled down to near-zero.
    _small_pool = {"delegate_tasks": {"used": 0, "limit": 4}}
    _scale_resume_quota_pool(_small_pool)
    assert _small_pool["delegate_tasks"]["limit"] == 3, _small_pool  # floor, not int(4*0.5)==2
    # Missing key (quota disabled for it) must be skipped silently, not raise.
    _scale_resume_quota_pool({"web_search": {"used": 0, "limit": 10}})

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
                    "findings_written_citable_count": 5,
                    "task_verification": {"some_task": {"status": "flagged", "reason": "x", "checked_at": 1.0}},
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
                        # check_stale_findings's write-time marker MUST survive resume -- confirmed
                        # live 2026-07-24 that omitting it from this carryover list silently resets
                        # it to None on every resume, permanently disabling that check for any
                        # resumed run (headless --resume-run had the exact same bug, fixed in
                        # run_cli's own copy of this key list).
                        "conv_run_state_marker": app._conv_run_state.data.get("findings_written_citable_count") if app._conv_run_state else None,
                        # task_verification (2026-07-26, VERIMAP-inspired ledger) must survive
                        # resume the same way -- same "new run_state.data key, same carryover
                        # allowlist trap" blast radius ARCHITECTURE.md warns about.
                        "conv_run_state_task_verification": app._conv_run_state.data.get("task_verification") if app._conv_run_state else None,
                    })
                app.run_agent = _fake_run_agent

                async with app.run_test():
                    app._show_run_picker()
                    assert app._run_picker_active
                    # 2026-08-24 TUI QoE: the run picker moved from the shared filtered-OptionList
                    # mechanism (_filtered_cmds) to a real DataTable with row selection -- see
                    # _show_run_picker's own docstring for why. Row key is the run's folder name.
                    from engine.tui import DataTable
                    table = app.query_one("#run-picker-table", DataTable)
                    assert table.row_count == 1
                    assert table.get_row_at(0)[0] == "my_interrupted_run", table.get_row_at(0)

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
                    assert call["conv_run_state_marker"] == 5, call
                    assert call["conv_run_state_task_verification"] == {
                        "some_task": {"status": "flagged", "reason": "x", "checked_at": 1.0}
                    }, call
                    # Reset back to False once run_agent returns (the `finally` in _resume_run).
                    assert app._resuming_run is False
            finally:
                if _orig_ws9 is None:
                    _config.cfg["settings"].pop("workspace", None)
                else:
                    _config.cfg["settings"]["workspace"] = _orig_ws9

    from engine.tui import BasicTuiAgent
    _asyncio_tui.run(_resume_run_tui_scenario())

    # The resume-carryover key list used to be a byte-identical copy-pasted tuple in BOTH
    # engine.tui's _resume_run AND run_cli independently (confirmed live 2026-07-24: both copies
    # had the same missing-key bug independently, both needed the fix by hand). Extracted
    # 2026-07-29 into utils.run_state.merge_resumed_state / _RESUME_CARRYOVER_KEYS, one source of
    # truth both call sites now use — so this now pins that (a) the shared allowlist itself still
    # includes the markers, and (b) run_cli actually calls the shared function rather than having
    # reintroduced its own private inline copy.
    import engine.tui as _tui_mod_check
    from utils.run_state import _RESUME_CARRYOVER_KEYS as _resume_keys
    assert "findings_written_citable_count" in _resume_keys, (
        "shared resume-carryover allowlist must include findings_written_citable_count")
    assert "task_verification" in _resume_keys, (
        "shared resume-carryover allowlist must include task_verification")
    _run_cli_src = _inspect.getsource(_tui_mod_check.run_cli)
    assert "merge_resumed_state(" in _run_cli_src, (
        "run_cli must call the shared merge_resumed_state, not reintroduce its own inline copy "
        "of the resume-carryover key list")

    # --- TUI/CLI parity fixes, 2026-07-29, RETARGETED 2026-09-11 (Phase 3 of the run_cli/
    # run_agent/_run_research lifecycle-loop unification): run_agent previously had NO
    # QuotaAbortException handling at all (run_cli explicitly catches and cleanly stops on it)
    # and NO crash-time run_state.save() outside normal loop completion (run_cli guarantees one
    # on any top-level crash, 2026-07-11). Both read as unintentional gaps, not deliberate
    # TUI/CLI divergences (unlike run_agent's separate, deliberate choice to never re-raise an
    # unrecognized exception -- see RunLoopSurface.on_malformed_give_up's docstring in
    # engine/run_loop.py and test_run_loop.py's
    # scenario_on_malformed_give_up_suppresses_reraise for that one).
    # As of Phase 3, run_agent's own turn loop was replaced by a call into the shared
    # engine.run_loop.run_agent_loop -- the QuotaAbortException dispatch and the /stop-preserving
    # asyncio.CancelledError check now live THERE, not textually inside run_agent itself (which
    # still only contains a comment mentioning QuotaAbortException, not the real handling -- a
    # source-string check against run_agent's own body would now pass coincidentally on that
    # comment instead of actually verifying the behavior). Retargeted accordingly. ---
    import engine.run_loop as _run_loop_mod_check
    _run_agent_loop_src = _inspect.getsource(_run_loop_mod_check.run_agent_loop)
    assert "QuotaAbortException" in _run_agent_loop_src, (
        "the shared run_agent_loop must handle QuotaAbortException (used by run_agent, run_cli, "
        "and _run_research alike), not let it propagate uncaught or fall into the generic "
        "malformed-retry path")
    assert "asyncio.CancelledError" in _run_agent_loop_src, (
        "the shared run_agent_loop's except-BaseException clause (needed to catch "
        "QuotaAbortException, a BaseException subclass) must not accidentally swallow /stop's "
        "or /cancel's asyncio.CancelledError")
    assert "run_state.save()" in _run_agent_loop_src, (
        "run_agent_loop must save run_state on a generic unrecognized exception before "
        "propagating (the gap-E fix) -- this is what makes run_agent's own crash-time save "
        "(checked below) actually redundant-but-safe rather than the only save that ever fires")
    # run_agent's OWN source still guarantees its two outer-level saves regardless of what the
    # shared loop does internally: one on normal loop completion, one on a top-level crash
    # (anything escaping run_agent_loop entirely, e.g. from create_local_agent or seed ingestion,
    # not just from inside the turn loop) -- this is a real, still-independently-meaningful pin,
    # not superseded by the run_agent_loop-level check above.
    _run_agent_src = _inspect.getsource(_tui_mod_check.BasicTuiAgent.run_agent)
    assert "run_agent_loop(" in _run_agent_src, (
        "run_agent must call the shared run_agent_loop, not reintroduce its own inline "
        "while-has-requests copy")
    _real_save_calls = [
        line for line in _run_agent_src.splitlines()
        if line.strip() == "run_state.save()"
    ]
    assert len(_real_save_calls) >= 2, (
        "run_agent must save run_state both at normal loop completion AND on a top-level crash "
        "(run_cli parity) — expected at least 2 real call sites, found "
        f"{len(_real_save_calls)}")

    # --- Circular import fix, 2026-07-29: engine.completion used to lazy-import
    # _find_last_substantial_text FROM engine.tui at call time, specifically to avoid a real
    # circular import (engine.tui imports engine.completion; engine.completion importing back
    # from a partially-initialized engine.tui at ITS top level would crash on load). Fixed via
    # callback injection (run_completion_check's find_substantial_text parameter) instead of a
    # shared-module extraction, since the underlying data (_session_events) is tui-specific
    # bookkeeping completion.py has no independent reason to know about. Pin both halves: the
    # import is gone, AND the actual module import order doesn't crash.
    import engine.completion as _completion_mod_check
    _completion_src = _inspect.getsource(_completion_mod_check)
    assert "from engine.tui import" not in _completion_src, (
        "engine.completion must not import from engine.tui at all — that was the circular-import "
        "workaround this fix removed; a new import here would reintroduce the cycle")
    assert "find_substantial_text" in _inspect.signature(_completion_mod_check.run_completion_check).parameters, (
        "run_completion_check must accept find_substantial_text as an injected callback")

    # --- check_not_delegated resume fix (2026-07-28): Ctx.delegated must also be true when the
    # live quota pool shows zero usage (always true at the start of a resumed process) but
    # run_state.data["fetched_urls"] is non-empty (real delegation happened in ANY session,
    # this one or a resumed prior one) -- otherwise a resumed Planner correctly told not to
    # re-delegate gets check_not_delegated's "your ONLY next tool call must be delegate_tasks"
    # directive anyway, a live-confirmed contradiction that derailed a resumed Ornith-1.0-9B run
    # into a think_tool reflection loop until it hit quota and was force-aborted. Ctx.delegated
    # construction moved from run_completion_check's own inline body to the extracted
    # _detect_verdict helper (2026-08-24, group E) -- this pins the OR-condition at the source
    # level of its new home, same "not a separately-callable unit" precedent as the
    # resume-carryover tuple assertions just above (still true for the OTHER inline logic that
    # stayed in run_completion_check, e.g. _BUILDER_NO_DELEGATE_CLARIFICATION below). ---
    from engine.completion import _detect_verdict
    _detect_verdict_src = _inspect.getsource(_detect_verdict)
    assert 'run_state.data.get("fetched_urls")' in _detect_verdict_src, (
        "_detect_verdict's Ctx.delegated construction must also check "
        "run_state.data['fetched_urls'] (resume-safe), not just the live quota pool")
    _run_completion_check_src = _inspect.getsource(_tui_mod_check.run_completion_check)

    # check_not_delegated itself must NOT fire once ctx.delegated is True, regardless of why --
    # confirms the consumer side still behaves correctly given the corrected Ctx construction.
    def _not_delegated_resume_scenario():
        with tempfile.TemporaryDirectory() as tmpdir6:
            rs6 = RunState(tmpdir6)
            rs6.set_query("q")
            resumed_ctx = Ctx(req_artifact="final_report.md", attempt=0, max_attempts=8,
                               delegated=True,  # what the fixed construction produces on resume
                               files=[], content=None, quotas={"delegate_tasks": {"used": 0, "limit": 6}},
                               run_state=rs6)
            assert check_not_delegated(resumed_ctx) is None, (
                "check_not_delegated must not fire when ctx.delegated is True, even with a "
                "fresh (zero-usage) quota pool")
    _not_delegated_resume_scenario()

    # --- Builder no-delegate clarification (2026-07-28 live bug): several _BUILDER_FIXABLE_PROBLEMS
    # checks' verdict.inject text tells the reader to "delegate a Searcher" / "your ONLY next tool
    # call must be delegate_tasks" (via the shared _redelegate_directive helper) -- worded for the
    # Planner, which has delegate_tasks. Builder does NOT (read_workspace_file/grep_workspace_file/
    # write_workspace_file/think_tool only), so embedding that text verbatim hands Builder a
    # genuinely impossible instruction. Live-confirmed: a Builder correction cycle got stuck
    # narrating "I will delegate a Searcher..." across multiple retries instead of ever rewriting
    # the file, because that's literally what its (wrong-audience) instructions told it to do.
    # Pins that both Builder-dispatch branches (classic + force_whole_rebuild) now append the
    # shared clarification after verdict.inject -- source-inspection, same "run_cli isn't easily
    # unit-testable in isolation" precedent as the resume-carryover assertions above, since the
    # instructions are built inline inside run_completion_check, not a separately-callable unit.
    from engine.completion import _BUILDER_NO_DELEGATE_CLARIFICATION
    assert "delegate_tasks tool" in _BUILDER_NO_DELEGATE_CLARIFICATION, (
        "_BUILDER_NO_DELEGATE_CLARIFICATION must explicitly tell Builder it cannot delegate")
    assert _run_completion_check_src.count("_BUILDER_NO_DELEGATE_CLARIFICATION") == 2, (
        "both Builder-dispatch branches (classic and force_whole_rebuild) in run_completion_check "
        "must append _BUILDER_NO_DELEGATE_CLARIFICATION after verdict.inject")

    # --- TUI QoE: widget maximize (2026-07-24) — ROADMAP.md flagged this as "likely already
    # works via Textual's default focus/ALLOW_MAXIMIZE mechanism (same category as the already-
    # confirmed Ctrl+C copy), needs live confirmation, not new code." Confirmed directly against
    # this project's own ToolCallWidget (a Collapsible wrapping two focusable RichLogs) rather
    # than trusting the framework docs alone: RichLog's can_focus=True makes it maximizable by
    # default (Widget.allow_maximize falls back to can_focus when ALLOW_MAXIMIZE is unset), and
    # Screen.action_maximize() maximizes whatever's currently focused — reachable via the command
    # palette (Ctrl+P → "Maximize"), which this app never disables (no ENABLE_COMMAND_PALETTE
    # override anywhere in this file). No code change needed; this pins that it keeps working. ---
    from engine.tui import ToolCallWidget
    from textual.containers import VerticalScroll

    # Shared fake builder for this TUI QoE section's scenarios (widget-maximize, theming,
    # autocomplete, run-picker DataTable, file-picker Tree) — hoisted out of
    # _widget_maximize_scenario (2026-08-24) once sibling scenarios needed it too.
    class _FakeBuilder3:
        name = "Planner"
        instructions = "test"
        tools = []
        sub_agents = []

    async def _widget_maximize_scenario():
        app = BasicTuiAgent(_FakeBuilder3())
        async with app.run_test() as pilot:
            widget = ToolCallWidget("web_search", "call_1")
            await app.query_one("#chat-container", VerticalScroll).mount(widget)
            await pilot.pause()
            widget.result_log.focus()
            await pilot.pause()
            assert app.screen.focused is widget.result_log, app.screen.focused
            assert widget.result_log.allow_maximize, "RichLog must be maximizable by default"
            app.screen.action_maximize()
            await pilot.pause()
            # Collapsible explicitly sets ALLOW_MAXIMIZE=True, and Screen.maximize()'s default
            # container=True walks UP to the outermost maximizable ancestor -- so maximizing a
            # focused RichLog inside a ToolCallWidget (a Collapsible) maximizes the whole card
            # (both Arguments and Result panels), not just the one focused log. That's the
            # correct, more useful behavior, not a bug in this project's widget structure.
            assert app.screen.maximized is widget, app.screen.maximized
            app.screen.action_minimize()
            await pilot.pause()
            assert app.screen.maximized is None, app.screen.maximized

    _asyncio_tui.run(_widget_maximize_scenario())

    # --- TUI QoE: theming (2026-08-24) — converted BasicTuiAgent.CSS's hardcoded hex colors to
    # Textual's theme CSS variables ($panel/$primary/etc). Confirms an actual rendered color
    # genuinely changes when the theme switches (a background, not $text -- text stays similar
    # across dark themes by design, that's correct, not a bug in this pin). ---
    async def _theming_scenario():
        app = BasicTuiAgent(_FakeBuilder3())
        async with app.run_test() as pilot:
            await pilot.pause()
            from textual.widgets import Static as _Static
            bubble = _Static("x", classes="user-bubble")
            await app.query_one("#chat-container", VerticalScroll).mount(bubble)
            await pilot.pause()
            before_bg = bubble.styles.background
            app.theme = "gruvbox"
            await pilot.pause()
            after_bg = bubble.styles.background
            assert before_bg != after_bg, (
                "switching themes must actually change .user-bubble's background — CSS still "
                "hardcoded to a literal hex value instead of a theme variable")

    _asyncio_tui.run(_theming_scenario())

    # --- TUI QoE: inline autocomplete (2026-08-24) — PromptInput now wires a SuggestFromList
    # suggester over SLASH_COMMANDS, complementing (not replacing) the existing filtered-
    # OptionList popup in on_input_changed. ---
    async def _autocomplete_scenario():
        from engine.tui import PromptInput
        app = BasicTuiAgent(_FakeBuilder3())
        async with app.run_test() as pilot:
            await pilot.pause()
            prompt = app.query_one("#prompt-input", PromptInput)
            assert prompt.suggester is not None, "PromptInput must have a suggester wired"
            prompt.focus()
            await pilot.pause()
            await pilot.press("/", "s", "t", "o")
            await pilot.pause()
            assert prompt._suggestion == "/stop", (
                f"expected inline suggestion '/stop' for '/sto', got {prompt._suggestion!r}")

    _asyncio_tui.run(_autocomplete_scenario())

    # --- TUI QoE: run picker -> real DataTable with row selection (2026-08-24). Replaced the
    # shared filtered-OptionList mechanism (pure display, selected only by typing the exact
    # folder name) — see _show_run_picker's own docstring for why this picker specifically
    # benefits. Confirms rows populate correctly AND that pressing Enter on a focused row
    # actually calls _open_selected_run with the right folder name (not just that the widget
    # renders). ---
    async def _run_picker_datatable_scenario():
        with tempfile.TemporaryDirectory() as tmpdir_rp:
            run_dir = os.path.join(tmpdir_rp, "a_test_run_20260101_120000")
            os.makedirs(run_dir)
            with open(os.path.join(run_dir, "_run_state.json"), "w") as f:
                json.dump({"query": "test query"}, f)

            _orig_ws_rp = _config.cfg.get("settings", {}).get("workspace")
            _config.cfg["settings"]["workspace"] = {"type": "disk", "dir": tmpdir_rp}
            try:
                app = BasicTuiAgent(_FakeBuilder3())
                resumed = {}

                async def _fake_resume_run(folder):
                    resumed["folder"] = folder
                app._resume_run = _fake_resume_run

                async with app.run_test() as pilot:
                    await pilot.pause()
                    app._show_run_picker()
                    await pilot.pause()
                    from engine.tui import DataTable as _DataTable
                    table = app.query_one("#run-picker-table", _DataTable)
                    assert table.row_count == 1, table.row_count
                    assert table.has_focus, "run-picker table must be focused for arrow+Enter to work"
                    assert table.get_row_at(0)[0] == "a_test_run_20260101_120000"
                    await pilot.press("enter")
                    await pilot.pause()
                    assert resumed.get("folder") == "a_test_run_20260101_120000", resumed
                    assert not app.query("#run-picker-table"), (
                        "table must be removed from the DOM after a row is selected")
            finally:
                if _orig_ws_rp is None:
                    _config.cfg["settings"].pop("workspace", None)
                else:
                    _config.cfg["settings"]["workspace"] = _orig_ws_rp

    _asyncio_tui.run(_run_picker_datatable_scenario())

    # --- TUI QoE: file picker -> real Tree with row selection (2026-08-24), same reasoning as
    # the run-picker DataTable above. Workspace files nest one level deep in practice (a
    # sources/ subdirectory) — confirms a nested file groups under its own folder branch AND
    # that selecting a leaf node actually calls _open_selected_file. ---
    async def _file_picker_tree_scenario():
        saved_fs = dict(_IN_MEMORY_FS)
        _orig_ws_fp = _config.cfg.get("settings", {}).get("workspace")
        _config.cfg["settings"]["workspace"] = {"type": "memory"}
        _IN_MEMORY_FS.clear()
        _IN_MEMORY_FS["sources/a.md"] = "hello world content"
        _IN_MEMORY_FS["plan.md"] = "top level file"
        try:
            app = BasicTuiAgent(_FakeBuilder3())
            displayed = {}
            app._display_file = lambda filename, collapsed_by_default=False: displayed.update(filename=filename)

            async with app.run_test() as pilot:
                await pilot.pause()
                app._show_file_picker()
                await pilot.pause()
                from engine.tui import Tree as _Tree
                tree = app.query_one("#file-picker-tree", _Tree)
                assert tree.has_focus, "file-picker tree must be focused"
                root_labels = sorted(str(c.label) for c in tree.root.children)
                assert any("plan.md" in lbl for lbl in root_labels), root_labels
                assert any(lbl == "sources" for lbl in root_labels), (
                    f"nested file must group under a 'sources' branch node, got {root_labels}")
                sources_folder = next(c for c in tree.root.children if str(c.label) == "sources")
                assert sources_folder.children[0].data == "sources/a.md", sources_folder.children[0].data

                plan_node = next(c for c in tree.root.children if c.data == "plan.md")
                tree.select_node(plan_node)
                tree.action_select_cursor()
                await pilot.pause()
                assert displayed.get("filename") == "plan.md", displayed
                assert not app.query("#file-picker-tree"), (
                    "tree must be removed from the DOM after a leaf is selected")
        finally:
            _IN_MEMORY_FS.clear()
            _IN_MEMORY_FS.update(saved_fs)
            if _orig_ws_fp is None:
                _config.cfg["settings"].pop("workspace", None)
            else:
                _config.cfg["settings"]["workspace"] = _orig_ws_fp

    _asyncio_tui.run(_file_picker_tree_scenario())

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



if __name__ == "__main__":
    main()
    print("test_resume_and_tui_qoe OK")
