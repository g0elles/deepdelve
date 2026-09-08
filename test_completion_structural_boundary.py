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
    Ctx, run_completion_check,
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
    # --- check_requested_count_shortfall boundary conditions (2026-08-28) -- the matrix row above
    # pins the full run_completion_check integration; this pins _extract_requested_item_range's own
    # parsing plus the check's conservative-by-construction thresholds directly. ---
    def _requested_count_shortfall_boundary_scenario():
        from engine.completion import check_requested_count_shortfall, _extract_requested_item_range

        # Parsing: only a small set of list-request verbs + a number/range count; everything else
        # (including a query that happens to contain an unrelated number) must return None.
        assert _extract_requested_item_range("Identify 4 to 6 niches.") == (4, 6)
        assert _extract_requested_item_range("List at least 3 examples.") == (3, 3)
        assert _extract_requested_item_range("Name 5 companies.") == (5, 5)
        assert _extract_requested_item_range("What regulation is Ley 1906 de 2021?") is None
        assert _extract_requested_item_range("") is None
        assert _extract_requested_item_range(None) is None

        with tempfile.TemporaryDirectory() as tmpdir_rcs:
            def _ctx_with(query: str, task_count: int) -> "Ctx":
                rs = RunState(tmpdir_rcs + f"/{query[:5]}{task_count}")
                rs.set_query(query)
                for i in range(task_count):
                    rs.add_finding(f"https://gov.example.co/n{i}", "real content.",
                                    task_name=f"niche_{i}", depth=1)
                return Ctx(req_artifact="final_report.md", attempt=0, max_attempts=8,
                           delegated=True, files=[], content=None, quotas={}, run_state=rs)

            # (a) No explicit count in the query -> never engages, regardless of task count.
            assert check_requested_count_shortfall(_ctx_with("Research Colombian B2B niches.", 1)) is None

            # (b) Explicit count but below the floor>=3 minimum engagement threshold (a 1-2 item ask
            # is well within "one task can cover it" territory) -> silent even with 0 tasks.
            assert check_requested_count_shortfall(_ctx_with("Identify 2 niches.", 0)) is None

            # (c) floor>=3, but the shortfall is a near-miss (< 2) -> silent, only clear shortfalls fire.
            assert check_requested_count_shortfall(_ctx_with("Identify 4 to 6 niches.", 3)) is None

            # (d) floor>=3, clear shortfall (>= 2) -> fires, naming the real floor and delegated count.
            v = check_requested_count_shortfall(_ctx_with("Identify 4 to 6 niches.", 2))
            assert v is not None and v.problem == "requested_count_shortfall", v
            assert "4" in v.inject and "2" in v.inject, v.inject

            # (e) floor met exactly -> silent (compares against the RANGE FLOOR, not the ceiling --
            # "4 to 6" is satisfied by 4, not held to the higher end).
            assert check_requested_count_shortfall(_ctx_with("Identify 4 to 6 niches.", 4)) is None

    contextvars.copy_context().run(_requested_count_shortfall_boundary_scenario)

    # --- check_missing_query_facet boundary conditions (2026-08-29) -- the matrix row above pins
    # the full run_completion_check integration; this pins the check's own conservative gates and
    # the exclusion-clause false-positive guard directly. ---
    def _missing_query_facet_boundary_scenario():
        from engine.completion import check_missing_query_facet

        with tempfile.TemporaryDirectory() as tmpdir_mqf:
            def _ctx_with(query: str, dispatched: list) -> "Ctx":
                rs = RunState(tmpdir_mqf + f"/{abs(hash((query, len(dispatched))))}")
                rs.set_query(query)
                rs.data["dispatched_tasks"] = dispatched
                return Ctx(req_artifact="final_report.md", attempt=0, max_attempts=8,
                           delegated=True, files=[], content=None, quotas={}, run_state=rs)

            lisbon_only = [{"task_name": "lisbon_rent",
                            "instructions": "Research rent prices in Lisbon."}]
            both_covered = lisbon_only + [{"task_name": "mexico_city_rent",
                            "instructions": "Research rent prices in Mexico City."}]

            # (a) Query names fewer than 2 unambiguous facets -> never engages, regardless of
            # what's dispatched.
            assert check_missing_query_facet(
                _ctx_with("Research Colombian B2B niches.", lisbon_only)) is None

            # (b) 2+ facets named, but nothing dispatched yet -> silent, check_not_delegated owns
            # that state instead.
            assert check_missing_query_facet(
                _ctx_with("Compare rents in Lisbon and Mexico City.", [])) is None

            # (c) Both facets covered -> silent.
            assert check_missing_query_facet(
                _ctx_with("Compare rents in Lisbon and Mexico City.", both_covered)) is None

            # (d) One facet missing -> fires, naming the missing facet.
            v = check_missing_query_facet(
                _ctx_with("Compare rents in Lisbon and Mexico City.", lisbon_only))
            assert v is not None and v.problem == "missing_query_facet", v
            assert "Mexico" in v.inject, v.inject

            # (e) Exclusion-clause guard: a task instruction that restates the missing facet only
            # to rule it OUT of scope must not count as covering it.
            excluded_mexico = lisbon_only + [{"task_name": "scope_note",
                "instructions": "Focus on Lisbon; excluding Mexico City, that's out of scope here."}]
            v2 = check_missing_query_facet(
                _ctx_with("Compare rents in Lisbon and Mexico City.", excluded_mexico))
            assert v2 is not None and v2.problem == "missing_query_facet", v2

            # (f) Escalated wording on the second consecutive occurrence.
            ctx3 = _ctx_with("Compare rents in Lisbon and Mexico City.", lisbon_only)
            ctx3.run_state.data["completion_check_attempts"] = [{"problem": "missing_query_facet"}]
            v3 = check_missing_query_facet(ctx3)
            assert v3 is not None and "after a prior warning" in v3.inject, v3.inject

    contextvars.copy_context().run(_missing_query_facet_boundary_scenario)

    # --- check_no_urls/check_non_url_citation/check_uncited_claims: style-aware citation-format
    # guidance (2026-08-24 live incident). Before this fix, all three hardcoded standard style's
    # `[Title](URL)` markdown-link format into their corrective directive regardless of
    # ctx.report_style (Ctx had no such field at all) -- confirmed live: a real --style academic
    # run oscillated between non_url_citation and claim_unsupported for ~18 completion-check
    # attempts across two live runs (~93 minutes combined) because every retry told the model to
    # switch to inline markdown links, directly contradicting ACADEMIC_CITATION_FORMAT_
    # INSTRUCTIONS' own (Author, Year) + References requirement. Pins that each check's `.inject`
    # text now names the CORRECT format per style, and that the pre-existing `.warning` phrases
    # the verdict-matrix rows above already pin are untouched (style-blind, as they should stay).
    def _style_aware_citation_guidance_scenario():
        from engine.completion import check_no_urls, check_non_url_citation, check_uncited_claims

        with tempfile.TemporaryDirectory() as tmpdir_saf:
            rs = RunState(tmpdir_saf)
            rs.set_query("q")
            base_kwargs = dict(req_artifact="final_report.md", attempt=0, max_attempts=8,
                                delegated=True, files=[], content=None, quotas={}, run_state=rs)

            # check_no_urls
            academic_ctx = Ctx(grounding_problem="no_urls", report_style="academic", **base_kwargs)
            v = check_no_urls(academic_ctx)
            assert "(Author, Year)" in v.inject and "References" in v.inject, v.inject
            assert "[Title](URL)" not in v.inject or "NOT an inline" in v.inject, v.inject
            assert v.warning == "`final_report.md` contains zero hyperlinked sources — no citations at all. Pushing agent to add real ones.", v.warning

            standard_ctx = Ctx(grounding_problem="no_urls", report_style="standard", **base_kwargs)
            v = check_no_urls(standard_ctx)
            assert "`- **[Title](URL)**`" in v.inject, v.inject
            assert "(Author, Year)" not in v.inject, v.inject

            # check_non_url_citation
            academic_ctx2 = Ctx(grounding_problem="non_url_citation:(DANE, 2020)",
                                 report_style="academic", **base_kwargs)
            v = check_non_url_citation(academic_ctx2)
            assert "(Author, Year)" in v.inject and "References" in v.inject, v.inject
            assert "do NOT switch to inline markdown links" in v.inject, v.inject

            standard_ctx2 = Ctx(grounding_problem="non_url_citation:(DANE, 2020)",
                                 report_style="standard", **base_kwargs)
            v = check_non_url_citation(standard_ctx2)
            assert "`- **[Title](URL)**`" in v.inject, v.inject

            # check_uncited_claims
            academic_ctx3 = Ctx(grounding_problem="uncited_claims:3 lines", report_style="academic",
                                 **base_kwargs)
            v = check_uncited_claims(academic_ctx3)
            assert "(Author, Year)" in v.inject, v.inject

            standard_ctx3 = Ctx(grounding_problem="uncited_claims:3 lines", report_style="standard",
                                 **base_kwargs)
            v = check_uncited_claims(standard_ctx3)
            assert "`- **[Title](URL)**`" in v.inject, v.inject

            # Default report_style (no caller passes it, e.g. an older test/caller) must still
            # behave exactly like "standard" -- Ctx's own field default, matching
            # config_template.yaml's default.
            default_ctx = Ctx(grounding_problem="no_urls", **base_kwargs)
            assert default_ctx.report_style == "standard"

    _style_aware_citation_guidance_scenario()

    # --- check_untracked_delegation (2026-07-22): the Planner dispatching delegate_tasks BEFORE
    # write_todos for that slot -- confirmed live, a 'background' task burned 14 web_search calls
    # with no matching _todos.md entry, then got redispatched properly as 'background_heuristics'.
    # Distinct from check_not_delegated (zero delegation) and check_thin_coverage (breadth failed);
    # this fires on a task that WAS delegated and DID succeed, just never got tracked in the plan. ---
    def _untracked_delegation_scenario():
        from tools.core import tool_quotas_ctx as q_ctx
        _orig_ws4 = _config.cfg.get("settings", {}).get("workspace")
        _config.cfg["settings"]["workspace"] = {"type": "memory", "required_artifact": "final_report.md"}
        saved_fs = dict(_IN_MEMORY_FS)
        try:
            _IN_MEMORY_FS.clear()
            reset_fetched_urls()
            record_fetched_url(_SRC, filename="sources/page.md")
            _IN_MEMORY_FS["sources/page.md"] = _SOURCE_TEXT
            _IN_MEMORY_FS["findings.md"] = _FINDINGS_OK
            _IN_MEMORY_FS["final_report.md"] = f"- el pais avanza de forma sostenida segun cifras oficiales [gov]({_SRC})"

            # (a) write_todos WAS used, the dispatched task_name never appears in _todos.md -> fires.
            _IN_MEMORY_FS["_todos.md"] = "- [x] background_heuristics\n- [ ] verification"
            q_ctx.set({"delegate_tasks": {"used": 1, "limit": 5}, "write_todos": {"used": 1, "limit": 5}})
            with tempfile.TemporaryDirectory() as tmpdir_a:
                rs = RunState(tmpdir_a)
                rs.add_finding(_SRC, "some background summary", task_name="background", depth=1)
                run_state_ctx.set(rs)
                msgs = []
                should_retry, _ = _asyncio.run(run_completion_check(
                    query="q", current_input="q", run_state=rs, notify=msgs.append))
                recorded = rs.data["completion_check_attempts"][-1]["problem"]
                assert recorded == "untracked_delegation", (recorded, msgs)
                assert should_retry
                assert "were never added to the written plan" in msgs[-1], msgs

                # (a2) Live-confirmed regression, 2026-07-22: a SECOND pass with the EXACT SAME
                # untracked condition still true must NOT fire again -- this is a one-time hygiene
                # nudge, not a blocking correctness gate, and must never be able to exhaust a run's
                # entire retry budget over wasted delegate_tasks quota alone (that's exactly what
                # happened live before this fix: 4 consecutive fires, "Retry budget exhausted...
                # could NOT be fully verified" on an otherwise-fine report).
                msgs2 = []
                should_retry2, _ = _asyncio.run(run_completion_check(
                    query="q", current_input="q", run_state=rs, notify=msgs2.append))
                recorded2 = rs.data["completion_check_attempts"][-1]["problem"]
                assert recorded2 != "untracked_delegation", (
                    "must fire at most once per run, never repeat/escalate", recorded2, msgs2)

            # (b) write_todos never used (sanctioned simple-query fast path) -> must NEVER fire,
            # even though the same task_name is absent from an empty/nonexistent _todos.md.
            _IN_MEMORY_FS.pop("_todos.md", None)
            q_ctx.set({"delegate_tasks": {"used": 1, "limit": 5}})
            with tempfile.TemporaryDirectory() as tmpdir_b:
                rs = RunState(tmpdir_b)
                rs.add_finding(_SRC, "some background summary", task_name="background", depth=1)
                run_state_ctx.set(rs)
                msgs = []
                should_retry, _ = _asyncio.run(run_completion_check(
                    query="q", current_input="q", run_state=rs, notify=msgs.append))
                recorded = rs.data["completion_check_attempts"][-1]["problem"]
                assert recorded != "untracked_delegation", (
                    "simple-query fast path (write_todos never called) must never be flagged", recorded, msgs)

            # (c) properly tracked -> clean pass, never fires.
            _IN_MEMORY_FS["_todos.md"] = "- [x] background\n- [ ] verification"
            q_ctx.set({"delegate_tasks": {"used": 1, "limit": 5}, "write_todos": {"used": 1, "limit": 5}})
            with tempfile.TemporaryDirectory() as tmpdir_c:
                rs = RunState(tmpdir_c)
                rs.add_finding(_SRC, "some background summary", task_name="background", depth=1)
                run_state_ctx.set(rs)
                msgs = []
                should_retry, _ = _asyncio.run(run_completion_check(
                    query="q", current_input="q", run_state=rs, notify=msgs.append))
                recorded = rs.data["completion_check_attempts"][-1]["problem"]
                assert recorded != "untracked_delegation", (
                    "a task_name that DOES appear in _todos.md must never be flagged", recorded, msgs)

            # (d) engine-driven deepening task ("Follow-up: ...") never in _todos.md by design ->
            # must never be flagged -- the Planner never chose that name itself.
            _IN_MEMORY_FS["_todos.md"] = "- [x] background_heuristics\n- [ ] verification"
            q_ctx.set({"delegate_tasks": {"used": 1, "limit": 5}, "write_todos": {"used": 1, "limit": 5}})
            with tempfile.TemporaryDirectory() as tmpdir_d:
                rs = RunState(tmpdir_d)
                rs.add_finding(_SRC, "some background summary", task_name="background_heuristics", depth=1)
                rs.add_finding(_SRC, "a follow-up lead", task_name="Follow-up: explore X", depth=1)
                run_state_ctx.set(rs)
                msgs = []
                should_retry, _ = _asyncio.run(run_completion_check(
                    query="q", current_input="q", run_state=rs, notify=msgs.append))
                recorded = rs.data["completion_check_attempts"][-1]["problem"]
                assert recorded != "untracked_delegation", (
                    "an engine-dispatched 'Follow-up: ...' deepening task must never be flagged", recorded, msgs)
        finally:
            _IN_MEMORY_FS.clear()
            _IN_MEMORY_FS.update(saved_fs)
            reset_fetched_urls()
            if _orig_ws4 is None:
                _config.cfg["settings"].pop("workspace", None)
            else:
                _config.cfg["settings"]["workspace"] = _orig_ws4

    contextvars.copy_context().run(_untracked_delegation_scenario)

    # --- distinct-problem retry-budget bonus (2026-07-29, Track A8): confirmed live, the
    # Ornith-1.0-9B run did 6 honest write-review-fix rounds each fixing a DIFFERENT real problem
    # and was cut off by the flat max_completion_check_attempts ceiling despite never looping on
    # any single issue. When the two most recently RECORDED attempts (before this round even
    # runs) show different problems -- genuine progress, not a repeat -- a bounded bonus attempt
    # (and a proportional slice of extra wall-clock budget) is granted, capped so a run can't earn
    # this forever. ---
    def _distinct_problem_bonus_scenario():
        from tools.core import tool_quotas_ctx as q_ctx
        _orig_ws6 = _config.cfg.get("settings", {}).get("workspace")
        _orig_mca = _config.cfg.get("settings", {}).get("max_completion_check_attempts")
        _config.cfg["settings"]["workspace"] = {"type": "memory"}
        _config.cfg["settings"]["max_completion_check_attempts"] = 3
        saved_fs = dict(_IN_MEMORY_FS)
        try:
            _IN_MEMORY_FS.clear()
            reset_fetched_urls()
            q_ctx.set({})  # nothing delegated -> check_not_delegated fires deterministically

            # (a) Already AT the ceiling (attempt == max_attempts), but the last two recorded
            # attempts show two DIFFERENT problems -- must grant one bonus attempt.
            with tempfile.TemporaryDirectory() as tmpdir6:
                rs = RunState(tmpdir6)
                rs.set_query("q")
                rs.attempt = 3  # already at the (unbumped) ceiling
                rs.data["completion_check_attempts"] = [
                    {"attempt": 0, "problem": "stub_source"},
                    {"attempt": 1, "problem": "not_grounded"},  # different from the one before it
                ]
                run_state_ctx.set(rs)
                msgs = []
                should_retry, _ = _asyncio.run(run_completion_check(
                    query="q", current_input="q", run_state=rs, notify=msgs.append))
                assert should_retry, (
                    "genuine progress (two different recorded problems in a row) must earn a "
                    "bonus attempt instead of terminating exactly at the flat ceiling", msgs)
                assert rs.data.get("distinct_problem_bonus_used") == 1, rs.data

            # (b) Same ceiling, but the last two recorded attempts show the SAME problem twice --
            # this is a real loop (already handled by the separate consecutive-same-problem
            # escalation), NOT genuine progress -- must NOT grant a bonus.
            with tempfile.TemporaryDirectory() as tmpdir7:
                rs2 = RunState(tmpdir7)
                rs2.set_query("q")
                rs2.attempt = 3
                rs2.data["completion_check_attempts"] = [
                    {"attempt": 0, "problem": "not_grounded"},
                    {"attempt": 1, "problem": "not_grounded"},  # identical -- a repeat, not progress
                ]
                run_state_ctx.set(rs2)
                msgs2 = []
                should_retry2, _ = _asyncio.run(run_completion_check(
                    query="q", current_input="q", run_state=rs2, notify=msgs2.append))
                assert not should_retry2, (
                    "a repeated identical problem must NOT earn a bonus attempt -- that's a real "
                    "loop, not progress", msgs2)
                assert rs2.data.get("distinct_problem_bonus_used", 0) == 0, rs2.data

            # (c) Bonus cap: once distinct_problem_bonus_used already sits at the cap, further
            # "different problem" history must NOT grant yet another bonus -- bounded, not a way
            # to disable the ceiling entirely for a run that keeps finding new-looking problems.
            with tempfile.TemporaryDirectory() as tmpdir8:
                rs3 = RunState(tmpdir8)
                rs3.set_query("q")
                rs3.attempt = 3
                rs3.data["distinct_problem_bonus_used"] = 4  # already at DISTINCT_PROBLEM_BONUS_CAP
                rs3.data["completion_check_attempts"] = [
                    {"attempt": 0, "problem": "stub_source"},
                    {"attempt": 1, "problem": "uncited_claims"},
                ]
                run_state_ctx.set(rs3)
                msgs3 = []
                should_retry3, _ = _asyncio.run(run_completion_check(
                    query="q", current_input="q", run_state=rs3, notify=msgs3.append))
                assert not should_retry3, (
                    "the distinct-problem bonus must be capped, not an unbounded ceiling override", msgs3)
                assert rs3.data["distinct_problem_bonus_used"] == 4, (
                    "bonus counter must not exceed its own cap", rs3.data)
        finally:
            _IN_MEMORY_FS.clear()
            _IN_MEMORY_FS.update(saved_fs)
            reset_fetched_urls()
            if _orig_ws6 is None:
                _config.cfg["settings"].pop("workspace", None)
            else:
                _config.cfg["settings"]["workspace"] = _orig_ws6
            if _orig_mca is None:
                _config.cfg["settings"].pop("max_completion_check_attempts", None)
            else:
                _config.cfg["settings"]["max_completion_check_attempts"] = _orig_mca

    contextvars.copy_context().run(_distinct_problem_bonus_scenario)



if __name__ == "__main__":
    main()
    print("test_completion_structural_boundary OK")
