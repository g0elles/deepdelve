import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from utils.run_state import reset_fetched_urls

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
    Ctx, run_completion_check, check_findings_underuses_evidence,
    _update_task_verification, check_task_verification_flagged,
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
    # --- ROADMAP Phase 5: coverage accounting (RunState.coverage(), pure-function, no fetched-fs
    # dependency) ---
    def _coverage_scenario():
        with tempfile.TemporaryDirectory() as tmpdir:
            # No findings at all -> vacuously "fully covered" (ratio 1.0, total 0) -- an empty run
            # must never look like a coverage FAILURE, that's missing_findings/not_delegated's job.
            rs = RunState(tmpdir)
            cov = rs.coverage()
            assert cov == {"total": 0, "covered": 0, "ratio": 1.0, "uncovered_task_names": [],
                            "per_task_counts": {}}, cov

            # A single top-level task with a real fetched URL -> fully covered.
            rs2 = RunState(tmpdir)
            rs2.add_finding("https://a.example.co/x", "summary", task_name="Background", depth=1)
            cov2 = rs2.coverage()
            assert cov2 == {"total": 1, "covered": 1, "ratio": 1.0, "uncovered_task_names": [],
                             "per_task_counts": {"Background": 1}}, cov2

            # Nested Analyzer-tier (depth=2) findings with no URL of their own must NOT count
            # against coverage -- that's expected, not a gap (see coverage()'s own docstring).
            rs3 = RunState(tmpdir)
            rs3.add_finding("https://a.example.co/x", "summary", task_name="Background", depth=1)
            rs3.add_finding("Background", "analyzer summary, no new URL", task_name="Analyze x", depth=2)
            cov3 = rs3.coverage()
            assert cov3 == {"total": 1, "covered": 1, "ratio": 1.0, "uncovered_task_names": [],
                             "per_task_counts": {"Background": 1}}, cov3

            # Empty-summary exclusion (2026-08-17, live incident, session_status 2026-08-16 item 3
            # follow-up): a real, http-prefixed URL whose summary is a completely empty string
            # (the model ended its turn right after a tool call with no synthesis text at all --
            # confirmed live, 25%/42% of two real runs' own findings) must NOT count as coverage --
            # confirmed live this let a task with 5 fetched-but-unsynthesized URLs read as the
            # run's BEST-covered task while a genuinely thin one got flagged instead.
            rs3b = RunState(tmpdir)
            rs3b.add_finding("https://a.example.co/x", "", task_name="Lisbon_visa", depth=1)
            rs3b.add_finding("https://b.example.co/y", "", task_name="Lisbon_visa", depth=1)
            rs3b.add_finding("https://c.example.co/z", "real content here", task_name="Rent", depth=1)
            cov3b = rs3b.coverage()
            assert cov3b == {"total": 2, "covered": 1, "ratio": 0.5,
                              "uncovered_task_names": ["Lisbon_visa"],
                              "per_task_counts": {"Lisbon_visa": 0, "Rent": 1}}, cov3b
            # Same treatment for a "nothing extracted" admission, not just true emptiness -- same
            # predicate _is_citable_finding already uses, so the two never disagree.
            rs3c = RunState(tmpdir)
            rs3c.add_finding("https://a.example.co/x",
                              "No key findings extracted from this source.", task_name="Thin", depth=1)
            cov3c = rs3c.coverage()
            assert cov3c["covered"] == 0 and cov3c["uncovered_task_names"] == ["Thin"], cov3c

            # Three top-level tasks, only one with a real URL -> thin (ratio 1/3).
            rs4 = RunState(tmpdir)
            rs4.add_finding("https://a.example.co/x", "summary", task_name="Background", depth=1)
            rs4.add_finding("Comparison A", "found nothing usable", task_name="Comparison A", depth=1)
            rs4.add_finding("Comparison B", "found nothing usable", task_name="Comparison B", depth=1)
            cov4 = rs4.coverage()
            assert cov4["total"] == 3 and cov4["covered"] == 1, cov4
            assert abs(cov4["ratio"] - 1 / 3) < 1e-9, cov4
            assert set(cov4["uncovered_task_names"]) == {"Comparison A", "Comparison B"}, cov4
            assert cov4["per_task_counts"] == {"Background": 1, "Comparison A": 0, "Comparison B": 0}, cov4

            # "superseded" task_names excluded from the denominator (2026-08-17 live incident): a
            # facet redispatched under a fresh, reworded task_name (never reusing the original)
            # must not inflate `total` -- once _update_task_verification (completion.py) has
            # already marked the stale name(s) "superseded" via _looks_like_renamed_task, coverage()
            # must not still count them as separate uncovered tasks. Without this, check_thin_
            # coverage kept re-firing "Only N/(growing total)" on a denominator padded by the exact
            # renaming this ledger status already accounts for.
            rs5 = RunState(tmpdir)
            rs5.add_finding("Mexico rent v1", "", task_name="Mexico rent v1", depth=1)
            rs5.add_finding("Mexico rent v2", "", task_name="Mexico rent v2", depth=1)
            rs5.add_finding("https://c.example.co/z", "real content", task_name="Mexico rent v3", depth=1)
            rs5.data["task_verification"] = {
                "Mexico rent v1": {"status": "superseded"},
                "Mexico rent v2": {"status": "superseded"},
                "Mexico rent v3": {"status": "verified"},
            }
            cov5 = rs5.coverage()
            assert cov5 == {"total": 1, "covered": 1, "ratio": 1.0, "uncovered_task_names": [],
                             "per_task_counts": {"Mexico rent v3": 1}}, cov5
            # A "flagged" (not yet superseded) task_name must still count normally -- only the
            # ledger's explicit "superseded" status is special-cased here.
            rs5b = RunState(tmpdir)
            rs5b.add_finding("Still Flagged", "", task_name="Still Flagged", depth=1)
            rs5b.data["task_verification"] = {"Still Flagged": {"status": "flagged"}}
            cov5b = rs5b.coverage()
            assert cov5b["total"] == 1 and cov5b["uncovered_task_names"] == ["Still Flagged"], cov5b

    _coverage_scenario()

    # --- ROADMAP Phase 5 follow-up (2026-07-14, live-caught): Builder/FindingsWriter/PeerReviewer
    # dispatches must never feed RunState.add_finding's coverage bookkeeping -- they land at
    # delegation_depth_ctx==1 exactly like a genuine Planner-delegated research task (see
    # orchestrator.py's _NON_RESEARCH_DISPATCH_ROLES comment) and none of them can ever have a
    # real source URL. Pins the exact role set the add_finding call site excludes. ---
    def _non_research_dispatch_roles_scenario():
        from engine.orchestrator import _NON_RESEARCH_DISPATCH_ROLES
        assert _NON_RESEARCH_DISPATCH_ROLES == {"Builder", "FindingsWriter", "PeerReviewer"}, (
            _NON_RESEARCH_DISPATCH_ROLES)

    _non_research_dispatch_roles_scenario()

    # --- ROADMAP "B4": run_cli/run_agent's duplicated malformed-tool-call retry logic extracted
    # into classify_malformed_retry (engine/orchestrator.py) -- pure decision logic, no event loop,
    # no I/O, unit-testable directly with a fake exception. ---
    def _malformed_retry_scenario():
        from engine.orchestrator import classify_malformed_retry

        class _FakeMalformedError(Exception):
            def __str__(self):
                return "error parsing tool call: bad escape"

        class _FakeOtherError(Exception):
            def __str__(self):
                return "some unrelated failure"

        # 1. Recognized class, under retry budget -> should_retry True, nudge appended, counter bumped.
        r = classify_malformed_retry(_FakeMalformedError(), malformed_retries=0, current_input="query")
        assert r.should_retry and not r.reraise and not r.force_final_verdict
        assert r.new_malformed_retries == 1
        assert isinstance(r.new_current_input, list) and len(r.new_current_input) == 2
        assert r.new_current_input[0] == "query"

        # 2. Recognized class, list current_input -> appended, not replaced/wrapped again.
        r2 = classify_malformed_retry(_FakeMalformedError(), malformed_retries=0, current_input=["a", "b"])
        assert r2.new_current_input == ["a", "b", r2.new_current_input[-1]]

        # 3. Recognized class, retry budget exhausted (== max_retries) -> force_final_verdict, no retry.
        r3 = classify_malformed_retry(_FakeMalformedError(), malformed_retries=2, current_input="q")
        assert not r3.should_retry and r3.force_final_verdict and not r3.reraise
        assert r3.new_malformed_retries == 2  # unchanged when not retrying

        # 4. Unrecognized exception class -> reraise True, no retry, no final-verdict force, counter
        #    unchanged, current_input echoed back untouched.
        r4 = classify_malformed_retry(_FakeOtherError(), malformed_retries=0, current_input="q")
        assert r4.reraise and not r4.should_retry and not r4.force_final_verdict
        assert r4.new_current_input == "q"
        assert r4.new_malformed_retries == 0

        # 5. Boundary: exactly max_retries-1 still retries (last allowed retry).
        r5 = classify_malformed_retry(_FakeMalformedError(), malformed_retries=1, current_input="q")
        assert r5.should_retry and r5.new_malformed_retries == 2

    _malformed_retry_scenario()

    # --- ROADMAP "B4": run_cli's deadline-racing stream iteration (previously inline, manually
    # driving stream.__aiter__() + asyncio.wait_for) extracted into iter_agent_stream
    # (engine/orchestrator.py), now shared with run_agent (TUI, deadline=None). ---
    async def _iter_agent_stream_scenario():
        import asyncio as _asyncio
        import time as _time
        from engine.orchestrator import iter_agent_stream

        class _FakeStream:
            def __init__(self, items, delays=None):
                self._items = list(items)
                self._delays = delays or [0] * len(self._items)
            def __aiter__(self):
                return self._gen()
            async def _gen(self):
                for item, delay in zip(self._items, self._delays):
                    if delay:
                        await _asyncio.sleep(delay)
                    yield item

        # deadline=None: unbounded, yields everything, no exception.
        out = [u async for u in iter_agent_stream(_FakeStream(["a", "b", "c"]), None)]
        assert out == ["a", "b", "c"]

        # deadline far in the future: same as unbounded for a fast fake stream.
        out2 = [u async for u in iter_agent_stream(_FakeStream(["x"]), _time.monotonic() + 5)]
        assert out2 == ["x"]

        # deadline already passed: first __anext__ should raise asyncio.TimeoutError immediately,
        # yielding nothing.
        got_timeout = False
        try:
            async for _ in iter_agent_stream(_FakeStream(["a"]), _time.monotonic() - 1):
                pass
        except _asyncio.TimeoutError:
            got_timeout = True
        assert got_timeout

        # deadline that expires mid-stream (between item 1 and item 2, via an injected delay) ->
        # partial yield then TimeoutError, not silently truncated/swallowed.
        seen = []
        got_timeout2 = False
        try:
            async for u in iter_agent_stream(_FakeStream(["a", "b"], delays=[0, 0.3]), _time.monotonic() + 0.1):
                seen.append(u)
        except _asyncio.TimeoutError:
            got_timeout2 = True
        assert seen == ["a"] and got_timeout2

    import asyncio as _asyncio_b4
    _asyncio_b4.run(_iter_agent_stream_scenario())

    # --- check_thin_coverage wiring (mirrors _line_claim_scenario's directness -- pure RunState
    # setup, no fetched-fs dependency needed since this check doesn't read workspace content) ---
    def _thin_coverage_wiring_scenario():
        from tools.core import tool_quotas_ctx as q_ctx
        from utils.run_state import RunState

        _orig_ws10 = _config.cfg.get("settings", {}).get("workspace")
        _config.cfg["settings"]["workspace"] = {"type": "memory", "required_artifact": "final_report.md"}
        saved_fs = dict(_IN_MEMORY_FS)
        try:
            _IN_MEMORY_FS.clear()
            reset_fetched_urls()
            q_ctx.set({"delegate_tasks": {"used": 1, "limit": 5}})

            # (a) 1/3 top-level tasks covered -> thin_coverage fires BEFORE missing_findings even
            # gets a chance to (COMPLETION_CHECKS order), current_input grows via the classic path
            # (not Builder/FindingsWriter-fixable -- this needs new delegation, not a rewrite).
            with tempfile.TemporaryDirectory() as tmpdir_a:
                rs = RunState(tmpdir_a)
                rs.add_finding("https://a.example.co/x", "summary", task_name="Background", depth=1)
                rs.add_finding("Comparison A", "found nothing usable", task_name="Comparison A", depth=1)
                rs.add_finding("Comparison B", "found nothing usable", task_name="Comparison B", depth=1)
                run_state_ctx.set(rs)
                msgs = []
                should_retry, new_input = _asyncio.run(run_completion_check(
                    query="q", current_input="q", run_state=rs, notify=msgs.append))
                recorded = rs.data["completion_check_attempts"][-1]["problem"]
                assert recorded == "thin_coverage", (recorded, msgs)
                assert should_retry
                assert isinstance(new_input, list) and len(new_input) == 2, new_input
                assert "Comparison A" in msgs[-1] and "1/3" in msgs[-1], msgs

            # (b) a single-task query that succeeded -> never flagged, regardless of "breadth"
            # (min_tasks gate wouldn't even matter here since ratio is already 1.0).
            with tempfile.TemporaryDirectory() as tmpdir_b:
                rs = RunState(tmpdir_b)
                rs.add_finding("https://a.example.co/x", "summary", task_name="Simple lookup", depth=1)
                run_state_ctx.set(rs)
                msgs = []
                should_retry, _ = _asyncio.run(run_completion_check(
                    query="q", current_input="q", run_state=rs, notify=msgs.append))
                recorded = rs.data["completion_check_attempts"][-1]["problem"]
                assert recorded != "thin_coverage", (recorded, msgs)

            # (c) 1/2 covered -> ratio exactly AT threshold (0.5) MUST fire (2026-07-23 fix:
            # at-or-below, not strictly-below -- see check_thin_coverage's own docstring for the
            # live incident this reversed test case is guarding against: a 2-task query with one
            # dead task used to sail through silently at exactly this ratio).
            with tempfile.TemporaryDirectory() as tmpdir_c:
                rs = RunState(tmpdir_c)
                rs.add_finding("https://a.example.co/x", "summary", task_name="Background", depth=1)
                rs.add_finding("Comparison A", "found nothing usable", task_name="Comparison A", depth=1)
                run_state_ctx.set(rs)
                msgs = []
                should_retry, _ = _asyncio.run(run_completion_check(
                    query="q", current_input="q", run_state=rs, notify=msgs.append))
                recorded = rs.data["completion_check_attempts"][-1]["problem"]
                assert recorded == "thin_coverage", (
                    "ratio exactly AT threshold (0.5) must fire -- this is the live-confirmed "
                    "2026-07-23 regression", recorded, msgs)

            # (d) 2/3 covered -> ratio (0.667) clearly ABOVE threshold must still NOT fire --
            # confirms the fix didn't overshoot into firing on any imbalance at all.
            with tempfile.TemporaryDirectory() as tmpdir_d:
                rs = RunState(tmpdir_d)
                rs.add_finding("https://a.example.co/x", "summary", task_name="Background", depth=1)
                rs.add_finding("https://b.example.co/x", "summary", task_name="Culture", depth=1)
                rs.add_finding("Comparison A", "found nothing usable", task_name="Comparison A", depth=1)
                run_state_ctx.set(rs)
                msgs = []
                _asyncio.run(run_completion_check(
                    query="q", current_input="q", run_state=rs, notify=msgs.append))
                recorded = rs.data["completion_check_attempts"][-1]["problem"]
                assert recorded != "thin_coverage", (
                    "ratio (0.667) above threshold must not fire", recorded, msgs)
        finally:
            _IN_MEMORY_FS.clear()
            _IN_MEMORY_FS.update(saved_fs)
            reset_fetched_urls()
            if _orig_ws10 is None:
                _config.cfg["settings"].pop("workspace", None)
            else:
                _config.cfg["settings"]["workspace"] = _orig_ws10

    contextvars.copy_context().run(_thin_coverage_wiring_scenario)

    # --- check_uneven_task_investment wiring (thin_coverage's blind spot: a task with 1 thin
    # source still counts as "covered" there; this instead compares real-source COUNTS across
    # covered tasks) ---
    def _uneven_task_investment_scenario():
        from tools.core import tool_quotas_ctx as q_ctx
        from utils.run_state import RunState

        _orig_ws11 = _config.cfg.get("settings", {}).get("workspace")
        _config.cfg["settings"]["workspace"] = {"type": "memory", "required_artifact": "final_report.md"}
        # Grounding-check disabled: irrelevant to this check's own logic, and simpler than
        # crafting a findings.md/final_report.md fixture that also passes every earlier
        # grounding gate just to reach this one.
        _orig_gc11 = _config.cfg.get("settings", {}).get("grounding_check")
        _config.cfg["settings"]["grounding_check"] = {"enabled": False}
        saved_fs = dict(_IN_MEMORY_FS)
        try:
            _IN_MEMORY_FS.clear()
            reset_fetched_urls()
            q_ctx.set({"delegate_tasks": {"used": 1, "limit": 5}})

            # (a) both tasks covered (thin_coverage's ratio is 1.0, would never fire), but one has
            # 1 source vs the other's 5 -> 1/5 = 0.2, below the 0.3 default threshold -> fires,
            # names the starved task specifically. findings.md AND final_report.md must already
            # exist (see (f)/(g) below for why -- this check reads live RunState tracking, not
            # either file, and needs both artifacts already written before it's meaningful).
            with tempfile.TemporaryDirectory() as tmpdir_a:
                # Cites both tasks (2026-07-26: check_findings_underuses_evidence now sits ahead
                # of this check in COMPLETION_CHECKS and would otherwise correctly fire first on a
                # bare "placeholder" findings.md that cites neither task's real URL).
                _IN_MEMORY_FS["findings.md"] = "- [Heuristics](https://a.example.co/x)\n- [Culture](https://b.example.co/0)"
                _IN_MEMORY_FS["final_report.md"] = "placeholder"
                rs = RunState(tmpdir_a)
                rs.add_finding("https://a.example.co/x", "summary", task_name="Heuristics", depth=1)
                for i in range(5):
                    rs.add_finding(f"https://b.example.co/{i}", "summary", task_name="Culture", depth=1)
                run_state_ctx.set(rs)
                msgs = []
                should_retry, new_input = _asyncio.run(run_completion_check(
                    query="q", current_input="q", run_state=rs, notify=msgs.append))
                recorded = rs.data["completion_check_attempts"][-1]["problem"]
                assert recorded == "uneven_task_investment", (recorded, msgs)
                assert should_retry
                assert "Heuristics" in msgs[-1], msgs

            # (b) balanced coverage (3 vs 4, ratio 0.75) -> does not fire.
            with tempfile.TemporaryDirectory() as tmpdir_b:
                _IN_MEMORY_FS["findings.md"] = "- [A](https://a.example.co/0)\n- [B](https://b.example.co/0)"
                _IN_MEMORY_FS["final_report.md"] = "placeholder"
                rs = RunState(tmpdir_b)
                for i in range(3):
                    rs.add_finding(f"https://a.example.co/{i}", "summary", task_name="A", depth=1)
                for i in range(4):
                    rs.add_finding(f"https://b.example.co/{i}", "summary", task_name="B", depth=1)
                run_state_ctx.set(rs)
                msgs = []
                _asyncio.run(run_completion_check(
                    query="q", current_input="q", run_state=rs, notify=msgs.append))
                recorded = rs.data["completion_check_attempts"][-1]["problem"]
                assert recorded != "uneven_task_investment", (recorded, msgs)

            # (c) only 1 covered task -> min_tasks gate blocks it regardless of imbalance.
            with tempfile.TemporaryDirectory() as tmpdir_c:
                _IN_MEMORY_FS["findings.md"] = "- [A](https://a.example.co/0)"
                _IN_MEMORY_FS["final_report.md"] = "placeholder"
                rs = RunState(tmpdir_c)
                for i in range(5):
                    rs.add_finding(f"https://a.example.co/{i}", "summary", task_name="A", depth=1)
                run_state_ctx.set(rs)
                msgs = []
                _asyncio.run(run_completion_check(
                    query="q", current_input="q", run_state=rs, notify=msgs.append))
                recorded = rs.data["completion_check_attempts"][-1]["problem"]
                assert recorded != "uneven_task_investment", (recorded, msgs)

            # (d) ratio alone WOULD fire (1/5 = 0.2 < 0.3) but total volume (6) is below an
            # explicitly-lowered min_total_sources gate (10) -- isolates the absolute-volume gate
            # from the ratio gate, since the two can't be isolated with default thresholds alone
            # (a ratio under 0.3 with a count-of-1 starved task always sums to >= min_total_sources
            # at its default of 4).
            _orig_cfg = _config.cfg["settings"].get("uneven_coverage_check")
            _config.cfg["settings"]["uneven_coverage_check"] = {"min_total_sources": 10}
            try:
                with tempfile.TemporaryDirectory() as tmpdir_d:
                    _IN_MEMORY_FS["findings.md"] = "- [Heuristics](https://a.example.co/x)\n- [Culture](https://b.example.co/0)"
                    _IN_MEMORY_FS["final_report.md"] = "placeholder"
                    rs = RunState(tmpdir_d)
                    rs.add_finding("https://a.example.co/x", "summary", task_name="Heuristics", depth=1)
                    for i in range(5):
                        rs.add_finding(f"https://b.example.co/{i}", "summary", task_name="Culture", depth=1)
                    run_state_ctx.set(rs)
                    msgs = []
                    _asyncio.run(run_completion_check(
                        query="q", current_input="q", run_state=rs, notify=msgs.append))
                    recorded = rs.data["completion_check_attempts"][-1]["problem"]
                    assert recorded != "uneven_task_investment", (
                        "total sources (6) below min_total_sources (10) must block firing "
                        "even though the ratio alone would qualify", recorded, msgs)
            finally:
                if _orig_cfg is None:
                    _config.cfg["settings"].pop("uneven_coverage_check", None)
                else:
                    _config.cfg["settings"]["uneven_coverage_check"] = _orig_cfg

            # (e) ratio exactly AT threshold (3/10 = 0.3) must not fire -- only below it, same
            # "below, not at-or-below" convention as check_thin_coverage's own boundary test.
            with tempfile.TemporaryDirectory() as tmpdir_e:
                _IN_MEMORY_FS["findings.md"] = "- [Heuristics](https://a.example.co/0)\n- [Culture](https://b.example.co/0)"
                _IN_MEMORY_FS["final_report.md"] = "placeholder"
                rs = RunState(tmpdir_e)
                for i in range(3):
                    rs.add_finding(f"https://a.example.co/{i}", "summary", task_name="Heuristics", depth=1)
                for i in range(10):
                    rs.add_finding(f"https://b.example.co/{i}", "summary", task_name="Culture", depth=1)
                run_state_ctx.set(rs)
                msgs = []
                _asyncio.run(run_completion_check(
                    query="q", current_input="q", run_state=rs, notify=msgs.append))
                recorded = rs.data["completion_check_attempts"][-1]["problem"]
                assert recorded != "uneven_task_investment", (
                    "ratio exactly AT threshold (0.3) must not fire -- only below it", recorded, msgs)

            # (f) REGRESSION TEST 1 (live-confirmed 2026-07-23): same severe imbalance as (a), but
            # neither findings.md NOR final_report.md exist yet -> must NOT fire, even though the
            # ratio/volume math alone would qualify. Without this gate, this check sits ahead of
            # check_missing_findings in COMPLETION_CHECKS and can win "first verdict wins" every
            # attempt purely on live RunState tracking, permanently starving check_missing_findings
            # of a turn -- confirmed live: a real run fired this check 4 consecutive times and
            # ended with findings.md NEVER written at all. check_missing_findings (or another
            # earlier-firing check) must get the verdict instead.
            with tempfile.TemporaryDirectory() as tmpdir_f:
                _IN_MEMORY_FS.pop("findings.md", None)
                _IN_MEMORY_FS.pop("final_report.md", None)
                rs = RunState(tmpdir_f)
                rs.add_finding("https://a.example.co/x", "summary", task_name="Heuristics", depth=1)
                for i in range(5):
                    rs.add_finding(f"https://b.example.co/{i}", "summary", task_name="Culture", depth=1)
                run_state_ctx.set(rs)
                msgs = []
                _asyncio.run(run_completion_check(
                    query="q", current_input="q", run_state=rs, notify=msgs.append))
                recorded = rs.data["completion_check_attempts"][-1]["problem"]
                assert recorded != "uneven_task_investment", (
                    "must never fire before findings.md exists, regardless of how severe the "
                    "live-tracked imbalance is -- this is the exact 2026-07-23 regression #1",
                    recorded, msgs)

            # (g) REGRESSION TEST 2 (live-confirmed 2026-07-23, SAME session, right after (f) was
            # fixed): findings.md exists now but final_report.md does NOT yet -> must still NOT
            # fire. Gating on findings.md alone (the first fix) left this check ahead of
            # check_missing_artifact in COMPLETION_CHECKS -- confirmed live: the very next run
            # after fixing (f) wrote findings.md successfully, then fired this check 4 consecutive
            # times and STILL ended with final_report.md never written, because it kept winning
            # "first verdict wins" over check_missing_artifact. check_missing_artifact must get
            # the verdict instead until the Builder actually gets dispatched at least once.
            with tempfile.TemporaryDirectory() as tmpdir_g:
                _IN_MEMORY_FS["findings.md"] = "- [Heuristics](https://a.example.co/x)\n- [Culture](https://b.example.co/0)"
                _IN_MEMORY_FS.pop("final_report.md", None)
                rs = RunState(tmpdir_g)
                rs.add_finding("https://a.example.co/x", "summary", task_name="Heuristics", depth=1)
                for i in range(5):
                    rs.add_finding(f"https://b.example.co/{i}", "summary", task_name="Culture", depth=1)
                run_state_ctx.set(rs)
                msgs = []
                _asyncio.run(run_completion_check(
                    query="q", current_input="q", run_state=rs, notify=msgs.append))
                recorded = rs.data["completion_check_attempts"][-1]["problem"]
                assert recorded != "uneven_task_investment", (
                    "must never fire before final_report.md exists, even once findings.md does "
                    "-- this is the exact 2026-07-23 regression #2", recorded, msgs)
                assert recorded == "missing_artifact", (
                    "check_missing_artifact must get the verdict here, not "
                    "check_findings_underuses_evidence (findings.md already cites both tasks)",
                    recorded, msgs)
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

    contextvars.copy_context().run(_uneven_task_investment_scenario)

    # --- check_findings_underuses_evidence (2026-07-26 live case: a balanced 2-facet run, green
    # tea + Roman Empire, both genuinely "covered" per coverage() -- FindingsWriter still wrote a
    # findings.md covering only one topic, and nothing caught the other topic's total omission).
    # Direct calls against the check function itself, not the full run_completion_check pipeline,
    # since the min_tasks gate and "at least one URL present" logic is self-contained. ---
    def _findings_underuses_evidence_scenario():
        from utils.run_state import RunState

        with tempfile.TemporaryDirectory() as tmpdir:
            rs = RunState(tmpdir)
            rs.add_finding("https://a.example.co/x", "s", task_name="green_tea", depth=1)
            rs.add_finding("https://b.example.co/y", "s", task_name="roman_empire", depth=1)
            # Nested (depth>1) Analyzer findings must be ignored -- only top-level task coverage
            # matters here, same convention as RunState.coverage() itself.
            rs.add_finding("https://a.example.co/nested", "s", task_name="green_tea", depth=2)

            def _ctx(findings_content):
                return Ctx(req_artifact="final_report.md", attempt=0, max_attempts=8,
                           delegated=True, files=["findings.md"], content=None,
                           quotas=None, run_state=rs)

            # (a) findings.md cites ONLY green_tea's URL -> roman_empire is entirely dropped, fires.
            verdict = check_findings_underuses_evidence(_ctx(None))
            # get_workspace_file_content reads the real workspace, not ctx.content -- set it up.
            from tools.fs import _IN_MEMORY_FS
            _orig_ws = _config.cfg.get("settings", {}).get("workspace")
            _config.cfg["settings"]["workspace"] = {"type": "memory"}
            saved_fs = dict(_IN_MEMORY_FS)
            try:
                _IN_MEMORY_FS.clear()
                _IN_MEMORY_FS["findings.md"] = "- [Green Tea](https://a.example.co/x)\n- more green tea content."
                verdict = check_findings_underuses_evidence(_ctx(None))
                assert verdict is not None and verdict.problem == "findings_underuses_evidence", verdict
                assert "roman_empire" in verdict.warning, verdict.warning

                # (b) findings.md cites BOTH tasks' real URLs -> no problem, even though citation
                # counts are uneven (that's check_uneven_task_investment's job, not this one).
                _IN_MEMORY_FS["findings.md"] = (
                    "- [Green Tea](https://a.example.co/x)\n- [Roman Empire](https://b.example.co/y)")
                assert check_findings_underuses_evidence(_ctx(None)) is None

                # (c) only 1 covered top-level task -> min_tasks gate blocks it regardless.
                rs2 = RunState(tmpdir)
                rs2.add_finding("https://a.example.co/x", "s", task_name="green_tea", depth=1)
                _IN_MEMORY_FS["findings.md"] = "no citations at all"
                ctx_one_task = Ctx(req_artifact="final_report.md", attempt=0, max_attempts=8,
                                    delegated=True, files=["findings.md"], content=None,
                                    quotas=None, run_state=rs2)
                assert check_findings_underuses_evidence(ctx_one_task) is None

                # (d) findings.md missing entirely -> not this check's job (check_missing_findings).
                _IN_MEMORY_FS.pop("findings.md", None)
                ctx_no_findings = Ctx(req_artifact="final_report.md", attempt=0, max_attempts=8,
                                       delegated=True, files=[], content=None,
                                       quotas=None, run_state=rs)
                assert check_findings_underuses_evidence(ctx_no_findings) is None
            finally:
                _IN_MEMORY_FS.clear()
                _IN_MEMORY_FS.update(saved_fs)
                if _orig_ws is None:
                    _config.cfg["settings"].pop("workspace", None)
                else:
                    _config.cfg["settings"]["workspace"] = _orig_ws

    contextvars.copy_context().run(_findings_underuses_evidence_scenario)

    # --- _update_task_verification / check_task_verification_flagged (2026-07-26, VERIMAP-inspired,
    # RESEARCH.md Sec.9): a structural per-task verification ledger -- fires when a task's EVERY
    # finding was excluded by _is_citable_finding (fabricated/off-topic/contradicted), distinct
    # from check_thin_coverage (zero findings at all) and check_uneven_task_investment (uneven
    # COUNTS across covered tasks). ---
    def _task_verification_scenario():
        from utils.run_state import RunState

        with tempfile.TemporaryDirectory() as tmpdir:
            rs = RunState(tmpdir)
            # task_kept: one real citable finding -> "verified".
            rs.add_finding("https://a.example.co/x", "real content", task_name="task_kept", depth=1)
            # task_flagged: only a verification-warning-flagged finding -> "flagged".
            rs.add_finding(
                "https://b.example.co/y",
                "N-BEATS improved forecast accuracy by 23%.\n\n"
                "[SYSTEM VERIFICATION WARNING: this summary cites a URL that does not match "
                "anything actually fetched this run (claim_unsupported:https://b.example.co/y).]",
                task_name="task_flagged", depth=1,
            )
            # Nested (depth>1) finding must be ignored, same convention as coverage()/
            # check_findings_underuses_evidence.
            rs.add_finding("https://a.example.co/nested", "s", task_name="task_kept", depth=2)

            _update_task_verification(rs)
            ledger = rs.data["task_verification"]
            assert ledger["task_kept"]["status"] == "verified", ledger["task_kept"]
            assert ledger["task_flagged"]["status"] == "flagged", ledger["task_flagged"]
            assert "SYSTEM VERIFICATION WARNING" in ledger["task_flagged"]["reason"], ledger["task_flagged"]
            # A task with no findings at all is NOT persisted -- still pending, not a problem.
            assert "task_never_dispatched" not in ledger

            # A task that later produces a real citable finding flips back to "verified" -- the
            # ledger is fully recomputed each call, never incrementally patched.
            rs.add_finding("https://b.example.co/z", "real content this time", task_name="task_flagged", depth=1)
            _update_task_verification(rs)
            assert rs.data["task_verification"]["task_flagged"]["status"] == "verified"

            # Ledger rollup (2026-08-17 live incident): a depth==1 task whose OWN findings are all
            # empty-summary (the zero-trailing-text synthesis-vanishing mechanism) must still read
            # "verified" if a nested Analyzer it dispatched -- carrying top_level_task_name back to
            # it -- produced real citable content. Without this, Lisbon_digital_nomad_visa-shaped
            # tasks read "flagged: no real citable source" despite 2 of 3 nested Analyzers having
            # full real content, and the Planner acknowledges a gap that doesn't exist.
            rs10 = RunState(tmpdir)
            rs10.add_finding("https://c.example.co/1", "", task_name="task_rollup", depth=1)
            rs10.add_finding("https://c.example.co/2", "", task_name="task_rollup", depth=1)
            rs10.add_finding(
                "https://c.example.co/analyzed", "real, substantive analysis of the fetched page",
                task_name="Analyze c_example_page", depth=2, top_level_task_name="task_rollup",
            )
            _update_task_verification(rs10)
            assert rs10.data["task_verification"]["task_rollup"]["status"] == "verified", \
                rs10.data["task_verification"]["task_rollup"]
            # A depth>1 finding with NO top_level_task_name (older data, or a non-rollup caller)
            # must not spuriously create or flip a ledger entry -- same "ignored" convention as the
            # depth==2 case asserted above, now stated for the field's absence specifically.
            rs10.add_finding("https://c.example.co/orphan", "some content", task_name="orphan_child", depth=2)
            _update_task_verification(rs10)
            assert "orphan_child" not in rs10.data["task_verification"]

            # check_task_verification_flagged wiring: re-flag task_flagged and confirm the check
            # fires, names it, and leaves task_kept alone.
            rs2 = RunState(tmpdir)
            rs2.add_finding("https://a.example.co/x", "real content", task_name="task_kept", depth=1)
            rs2.add_finding(
                "https://b.example.co/y",
                "[SYSTEM RELEVANCE WARNING: none of the sources fetched for this task actually "
                "mention the required entity.]",
                task_name="task_flagged", depth=1,
            )
            _update_task_verification(rs2)
            ctx = Ctx(req_artifact="final_report.md", attempt=0, max_attempts=8, delegated=True,
                      files=[], content=None, quotas=None, run_state=rs2)
            verdict = check_task_verification_flagged(ctx)
            assert verdict is not None and verdict.problem == "task_verification_flagged", verdict
            assert "task_flagged" in verdict.warning and "task_kept" not in verdict.warning, verdict.warning

            # Quota NOT exhausted (used < limit) -> original "delegate_tasks again" directive
            # fires. Checked BEFORE the exhausted case below, on its own fresh ledger snapshot --
            # the exhausted case now mutates rs2's ledger (gap_acknowledged), so order matters.
            rs2b = RunState(tmpdir)
            rs2b.add_finding("https://a.example.co/x", "real content", task_name="task_kept", depth=1)
            rs2b.add_finding(
                "https://b.example.co/y",
                "[SYSTEM RELEVANCE WARNING: none of the sources fetched for this task actually "
                "mention the required entity.]",
                task_name="task_flagged", depth=1,
            )
            _update_task_verification(rs2b)
            ctx_quota_ok = Ctx(req_artifact="final_report.md", attempt=0, max_attempts=8,
                                delegated=True, files=[], content=None,
                                quotas={"delegate_tasks": {"used": 2, "limit": 6}}, run_state=rs2b)
            verdict_ok = check_task_verification_flagged(ctx_quota_ok)
            assert "delegate_tasks again" in verdict_ok.inject, verdict_ok.inject

            # Quota-aware directive (2026-07-27 live regression, delegate_tasks tightened 15->6):
            # telling the Planner to "delegate_tasks again" when its delegate_tasks quota is
            # already exhausted produced 4 wasted attempts of the Planner narrating a fake report
            # as chat text instead. With quota exhausted, the directive must say to stop, never
            # "delegate_tasks again".
            ctx_quota_exhausted = Ctx(req_artifact="final_report.md", attempt=0, max_attempts=8,
                                       delegated=True, files=[], content=None,
                                       quotas={"delegate_tasks": {"used": 6, "limit": 6}}, run_state=rs2)
            verdict_exhausted = check_task_verification_flagged(ctx_quota_exhausted)
            assert verdict_exhausted is not None and verdict_exhausted.problem == "task_verification_flagged"
            assert "delegate_tasks again" not in verdict_exhausted.inject, verdict_exhausted.inject
            assert "stop" in verdict_exhausted.inject.lower(), verdict_exhausted.inject

            # gap_acknowledged oscillation fix (2026-08-16 live incident): a real run had
            # retry_quota_topup refill delegate_tasks' LIMIT on a later completion-check attempt,
            # flipping quota_exhausted back to False and making this check reissue "delegate_tasks
            # again" for a task it had JUST told the model to stop redelegating -- the model then
            # degraded into narrating instead of calling tools across the resulting stop/redo/stop
            # oscillation, and the run only survived via final_report.md salvage. Once quota_
            # exhausted has fired for a task (as it just did on rs2/ctx_quota_exhausted above), a
            # later quota top-up making quota look available again must NOT resurrect the "redo
            # it" directive for that same task.
            ctx_quota_refilled = Ctx(req_artifact="final_report.md", attempt=0, max_attempts=8,
                                      delegated=True, files=[], content=None,
                                      quotas={"delegate_tasks": {"used": 6, "limit": 9}}, run_state=rs2)
            assert check_task_verification_flagged(ctx_quota_refilled) is None, (
                "a task already told to stop must not be re-nudged just because quota was topped up")
            # Full-ledger recompute (e.g. next completion-check attempt) must preserve the
            # acknowledgment -- confirmed via _update_task_verification, not just an in-memory dict.
            _update_task_verification(rs2)
            assert rs2.data["task_verification"]["task_flagged"]["gap_acknowledged"] is True
            assert check_task_verification_flagged(ctx_quota_refilled) is None, (
                "gap_acknowledged must survive a full ledger recompute, not just the original dict")

            # No flagged tasks at all -> no verdict.
            rs3 = RunState(tmpdir)
            rs3.add_finding("https://a.example.co/x", "real content", task_name="task_kept", depth=1)
            _update_task_verification(rs3)
            ctx3 = Ctx(req_artifact="final_report.md", attempt=0, max_attempts=8, delegated=True,
                       files=[], content=None, quotas=None, run_state=rs3)
            assert check_task_verification_flagged(ctx3) is None

            # --- "superseded" (2026-07-26 follow-up, same day: a flagged task got redispatched
            # under a RENAMED task_name instead of being retried under its own name, and the
            # renamed sibling succeeded -- check_task_verification_flagged kept nudging the stale
            # original name forever, burning the entire retry budget with zero report ever
            # written). A flagged task whose dispatched instructions closely match an already-
            # verified task's instructions must be downgraded to "superseded", not left "flagged". ---
            rs4 = RunState(tmpdir)
            rs4.data["dispatched_tasks"] = [
                {"task_name": "task_flagged", "instructions": "Research the K-Pg boundary definition and age."},
                {"task_name": "task_kept", "instructions": "Research the K-Pg boundary definition and age, narrower focus."},
            ]
            rs4.add_finding("https://a.example.co/x", "real content", task_name="task_kept", depth=1)
            rs4.add_finding(
                "https://b.example.co/y",
                "[SYSTEM VERIFICATION WARNING: stub_source:https://b.example.co/y]",
                task_name="task_flagged", depth=1,
            )
            _update_task_verification(rs4)
            assert rs4.data["task_verification"]["task_flagged"]["status"] == "superseded", (
                rs4.data["task_verification"]["task_flagged"])
            ctx4 = Ctx(req_artifact="final_report.md", attempt=0, max_attempts=8, delegated=True,
                       files=[], content=None, quotas=None, run_state=rs4)
            assert check_task_verification_flagged(ctx4) is None, (
                "a superseded task must not still be nudged as flagged")

            # Negative case: two genuinely UNRELATED tasks (one flagged, one verified, dissimilar
            # instructions) must NOT be superseded -- the flagged one stays flagged and still gets
            # nudged, since it was never actually covered by anything else.
            rs5 = RunState(tmpdir)
            rs5.data["dispatched_tasks"] = [
                {"task_name": "task_flagged", "instructions": "Research the K-Pg boundary definition and age."},
                {"task_name": "task_kept", "instructions": "Find the current population of Bogota, Colombia."},
            ]
            rs5.add_finding("https://a.example.co/x", "real content", task_name="task_kept", depth=1)
            rs5.add_finding(
                "https://b.example.co/y",
                "[SYSTEM VERIFICATION WARNING: stub_source:https://b.example.co/y]",
                task_name="task_flagged", depth=1,
            )
            _update_task_verification(rs5)
            assert rs5.data["task_verification"]["task_flagged"]["status"] == "flagged", (
                rs5.data["task_verification"]["task_flagged"])
            ctx5 = Ctx(req_artifact="final_report.md", attempt=0, max_attempts=8, delegated=True,
                       files=[], content=None, quotas=None, run_state=rs5)
            verdict5 = check_task_verification_flagged(ctx5)
            assert verdict5 is not None and "task_flagged" in verdict5.warning, verdict5

            # --- 2026-07-29 live incident: an untracked_delegation attempt sandwiched between two
            # task_verification_flagged attempts must NOT reset the escalation streak -- it's a
            # direct symptom of the model failing to comply with THIS check's own "stop
            # redelegating" directive, not a genuinely different problem. Before this fix, this
            # exact sequence kept getting the fresh "delegate_tasks again" directive forever
            # instead of ever escalating to "acknowledge the gap." ---
            rs6 = RunState(tmpdir)
            rs6.add_finding(
                "https://b.example.co/y",
                "[SYSTEM VERIFICATION WARNING: stub_source:https://b.example.co/y]",
                task_name="task_flagged", depth=1,
            )
            _update_task_verification(rs6)
            rs6.data["completion_check_attempts"] = [
                {"problem": "task_verification_flagged"},
                {"problem": "untracked_delegation"},
            ]
            ctx6 = Ctx(req_artifact="final_report.md", attempt=2, max_attempts=8, delegated=True,
                       files=[], content=None, quotas=None, run_state=rs6)
            verdict6 = check_task_verification_flagged(ctx6)
            assert verdict6 is not None
            assert "delegate_tasks again" not in verdict6.inject, (
                "untracked_delegation interrupting the streak must not reset prior_same to 0", verdict6.inject)
            assert "acknowledged gap" in verdict6.inject, verdict6.inject
            # The .warning (human/log-facing) field must reflect the SAME branch as .inject
            # (model-facing) -- confirmed live this was a static string claiming "redo them
            # specifically" even on the acknowledge-the-gap branch, making a stuck run's real
            # cause harder to diagnose from _run_state.json alone.
            assert "redo them specifically" not in verdict6.warning, verdict6.warning
            assert "acknowledge" in verdict6.warning.lower(), verdict6.warning

            # A genuinely different, unrelated problem in between (not untracked_delegation) still
            # correctly breaks the streak -- this fix is scoped to that one specific symptom, not a
            # blanket "ignore any interruption."
            rs7 = RunState(tmpdir)
            rs7.add_finding(
                "https://b.example.co/y",
                "[SYSTEM VERIFICATION WARNING: stub_source:https://b.example.co/y]",
                task_name="task_flagged", depth=1,
            )
            _update_task_verification(rs7)
            rs7.data["completion_check_attempts"] = [
                {"problem": "task_verification_flagged"},
                {"problem": "missing_artifact"},
            ]
            ctx7 = Ctx(req_artifact="final_report.md", attempt=2, max_attempts=8, delegated=True,
                       files=[], content=None, quotas=None, run_state=rs7)
            verdict7 = check_task_verification_flagged(ctx7)
            assert "delegate_tasks again" in verdict7.inject, (
                "an unrelated interrupting problem SHOULD still reset the streak", verdict7.inject)

            # --- 2026-07-31 live incident (gpt-oss AND Ornith-1.0-9B, same night): this check sits
            # ABOVE check_missing_findings/check_missing_artifact in COMPLETION_CHECKS and isn't
            # itself Builder/FindingsWriter-fixable, so as long as one task stays flagged it wins
            # first-match on EVERY attempt, permanently starving the checks that actually dispatch
            # a real writer role -- confirmed live: findings.md never got written despite real,
            # usable findings for every OTHER task, and the quota_exhausted branch's own directive
            # ("the writer roles will note X as an acknowledged gap") never came true because those
            # writer roles never got dispatched. Must return None on a 3rd+ occurrence so the
            # pipeline falls through. ---
            rs8 = RunState(tmpdir)
            rs8.add_finding(
                "https://b.example.co/y",
                "[SYSTEM VERIFICATION WARNING: stub_source:https://b.example.co/y]",
                task_name="task_flagged", depth=1,
            )
            _update_task_verification(rs8)
            rs8.data["completion_check_attempts"] = [
                {"problem": "task_verification_flagged"},
                {"problem": "task_verification_flagged"},
                {"problem": "task_verification_flagged"},
            ]
            ctx8 = Ctx(req_artifact="final_report.md", attempt=3, max_attempts=8, delegated=True,
                       files=[], content=None, quotas=None, run_state=rs8)
            assert check_task_verification_flagged(ctx8) is None, (
                "a 4th+ consecutive occurrence must go quiet and yield to missing_findings/"
                "missing_artifact instead of permanently starving them")

            # Below the cap (3rd occurrence, prior_same==2): must still fire normally, so
            # force_whole_rebuild's own one-extra-attempt escalation gets its turn first.
            rs9 = RunState(tmpdir)
            rs9.add_finding(
                "https://b.example.co/y",
                "[SYSTEM VERIFICATION WARNING: stub_source:https://b.example.co/y]",
                task_name="task_flagged", depth=1,
            )
            _update_task_verification(rs9)
            rs9.data["completion_check_attempts"] = [
                {"problem": "task_verification_flagged"},
                {"problem": "task_verification_flagged"},
            ]
            ctx9 = Ctx(req_artifact="final_report.md", attempt=2, max_attempts=8, delegated=True,
                       files=[], content=None, quotas=None, run_state=rs9)
            assert check_task_verification_flagged(ctx9) is not None, (
                "the 3rd occurrence itself must still fire (force_whole_rebuild's turn)")

    contextvars.copy_context().run(_task_verification_scenario)



if __name__ == "__main__":
    main()
    print("test_coverage_and_thin_coverage OK")
