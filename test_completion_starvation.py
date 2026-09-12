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
import tempfile

from engine.completion import (
    Ctx, Verdict, _yield_to_starved_check, _consecutive_occurrences,
)
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
    # --- _yield_to_starved_check (2026-07-24): check_untracked_delegation/
    # check_report_underuses_findings are deliberately placed LAST in their own check lists (lower
    # priority than real correctness problems) -- confirmed live this can starve them for an
    # entire run's retry budget when some OTHER problem keeps recurring every attempt, "wait a
    # cycle" becoming "wait forever" in practice. Tested directly against the helper's own
    # inputs/outputs rather than needing to construct two simultaneously-triggering real checks. ---

    def _starvation_guard_scenario():
        with tempfile.TemporaryDirectory() as tmpdir5:
            rs = RunState(tmpdir5)
            rs.set_query("q")
            ctx = Ctx(req_artifact="final_report.md", attempt=0, max_attempts=8, delegated=True,
                      files=[], content=None, quotas={}, run_state=rs)
            stuck = Verdict("uneven_task_investment", "w", "i")
            starved = Verdict("untracked_delegation", "w2", "i2")

            # Not stuck yet (0 consecutive occurrences) -> unchanged, starved check never even
            # consulted (confirmed via a callable that would raise if invoked).
            def _must_not_be_called(_ctx):
                raise AssertionError("starved check must not be probed before the threshold")
            assert _yield_to_starved_check(stuck, ctx, _must_not_be_called) is stuck

            # Stuck 2x consecutive -> starved check gets a direct turn and wins.
            rs.data["completion_check_attempts"] = [
                {"problem": "uneven_task_investment"}, {"problem": "uneven_task_investment"},
            ]
            assert _consecutive_occurrences(rs, "uneven_task_investment") == 2
            result = _yield_to_starved_check(stuck, ctx, lambda c: starved)
            assert result is starved, result

            # Stuck, but the starved check genuinely has nothing to report -> falls back to the
            # original (a genuinely single-problem run is never worse off than before this existed).
            assert _yield_to_starved_check(stuck, ctx, lambda c: None) is stuck

            # A non-matching prior attempt breaks the consecutive streak -> not stuck, unchanged.
            rs.data["completion_check_attempts"] = [
                {"problem": "missing_artifact"}, {"problem": "uneven_task_investment"},
            ]
            assert _consecutive_occurrences(rs, "uneven_task_investment") == 1
            assert _yield_to_starved_check(stuck, ctx, _must_not_be_called) is stuck

            # verdict is None (nothing wrong at all) -> no-op, never probes the starved check.
            assert _yield_to_starved_check(None, ctx, _must_not_be_called) is None

            # never_final_blocker (2026-07-28 live bug): once a forced-final cycle has already set
            # ctx.attempt >= ctx.max_attempts (tui.py's context-budget/max_run_minutes/malformed-
            # retry paths jump straight there via run_state.attempt = 10**6), a check documented as
            # "will NOT block this run from finishing" (check_untracked_delegation) must not be
            # allowed to displace the real, still-retriable problem as the reported terminal
            # verdict -- even if it's otherwise "due" a turn per the starvation window below.
            rs.data["completion_check_attempts"] = [
                {"problem": "uneven_task_investment"}, {"problem": "uneven_task_investment"},
            ]
            final_ctx = Ctx(req_artifact="final_report.md", attempt=8, max_attempts=8, delegated=True,
                             files=[], content=None, quotas={}, run_state=rs)
            result = _yield_to_starved_check(stuck, final_ctx, lambda c: starved, never_final_blocker=True)
            assert result is stuck, result
            # The OTHER caller (check_report_underuses_findings, a real correctness signal, not a
            # "never blocks" hygiene check) keeps the pre-existing yield-even-when-final behavior --
            # never_final_blocker defaults False, so nothing changes for it.
            result = _yield_to_starved_check(stuck, final_ctx, lambda c: starved)
            assert result is starved, result

    _starvation_guard_scenario()

    # --- _capped (2026-07-31): a non-self-resolving check (not Builder/FindingsWriter-fixable)
    # must go quiet after CONSECUTIVE_SAME_PROBLEM_ESCALATION_THRESHOLD (3) consecutive same-problem
    # occurrences, so first-match ordering in COMPLETION_CHECKS/GROUNDING_CHECKS can fall through to
    # whatever's next instead of starving it forever. This was previously enforced only via a
    # static grep ("every non-self-resolving check calls _capped") -- no test exercised _capped's
    # OWN behavior directly, a gap flagged by the group-D coverage audit before any decomposition
    # of the starvation/capping state machine was attempted (ARCHITECTURE.md's flagged hazard). ---
    from engine.completion import _capped, CONSECUTIVE_SAME_PROBLEM_ESCALATION_THRESHOLD

    def _capped_scenario():
        with tempfile.TemporaryDirectory() as tmpdir_cap:
            rs = RunState(tmpdir_cap)
            rs.set_query("q")
            ctx = Ctx(req_artifact="final_report.md", attempt=0, max_attempts=8, delegated=True,
                      files=[], content=None, quotas={}, run_state=rs)
            v = Verdict("thin_coverage", "w", "i")

            # verdict is None -> no-op passthrough regardless of history.
            assert _capped(ctx, "thin_coverage", None) is None

            # Below threshold (2 consecutive) -> verdict passes through unchanged.
            rs.data["completion_check_attempts"] = [
                {"problem": "thin_coverage"}, {"problem": "thin_coverage"},
            ]
            assert _capped(ctx, "thin_coverage", v) is v

            # At threshold (3 consecutive) -> goes quiet (None), letting the pipeline fall through
            # to whatever check is next in COMPLETION_CHECKS/GROUNDING_CHECKS.
            rs.data["completion_check_attempts"] = [
                {"problem": "thin_coverage"}, {"problem": "thin_coverage"}, {"problem": "thin_coverage"},
            ]
            assert _consecutive_occurrences(rs, "thin_coverage") == CONSECUTIVE_SAME_PROBLEM_ESCALATION_THRESHOLD
            assert _capped(ctx, "thin_coverage", v) is None

            # skip_problems: an interrupting problem in the skip set doesn't break the streak.
            rs.data["completion_check_attempts"] = [
                {"problem": "thin_coverage"}, {"problem": "untracked_delegation"},
                {"problem": "thin_coverage"}, {"problem": "thin_coverage"},
            ]
            assert _capped(ctx, "thin_coverage", v, skip_problems=frozenset({"untracked_delegation"})) is None

            # A genuinely different interrupting problem (not in skip set) breaks the streak ->
            # verdict passes through again instead of staying capped.
            rs.data["completion_check_attempts"] = [
                {"problem": "thin_coverage"}, {"problem": "missing_artifact"},
                {"problem": "thin_coverage"}, {"problem": "thin_coverage"},
            ]
            assert _capped(ctx, "thin_coverage", v) is v

    _capped_scenario()

    # --- get_escalation_threshold (2026-09-11, RESEARCH_small_model_agentic_reliability.md
    # Finding B): settings.completion_check_escalation_threshold overrides the shared 3-strike
    # default so a known-weaker candidate config can bail into salvage sooner. Unset/absent must
    # be a true no-op (same default _capped_scenario above already pins); when set, _capped must
    # actually honor the lower number, not just the module constant. ---
    import config as _config_mod
    from engine.completion import get_escalation_threshold

    def _escalation_threshold_override_scenario():
        _orig = _config_mod.cfg.get("settings", {}).get("completion_check_escalation_threshold")
        try:
            # Absent -> falls back to the unchanged default of 3.
            _config_mod.cfg["settings"].pop("completion_check_escalation_threshold", None)
            assert get_escalation_threshold() == CONSECUTIVE_SAME_PROBLEM_ESCALATION_THRESHOLD == 3

            # Overridden to 1 -> _capped must go quiet after just ONE occurrence, not three.
            _config_mod.cfg["settings"]["completion_check_escalation_threshold"] = 1
            assert get_escalation_threshold() == 1
            with tempfile.TemporaryDirectory() as tmpdir_esc:
                rs = RunState(tmpdir_esc)
                rs.set_query("q")
                ctx = Ctx(req_artifact="final_report.md", attempt=0, max_attempts=8, delegated=True,
                          files=[], content=None, quotas={}, run_state=rs)
                v = Verdict("thin_coverage", "w", "i")
                rs.data["completion_check_attempts"] = [{"problem": "thin_coverage"}]
                assert _capped(ctx, "thin_coverage", v) is None, (
                    "a lowered threshold must cap after 1 occurrence, not the default 3"
                )
        finally:
            if _orig is None:
                _config_mod.cfg["settings"].pop("completion_check_escalation_threshold", None)
            else:
                _config_mod.cfg["settings"]["completion_check_escalation_threshold"] = _orig

    _escalation_threshold_override_scenario()

    # --- _apply_starvation_yield (2026-07-31): declarative sibling-yield -- report_underuses_
    # findings must yield to report_underuses_evidence once stuck _STARVATION_SKIP_THRESHOLD times,
    # structurally unable to repeat the old dead-code `lambda c: A(c) or B(c)` bug (A always wins
    # the `or` since it's the same check already winning the scan, so B never actually runs). No
    # prior test exercised this helper directly (another group-D coverage gap). ---
    from engine.completion import _apply_starvation_yield, _STARVATION_YIELD_TARGETS, _STARVATION_SKIP_THRESHOLD

    def _apply_starvation_yield_scenario():
        with tempfile.TemporaryDirectory() as tmpdir_asy:
            rs = RunState(tmpdir_asy)
            rs.set_query("q")
            ctx = Ctx(req_artifact="final_report.md", attempt=0, max_attempts=8, delegated=True,
                      files=[], content=None, quotas={}, run_state=rs)
            v = Verdict("report_underuses_findings", "w", "i")

            # verdict is None -> no-op.
            assert _apply_starvation_yield(None, ctx) is None

            # A problem with no declared yield target -> unchanged regardless of history.
            other = Verdict("missing_artifact", "w", "i")
            rs.data["completion_check_attempts"] = [{"problem": "missing_artifact"}] * 5
            assert _apply_starvation_yield(other, ctx) is other

            orig_target = _STARVATION_YIELD_TARGETS["report_underuses_findings"]
            called = []
            try:
                def _fake_target(_ctx):
                    called.append(1)
                    return Verdict("report_underuses_evidence", "w2", "i2")
                _STARVATION_YIELD_TARGETS["report_underuses_findings"] = _fake_target

                # Has a target, but not yet stuck -> unchanged, target never even consulted.
                rs.data["completion_check_attempts"] = [{"problem": "report_underuses_findings"}]
                assert _apply_starvation_yield(v, ctx) is v
                assert not called, "target must not be probed before the threshold"

                # Stuck (>= _STARVATION_SKIP_THRESHOLD consecutive) -> target gets probed and wins.
                rs.data["completion_check_attempts"] = [
                    {"problem": "report_underuses_findings"}
                ] * _STARVATION_SKIP_THRESHOLD
                result = _apply_starvation_yield(v, ctx)
                assert called
                assert result.problem == "report_underuses_evidence", result

                # Target returns the SAME problem as the winner -> falls back to the original
                # verdict rather than looping (structural guard, even though this specific pairing
                # can't hit it in practice).
                called.clear()
                _STARVATION_YIELD_TARGETS["report_underuses_findings"] = lambda c: v
                assert _apply_starvation_yield(v, ctx) is v

                # Target has nothing to report -> falls back to the original verdict.
                _STARVATION_YIELD_TARGETS["report_underuses_findings"] = lambda c: None
                assert _apply_starvation_yield(v, ctx) is v
            finally:
                _STARVATION_YIELD_TARGETS["report_underuses_findings"] = orig_target

    _apply_starvation_yield_scenario()

    # --- _collect_other_active_problems / _with_other_problems_addendum (2026-07-29): a winning
    # verdict must not silently shadow OTHER, simultaneously-true COMPLETION_CHECKS problems for an
    # entire run -- confirmed live a real mid-priority check (check_uncited_claims) sat true for 3
    # attempts behind a persistently-recurring stub_source and was never disclosed, even in the
    # terminal "retry budget exhausted" message. No prior test exercised either helper directly. ---
    from engine.completion import (
        _collect_other_active_problems, _with_other_problems_addendum, _OTHER_ACTIVE_PROBLEMS_CAP,
    )

    def _other_active_problems_scenario():
        with tempfile.TemporaryDirectory() as tmpdir_oap:
            rs = RunState(tmpdir_oap)
            rs.set_query("q")
            ctx = Ctx(req_artifact="final_report.md", attempt=0, max_attempts=8, delegated=True,
                      files=[], content=None, quotas={}, run_state=rs)
            winner = Verdict("stub_source", "stub warning", "stub inject")

            v_a = Verdict("uncited_claims", "wa", "ia")
            v_b = Verdict("topical_mismatch", "wb", "ib")
            v_same = Verdict("stub_source", "dup", "dup")  # same problem as the winner -> excluded
            checks = [
                lambda c: None,  # a clean check contributes nothing
                lambda c: v_a,
                lambda c: v_same,
                lambda c: v_b,
            ]

            others = _collect_other_active_problems(ctx, checks, "stub_source")
            assert others == [v_a, v_b], others

            augmented = _with_other_problems_addendum(winner, ctx, checks)
            assert augmented.inject.startswith("stub inject")
            assert "ALSO currently true" in augmented.inject
            assert "uncited_claims" in augmented.inject and "topical_mismatch" in augmented.inject
            # The recorded problem/warning must stay untouched -- only inject text gains an addendum.
            assert augmented.problem == "stub_source" and augmented.warning == "stub warning"

            # Nothing else active -> no-op, byte-identical verdict object (not just equal content).
            clean_checks = [lambda c: None, lambda c: v_same]
            assert _with_other_problems_addendum(winner, ctx, clean_checks) is winner

            # Cap: more active problems than _OTHER_ACTIVE_PROBLEMS_CAP (3) still returns at most 3.
            many = [lambda c, i=i: Verdict(f"p{i}", f"w{i}", f"i{i}") for i in range(5)]
            capped_others = _collect_other_active_problems(ctx, many, "nonexistent")
            assert len(capped_others) == _OTHER_ACTIVE_PROBLEMS_CAP, capped_others

    _other_active_problems_scenario()



if __name__ == "__main__":
    main()
    print("test_completion_starvation OK")
