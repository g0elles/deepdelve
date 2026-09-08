import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from engine.completion import (
    _ablation_disabled,
)
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
    run_completion_check, _dispatch_writer_review_fix,
    _ensure_reader_quota_headroom, _update_task_verification,
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
    # --- controlled-ablation switches (2026-08-17, RESEARCH.md §18f): settings.ablation.disable_*
    # keys, default unset -> current behavior completely unaffected. Direct unit test on the shared
    # lookup helper; the dispatch-level effect of disable_force_whole_rebuild (skipping the whole
    # escalation block above) follows mechanically from _consecutive_occurrences/
    # _content_unchanged_since_last_quarantine never being consulted when this returns True, and is
    # not re-tested at the full dispatch level here to avoid duplicating
    # _force_whole_rebuild_dispatch_scenario's own coverage. ---
    def _ablation_switch_scenario():
        _orig_ablation = _config.cfg.get("settings", {}).get("ablation")
        try:
            _config.cfg.setdefault("settings", {}).pop("ablation", None)
            assert _ablation_disabled("force_whole_rebuild") is False, (
                "unset settings.ablation -> every mechanism stays enabled by default")
            assert _ablation_disabled("no_progress_guard") is False

            _config.cfg["settings"]["ablation"] = {"disable_force_whole_rebuild": True}
            assert _ablation_disabled("force_whole_rebuild") is True
            assert _ablation_disabled("no_progress_guard") is False, (
                "one mechanism's toggle must not leak into another's")
        finally:
            if _orig_ablation is None:
                _config.cfg.get("settings", {}).pop("ablation", None)
            else:
                _config.cfg["settings"]["ablation"] = _orig_ablation

    _ablation_switch_scenario()

    # --- no-progress guard's own ablation switch, same pattern, in tools/core.py ---
    def _no_progress_guard_ablation_scenario():
        from tools.core import tool_quotas_ctx as q_ctx, check_quota, _record_call_outcome, TOOL_ERROR_PREFIX

        _orig_ablation2 = _config.cfg.get("settings", {}).get("ablation")
        try:
            # Also disables the tool-failure streak guard (2026-08-17) -- this scenario repeats the
            # SAME failing call 4 times, which would otherwise trip that orthogonal, tool-wide
            # guard regardless of no_progress_guard's own state; isolating the mechanism under test
            # here, not testing the two guards' interaction (see _tool_failure_streak_scenario for
            # that guard's own dedicated coverage).
            _config.cfg.setdefault("settings", {})["ablation"] = {
                "disable_no_progress_guard": True, "disable_tool_failure_streak_guard": True,
            }
            q_ctx.set({"edit_workspace_file": {"used": 0, "limit": 10}})
            bad_key = (("findings.md", "wrong old_string", "new"), ())
            for _ in range(3):
                assert check_quota("edit_workspace_file", bad_key) is None, (
                    "with the guard disabled, an identical repeated failing call must pass through "
                    "normally, never intercepted")
                _record_call_outcome("edit_workspace_file", bad_key, f"{TOOL_ERROR_PREFIX}old_string not found")
            # A 4th identical failing call still passes through -- disabled means disabled, not
            # just a higher threshold.
            assert check_quota("edit_workspace_file", bad_key) is None
        finally:
            if _orig_ablation2 is None:
                _config.cfg.get("settings", {}).pop("ablation", None)
            else:
                _config.cfg["settings"]["ablation"] = _orig_ablation2

    contextvars.copy_context().run(_no_progress_guard_ablation_scenario)

    # --- tool-failure streak guard's own ablation switch, same pattern ---
    def _tool_failure_streak_ablation_scenario():
        from tools.core import tool_quotas_ctx as q_ctx, check_quota, _record_call_outcome, TOOL_ERROR_PREFIX

        _orig_ablation3 = _config.cfg.get("settings", {}).get("ablation")
        try:
            _config.cfg.setdefault("settings", {})["ablation"] = {"disable_tool_failure_streak_guard": True}
            q_ctx.set({"read_workspace_file": {"used": 0, "limit": 20}})
            for i in range(4):
                key = ((f"guess_{i}.md",), ())
                assert check_quota("read_workspace_file", key) is None, (
                    "with the guard disabled, a run of different-args failures must pass through "
                    "normally, never intercepted")
                _record_call_outcome("read_workspace_file", key, f"{TOOL_ERROR_PREFIX}file not found")
        finally:
            if _orig_ablation3 is None:
                _config.cfg.get("settings", {}).pop("ablation", None)
            else:
                _config.cfg["settings"]["ablation"] = _orig_ablation3

    contextvars.copy_context().run(_tool_failure_streak_ablation_scenario)

    # `_rename_match_escalates` (2026-08-17's second-match-only escalation predicate) was removed
    # 2026-08-27: `_dispatch_tasks_batch` now skips dispatch on EVERY `_looks_like_renamed_task`
    # match, not just the second+ against the same target (session_status 2026-08-27 -- corpus
    # data showed most of the wasted-dispatch-slot cost was first-time matches, which the old
    # escalate-only-on-repeat design let straight through). The remaining skip-vs-dispatch
    # decision is a trivial `if renamed_from and not rename_reject_escalation_disabled` at the
    # call site -- `_looks_like_renamed_task`'s own tests above already cover the matching logic
    # that actually has real branching to verify.

    # --- force_whole_rebuild's OWN consecutive-counter must also survive an untracked_delegation
    # interruption for task_verification_flagged (2026-07-31 live incident, Ornith-1.0-9B re-test):
    # check_task_verification_flagged's own prior_same counter (its wording/escalation) was fixed
    # for this 2026-07-29 (7ec86ef), but run_completion_check's SEPARATE consecutive-counter that
    # drives force_whole_rebuild is different code and was not touched by that fix -- a run stuck on
    # task_verification_flagged with one untracked_delegation blip in the middle never escalated to
    # the stronger "reconsider your whole approach" directive either, confirmed live (Report: NOT
    # WRITTEN after 5 attempts, force_whole_rebuild never fired). task_verification_flagged is not
    # Builder/FindingsWriter-fixable (not in either _*_FIXABLE_PROBLEMS tuple), so this exercises the
    # classic Planner-injection path, not a writer dispatch. ---
    def _force_whole_rebuild_survives_untracked_delegation_interruption_scenario():
        from tools.core import tool_quotas_ctx as q_ctx

        _orig_ws13 = _config.cfg.get("settings", {}).get("workspace")
        _config.cfg["settings"]["workspace"] = {"type": "memory", "required_artifact": "final_report.md"}
        _orig_gc13 = _config.cfg.get("settings", {}).get("grounding_check")
        _config.cfg["settings"]["grounding_check"] = {"nli_verify": False, "topical_relevance_check": False}
        saved_fs = dict(_IN_MEMORY_FS)
        try:
            _IN_MEMORY_FS.clear()
            reset_fetched_urls()
            record_fetched_url(_SRC, filename="sources/page.md")
            _IN_MEMORY_FS["sources/page.md"] = _SOURCE_TEXT
            _IN_MEMORY_FS["findings.md"] = "- Real finding with a real cited URL (" + _SRC + ")"
            _IN_MEMORY_FS["final_report.md"] = f"- x [g]({_SRC})"
            q_ctx.set({"delegate_tasks": {"used": 1, "limit": 5},
                       "read_workspace_file": {"used": 1, "limit": 30},
                       "write_workspace_file": {"used": 0, "limit": 30},
                       "think_tool": {"used": 1, "limit": 30}})

            with tempfile.TemporaryDirectory() as tmpdir:
                rs = RunState(tmpdir)
                rs.data["dispatched_tasks"] = [
                    {"task_name": "task_flagged", "instructions": "Research the K-Pg boundary definition and age."},
                ]
                rs.add_finding(
                    "https://b.example.co/y",
                    "[SYSTEM VERIFICATION WARNING: stub_source:https://b.example.co/y]",
                    task_name="task_flagged", depth=1,
                )
                run_state_ctx.set(rs)
                _update_task_verification(rs)
                assert rs.data["task_verification"]["task_flagged"]["status"] == "flagged", rs.data["task_verification"]
                rs.data["completion_check_attempts"] = [
                    {"attempt": 0, "problem": "task_verification_flagged"},
                    {"attempt": 1, "problem": "untracked_delegation"},
                    {"attempt": 2, "problem": "task_verification_flagged"},
                ]
                msgs = []
                should_retry, new_inputs = _asyncio.run(run_completion_check(
                    query="q", current_input="q", run_state=rs, notify=msgs.append))
                assert should_retry is True, (should_retry, msgs)
                inject_msg = new_inputs[-1].contents[0].text
                assert "reconsider your whole approach" in inject_msg, (
                    "untracked_delegation interrupting the streak must not reset force_whole_rebuild's "
                    "own consecutive counter either", inject_msg)
                assert rs.data.get("whole_approach_retry_used_for") == {"task_verification_flagged": True}, rs.data
        finally:
            _IN_MEMORY_FS.clear()
            _IN_MEMORY_FS.update(saved_fs)
            reset_fetched_urls()
            if _orig_ws13 is None:
                _config.cfg["settings"].pop("workspace", None)
            else:
                _config.cfg["settings"]["workspace"] = _orig_ws13
            if _orig_gc13 is None:
                _config.cfg["settings"].pop("grounding_check", None)
            else:
                _config.cfg["settings"]["grounding_check"] = _orig_gc13

    contextvars.copy_context().run(_force_whole_rebuild_survives_untracked_delegation_interruption_scenario)

    # --- run_completion_check's final-verdict salvage must also cover task_verification_flagged,
    # not just missing_artifact (2026-07-31 live incident, Ornith-1.0-9B re-test #2): confirmed
    # live that a run whose retry budget exhausted with task_verification_flagged as the ONLY
    # unresolved problem, req_artifact never written, reported "Report NOT WRITTEN" and discarded
    # the model's own substantial final narration -- even though _salvage_narrated_report is
    # generic and doesn't care WHY the artifact is missing. Model-independent: any candidate
    # ending a run stuck on an unfillable task-level source gap hits this same dead end. Disk mode
    # (not memory) -- the salvage helpers write via plain open()/_get_safe_path, bypassing the
    # in-memory FS dict entirely, same reason the other salvage scenario in this file uses disk. ---
    with tempfile.TemporaryDirectory() as tmpdir2:
        def _task_verification_flagged_final_salvage_scenario():
            from tools.core import tool_quotas_ctx as q_ctx
            from engine.completion import _update_task_verification

            _orig_ws14 = _config.cfg.get("settings", {}).get("workspace")
            _config.cfg["settings"]["workspace"] = {"type": "disk", "dir": tmpdir2, "required_artifact": "final_report.md"}
            _orig_gc14 = _config.cfg.get("settings", {}).get("grounding_check")
            _config.cfg["settings"]["grounding_check"] = {"nli_verify": False, "topical_relevance_check": False}
            _orig_max14 = _config.cfg.get("settings", {}).get("max_completion_check_attempts")
            _config.cfg["settings"]["max_completion_check_attempts"] = 1
            try:
                reset_fetched_urls()
                q_ctx.set({"delegate_tasks": {"used": 1, "limit": 5},
                           "read_workspace_file": {"used": 0, "limit": 30},
                           "write_workspace_file": {"used": 0, "limit": 30},
                           "think_tool": {"used": 0, "limit": 30}})

                rs = RunState(tmpdir2)
                rs.attempt = 1  # already at max_completion_check_attempts (1) -- exhausted on this pass
                rs.data["dispatched_tasks"] = [
                    {"task_name": "task_flagged", "instructions": "Research the K-Pg boundary definition and age."},
                ]
                rs.add_finding(
                    "https://b.example.co/y",
                    "[SYSTEM VERIFICATION WARNING: stub_source:https://b.example.co/y]",
                    task_name="task_flagged", depth=1,
                )
                run_state_ctx.set(rs)
                _update_task_verification(rs)
                assert rs.data["task_verification"]["task_flagged"]["status"] == "flagged", rs.data["task_verification"]

                narrated_summary = (
                    "## Scoping Summary\n\n" + ("Real substantive research content, acknowledged gap. " * 15)
                )
                msgs = []
                should_retry, _ = _asyncio.run(run_completion_check(
                    query="q", current_input="q", run_state=rs, notify=msgs.append,
                    last_assistant_text=narrated_summary))
                assert should_retry is False, (should_retry, msgs)

                path = os.path.join(tmpdir2, "final_report.md")
                assert os.path.exists(path), (
                    "task_verification_flagged as the terminal exhausted problem must still salvage "
                    "the model's own substantial narration into the required artifact, same as "
                    "missing_artifact already does", msgs)
                content = open(path, encoding="utf-8").read()
                assert "Real substantive research content" in content, content
                assert "AUTO-RECOVERED" in content or "unverified" in content.lower(), (
                    "salvaged content must be clearly flagged as unverified", content)
            finally:
                if _orig_ws14 is None:
                    _config.cfg["settings"].pop("workspace", None)
                else:
                    _config.cfg["settings"]["workspace"] = _orig_ws14
                if _orig_gc14 is None:
                    _config.cfg["settings"].pop("grounding_check", None)
                else:
                    _config.cfg["settings"]["grounding_check"] = _orig_gc14
                if _orig_max14 is None:
                    _config.cfg["settings"].pop("max_completion_check_attempts", None)
                else:
                    _config.cfg["settings"]["max_completion_check_attempts"] = _orig_max14
                reset_fetched_urls()

        contextvars.copy_context().run(_task_verification_flagged_final_salvage_scenario)

    # --- Same salvage broadening, one more problem type (2026-07-31, later same night): a gpt-oss
    # re-test AFTER the check_task_verification_flagged/check_thin_coverage starvation fixes above
    # confirmed those fixes work (missing_findings finally got a real turn instead of being
    # permanently blocked) but landed on the final branch before FindingsWriter got a chance to
    # actually run -- and missing_findings wasn't in the salvage condition either. Same generic
    # fix, same reasoning: a coherent narrated summary already exists, don't discard it. ---
    with tempfile.TemporaryDirectory() as tmpdir3:
        def _missing_findings_final_salvage_scenario():
            from tools.core import tool_quotas_ctx as q_ctx

            _orig_ws15 = _config.cfg.get("settings", {}).get("workspace")
            _config.cfg["settings"]["workspace"] = {"type": "disk", "dir": tmpdir3, "required_artifact": "final_report.md"}
            _orig_gc15 = _config.cfg.get("settings", {}).get("grounding_check")
            _config.cfg["settings"]["grounding_check"] = {"nli_verify": False, "topical_relevance_check": False}
            _orig_max15 = _config.cfg.get("settings", {}).get("max_completion_check_attempts")
            _config.cfg["settings"]["max_completion_check_attempts"] = 1
            try:
                reset_fetched_urls()
                q_ctx.set({"delegate_tasks": {"used": 1, "limit": 5},
                           "read_workspace_file": {"used": 0, "limit": 30},
                           "write_workspace_file": {"used": 0, "limit": 30},
                           "think_tool": {"used": 0, "limit": 30}})

                rs = RunState(tmpdir3)
                rs.attempt = 1  # already at max_completion_check_attempts (1) -- exhausted on this pass
                rs.add_finding("https://a.example.co/x", "real content", task_name="task_real", depth=1)
                run_state_ctx.set(rs)

                narrated_summary = (
                    "## Scoping Summary\n\n" + ("Real substantive research content, gathered but never written. " * 15)
                )
                msgs = []
                should_retry, _ = _asyncio.run(run_completion_check(
                    query="q", current_input="q", run_state=rs, notify=msgs.append,
                    last_assistant_text=narrated_summary))
                assert should_retry is False, (should_retry, msgs)

                path = os.path.join(tmpdir3, "final_report.md")
                assert os.path.exists(path), (
                    "missing_findings as the terminal exhausted problem must still salvage the "
                    "model's own substantial narration into the required artifact", msgs)
                content = open(path, encoding="utf-8").read()
                assert "Real substantive research content" in content, content
                assert "AUTO-RECOVERED" in content or "unverified" in content.lower(), (
                    "salvaged content must be clearly flagged as unverified", content)
            finally:
                if _orig_ws15 is None:
                    _config.cfg["settings"].pop("workspace", None)
                else:
                    _config.cfg["settings"]["workspace"] = _orig_ws15
                if _orig_gc15 is None:
                    _config.cfg["settings"].pop("grounding_check", None)
                else:
                    _config.cfg["settings"]["grounding_check"] = _orig_gc15
                if _orig_max15 is None:
                    _config.cfg["settings"].pop("max_completion_check_attempts", None)
                else:
                    _config.cfg["settings"]["max_completion_check_attempts"] = _orig_max15
                reset_fetched_urls()

        contextvars.copy_context().run(_missing_findings_final_salvage_scenario)

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

    # --- _dispatch_writer_review_fix deterministic-content salvage (2026-07-26 live case:
    # gpt-oss/Ollama, explain_the_health_benefits_of_green_tea_and_separ_20260726_103135 --
    # FindingsWriter returned nothing usable, both original AND immediate retry, on SIX
    # consecutive completion-check attempts, contradicting the "isolated, never two in a row"
    # assumption the c2/c3 immediate-retry fix above was built on. Unlike the narration salvage,
    # this content never comes from the model -- it's `_build_findings_source_material`'s own
    # deterministic evidence text, passed in by the caller only for the FindingsWriter role. ---
    with tempfile.TemporaryDirectory() as tmpdir:
        def _deterministic_fallback_salvage_scenario():
            from unittest.mock import AsyncMock

            _orig_ws13 = _config.cfg.get("settings", {}).get("workspace")
            _config.cfg["settings"]["workspace"] = {"type": "disk", "dir": tmpdir}
            try:
                msgs = []
                real_evidence = "### [Real Source](https://example.com/x) (saved as sources/x.md)\n" + (
                    "A real finding sentence. " * 20)

                async def _side_effect_always_empty(name, instructions, role):
                    if role == "FindingsWriter":
                        return ""  # genuinely empty, both the original AND the retry
                    return "REVIEW: CLEAN"

                dispatch = AsyncMock(side_effect=_side_effect_always_empty)
                _asyncio.run(_dispatch_writer_review_fix(
                    dispatch, "FindingsWriter", "findings.md", "write it now", 0, msgs.append,
                    deterministic_fallback=real_evidence))

                path = os.path.join(tmpdir, "findings.md")
                assert os.path.exists(path), (
                    "an empty response twice in a row with a deterministic fallback available "
                    "must still produce findings.md, not raise", msgs)
                content = open(path, encoding="utf-8").read()
                assert "A real finding sentence." in content, content
                assert "FindingsWriter produced no usable output" in content, (
                    "must use the deterministic banner, not the narrated-text one", content)
                assert "every retry" in content, content
                assert "narrated this content as chat text" not in content, content
                assert any("auto-recovered" in m for m in msgs), msgs
                # Write, retry-Write x _WRITER_EMPTY_RETRY_ATTEMPTS, PeerReviewer -- converges
                # instead of raising.
                from engine.completion import _WRITER_EMPTY_RETRY_ATTEMPTS
                assert dispatch.call_count == 2 + _WRITER_EMPTY_RETRY_ATTEMPTS, dispatch.call_args_list
            finally:
                if _orig_ws13 is None:
                    _config.cfg["settings"].pop("workspace", None)
                else:
                    _config.cfg["settings"]["workspace"] = _orig_ws13

        contextvars.copy_context().run(_deterministic_fallback_salvage_scenario)

    # --- FindingsWriter's immediate retry gets STRENGTHENED, non-identical instructions, not a
    # verbatim repeat (2026-08-17 live incident): a real transcript showed the original dispatch's
    # first tool call violate writer_gate_ctx (called read_workspace_file before writing) and the
    # turn then ended with zero further action -- retrying with the exact same instructions gives
    # the model no new signal to avoid repeating the identical first move. Builder is never gated
    # by writer_gate_ctx at all, so its own retry must stay untouched (same instructions both
    # times) -- this fix is deliberately scoped to FindingsWriter only. ---
    with tempfile.TemporaryDirectory() as tmpdir:
        def _strengthened_retry_scenario():
            from unittest.mock import AsyncMock

            _orig_ws14 = _config.cfg.get("settings", {}).get("workspace")
            _config.cfg["settings"]["workspace"] = {"type": "disk", "dir": tmpdir}
            try:
                calls = []

                async def _side_effect(name, instructions, role):
                    calls.append((name, instructions, role))
                    if role == "FindingsWriter" and "_retry" not in name:
                        return ""  # original attempt: empty, forces the retry path
                    if role == "FindingsWriter":
                        with open(os.path.join(tmpdir, "findings.md"), "w", encoding="utf-8") as f:
                            f.write("### Source: x\nreal content")
                        return "## Result\nWrote findings.md\n---"
                    return "REVIEW: CLEAN"

                dispatch = AsyncMock(side_effect=_side_effect)
                msgs = []
                _asyncio.run(_dispatch_writer_review_fix(
                    dispatch, "FindingsWriter", "findings.md", "the ORIGINAL instructions text", 0,
                    msgs.append))

                retry_calls = [c for c in calls if "_retry" in c[0]]
                assert len(retry_calls) == 1, calls
                retry_instructions = retry_calls[0][1]
                assert retry_instructions != "the ORIGINAL instructions text", (
                    "the retry must NOT reuse the exact same instructions verbatim", retry_instructions)
                assert "the ORIGINAL instructions text" in retry_instructions, (
                    "the retry must still include the real task instructions, just not ONLY them",
                    retry_instructions)
                assert "CRITICAL" in retry_instructions and "write_workspace_file" in retry_instructions, (
                    retry_instructions)

                # Builder is never gated by writer_gate_ctx -- its own retry (if it ever needed one)
                # must stay byte-identical, no strengthening applied.
                calls.clear()

                async def _builder_side_effect(name, instructions, role):
                    calls.append((name, instructions, role))
                    if role == "Builder" and "_retry" not in name:
                        return ""
                    if role == "Builder":
                        with open(os.path.join(tmpdir, "final_report.md"), "w", encoding="utf-8") as f:
                            f.write("- x")
                        return "## Result\nWrote final_report.md\n---"
                    return "REVIEW: CLEAN"

                dispatch_builder = AsyncMock(side_effect=_builder_side_effect)
                _asyncio.run(_dispatch_writer_review_fix(
                    dispatch_builder, "Builder", "final_report.md", "the ORIGINAL instructions text", 0,
                    msgs.append))
                builder_retry_calls = [c for c in calls if "_retry" in c[0]]
                assert len(builder_retry_calls) == 1, calls
                assert builder_retry_calls[0][1] == "the ORIGINAL instructions text", (
                    "Builder's retry must stay untouched -- it's never gated, so there's nothing to "
                    "strengthen against", builder_retry_calls[0][1])
            finally:
                if os.path.exists(os.path.join(tmpdir, "findings.md")):
                    os.remove(os.path.join(tmpdir, "findings.md"))
                if os.path.exists(os.path.join(tmpdir, "final_report.md")):
                    os.remove(os.path.join(tmpdir, "final_report.md"))
                if _orig_ws14 is None:
                    _config.cfg["settings"].pop("workspace", None)
                else:
                    _config.cfg["settings"]["workspace"] = _orig_ws14

        contextvars.copy_context().run(_strengthened_retry_scenario)

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

    pool_c = {"read_workspace_file": {"used": 30, "limit": 30}}
    _ensure_reader_quota_headroom(pool_c)
    assert pool_c["read_workspace_file"]["limit"] - pool_c["read_workspace_file"]["used"] == 2, (
        "an exhausted read_workspace_file pool must be topped up to guarantee 2 more calls", pool_c)

    pool_d = {"read_workspace_file": {"used": 1, "limit": 10}}
    _ensure_reader_quota_headroom(pool_d)
    assert pool_d["read_workspace_file"]["limit"] == 10, (
        "plenty of existing headroom must be left untouched, no silent inflation", pool_d)

    _ensure_reader_quota_headroom({"write_workspace_file": {"used": 0, "limit": 5}})  # no-op, no KeyError

    # --- read_workspace_file headroom sizing correction (found live 2026-07-27, fixed 2026-07-29):
    # the Builder/final_report.md review cycle needs PeerReviewer to read BOTH final_report.md AND
    # findings.md (PEER_REVIEWER_INSTRUCTIONS), not just one file — the old blanket needed=2 default
    # undersized this specific path. Callers now pass needed=3 for the Builder dispatch site. ---
    pool_e = {"read_workspace_file": {"used": 29, "limit": 30}}
    _ensure_reader_quota_headroom(pool_e, needed=3)
    assert pool_e["read_workspace_file"]["limit"] - pool_e["read_workspace_file"]["used"] == 3, (
        "Builder/PeerReviewer's two-file review cycle needs 3 guaranteed reads, not 2", pool_e)

    # An 8-cycle Write->Review->Fix escalation chain (the exact shape of the live 2026-07-27
    # incident) must not starve a late cycle even with a small starting pool, now that
    # read_workspace_file also gets per-attempt retry_quota_topup replenishment (config_template
    # .yaml) in addition to this per-cycle guard.
    pool_f = {"read_workspace_file": {"used": 0, "limit": 5}}
    for _cycle in range(8):
        # Simulate topup_quota_pool's per-attempt replenishment (settings.retry_quota_topup).
        pool_f["read_workspace_file"]["limit"] += 3
        _ensure_reader_quota_headroom(pool_f, needed=3)
        headroom = pool_f["read_workspace_file"]["limit"] - pool_f["read_workspace_file"]["used"]
        assert headroom >= 3, (
            f"cycle {_cycle}: a late Write->Review->Fix cycle must always have 3 guaranteed reads "
            f"available, even after 8 escalation rounds", pool_f)
        # Consume the reads this cycle would actually make (2 reviewer reads + 1 fix re-read).
        pool_f["read_workspace_file"]["used"] += 3

    # --- missing_findings escalation (live case 2026-07-13): a real run produced literally ZERO
    # content (no tool call, no text) in response to this exact nudge for 6 consecutive attempts,
    # then genuinely self-corrected with real findings.md content on the 7th. Unlike
    # missing_artifact, late recovery is real here -- wording escalates and, on repeat, hands the
    # model its actual fetched URLs as proof material exists, but deliberately does NOT get the
    # aggressive early-cutoff missing_artifact has (that would have killed this run's real
    # recovery at attempt 3). ---
    def _missing_findings_escalation_scenario():
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



if __name__ == "__main__":
    main()
    print("test_dispatch_ablation_and_salvage OK")
