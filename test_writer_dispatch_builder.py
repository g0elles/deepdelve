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
    run_completion_check,
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

            # (c2) Write dispatch returns a genuinely EMPTY response on EVERY attempt (original
            # 2026-07-24 live case: gpt-oss, 3 separate occurrences in one run, motivated one
            # immediate retry; 2026-08-18: bumped to _WRITER_EMPTY_RETRY_ATTEMPTS retries after a
            # later live run showed a single retry alone doesn't reliably recover it) -- only once
            # every retry ALSO produces nothing does this raise BEFORE ever dispatching
            # PeerReviewer -- confirmed live that dispatching PeerReviewer against a nonexistent
            # artifact makes it degrade into guessing wrong filenames and burn its entire
            # read_workspace_file quota on nothing. Exactly 1 + _WRITER_EMPTY_RETRY_ATTEMPTS
            # dispatches (Builder, Builder retries), falls back to the classic inject-into-Planner
            # nudge.
            with tempfile.TemporaryDirectory() as tmpdir_c2:
                _IN_MEMORY_FS.pop("final_report.md", None)
                rs = RunState(tmpdir_c2)
                run_state_ctx.set(rs)
                msgs = []

                async def _side_effect_c2(name, instructions, role):
                    return ""  # genuinely empty response, no tool call, nothing narrated, ever

                dispatch = AsyncMock(side_effect=_side_effect_c2)
                orig_input = "q"
                should_retry, new_input = _asyncio.run(run_completion_check(
                    query="q", current_input=orig_input, run_state=rs, notify=msgs.append,
                    dispatch_task=dispatch))
                from engine.completion import _WRITER_EMPTY_RETRY_ATTEMPTS
                assert dispatch.call_count == 1 + _WRITER_EMPTY_RETRY_ATTEMPTS, (
                    "an empty Write response must retry _WRITER_EMPTY_RETRY_ATTEMPTS times, then "
                    "raise BEFORE PeerReviewer is ever dispatched", dispatch.call_args_list)
                for c in dispatch.call_args_list[1:]:
                    assert "_retry" in c.args[0], dispatch.call_args_list
                assert should_retry, msgs
                assert any("Builder dispatch failed" in m for m in msgs), msgs

            # (c3) Write dispatch returns empty ONCE, then the immediate retry genuinely writes --
            # the retry must be given a real chance to succeed outright, converging normally
            # (PeerReviewer dispatched against a real artifact this time) instead of raising just
            # because the FIRST attempt was empty.
            with tempfile.TemporaryDirectory() as tmpdir_c3:
                _IN_MEMORY_FS.pop("final_report.md", None)
                rs = RunState(tmpdir_c3)
                run_state_ctx.set(rs)
                msgs = []
                write_calls = []

                async def _side_effect_c3(name, instructions, role):
                    if role == "PeerReviewer":
                        return "REVIEW: CLEAN\nNo issues found."
                    write_calls.append(name)
                    if len(write_calls) == 1:
                        return ""  # first Write attempt: empty
                    _IN_MEMORY_FS["final_report.md"] = _CLEAN_REPORT
                    return "## Result\nWrote report\n---"

                dispatch = AsyncMock(side_effect=_side_effect_c3)
                should_retry, new_input = _asyncio.run(run_completion_check(
                    query="q", current_input="q", run_state=rs, notify=msgs.append,
                    dispatch_task=dispatch))
                assert not should_retry, (
                    "a Write dispatch that succeeds on its immediate retry must converge "
                    "normally, not be treated as a failure", msgs)
                assert dispatch.call_count == 3, dispatch.call_args_list  # Write, retry-Write, PeerReviewer

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

            # (f) propagated_ungrounded (2026-08-29, live incident: this problem had no fix path
            # at all before today, exhausting a real run's retry budget) must now dispatch Builder
            # too, same as any other _BUILDER_FIXABLE_PROBLEMS entry -- same findings shape as the
            # propagated_ungrounded matrix row above (a same-task_name uncited/citable pair sharing
            # identical content), but with dispatch_task/Builder+PeerReviewer registered this time.
            with tempfile.TemporaryDirectory() as tmpdir_f:
                _IN_MEMORY_FS["final_report.md"] = (
                    f"- el pais avanza de forma sostenida segun cifras oficiales [gov]({_SRC})\n"
                    "- The Temporal Fusion Transformer improved forecast accuracy by 23% over "
                    "classical baselines in a 2024 benchmark study of Time Series Models. "
                    "[source](https://forecastio.ai/other)"
                )
                record_fetched_url("https://forecastio.ai/other", filename="sources/other.md")
                _IN_MEMORY_FS["sources/other.md"] = (
                    "Source-URL: https://forecastio.ai/other\n\nThe Temporal Fusion Transformer "
                    "improved forecast accuracy by 23% over classical baselines in a 2024 "
                    "benchmark study of Time Series Models."
                )
                rs = RunState(tmpdir_f)
                run_state_ctx.set(rs)
                rs.add_finding("background_heuristics", "The Temporal Fusion Transformer improved "
                    "forecast accuracy by 23% over classical baselines in a 2024 benchmark study "
                    "of Time Series Models.", task_name="background_heuristics")
                rs.add_finding("https://forecastio.ai/other", "The Temporal Fusion Transformer "
                    "improved forecast accuracy by 23% over classical baselines in a 2024 "
                    "benchmark study of Time Series Models.", task_name="background_heuristics")
                msgs = []

                async def _side_effect_f(name, instructions, role):
                    if role == "Builder":
                        _IN_MEMORY_FS["final_report.md"] = _CLEAN_REPORT
                        return "## Result for BuilderFix\nWrote report\n---"
                    return "REVIEW: CLEAN\nNo issues found."

                dispatch = AsyncMock(side_effect=_side_effect_f)
                should_retry, new_input = _asyncio.run(run_completion_check(
                    query="q", current_input="q", run_state=rs, notify=msgs.append,
                    dispatch_task=dispatch))
                assert dispatch.call_args_list[0].args[2] == "Builder", (
                    "propagated_ungrounded must now dispatch Builder, not just nudge the Planner",
                    dispatch.call_args_list)
                assert not should_retry, (
                    "a Builder dispatch that genuinely fixes the artifact must converge", msgs)
                _IN_MEMORY_FS["findings.md"] = _FINDINGS_OK
                reset_fetched_urls()
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

    # --- Per-facet Builder dispatch for report_underuses_evidence (2026-08-01): the combined-
    # instruction Planner-mediated fix (commit 67e4b00) got a clean negative live result (commit
    # 1092add) — asking Builder to fix every neglected facet in one turn crowded a facet out
    # further instead of restoring it. run_completion_check must instead dispatch ONE fresh-context
    # Builder Write->Review->Fix cycle PER dropped facet (see _dispatch_per_facet_builder_fix /
    # _facet_coverage), sequentially, each scoped to ONLY that facet's real findings.md URLs. ---
    def _per_facet_builder_dispatch_scenario():
        from tools.core import tool_quotas_ctx as q_ctx
        from unittest.mock import AsyncMock
        from engine.orchestrator import available_sub_agents_ctx

        class _FakeSubAgentConfig:
            def __init__(self, name):
                self.name = name

        _orig_ws_pf = _config.cfg.get("settings", {}).get("workspace")
        _config.cfg["settings"]["workspace"] = {"type": "memory", "required_artifact": "final_report.md"}
        _orig_gc_pf = _config.cfg.get("settings", {}).get("grounding_check")
        _config.cfg["settings"]["grounding_check"] = {"nli_verify": False, "topical_relevance_check": False}
        _orig_uc_pf = _config.cfg.get("settings", {}).get("uneven_coverage_check")
        _config.cfg["settings"]["uneven_coverage_check"] = {"enabled": False}
        saved_fs = dict(_IN_MEMORY_FS)
        try:
            # Same shape as check_report_underuses_evidence's own direct-call scenario: heuristics
            # (2 URLs) and colombia (3 URLs), both surviving into findings.md. Citing colombia only
            # clears check_report_underuses_findings' 50% raw-ratio threshold (3/5=60%), so THIS
            # check is the one that must fire and dispatch — the live incident's exact shape.
            heur_urls = ["https://a.example.co/heur1", "https://a.example.co/heur2"]
            colo_urls = ["https://b.example.co/colo1", "https://b.example.co/colo2", "https://b.example.co/colo3"]
            findings_md = "\n\n".join(f"### [Src]({u})\n- real finding." for u in heur_urls + colo_urls)

            # (a) one dropped facet (heuristics) -> exactly one facet's worth of dispatches
            # (Builder, PeerReviewer), scoped to ONLY heuristics' URLs, and the fix converges.
            with tempfile.TemporaryDirectory() as tmpdir_a:
                _IN_MEMORY_FS.clear()
                _IN_MEMORY_FS["findings.md"] = findings_md
                _IN_MEMORY_FS["final_report.md"] = "\n".join(f"- dato. [Src]({u})" for u in colo_urls)
                reset_fetched_urls()
                for u in heur_urls + colo_urls:
                    record_fetched_url(u, filename=f"sources/{u.rsplit('/', 1)[-1]}.md")
                rs = RunState(tmpdir_a)
                run_state_ctx.set(rs)
                for u in heur_urls:
                    rs.add_finding(u, "heuristic finding", task_name="heuristics", depth=1)
                for u in colo_urls:
                    rs.add_finding(u, "colombia finding", task_name="colombia", depth=1)
                q_ctx.set({"delegate_tasks": {"used": 1, "limit": 5}})
                available_sub_agents_ctx.set([_FakeSubAgentConfig("Builder"), _FakeSubAgentConfig("PeerReviewer")])
                msgs = []

                async def _side_effect_a(name, instructions, role):
                    if role == "Builder":
                        assert "heuristics" in instructions, instructions
                        assert "colombia" not in instructions, instructions
                        for u in heur_urls:
                            assert u in instructions, instructions
                        _IN_MEMORY_FS["final_report.md"] += "\n" + "\n".join(
                            f"- dato. [Src]({u})" for u in heur_urls)
                        return "## Result\nAdded heuristics section\n---"
                    return "REVIEW: CLEAN\nNo issues found."

                dispatch = AsyncMock(side_effect=_side_effect_a)
                orig_input = "q"
                should_retry, new_input = _asyncio.run(run_completion_check(
                    query="q", current_input=orig_input, run_state=rs, notify=msgs.append,
                    dispatch_task=dispatch))
                assert not should_retry, (
                    "a per-facet Builder dispatch that genuinely restores the dropped facet must "
                    "converge within this call instead of returning control to the Planner", msgs)
                assert new_input == orig_input
                assert dispatch.call_count == 2, dispatch.call_args_list
                assert dispatch.call_args_list[0].args[2] == "Builder", dispatch.call_args_list
                assert dispatch.call_args_list[1].args[2] == "PeerReviewer", dispatch.call_args_list
                assert rs.data["completion_check_attempts"][0]["problem"] == "report_underuses_evidence"
                assert any("neglected facet" in m for m in msgs), msgs

            # (b) two dropped facets (heuristics, extra), out of 3 tasks -> sequential dispatch,
            # ONE Write->Review->Fix cycle per facet (4 dispatches total: Builder/PeerReviewer x2),
            # never one combined instruction covering both.
            with tempfile.TemporaryDirectory() as tmpdir_b:
                extra_urls = [f"https://c.example.co/extra{i}" for i in range(10)]
                findings_md_b = "\n\n".join(
                    f"### [Src]({u})\n- real finding." for u in heur_urls + colo_urls + extra_urls)
                _IN_MEMORY_FS.clear()
                _IN_MEMORY_FS["findings.md"] = findings_md_b
                # Cites only 'extra' -> ratio 10/15=67% clears report_underuses_findings' 50%
                # threshold, but heuristics AND colombia are both dropped for this check.
                _IN_MEMORY_FS["final_report.md"] = "\n".join(f"- dato. [Src]({u})" for u in extra_urls)
                reset_fetched_urls()
                for u in heur_urls + colo_urls + extra_urls:
                    record_fetched_url(u, filename=f"sources/{u.rsplit('/', 1)[-1]}.md")
                rs = RunState(tmpdir_b)
                run_state_ctx.set(rs)
                for u in heur_urls:
                    rs.add_finding(u, "heuristic finding", task_name="heuristics", depth=1)
                for u in colo_urls:
                    rs.add_finding(u, "colombia finding", task_name="colombia", depth=1)
                for u in extra_urls:
                    rs.add_finding(u, "extra finding", task_name="extra", depth=1)
                q_ctx.set({"delegate_tasks": {"used": 1, "limit": 5}})
                available_sub_agents_ctx.set([_FakeSubAgentConfig("Builder"), _FakeSubAgentConfig("PeerReviewer")])
                msgs = []
                builder_calls = []

                async def _side_effect_b(name, instructions, role):
                    if role == "Builder":
                        builder_calls.append(instructions)
                        if "heuristics" in instructions:
                            urls = heur_urls
                        elif "colombia" in instructions:
                            urls = colo_urls
                        else:
                            raise AssertionError(f"unexpected Builder instructions: {instructions}")
                        _IN_MEMORY_FS["final_report.md"] += "\n" + "\n".join(
                            f"- dato. [Src]({u})" for u in urls)
                        return "## Result\nAdded section\n---"
                    return "REVIEW: CLEAN\nNo issues found."

                dispatch = AsyncMock(side_effect=_side_effect_b)
                should_retry, new_input = _asyncio.run(run_completion_check(
                    query="q", current_input="q", run_state=rs, notify=msgs.append,
                    dispatch_task=dispatch))
                assert not should_retry, msgs
                assert dispatch.call_count == 4, (
                    "two dropped facets must produce two independent Write->Review->Fix cycles "
                    "(4 dispatches), not one combined instruction", dispatch.call_args_list)
                assert len(builder_calls) == 2, builder_calls
                # colombia sorts before heuristics alphabetically -- _facet_coverage's `dropped`
                # is sorted, and the dispatch loop must preserve that order.
                assert "colombia" in builder_calls[0] and "heuristics" not in builder_calls[0], builder_calls
                assert "heuristics" in builder_calls[1] and "colombia" not in builder_calls[1], builder_calls
        finally:
            _IN_MEMORY_FS.clear()
            _IN_MEMORY_FS.update(saved_fs)
            reset_fetched_urls()
            if _orig_ws_pf is None:
                _config.cfg["settings"].pop("workspace", None)
            else:
                _config.cfg["settings"]["workspace"] = _orig_ws_pf
            if _orig_gc_pf is None:
                _config.cfg["settings"].pop("grounding_check", None)
            else:
                _config.cfg["settings"]["grounding_check"] = _orig_gc_pf
            if _orig_uc_pf is None:
                _config.cfg["settings"].pop("uneven_coverage_check", None)
            else:
                _config.cfg["settings"]["uneven_coverage_check"] = _orig_uc_pf

    contextvars.copy_context().run(_per_facet_builder_dispatch_scenario)

    # --- Cross-TIER starvation yield (2026-08-01, RESEARCH.md Sec.17f): GROUNDING_CHECKS is only
    # ever evaluated when COMPLETION_CHECKS returns None for the WHOLE scan -- a hard two-tier
    # gate, not just first-match priority within one list. Live-confirmed: a resumed run's
    # check_task_verification_flagged (COMPLETION_CHECKS) recurred for 6 of 8 attempts on a real,
    # still-unresolved problem, and report_underuses_evidence (GROUNDING_CHECKS, built specifically
    # to catch a Builder draft dropping a covered facet) never got evaluated ONCE in the entire
    # run. run_completion_check must give report_underuses_evidence one direct probe (via the
    # EXISTING generic _yield_to_starved_check mechanism, already protecting
    # check_untracked_delegation the same way) once the CURRENT COMPLETION_CHECKS winner has
    # recurred _STARVATION_SKIP_THRESHOLD times, regardless of which COMPLETION_CHECKS problem is
    # winning. ---
    def _cross_tier_starvation_yield_scenario():
        from tools.core import tool_quotas_ctx as q_ctx
        from unittest.mock import AsyncMock
        from engine.orchestrator import available_sub_agents_ctx

        class _FakeSubAgentConfig:
            def __init__(self, name):
                self.name = name

        _orig_ws_ct = _config.cfg.get("settings", {}).get("workspace")
        _config.cfg["settings"]["workspace"] = {"type": "memory", "required_artifact": "final_report.md"}
        _orig_gc_ct = _config.cfg.get("settings", {}).get("grounding_check")
        _config.cfg["settings"]["grounding_check"] = {"nli_verify": False, "topical_relevance_check": False}
        _orig_uc_ct = _config.cfg.get("settings", {}).get("uneven_coverage_check")
        _config.cfg["settings"]["uneven_coverage_check"] = {"enabled": False}
        saved_fs = dict(_IN_MEMORY_FS)
        try:
            _IN_MEMORY_FS.clear()
            reset_fetched_urls()
            # Task A: a real, citable, DROPPED-from-report facet (report_underuses_evidence's own
            # trigger condition). Task C (colombia): a second real, citable, COVERED task -- needed
            # because check_report_underuses_evidence requires min_tasks>=2 real covered tasks in
            # by_task before it evaluates at all (a single-task by_task can't distinguish "dropped"
            # from "there was only ever one thing to cite").
            heur_urls = ["https://a.example.co/heur1", "https://a.example.co/heur2"]
            colo_urls = ["https://c.example.co/colo1", "https://c.example.co/colo2"]
            for u in heur_urls + colo_urls:
                record_fetched_url(u, filename=f"sources/{u.rsplit('/', 1)[-1]}.md")
            findings_md = "\n\n".join(f"### [Src]({u})\n- real finding." for u in heur_urls + colo_urls)
            _IN_MEMORY_FS["findings.md"] = findings_md
            # Report drops heuristics entirely, cites only colombia.
            _IN_MEMORY_FS["final_report.md"] = "\n".join(f"- dato. [Src]({u})" for u in colo_urls)

            with tempfile.TemporaryDirectory() as tmpdir_ct:
                rs = RunState(tmpdir_ct)
                run_state_ctx.set(rs)
                for u in heur_urls:
                    rs.add_finding(u, "heuristic finding", task_name="heuristics", depth=1)
                for u in colo_urls:
                    rs.add_finding(u, "colombia finding", task_name="colombia", depth=1)
                # Task B: a genuinely FLAGGED task -- a REAL http source_url (so check_thin_coverage,
                # which sits ABOVE task_verification_flagged in COMPLETION_CHECKS and only cares
                # whether a task has ANY real URL, stays quiet) but a summary carrying a SYSTEM
                # VERIFICATION WARNING marker, which _is_citable_finding specifically excludes --
                # this isolates task_verification_flagged as the actual COMPLETION_CHECKS winner,
                # not a different, higher-priority sibling check.
                rs.add_finding(
                    "https://flagged.example.co/page",
                    "[SYSTEM VERIFICATION WARNING: this summary cites 'X', which does not match "
                    "the source URL you were actually given to analyze.]",
                    task_name="flagged_task", depth=1,
                )
                q_ctx.set({"delegate_tasks": {"used": 1, "limit": 5}})
                available_sub_agents_ctx.set([_FakeSubAgentConfig("Builder"), _FakeSubAgentConfig("PeerReviewer")])

                # Pre-seed 2 consecutive prior attempts already recording task_verification_flagged
                # -- _STARVATION_SKIP_THRESHOLD (2) worth of history, so THIS call's own yield check
                # is already past the skip threshold.
                rs.record_attempt(0, "task_verification_flagged", 2)
                rs.record_attempt(1, "task_verification_flagged", 2)
                rs.attempt = 2

                async def _side_effect_ct(name, instructions, role):
                    if role == "Builder":
                        assert "heuristics" in instructions, instructions
                        _IN_MEMORY_FS["final_report.md"] += "\n" + "\n".join(
                            f"- dato. [Src]({u})" for u in heur_urls)
                        return "## Result\nAdded heuristics section\n---"
                    return "REVIEW: CLEAN\nNo issues found."

                dispatch = AsyncMock(side_effect=_side_effect_ct)
                msgs = []
                should_retry, _ = _asyncio.run(run_completion_check(
                    query="q", current_input="q", run_state=rs, notify=msgs.append,
                    dispatch_task=dispatch))
                recorded_problems = [a["problem"] for a in rs.data["completion_check_attempts"]]
                # report_underuses_evidence must have won a turn and been genuinely dispatched
                # (Builder called, scoped to heuristics, and cleanly reviewed) somewhere in this
                # run -- NOT necessarily the LAST recorded problem, since _dispatch_per_facet_
                # builder_fix chains straight into the next completion-check iteration rather than
                # returning, and that next iteration correctly goes on to address the STILL-
                # unresolved flagged_task afterward. The point being tested is that GROUNDING_CHECKS
                # got a turn at all, not that it's the run's final word.
                assert "report_underuses_evidence" in recorded_problems, (
                    "after task_verification_flagged recurred past the starvation threshold, "
                    "report_underuses_evidence must get a direct probe and win if it has something "
                    "real to report -- GROUNDING_CHECKS must not be starved forever just because "
                    "COMPLETION_CHECKS keeps returning non-None", recorded_problems, msgs)
                assert any(
                    call.args[2] == "Builder" and "heuristics" in call.args[1]
                    for call in dispatch.call_args_list
                ), dispatch.call_args_list
                assert any("dispatching Builder once per neglected facet" in m for m in msgs), msgs
        finally:
            _IN_MEMORY_FS.clear()
            _IN_MEMORY_FS.update(saved_fs)
            reset_fetched_urls()
            if _orig_ws_ct is None:
                _config.cfg["settings"].pop("workspace", None)
            else:
                _config.cfg["settings"]["workspace"] = _orig_ws_ct
            if _orig_gc_ct is None:
                _config.cfg["settings"].pop("grounding_check", None)
            else:
                _config.cfg["settings"]["grounding_check"] = _orig_gc_ct
            if _orig_uc_ct is None:
                _config.cfg["settings"].pop("uneven_coverage_check", None)
            else:
                _config.cfg["settings"]["uneven_coverage_check"] = _orig_uc_ct

    contextvars.copy_context().run(_cross_tier_starvation_yield_scenario)

    # --- Cross-TIER starvation yield, DIFFERENT-problem case (2026-08-16 follow-up incident):
    # the scenario above only pre-seeds the SAME problem (task_verification_flagged) twice, which
    # the ORIGINAL same-problem-only _consecutive_occurrences check already caught. A live run
    # instead cycled missing_findings -> missing_artifact -> uneven_task_investment ->
    # task_verification_flagged, never repeating the same problem twice in a row, and
    # report_underuses_evidence never got a single turn despite the report on disk having dropped
    # 3 of 4 requested facets. This scenario is identical to the one above except the two
    # pre-seeded attempts record TWO DIFFERENT COMPLETION_CHECKS problems (neither of which equals
    # this attempt's own winner) -- proving the fix is the TIER-wide check
    # (_consecutive_tier_wins / tier_problems=_COMPLETION_TIER_PROBLEMS), not just the pre-existing
    # same-problem one. ---
    def _cross_tier_starvation_yield_different_problems_scenario():
        from tools.core import tool_quotas_ctx as q_ctx
        from unittest.mock import AsyncMock
        from engine.orchestrator import available_sub_agents_ctx

        class _FakeSubAgentConfig:
            def __init__(self, name):
                self.name = name

        _orig_ws_ct2 = _config.cfg.get("settings", {}).get("workspace")
        _config.cfg["settings"]["workspace"] = {"type": "memory", "required_artifact": "final_report.md"}
        _orig_gc_ct2 = _config.cfg.get("settings", {}).get("grounding_check")
        _config.cfg["settings"]["grounding_check"] = {"nli_verify": False, "topical_relevance_check": False}
        _orig_uc_ct2 = _config.cfg.get("settings", {}).get("uneven_coverage_check")
        _config.cfg["settings"]["uneven_coverage_check"] = {"enabled": False}
        saved_fs = dict(_IN_MEMORY_FS)
        try:
            _IN_MEMORY_FS.clear()
            reset_fetched_urls()
            heur_urls = ["https://a.example.co/heur1", "https://a.example.co/heur2"]
            colo_urls = ["https://c.example.co/colo1", "https://c.example.co/colo2"]
            for u in heur_urls + colo_urls:
                record_fetched_url(u, filename=f"sources/{u.rsplit('/', 1)[-1]}.md")
            findings_md = "\n\n".join(f"### [Src]({u})\n- real finding." for u in heur_urls + colo_urls)
            _IN_MEMORY_FS["findings.md"] = findings_md
            _IN_MEMORY_FS["final_report.md"] = "\n".join(f"- dato. [Src]({u})" for u in colo_urls)

            with tempfile.TemporaryDirectory() as tmpdir_ct2:
                rs = RunState(tmpdir_ct2)
                run_state_ctx.set(rs)
                for u in heur_urls:
                    rs.add_finding(u, "heuristic finding", task_name="heuristics", depth=1)
                for u in colo_urls:
                    rs.add_finding(u, "colombia finding", task_name="colombia", depth=1)
                rs.add_finding(
                    "https://flagged.example.co/page",
                    "[SYSTEM VERIFICATION WARNING: this summary cites 'X', which does not match "
                    "the source URL you were actually given to analyze.]",
                    task_name="flagged_task", depth=1,
                )
                q_ctx.set({"delegate_tasks": {"used": 1, "limit": 5}})
                available_sub_agents_ctx.set([_FakeSubAgentConfig("Builder"), _FakeSubAgentConfig("PeerReviewer")])

                # TWO DIFFERENT tier problems, neither equal to this attempt's own winner
                # (task_verification_flagged) -- the same-problem-only check would see 0
                # consecutive occurrences here and never yield.
                rs.record_attempt(0, "missing_artifact", 0)
                rs.record_attempt(1, "uneven_task_investment", 2)
                rs.attempt = 2

                async def _side_effect_ct2(name, instructions, role):
                    if role == "Builder":
                        assert "heuristics" in instructions, instructions
                        _IN_MEMORY_FS["final_report.md"] += "\n" + "\n".join(
                            f"- dato. [Src]({u})" for u in heur_urls)
                        return "## Result\nAdded heuristics section\n---"
                    return "REVIEW: CLEAN\nNo issues found."

                dispatch = AsyncMock(side_effect=_side_effect_ct2)
                msgs = []
                should_retry, _ = _asyncio.run(run_completion_check(
                    query="q", current_input="q", run_state=rs, notify=msgs.append,
                    dispatch_task=dispatch))
                recorded_problems = [a["problem"] for a in rs.data["completion_check_attempts"]]
                assert "report_underuses_evidence" in recorded_problems, (
                    "a run where COMPLETION_CHECKS keeps winning with a DIFFERENT problem every "
                    "attempt must still yield to report_underuses_evidence once the whole TIER "
                    "has recurred _STARVATION_SKIP_THRESHOLD times -- not just when the exact same "
                    "problem repeats", recorded_problems, msgs)
                assert any(
                    call.args[2] == "Builder" and "heuristics" in call.args[1]
                    for call in dispatch.call_args_list
                ), dispatch.call_args_list
        finally:
            _IN_MEMORY_FS.clear()
            _IN_MEMORY_FS.update(saved_fs)
            reset_fetched_urls()
            if _orig_ws_ct2 is None:
                _config.cfg["settings"].pop("workspace", None)
            else:
                _config.cfg["settings"]["workspace"] = _orig_ws_ct2
            if _orig_gc_ct2 is None:
                _config.cfg["settings"].pop("grounding_check", None)
            else:
                _config.cfg["settings"]["grounding_check"] = _orig_gc_ct2
            if _orig_uc_ct2 is None:
                _config.cfg["settings"].pop("uneven_coverage_check", None)
            else:
                _config.cfg["settings"]["uneven_coverage_check"] = _orig_uc_ct2

    contextvars.copy_context().run(_cross_tier_starvation_yield_different_problems_scenario)

    # --- WITHIN-GROUNDING_CHECKS starvation yield to check_missing_specific_item_per_facet
    # (2026-09-04, closing the "never fires live" gap noted in session_status/CURRENT.md's
    # 2026-08-30 entries): that check doesn't key off the single shared ctx.grounding_problem the
    # way most of its GROUNDING_CHECKS siblings do, so it's starved for as long as ANY
    # earlier-positioned entry keeps winning -- even across attempts where the winning PROBLEM
    # changes every time (exactly the shape live-confirmed across 4 real runs: propagated_
    # ungrounded/non_url_citation/uncited_claims/claim_unsupported each won in turn, and the check
    # never once fired inside the real dispatch loop). Report has a genuine duplicate-section
    # problem (wins the main GROUNDING_CHECKS scan, positioned earlier in the list) AND a genuine
    # missing-per-facet-regulation gap (Japan cites only a target, never a named regulation) --
    # pre-seeded history uses two DIFFERENT tier problems (proving this is _GROUNDING_TIER_
    # PROBLEMS-wide, not same-problem-only) neither equal to duplicate_report_sections or
    # missing_specific_item_per_facet. ---
    def _within_grounding_tier_starvation_yield_scenario():
        from tools.core import tool_quotas_ctx as q_ctx
        from unittest.mock import AsyncMock
        from engine.orchestrator import available_sub_agents_ctx

        class _FakeSubAgentConfig:
            def __init__(self, name):
                self.name = name

        _orig_ws_gt = _config.cfg.get("settings", {}).get("workspace")
        _config.cfg["settings"]["workspace"] = {"type": "memory", "required_artifact": "final_report.md"}
        _orig_gc_gt = _config.cfg.get("settings", {}).get("grounding_check")
        _config.cfg["settings"]["grounding_check"] = {"nli_verify": False, "topical_relevance_check": False}
        _orig_uc_gt = _config.cfg.get("settings", {}).get("uneven_coverage_check")
        _config.cfg["settings"]["uneven_coverage_check"] = {"enabled": False}
        de_url = "https://gov.example.de/eeg-act-gt"
        jp_url = "https://gov.example.jp/2040-target-gt"
        de_text = ("Source-URL: " + de_url + "\n\nGermany's Renewable Energy Sources Act (EEG) "
                   "mandates increased funding for renewable energy generation nationwide.")
        jp_text = ("Source-URL: " + jp_url + "\n\nJapan targets a 40 to 50 percent renewable "
                   "energy share by 2040.")
        saved_fs = dict(_IN_MEMORY_FS)
        try:
            _IN_MEMORY_FS.clear()
            reset_fetched_urls()
            record_fetched_url(de_url, filename="sources/de_gt.md")
            record_fetched_url(jp_url, filename="sources/jp_gt.md")
            _IN_MEMORY_FS["sources/de_gt.md"] = de_text
            _IN_MEMORY_FS["sources/jp_gt.md"] = jp_text
            _IN_MEMORY_FS["findings.md"] = (
                f"### [DE]({de_url})\n- Germany's Renewable Energy Sources Act (EEG) mandates "
                f"increased funding for renewable energy generation nationwide.\n\n"
                f"### [JP]({jp_url})\n- Japan targets a 40 to 50 percent renewable energy share "
                f"by 2040.\n")
            _IN_MEMORY_FS["final_report.md"] = (
                "## Germany's Renewable Energy Policy\n"
                f"- Germany's Renewable Energy Sources Act (EEG) mandates increased funding for "
                f"renewable energy generation nationwide. [gov]({de_url})\n\n"
                "## Germany - Policy Overview\n"
                f"- Germany's Renewable Energy Sources Act (EEG) mandates increased funding for "
                f"renewable energy generation nationwide. [gov]({de_url})\n\n"
                "## Japan's Renewable Energy Policy\n"
                f"- Japan targets a 40 to 50 percent renewable energy share by 2040. [gov]({jp_url})\n"
            )

            with tempfile.TemporaryDirectory() as tmpdir_gt:
                rs = RunState(tmpdir_gt)
                rs.set_query("Compare Germany and Japan's renewable energy policy, citing at "
                             "least one specific regulation for each.")
                run_state_ctx.set(rs)
                rs.add_finding(de_url, "Germany finding", task_name="germany", depth=1)
                rs.add_finding(jp_url, "Japan finding", task_name="japan", depth=1)
                q_ctx.set({"delegate_tasks": {"used": 1, "limit": 5}})
                available_sub_agents_ctx.set([_FakeSubAgentConfig("Builder"), _FakeSubAgentConfig("PeerReviewer")])

                # TWO DIFFERENT GROUNDING_CHECKS-tier problems, neither equal to this attempt's
                # own main-scan winner (duplicate_report_sections) nor to missing_specific_item_
                # per_facet -- the same-problem-only check would see 0 consecutive occurrences and
                # never yield.
                rs.record_attempt(0, "non_url_citation", 0)
                rs.record_attempt(1, "propagated_ungrounded", 2)
                rs.attempt = 2

                async def _side_effect_gt(name, instructions, role):
                    if role == "Builder":
                        assert "Japan" in instructions, instructions
                        return "## Result\nNo change needed for this test\n---"
                    return "REVIEW: CLEAN\nNo issues found."

                dispatch = AsyncMock(side_effect=_side_effect_gt)
                msgs = []
                should_retry, _ = _asyncio.run(run_completion_check(
                    query="q", current_input="q", run_state=rs, notify=msgs.append,
                    dispatch_task=dispatch))
                recorded_problems = [a["problem"] for a in rs.data["completion_check_attempts"]]
                assert "missing_specific_item_per_facet" in recorded_problems, (
                    "a run where GROUNDING_CHECKS keeps winning with a DIFFERENT problem every "
                    "attempt (none of them missing_specific_item_per_facet, which doesn't key off "
                    "ctx.grounding_problem at all) must still yield to it once the whole TIER has "
                    "recurred _STARVATION_SKIP_THRESHOLD times", recorded_problems, msgs)
                assert any(
                    call.args[2] == "Builder" and "Japan" in call.args[1]
                    for call in dispatch.call_args_list
                ), dispatch.call_args_list
        finally:
            _IN_MEMORY_FS.clear()
            _IN_MEMORY_FS.update(saved_fs)
            reset_fetched_urls()
            if _orig_ws_gt is None:
                _config.cfg["settings"].pop("workspace", None)
            else:
                _config.cfg["settings"]["workspace"] = _orig_ws_gt
            if _orig_gc_gt is None:
                _config.cfg["settings"].pop("grounding_check", None)
            else:
                _config.cfg["settings"]["grounding_check"] = _orig_gc_gt
            if _orig_uc_gt is None:
                _config.cfg["settings"].pop("uneven_coverage_check", None)
            else:
                _config.cfg["settings"]["uneven_coverage_check"] = _orig_uc_gt

    contextvars.copy_context().run(_within_grounding_tier_starvation_yield_scenario)



if __name__ == "__main__":
    main()
    print("test_writer_dispatch_builder OK")
