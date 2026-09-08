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
    # --- Per-facet FindingsWriter dispatch for findings_underuses_evidence (2026-08-01): the
    # combined-instruction version (routed through _FINDINGS_WRITER_FIXABLE_PROBLEMS until this
    # fix) got the same live-confirmed negative result as report_underuses_evidence's own Builder-
    # level version, one layer upstream — FindingsWriter given a complete, well-under-budget
    # multi-facet evidence blob in ONE dispatch wrote real content for only 1 of 4 facets.
    # run_completion_check must instead dispatch ONE fresh-context FindingsWriter Write->Review->Fix
    # cycle PER dropped facet (see _dispatch_per_facet_findings_writer_fix / _findings_facet_
    # coverage), sequentially, each scoped to ONLY that facet's own findings. ---
    def _per_facet_findings_writer_dispatch_scenario():
        from tools.core import tool_quotas_ctx as q_ctx
        from unittest.mock import AsyncMock
        from engine.orchestrator import available_sub_agents_ctx

        class _FakeSubAgentConfig:
            def __init__(self, name):
                self.name = name

        _orig_ws_pfw = _config.cfg.get("settings", {}).get("workspace")
        _config.cfg["settings"]["workspace"] = {"type": "memory", "required_artifact": "final_report.md"}
        _orig_gc_pfw = _config.cfg.get("settings", {}).get("grounding_check")
        _config.cfg["settings"]["grounding_check"] = {"nli_verify": False, "topical_relevance_check": False}
        _orig_uc_pfw = _config.cfg.get("settings", {}).get("uneven_coverage_check")
        _config.cfg["settings"]["uneven_coverage_check"] = {"enabled": False}
        saved_fs = dict(_IN_MEMORY_FS)
        try:
            heur_urls = ["https://a.example.co/heur1", "https://a.example.co/heur2"]
            colo_urls = ["https://b.example.co/colo1", "https://b.example.co/colo2", "https://b.example.co/colo3"]

            # (a) one dropped facet (heuristics, real findings exist in run_state.data["findings"]
            # but findings.md only ever mentions colombia) -> exactly one facet's worth of
            # dispatches (FindingsWriter, PeerReviewer), scoped to ONLY heuristics' own findings,
            # and the fix converges.
            with tempfile.TemporaryDirectory() as tmpdir_a:
                _IN_MEMORY_FS.clear()
                # findings.md exists (required for the check to even run) but never cites any
                # heuristics URL -- the live incident's exact shape (real research, dropped anyway).
                _IN_MEMORY_FS["findings.md"] = "\n\n".join(f"### [Src]({u})\n- colombia finding." for u in colo_urls)
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
                available_sub_agents_ctx.set([_FakeSubAgentConfig("FindingsWriter"), _FakeSubAgentConfig("PeerReviewer")])
                msgs = []

                async def _side_effect_a(name, instructions, role):
                    if role == "FindingsWriter":
                        assert "heuristics" in instructions, instructions
                        assert "colombia finding" not in instructions, (
                            "the scoped evidence blob must not contain the OTHER facet's own "
                            "finding text -- that's the whole point of scoping it", instructions)
                        for u in heur_urls:
                            assert u in instructions, instructions
                        _IN_MEMORY_FS["findings.md"] += "\n\n" + "\n\n".join(
                            f"### [Src]({u})\n- heuristic finding." for u in heur_urls)
                        return "## Result\nAdded heuristics entries\n---"
                    return "REVIEW: CLEAN\nNo issues found."

                dispatch = AsyncMock(side_effect=_side_effect_a)
                orig_input = "q"
                should_retry, new_input = _asyncio.run(run_completion_check(
                    query="q", current_input=orig_input, run_state=rs, notify=msgs.append,
                    dispatch_task=dispatch))
                # NOT asserting `not should_retry` here, unlike the Builder scenario above: fixing
                # findings.md is not the LAST step in the pipeline (final_report.md still doesn't
                # exist in this minimal test setup, deliberately), so the very next completion-check
                # iteration correctly moves on to missing_artifact -- should_retry True here is
                # expected and correct, not a sign the fix didn't work. What this test verifies is
                # the DISPATCH shape: exactly one scoped FindingsWriter+PeerReviewer cycle for the
                # dropped facet, with findings.md genuinely fixed as a result.
                assert dispatch.call_count == 2, dispatch.call_args_list
                assert dispatch.call_args_list[0].args[2] == "FindingsWriter", dispatch.call_args_list
                assert dispatch.call_args_list[1].args[2] == "PeerReviewer", dispatch.call_args_list
                assert rs.data["completion_check_attempts"][0]["problem"] == "findings_underuses_evidence"
                assert rs.data["completion_check_attempts"][1]["problem"] == "missing_artifact", (
                    "findings_underuses_evidence must be genuinely RESOLVED (not re-fired) by the "
                    "next iteration -- the pipeline should move on to the next real problem, "
                    "not loop on the same one", rs.data["completion_check_attempts"])
                assert any("neglected facet" in m for m in msgs), msgs
                # Staleness marker updated after the per-facet dispatch, same as the generic branch.
                assert rs.data.get("findings_written_citable_count", 0) > 0, rs.data
                for u in heur_urls:
                    assert u in _IN_MEMORY_FS["findings.md"], "heuristics must actually be in findings.md now"

            # (b) two dropped facets (heuristics, extra) out of 3 tasks -> sequential dispatch, ONE
            # Write->Review->Fix cycle per facet (4 dispatches total), never one combined
            # instruction covering both, and each scoped instruction never leaks the other
            # dropped facet's findings either.
            with tempfile.TemporaryDirectory() as tmpdir_b:
                extra_urls = [f"https://c.example.co/extra{i}" for i in range(3)]
                _IN_MEMORY_FS.clear()
                _IN_MEMORY_FS["findings.md"] = "\n\n".join(f"### [Src]({u})\n- colombia finding." for u in colo_urls)
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
                available_sub_agents_ctx.set([_FakeSubAgentConfig("FindingsWriter"), _FakeSubAgentConfig("PeerReviewer")])
                msgs = []
                writer_calls = []

                async def _side_effect_b(name, instructions, role):
                    if role == "FindingsWriter":
                        writer_calls.append(instructions)
                        if "heuristics" in instructions:
                            urls, other_text = heur_urls, "extra finding"
                        elif "extra" in instructions:
                            urls, other_text = extra_urls, "heuristic finding"
                        else:
                            raise AssertionError(f"unexpected FindingsWriter instructions: {instructions}")
                        assert other_text not in instructions, instructions
                        assert "colombia finding" not in instructions, instructions
                        _IN_MEMORY_FS["findings.md"] += "\n\n" + "\n\n".join(
                            f"### [Src]({u})\n- finding." for u in urls)
                        return "## Result\nAdded entries\n---"
                    return "REVIEW: CLEAN\nNo issues found."

                dispatch = AsyncMock(side_effect=_side_effect_b)
                should_retry, new_input = _asyncio.run(run_completion_check(
                    query="q", current_input="q", run_state=rs, notify=msgs.append,
                    dispatch_task=dispatch))
                # Same non-assertion of `not should_retry` as scenario (a) above, same reason:
                # final_report.md still doesn't exist in this minimal setup, so the pipeline
                # correctly moves on to missing_artifact next -- that's expected, not a failure.
                assert dispatch.call_count == 4, (
                    "two dropped facets must produce two independent Write->Review->Fix cycles "
                    "(4 dispatches), not one combined instruction", dispatch.call_args_list)
                assert len(writer_calls) == 2, writer_calls
                # 'extra' sorts before 'heuristics' alphabetically -- _findings_facet_coverage's
                # `dropped` is sorted, and the dispatch loop must preserve that order.
                assert "extra" in writer_calls[0] and "heuristics" not in writer_calls[0], writer_calls
                assert "heuristics" in writer_calls[1] and "extra" not in writer_calls[1], writer_calls
        finally:
            _IN_MEMORY_FS.clear()
            _IN_MEMORY_FS.update(saved_fs)
            reset_fetched_urls()
            if _orig_ws_pfw is None:
                _config.cfg["settings"].pop("workspace", None)
            else:
                _config.cfg["settings"]["workspace"] = _orig_ws_pfw
            if _orig_gc_pfw is None:
                _config.cfg["settings"].pop("grounding_check", None)
            else:
                _config.cfg["settings"]["grounding_check"] = _orig_gc_pfw
            if _orig_uc_pfw is None:
                _config.cfg["settings"].pop("uneven_coverage_check", None)
            else:
                _config.cfg["settings"]["uneven_coverage_check"] = _orig_uc_pfw

    contextvars.copy_context().run(_per_facet_findings_writer_dispatch_scenario)

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

            # (b2) findings_ungrounded via the SPECIFIC-URL gate (partially_ungrounded's
            # unverified_entry_sources, not fully_ungrounded's wholesale no_urls) must name the
            # exact bad URL in FindingsWriter's rebuild instructions, not just say "it was
            # ungrounded" generically (2026-08-18 live incident: findings.md.rejected_attempt_3
            # and _4 were byte-identical 11 minutes apart — FindingsWriter kept re-citing the
            # SAME hallucinated URL because the old directive never named it).
            with tempfile.TemporaryDirectory() as tmpdir_b2:
                _bad_url = "https://never-fetched.example.com/fake"
                _IN_MEMORY_FS["findings.md"] = (
                    f"### [Real Finding]({_SRC}) [PRIMARY]\n- Key Findings: real.\n\n"
                    f"### [Fake Finding]({_bad_url}) [SECONDARY]\n- Key Findings: invented."
                )
                _IN_MEMORY_FS["final_report.md"] = _CLEAN_REPORT
                rs = RunState(tmpdir_b2)
                rs.add_finding(_SRC, "the real finding a dispatched Searcher actually returned")
                run_state_ctx.set(rs)
                msgs = []

                async def _side_effect_b2(name, instructions, role):
                    if role == "FindingsWriter":
                        _IN_MEMORY_FS["findings.md"] = _FINDINGS_OK
                        return "## Result for FindingsWriterFix\nWrote findings.md\n---"
                    return "REVIEW: CLEAN"

                dispatch = AsyncMock(side_effect=_side_effect_b2)
                _asyncio.run(run_completion_check(
                    query="q", current_input="q", run_state=rs, notify=msgs.append,
                    dispatch_task=dispatch))
                write_instructions = dispatch.call_args_list[0].args[1]
                assert dispatch.call_args_list[0].args[2] == "FindingsWriter", dispatch.call_args_list
                assert _bad_url in write_instructions, (
                    "FindingsWriter's rebuild instructions must name the SPECIFIC URL that failed "
                    "verification, not just say 'it was ungrounded' -- otherwise a retry has no "
                    "signal to avoid re-citing the exact same hallucinated source", write_instructions)
                assert "do not attribute any finding to these again" in write_instructions, write_instructions
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
                # "Wait." prefix (2026-08-17, RESEARCH.md §18b, Self-Correction Bench
                # arXiv:2507.02778): the Fix-pass dispatch's own instructions must lead with it.
                assert dispatch.call_args_list[2].args[1].startswith("Wait."), (
                    "Fix-pass instructions must start with the 'Wait.' deliberation cue",
                    dispatch.call_args_list[2].args[1][:60])

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



if __name__ == "__main__":
    main()
    print("test_writer_dispatch_findings OK")
