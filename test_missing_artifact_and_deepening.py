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
import asyncio as _asyncio
import config as _config
import contextvars
import tempfile

from engine.completion import (
    Ctx, run_completion_check, check_thin_coverage,
)
from tools.fs import _IN_MEMORY_FS
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
    # --- missing_artifact escalation (live case 2026-07-12: 24 real fetched URLs + a populated
    # findings.md, but the model still got this nudge 5x verbatim and never once attempted
    # write_workspace_file). Two behaviors added: findings.md content quoted directly in the
    # nudge, and wording/attempt-budget escalate once the SAME problem repeats. ---
    def _missing_artifact_escalation_scenario():
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

            # 4th consecutive occurrence (2026-07-31, BEHAVIOR CHANGED by check_thin_coverage's own
            # 3-firing cap, same landmine class as check_task_verification_flagged found the same
            # night -- see that function's docstring): check_thin_coverage now goes quiet at
            # prior_same>=3 instead of firing a 4th time, so it no longer reaches this generalized
            # escalation guard at all for thin_coverage specifically. The pipeline correctly falls
            # through to WHICHEVER check is next in COMPLETION_CHECKS priority order with something
            # real to say -- here, check_task_verification_flagged (this fixture's "found nothing
            # usable" summaries fail the citability check too) -- and gets a normal retry instead of
            # a forced final-verdict/salvage stop. This is the deliberate improvement: real ongoing
            # progress beats a premature hard stop.
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
                assert should_retry, (
                    "thin_coverage's own cap must yield to the next real problem (here, "
                    "task_verification_flagged) for a normal retry, not force a premature final "
                    "verdict while real recovery is still possible", msgs)
        finally:
            _IN_MEMORY_FS.clear()
            _IN_MEMORY_FS.update(saved_fs)
            reset_fetched_urls()
            if _orig_ws11 is None:
                _config.cfg["settings"].pop("workspace", None)
            else:
                _config.cfg["settings"]["workspace"] = _orig_ws11

    contextvars.copy_context().run(_thin_coverage_escalation_scenario)

    # --- check_thin_coverage's own 3-firing cap, tested directly against the function (not just
    # through run_completion_check above) -- same landmine class and same threshold reasoning as
    # check_task_verification_flagged's cap (2026-07-31, see that function's docstring). ---
    def _thin_coverage_cap_scenario():
        rs = RunState(tmpdir_root := tempfile.mkdtemp())
        try:
            rs.add_finding("https://a.example.co/x", "summary", task_name="Background", depth=1)
            rs.add_finding("Comparison A", "found nothing usable", task_name="Comparison A", depth=1)
            rs.add_finding("Comparison B", "found nothing usable", task_name="Comparison B", depth=1)

            # Below the cap (3rd occurrence, prior_same==2): must still fire.
            rs.data["completion_check_attempts"] = [
                {"problem": "thin_coverage"},
                {"problem": "thin_coverage"},
            ]
            ctx = Ctx(req_artifact="final_report.md", attempt=2, max_attempts=8, delegated=True,
                      files=[], content=None, quotas=None, run_state=rs)
            assert check_thin_coverage(ctx) is not None, (
                "the 3rd occurrence itself must still fire (force_whole_rebuild's turn)")

            # At the cap (4th occurrence, prior_same==3): must go quiet.
            rs.data["completion_check_attempts"] = [
                {"problem": "thin_coverage"},
                {"problem": "thin_coverage"},
                {"problem": "thin_coverage"},
            ]
            ctx2 = Ctx(req_artifact="final_report.md", attempt=3, max_attempts=8, delegated=True,
                       files=[], content=None, quotas=None, run_state=rs)
            assert check_thin_coverage(ctx2) is None, (
                "a 4th+ consecutive occurrence must go quiet and yield to missing_findings/"
                "missing_artifact instead of permanently starving them")

            # untracked_delegation interrupting the streak must not reset the count either --
            # same reasoning as check_task_verification_flagged's own cap.
            rs.data["completion_check_attempts"] = [
                {"problem": "thin_coverage"},
                {"problem": "untracked_delegation"},
                {"problem": "thin_coverage"},
                {"problem": "thin_coverage"},
            ]
            ctx3 = Ctx(req_artifact="final_report.md", attempt=4, max_attempts=8, delegated=True,
                       files=[], content=None, quotas=None, run_state=rs)
            assert check_thin_coverage(ctx3) is None, (
                "untracked_delegation interrupting the streak must not reset thin_coverage's own cap")
        finally:
            import shutil
            shutil.rmtree(tmpdir_root, ignore_errors=True)

    contextvars.copy_context().run(_thin_coverage_cap_scenario)

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
        from engine.completion import Ctx
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

    from engine.completion import _BUILDER_FIXABLE_PROBLEMS as _bfp_check
    assert "propagated_ungrounded" in _bfp_check, (
        "2026-08-29 fix: this problem must have a real Builder-dispatch remediation path, not "
        "just a Planner nag with no fix mechanism")

    from engine.completion import GROUNDING_CHECKS as _gc_check, check_editorializing_content as _cec_check
    assert "editorializing" in _bfp_check, (
        "2026-08-29 fix: editorializing must be Builder-fixable, same as its nli_unsupported/"
        "topical_mismatch siblings")
    assert _cec_check in _gc_check, (
        "2026-08-29 fix: check_editorializing_content must be registered in GROUNDING_CHECKS")



if __name__ == "__main__":
    main()
    print("test_missing_artifact_and_deepening OK")
