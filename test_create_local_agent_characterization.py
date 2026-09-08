import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from utils.run_state import record_fetched_url

# noqa: F401 -- common test-infra names available to every split file below, regardless of
# whether a given file's own retained sections happen to use all of them (ruff prunes genuinely
# unused ones per file). Split 2026-09-07 out of the former single 10,701-line
# test_structural_checks.py (session_status/CURRENT.md carried-forward TODO) -- see
# test_structural_checks.py's own new header for the full split rationale and the file-to-topic
# map. Pure move: every assertion below is byte-identical to its prior body, just regrouped by
# topic into its own main(), all still called in original order from the new thin
# test_structural_checks.py orchestrator.

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
    # --- create_local_agent characterization tests (ROADMAP.md Pending: "create_local_agent's
    # nested-closure god-function... zero direct test coverage"). These pin the CURRENT behavior
    # of delegate_tasks' pre-dispatch validation gauntlet and _run_single_task's bad-agent_id early
    # return -- the exact two closures the Pending entry names -- as a safety net before any future
    # decomposition attempt, per that entry's own "characterization tests first" recommendation.
    # Deliberately scoped to paths that reject/return BEFORE any real sub_agent.run() dispatch, so
    # these run with zero network/model calls -- true end-to-end dispatch (the asyncio.gather over
    # real sub-agent streams) is out of scope here and would need SDK-level mocking to add safely. ---
    def _create_local_agent_characterization_scenario():
        import asyncio as _asyncio_cla
        import contextvars as _contextvars_cla
        import tempfile as _tempfile_cla
        import config
        from unittest.mock import patch as _patch_cla
        from engine.orchestrator import (
            create_local_agent, delegation_depth_ctx, specialist_delegate_task_count_ctx,
        )
        from engine.sdk import AgentBuilder, SubAgentConfig
        from utils.run_state import task_fetched_urls_ctx

        def _get_delegate_tasks_tool(builder):
            # delegate_tasks is a closure private to create_local_agent, never returned directly
            # (only agent/session/_run_single_task are) -- capture it off the `tools` kwarg the
            # SDK's own as_agent() receives, the one place the closure object crosses a boundary
            # this test can intercept.
            from agent_framework.openai import OpenAIChatCompletionClient
            captured = {}
            orig_as_agent = OpenAIChatCompletionClient.as_agent

            def _patched(self, *args, **kwargs):
                captured["tools"] = kwargs.get("tools")
                return orig_as_agent(self, *args, **kwargs)

            with _patch_cla.object(OpenAIChatCompletionClient, "as_agent", _patched):
                agent, session, dispatch_task = create_local_agent(builder=builder)
            dt = next(t for t in captured["tools"] if getattr(t, "name", None) == "delegate_tasks")
            return dt, dispatch_task

        def _scenario():
            sub = SubAgentConfig(name="WebSearcher", instructions="x {date} {task_name}", tools=[])
            builder = AgentBuilder(
                name="Planner", description="d",
                instructions=(
                    "i {date} {workspace_dir} {delegation_instructions} "
                    "{report_style_instructions} {citation_format_instructions}"
                ),
                tools=[], sub_agents=[sub],
            )
            dt, dispatch_task = _get_delegate_tasks_tool(builder)

            async def _run_checks():
                # Malformed schema (wrong field names) -> rejected wholesale, no dispatch.
                r = await dt.func(tasks=[{"due": 1, "task": "x"}])
                assert "missing or empty required field" in r, r

                # Unresolved numeric/letter placeholder in instructions -> rejected.
                r = await dt.func(tasks=[{
                    "task_name": "t1", "instructions": "Analyze sector 3 in Colombia.",
                    "agent_id": "WebSearcher",
                }])
                assert "unresolved placeholder" in r, r

                # Pronoun with no concrete subject -> rejected.
                r = await dt.func(tasks=[{
                    "task_name": "t1", "instructions": "Summarize its headline feature.",
                    "agent_id": "WebSearcher",
                }])
                assert "no concrete subject" in r, r

                # Cross-task dependency phrasing ("for each identified ...") -> rejected, since
                # delegate_tasks' own batch runs concurrently, not sequentially.
                r = await dt.func(tasks=[{
                    "task_name": "t1",
                    "instructions": "For each identified sector, analyze the market size.",
                    "agent_id": "WebSearcher",
                }])
                assert "SEQUENTIAL" in r, r

                # Analyzer instructed to read a URL never fetched this task -> rejected.
                r = await dt.func(tasks=[{
                    "task_name": "t2",
                    "instructions": "Read https://never-fetched.example.com/page for details.",
                    "agent_id": "DocumentAnalyzer",
                }])
                assert "was never actually fetched" in r, r

                # Analyzer instructed with a GUESSED filename for a URL that WAS really fetched
                # (vs. the real hash-suffixed filename fetch_url_to_workspace actually returned).
                task_fetched_urls_ctx.set([{
                    "url": "https://real.example.com/page",
                    "filename": "sources/real_example_abc123.md",
                }])
                r = await dt.func(tasks=[{
                    "task_name": "t4",
                    "instructions": (
                        "Read the file 'sources/real_example_page.md'. "
                        "Source URL: https://real.example.com/page"
                    ),
                    "agent_id": "DocumentAnalyzer",
                }])
                assert "was GUESSED" in r, r
                task_fetched_urls_ctx.set([])

                # Every task in the batch matches an explicit query exclusion -> full rejection,
                # nothing dispatched (distinct code path from the per-task validation errors above).
                r = await dt.func(tasks=[{
                    "task_name": "t3",
                    "instructions": "Research the Agritech sector market size.",
                    "agent_id": "WebSearcher",
                }])
                assert "explicitly excluded" in r, r

                # Planner replan-round hard cap (depth==0) -- rejected once the round count already
                # meets the configured cap, no quota consumed.
                _orig_cap = config.cfg["settings"].get("max_planner_delegate_rounds")
                config.cfg["settings"]["max_planner_delegate_rounds"] = 1
                try:
                    rs = run_state_ctx.get()
                    rs.data["planner_delegate_rounds"] = 1
                    r = await dt.func(tasks=[{
                        "task_name": "t5", "instructions": "Research fintech market size in Peru.",
                        "agent_id": "WebSearcher",
                    }])
                    assert "already run 1 planning round" in r, r
                finally:
                    if _orig_cap is None:
                        config.cfg["settings"].pop("max_planner_delegate_rounds", None)
                    else:
                        config.cfg["settings"]["max_planner_delegate_rounds"] = _orig_cap

                # Specialist per-task delegation cap (depth>0, a Tier-2 specialist's own
                # delegate_tasks call to its Analyzer children) -- separate cap/code path from the
                # Planner's round cap above.
                _orig_scap = config.cfg["settings"].get("specialist_delegation_cap")
                config.cfg["settings"]["specialist_delegation_cap"] = 1
                depth_token = delegation_depth_ctx.set(1)
                count_token = specialist_delegate_task_count_ctx.set([1])
                try:
                    r = await dt.func(tasks=[{
                        "task_name": "t6", "instructions": "Analyze a second market report.",
                        "agent_id": "DocumentAnalyzer",
                    }])
                    assert "already delegated 1 source" in r, r
                finally:
                    delegation_depth_ctx.reset(depth_token)
                    specialist_delegate_task_count_ctx.reset(count_token)
                    if _orig_scap is None:
                        config.cfg["settings"].pop("specialist_delegation_cap", None)
                    else:
                        config.cfg["settings"]["specialist_delegation_cap"] = _orig_scap

                # _run_single_task (the dispatch_task closure): a hallucinated agent_id not matching
                # any real sub-agent must hit the early-return error path cleanly, no crash -- this
                # is the exact path a past latent bug lived in (children_token unset on this path
                # made the `finally` block's reset crash with UnboundLocalError, see the closure's
                # own header comment on `children_token`).
                r = await dispatch_task("t7", "instr", agent_id="NonExistentAgent")
                assert "does not exist" in r, r
                assert "Available sub-agents" in r, r

            with _tempfile_cla.TemporaryDirectory() as td:
                rs = RunState(td)
                rs.data["query"] = "Research Colombia markets, excluding Agritech."
                tok = run_state_ctx.set(rs)
                try:
                    _asyncio_cla.run(_run_checks())
                finally:
                    run_state_ctx.reset(tok)

        _contextvars_cla.copy_context().run(_scenario)

    _create_local_agent_characterization_scenario()

    # --- _run_single_task streaming-loop characterization tests (extends the coverage above into
    # the `while has_requests` loop itself -- malformed-tool-call nudges, budget nudges,
    # zero-synthesis nudges, deadline extension -- the part the entry above explicitly flagged as
    # still uncovered). Needs a fake streaming sub-agent since the real loop drives
    # `sub_agent.run(...).__aiter__()`; patches `OpenAIChatCompletionClient.as_agent` to return a
    # scripted fake for the ENTIRE create+dispatch call (not just agent construction) -- an earlier
    # draft of this harness left the patch's `with` block around only `create_local_agent()` and
    # let the dispatch itself fall through to the real backend, which actually round-tripped a live
    # Ollama call before being caught; keep the patch scoped around the whole dispatch. ---
    def _run_single_task_streaming_characterization_scenario():
        import asyncio as _asyncio_st
        import contextvars as _contextvars_st
        import tempfile as _tempfile_st
        import time as _time_st
        import config
        from unittest.mock import patch as _patch_st
        from engine.orchestrator import create_local_agent
        from engine.sdk import AgentBuilder, SubAgentConfig

        class _FakeContent:
            def __init__(self, type, text=None, result=None):
                self.type = type
                self.text = text
                self.result = result

        class _FakeUpdate:
            def __init__(self, contents=None, user_input_requests=None):
                self.contents = contents or []
                self.user_input_requests = user_input_requests

        class _FakeStream:
            """One `sub_agent.run(...)` turn's worth of updates, or an exception to raise on the
            first `__anext__` (malformed-tool-call path), or a `sleep` before ever yielding
            (deadline path) -- mirrors the real stream's async-iterator shape closely enough to
            drive `_run_single_task`'s manually-driven __anext__ + asyncio.wait_for loop."""
            def __init__(self, updates=None, raise_exc=None, sleep=0):
                self._updates = list(updates or [])
                self._raise_exc = raise_exc
                self._sleep = sleep
                self._raised = False

            def __aiter__(self):
                return self

            async def __anext__(self):
                if self._sleep:
                    await _asyncio_st.sleep(self._sleep)
                if self._raise_exc is not None and not self._raised:
                    self._raised = True
                    raise self._raise_exc
                if not self._updates:
                    raise StopAsyncIteration
                return self._updates.pop(0)

        class _FakeSubAgent:
            """One scripted `_FakeStream` per `.run()` call (i.e. per internal retry turn)."""
            def __init__(self, turns):
                self._turns = list(turns)

            def run(self, current_input, stream=True):
                return self._turns.pop(0)

            def create_session(self):
                return None  # settings.enable_conversational_memory defaults True

        def _patch_as_agent(fake_agent):
            from agent_framework.openai import OpenAIChatCompletionClient
            def _patched(self, *a, **k):
                return fake_agent
            return _patch_st.object(OpenAIChatCompletionClient, "as_agent", _patched)

        def _make_builder():
            sub = SubAgentConfig(name="WebSearcher", instructions="x {date} {task_name}", tools=[])
            return AgentBuilder(
                name="Planner", description="d",
                instructions=(
                    "i {date} {workspace_dir} {delegation_instructions} "
                    "{report_style_instructions} {citation_format_instructions}"
                ),
                tools=[], sub_agents=[sub],
            )

        async def _dispatch_within_patch(fake_agent, task_name):
            # The patch MUST stay active across both create_local_agent() (which builds the
            # Planner agent) and the dispatch call itself (which builds a FRESH sub-agent per
            # dispatch via the same as_agent) -- see this scenario's own header note.
            with _patch_as_agent(fake_agent):
                agent, session, dispatch_task = create_local_agent(builder=_make_builder())
                with _tempfile_st.TemporaryDirectory() as td:
                    rs = RunState(td)
                    tok = run_state_ctx.set(rs)
                    try:
                        return await dispatch_task(task_name, "instr", agent_id="WebSearcher")
                    finally:
                        run_state_ctx.reset(tok)

        async def _run_checks():
            # Malformed tool-call JSON on turn 1 -> one-turn nudge retry -> turn 2 succeeds.
            fake = _FakeSubAgent([
                _FakeStream(raise_exc=Exception("Error parsing tool call: bad escape")),
                _FakeStream(updates=[_FakeUpdate(contents=[
                    _FakeContent("text", text="Recovered findings from a real source.")])]),
            ])
            r = await _dispatch_within_patch(fake, "t_malformed")
            assert "Recovered findings from a real source." in r, r

            # Turn 1 calls a tool but returns zero narration text -> zero-synthesis nudge ->
            # turn 2 actually synthesizes. This is the exact path that (before this session's fix)
            # crashed with UnboundLocalError on `Message` -- three sibling branches in
            # _run_single_task each did their own `from agent_framework import Message` inside the
            # function body, which makes Python treat `Message` as function-local EVERYWHERE in
            # that function, so this fourth branch (the only one without its own local import)
            # threw instead of nudging whenever it fired as a dispatch's first retry. Fixed by
            # deleting the three redundant local imports (Message is already imported at module
            # scope) rather than adding a fourth copy — same class of bug can't recur in a future
            # branch now that nothing shadows the module-level name. This assertion is what
            # actually catches a regression of that shadowing, not just the pure predicate.
            fake = _FakeSubAgent([
                _FakeStream(updates=[_FakeUpdate(contents=[
                    _FakeContent("function_result", result="Tool ran, found something.")])]),
                _FakeStream(updates=[_FakeUpdate(contents=[
                    _FakeContent("text", text="Real synthesized findings: X=42 (source: example).")])]),
            ])
            r = await _dispatch_within_patch(fake, "t_zero_synthesis")
            assert "Real synthesized findings: X=42" in r, r

            # Turn 1 overflows context_budget_chars -> one wrap-up nudge (with the softened/
            # alarming cutoff marker chosen by real fetch count) -> turn 2 wraps up.
            _orig_budget = config.cfg["settings"].get("context_budget_chars")
            config.cfg["settings"]["context_budget_chars"] = 10
            try:
                fake = _FakeSubAgent([
                    _FakeStream(updates=[_FakeUpdate(contents=[_FakeContent("text", text="A" * 50)])]),
                    _FakeStream(updates=[_FakeUpdate(contents=[
                        _FakeContent("text", text=" Final wrap-up findings.")])]),
                ])
                r = await _dispatch_within_patch(fake, "t_budget")
                assert "hit its context budget" in r, r
                assert "Final wrap-up findings." in r, r
            finally:
                if _orig_budget is None:
                    config.cfg["settings"].pop("context_budget_chars", None)
                else:
                    config.cfg["settings"]["context_budget_chars"] = _orig_budget

            # sub_agent_timeout_minutes fires on a stream that goes silent past its deadline (same
            # manually-driven __anext__ + asyncio.wait_for mechanism as engine/tui.py's run_cli,
            # 2026-07-12) -- no real sources fetched, so _try_extend_deadline_once's ring-fence
            # must NOT extend it, and the cutoff must fire close to the deadline, not wait out the
            # stream's full (much longer) sleep.
            _orig_timeout = config.cfg["settings"].get("sub_agent_timeout_minutes")
            config.cfg["settings"]["sub_agent_timeout_minutes"] = 0.2 / 60.0
            try:
                fake = _FakeSubAgent([_FakeStream(sleep=5)])
                start = _time_st.monotonic()
                r = await _dispatch_within_patch(fake, "t_deadline")
                elapsed = _time_st.monotonic() - start
                assert "cut short" in r, r
                assert "sub_agent_timeout_minutes" in r, r
                assert elapsed < 3, f"cutoff must fire near the 0.2s deadline, not the 5s stall ({elapsed}s)"
            finally:
                if _orig_timeout is None:
                    config.cfg["settings"].pop("sub_agent_timeout_minutes", None)
                else:
                    config.cfg["settings"]["sub_agent_timeout_minutes"] = _orig_timeout

        def _scenario():
            _asyncio_st.run(_run_checks())

        _contextvars_st.copy_context().run(_scenario)

    _run_single_task_streaming_characterization_scenario()

    # --- _run_single_task post-loop characterization tests: the grounding-check branches and
    # finding-storage wiring that run AFTER the `while has_requests` loop ends (still flagged open
    # by the entry above -- "real_grounding_problem, NLI/reranker... still untested directly").
    # Mocks utils.grounding.real_grounding_problem itself (an AsyncMock) rather than the NLI/
    # reranker models underneath it -- exercising the real model calls would need the local NLI/
    # reranker weights loaded per-run, which the pure-function tests elsewhere in this file already
    # avoid; this pass verifies real_grounding_problem's RETURN VALUE is correctly threaded into
    # verification_warnings and RunState.add_finding, not the entailment model's own correctness
    # (real_grounding_problem has its own coverage for that). Scope-relevance
    # (verify_scope_relevance) is a separate check from real_grounding_problem and still NOT
    # covered here -- it needs a real workspace-file content fixture, left for a future pass. ---
    def _run_single_task_post_loop_characterization_scenario():
        import asyncio as _asyncio_pl
        import contextvars as _contextvars_pl
        import tempfile as _tempfile_pl
        import config
        from unittest.mock import patch as _patch_pl, AsyncMock as _AsyncMock_pl
        from engine.orchestrator import create_local_agent
        from engine.sdk import AgentBuilder, SubAgentConfig
        import utils.grounding as _grounding_mod_pl

        class _FakeContentPl:
            def __init__(self, type, text=None, result=None):
                self.type = type
                self.text = text
                self.result = result

        class _FakeUpdatePl:
            def __init__(self, contents=None, user_input_requests=None, side_effect=None):
                self.contents = contents or []
                self.user_input_requests = user_input_requests
                # Optional zero-arg callable run when this update is consumed, in the SAME
                # coroutine/contextvars.Context as the dispatch itself -- lets a scripted update
                # simulate a real tool's side effect (e.g. record_fetched_url), since
                # task_fetched_urls_ctx is reset fresh at the start of every real dispatch and
                # can't be pre-seeded from outside it.
                self.side_effect = side_effect

        class _FakeStreamPl:
            def __init__(self, updates=None):
                self._updates = list(updates or [])

            def __aiter__(self):
                return self

            async def __anext__(self):
                if not self._updates:
                    raise StopAsyncIteration
                u = self._updates.pop(0)
                if u.side_effect:
                    u.side_effect()
                return u

        class _FakeSubAgentPl:
            def __init__(self, turns):
                self._turns = list(turns)

            def run(self, current_input, stream=True):
                return self._turns.pop(0)

            def create_session(self):
                return None

        def _patch_as_agent_pl(fake_agent):
            from agent_framework.openai import OpenAIChatCompletionClient
            def _patched(self, *a, **k):
                return fake_agent
            return _patch_pl.object(OpenAIChatCompletionClient, "as_agent", _patched)

        async def _dispatch_pl(fake_agent, task_name, instructions, agent_id, sub_agents_children=None):
            sub = SubAgentConfig(
                name=agent_id, instructions="y {date} {task_name}", tools=[],
                sub_agents=sub_agents_children or [],
            )
            builder = AgentBuilder(
                name="Planner", description="d",
                instructions=(
                    "i {date} {workspace_dir} {delegation_instructions} "
                    "{report_style_instructions} {citation_format_instructions}"
                ),
                tools=[], sub_agents=[sub],
            )
            with _patch_as_agent_pl(fake_agent):
                agent, session, dispatch_task = create_local_agent(builder=builder)
                with _tempfile_pl.TemporaryDirectory() as td:
                    rs = RunState(td)
                    tok = run_state_ctx.set(rs)
                    try:
                        r = await dispatch_task(task_name, instructions, agent_id=agent_id)
                    finally:
                        run_state_ctx.reset(tok)
            return r, rs

        async def _run_checks():
            # Searcher-tier (target_children truthy) grounding check: real_grounding_problem
            # flags the summary -> SYSTEM VERIFICATION WARNING appended to the result AND to the
            # stored finding's summary, and the finding is keyed to the URL named in the task's
            # own instructions (reference_urls fallback -- no real fetch happened this dispatch).
            fake = _FakeSubAgentPl([_FakeStreamPl(updates=[_FakeUpdatePl(contents=[_FakeContentPl(
                "text", text="Found a report at https://example.com/report with key figures.")])])])
            child = SubAgentConfig(name="DocumentAnalyzer", instructions="z", tools=[])
            with _patch_pl.object(_grounding_mod_pl, "real_grounding_problem",
                                   new=_AsyncMock_pl(return_value="fake_problem_reason")):
                r, rs = await _dispatch_pl(
                    fake, "t_search_ground", "Research https://example.com/report",
                    "WebSearcher", sub_agents_children=[child],
                )
            assert "SYSTEM VERIFICATION WARNING" in r, r
            assert any(
                f["source_url"] == "https://example.com/report"
                and "SYSTEM VERIFICATION WARNING" in f["summary"]
                for f in rs.data["findings"]
            ), rs.data["findings"]

            # Analyzer-tier (DocumentAnalyzer/DataAnalyzer, no children) reconstructed-URL check:
            # the summary cites a DIFFERENT URL than the one the task's own instructions named as
            # the real source -- a guessed/hallucinated citation, not the URL actually handed to
            # this Analyzer. real_grounding_problem mocked clean (None) so only this check's own
            # warning is under test, isolated from the content-level check that runs after it in
            # the same branch.
            fake2 = _FakeSubAgentPl([_FakeStreamPl(updates=[_FakeUpdatePl(contents=[_FakeContentPl(
                "text", text="According to https://fake-reconstructed.example.com/page, the figure is 42.")])])])
            with _patch_pl.object(_grounding_mod_pl, "real_grounding_problem",
                                   new=_AsyncMock_pl(return_value=None)):
                r2, _rs2 = await _dispatch_pl(
                    fake2, "t_analyzer",
                    "Read the file 'sources/real_page.md'. Source URL: https://real.example.com/page",
                    "DocumentAnalyzer",
                )
            assert "looks like a reconstructed or guessed URL" in r2, r2

            # Scope-relevance check (verify_scope_relevance, the last still-uncovered branch this
            # entry flagged): requires a REAL fetch this dispatch (new_urls, not reference_urls),
            # so task_fetched_urls_ctx must be populated DURING the fake stream via
            # record_fetched_url -- a plain pre-set before dispatch would be silently wiped, since
            # _dispatch_single_task resets that contextvar to [] at the start of every real task.
            # Needs settings.workspace switched to "memory" so get_workspace_file_content reads
            # _IN_MEMORY_FS instead of real disk (default workspace type is "disk").
            _orig_workspace_pl = config.cfg["settings"].get("workspace")
            config.cfg["settings"]["workspace"] = {"type": "memory"}
            saved_fs_pl = dict(_IN_MEMORY_FS)
            try:
                _IN_MEMORY_FS.clear()

                # (a) fetched content does NOT mention the required scope entity -> flagged.
                _IN_MEMORY_FS["sources/mismatch.md"] = "This page discusses general fintech trends worldwide."
                child_a = SubAgentConfig(name="DocumentAnalyzer", instructions="z", tools=[])
                fake_mismatch = _FakeSubAgentPl([_FakeStreamPl(updates=[
                    _FakeUpdatePl(
                        contents=[_FakeContentPl("function_result", result="fetched")],
                        side_effect=lambda: record_fetched_url(
                            "https://example.com/fintech", "sources/mismatch.md"),
                    ),
                    _FakeUpdatePl(contents=[_FakeContentPl("text", text="Found relevant fintech data.")]),
                ])])
                with _patch_pl.object(_grounding_mod_pl, "real_grounding_problem",
                                       new=_AsyncMock_pl(return_value=None)):
                    r3, _rs3 = await _dispatch_pl(
                        fake_mismatch, "t_scope_mismatch", "Research the fintech market in Colombia.",
                        "WebSearcher", sub_agents_children=[child_a],
                    )
                assert "SYSTEM RELEVANCE WARNING" in r3, r3
                assert "Colombia" in r3, r3

                # (b) fetched content DOES mention it -> no false-positive on a genuinely on-topic
                # source, isolated from case (a) via a fresh _IN_MEMORY_FS entry/filename.
                _IN_MEMORY_FS["sources/match.md"] = (
                    "This report covers fintech adoption trends in Colombia specifically.")
                child_b = SubAgentConfig(name="DocumentAnalyzer", instructions="z", tools=[])
                fake_match = _FakeSubAgentPl([_FakeStreamPl(updates=[
                    _FakeUpdatePl(
                        contents=[_FakeContentPl("function_result", result="fetched")],
                        side_effect=lambda: record_fetched_url(
                            "https://example.com/fintech-co", "sources/match.md"),
                    ),
                    _FakeUpdatePl(contents=[_FakeContentPl(
                        "text", text="Found relevant fintech data for Colombia.")]),
                ])])
                with _patch_pl.object(_grounding_mod_pl, "real_grounding_problem",
                                       new=_AsyncMock_pl(return_value=None)):
                    r4, _rs4 = await _dispatch_pl(
                        fake_match, "t_scope_match", "Research the fintech market in Colombia.",
                        "WebSearcher", sub_agents_children=[child_b],
                    )
                assert "SYSTEM RELEVANCE WARNING" not in r4, r4
            finally:
                _IN_MEMORY_FS.clear()
                _IN_MEMORY_FS.update(saved_fs_pl)
                if _orig_workspace_pl is None:
                    config.cfg["settings"].pop("workspace", None)
                else:
                    config.cfg["settings"]["workspace"] = _orig_workspace_pl

        def _scenario():
            _asyncio_pl.run(_run_checks())

        _contextvars_pl.copy_context().run(_scenario)

    _run_single_task_post_loop_characterization_scenario()



if __name__ == "__main__":
    main()
    print("test_create_local_agent_characterization OK")
