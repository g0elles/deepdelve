"""Smallest thing that fails if src/api.py's run-lifecycle loop breaks. api.py had ZERO test
coverage before this file (confirmed via codegraph before this fix) -- this pins the one new
piece of logic added 2026-08-24: malformed-tool-call retry + QuotaAbortException handling in
_run_research's stream-consumption loop, mirroring run_cli/run_agent's own already-tested copies
of this exact pattern (engine/tui.py). Also pins _worker/_job_queue's single-flight guarantee
(added after a QA audit flagged it as untested) -- ARCHITECTURE.md §5's whole safety argument for
api.py's module-level globals (_session, tui.py's session-log state) not being contextvar-safe is
that the FIFO queue+worker never runs two jobs concurrently "by construction, not by a lock"; this
was previously asserted only in prose, never verified.

2026-09-09: pins three fixes closing a real drift from run_cli's ALREADY-fixed behavior, found by
a QA audit tracing all three run-lifecycle loops in full: (1) QuotaAbortException falling through
into run_completion_check instead of `break`ing past it (run_cli fixed this exact mistake
2026-08-27; api.py never got the update); (2) a deadline-exceeded cutoff now emits a system event
instead of silently forcing the final-verdict path; (3) context_budget_chars accounting for a
completion-check-injected message (mirrors run_cli's own re-scan) -- this third one has no
dedicated test here (run_stream_chars is a local var with no externally observable effect in this
black-box harness), verified instead by direct code symmetry with run_cli's already-correct copy,
not independent test coverage.

Does not exercise the rest of _run_research (SSE draining, file uploads) -- still uncovered; a
fuller api.py test harness is a separate, larger undertaking than this one targeted pin.

2026-09-11: _run_research's inline loop was replaced by a call into the shared
engine.run_loop.run_agent_loop (Phase 2 of the run_cli/run_agent/_run_research lifecycle-loop
unification, see session_status/CURRENT.md and ~/.claude/plans/polymorphic-wibbling-fiddle.md).
run_completion_check is now called through run_loop's own binding, not api.py's re-export --
_run_scenario patches engine.run_loop.run_completion_check accordingly. This migration closes a
real, previously-undocumented gap for free: _run_research used to only call run_state.save() on
asyncio.CancelledError, never on a generic exception, so a crash mid-run left no forensic
_run_state.json update; run_agent_loop's own except-Exception wrapper now saves on every exit
path, pinned below by a dedicated regression case.

Run: ~/.venvs/deepdelve/bin/python test_api.py (no framework needed, same convention as
test_tools.py/test_structural_checks.py).
"""
import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

import config


class _FakeStream:
    """Minimal stand-in for the agent-framework's stream object: iter_agent_stream drives it via
    stream.__aiter__().__anext__(), wrapped in asyncio.wait_for -- a plain async generator
    satisfies that contract without needing the real SDK's stream class."""
    def __init__(self, raise_exc=None):
        self._raise_exc = raise_exc
        self._raised = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._raise_exc is not None and not self._raised:
            self._raised = True
            raise self._raise_exc
        raise StopAsyncIteration


class _FakeAgent:
    def __init__(self, first_call_exc):
        self.call_count = 0
        self._first_call_exc = first_call_exc

    def run(self, current_input, session=None, stream=True):
        self.call_count += 1
        if self.call_count == 1:
            return _FakeStream(raise_exc=self._first_call_exc)
        return _FakeStream(raise_exc=None)


def _run_scenario(first_call_exc, expect_completion_check_called, extra_settings=None,
                   expect_exception=False):
    """expect_exception: when True, _run_research is expected to raise first_call_exc straight
    through (the reraise=True classify_malformed_retry path -- a generic, unrecognized exception),
    and the caller inspects run_state.save() directly rather than the collected event list."""
    import api
    import engine.run_loop as run_loop

    with tempfile.TemporaryDirectory() as tmpdir:
        orig_ws = config.cfg.get("settings", {}).get("workspace")
        config.cfg.setdefault("settings", {})["workspace"] = {"type": "disk", "dir": tmpdir}

        orig_extra = {}
        for key, val in (extra_settings or {}).items():
            orig_extra[key] = config.cfg["settings"].get(key)
            config.cfg["settings"][key] = val

        from utils.run_state import RunState

        orig_create_local_agent = api.create_local_agent
        # run_agent_loop calls run_completion_check via its OWN imported binding in
        # engine/run_loop.py, not through api.py's re-export -- api.py no longer calls it
        # directly since the run_cli/run_agent/_run_research lifecycle-loop unification (see
        # session_status/CURRENT.md, 2026-09-11), so the fake must be patched there instead.
        orig_run_completion_check = run_loop.run_completion_check
        orig_write_bib = api._write_bibliography
        orig_export_pdf = api._export_pdf
        orig_run_state_save = RunState.save

        completion_check_calls = []
        save_calls = []

        def _fake_create_local_agent(builder, subagent_callback=None, session_data=None):
            return _FakeAgent(first_call_exc), None, None

        async def _fake_run_completion_check(**kwargs):
            completion_check_calls.append(kwargs)
            return False, kwargs["current_input"]

        def _spy_save(self):
            save_calls.append(True)
            orig_run_state_save(self)

        api.create_local_agent = _fake_create_local_agent
        run_loop.run_completion_check = _fake_run_completion_check
        api._write_bibliography = lambda run_state: None
        api._export_pdf = lambda run_id: (None, None)
        RunState.save = _spy_save

        try:
            events = asyncio.Queue()

            class _FakeApp:
                class state:
                    builder = None
            api.app = _FakeApp()

            if expect_exception:
                raised = None
                try:
                    asyncio.run(api._run_research("test_run_id", "test query", {"mode": "fresh"}, events))
                except type(first_call_exc) as e:
                    raised = e
                assert raised is not None and str(raised) == str(first_call_exc), raised
                # Gap-E regression pin: a generic unrecognized exception must still call
                # run_state.save() (via run_agent_loop's own except-Exception wrapper) before
                # propagating to _worker -- previously _run_research only saved on
                # asyncio.CancelledError, so _run_state.json never got this crash's forensics.
                return save_calls

            asyncio.run(api._run_research("test_run_id", "test query", {"mode": "fresh"}, events))

            collected = []
            while not events.empty():
                collected.append(events.get_nowait())
            assert (len(completion_check_calls) > 0) == expect_completion_check_called, (
                f"expected completion-check called={expect_completion_check_called}, "
                f"got {len(completion_check_calls)} calls; events={collected}"
            )
            return collected
        finally:
            api.create_local_agent = orig_create_local_agent
            run_loop.run_completion_check = orig_run_completion_check
            api._write_bibliography = orig_write_bib
            api._export_pdf = orig_export_pdf
            RunState.save = orig_run_state_save
            if orig_ws is None:
                config.cfg["settings"].pop("workspace", None)
            else:
                config.cfg["settings"]["workspace"] = orig_ws
            for key, val in orig_extra.items():
                if val is None:
                    config.cfg["settings"].pop(key, None)
                else:
                    config.cfg["settings"][key] = val


async def _queue_serialization_scenario():
    """Two jobs enqueued back-to-back must never run concurrently -- _worker() pulls one job at a
    time and awaits it fully before calling _job_queue.get() again. Proves that by construction,
    not by inspecting the source: a fake _run_research tracks how many instances are in flight at
    once and records start/end order, so a regression that let _worker overlap two jobs (e.g. a
    stray asyncio.create_task instead of an awaited call) would show max_concurrent > 1 or
    interleaved start/end order, not just a slower test."""
    import api

    concurrent = 0
    max_concurrent = 0
    order = []

    async def _fake_run_research(run_id, query, opts, events):
        nonlocal concurrent, max_concurrent
        concurrent += 1
        max_concurrent = max(max_concurrent, concurrent)
        order.append(("start", run_id))
        await asyncio.sleep(0.05)
        order.append(("end", run_id))
        concurrent -= 1

    orig_run_research = api._run_research
    orig_jobs = api._jobs
    orig_queue = api._job_queue
    orig_worker_started = api._worker_started
    api._run_research = _fake_run_research
    api._jobs = {}
    api._job_queue = asyncio.Queue()
    # Bypass _ensure_worker (module-global, would spawn against the REAL _job_queue) -- start our
    # own worker directly against the swapped-in fake queue/jobs, same object _worker() closes
    # over via the module attribute, then mark _worker_started so nothing else double-spawns one.
    api._worker_started = True
    worker_task = asyncio.create_task(api._worker())

    try:
        for run_id in ("run_a", "run_b"):
            api._jobs[run_id] = {
                "status": "queued", "queue": asyncio.Queue(), "error": None, "task": None, "query": "q",
            }
            await api._job_queue.put((run_id, "q", {"mode": "fresh"}))

        for run_id in ("run_a", "run_b"):
            while True:
                event = await asyncio.wait_for(api._jobs[run_id]["queue"].get(), timeout=5)
                if event.get("type") == "done":
                    break

        assert max_concurrent == 1, f"expected single-flight (max 1 concurrent job), got {max_concurrent}"
        assert order == [("start", "run_a"), ("end", "run_a"), ("start", "run_b"), ("end", "run_b")], order
    finally:
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass
        api._run_research = orig_run_research
        api._jobs = orig_jobs
        api._job_queue = orig_queue
        api._worker_started = orig_worker_started


def main():
    # --- Malformed tool call: must retry once (via classify_malformed_retry), not crash the job.
    # Before this fix, this exact exception propagated straight out of _run_research uncaught. ---
    events = _run_scenario(Exception("error parsing tool call: bad json"), expect_completion_check_called=True)
    retry_events = [e for e in events if "malformed tool call" in e.get("text", "")]
    assert retry_events, f"expected a retry notification, got {events}"
    assert "1/2" in retry_events[0]["text"], retry_events[0]

    # --- QuotaAbortException: must abort cleanly with a system event AND still reach
    # run_completion_check (2026-09-09 fix: this used to `break` and skip it entirely, throwing
    # away real fetched research with zero quarantine-restore/salvage attempt -- the exact mistake
    # run_cli itself fixed on 2026-08-27, never propagated here until now). ---
    from tools import QuotaAbortException
    events = _run_scenario(QuotaAbortException("stuck in a loop"), expect_completion_check_called=True)
    abort_events = [e for e in events if "forcefully aborted" in e.get("text", "")]
    assert abort_events, f"expected a forced-abort notification, got {events}"
    assert "stuck in a loop" in abort_events[0]["text"], abort_events[0]

    # --- Deadline exceeded must be explicitly narrated to the client (mirrors run_cli's own
    # notify), not silently forced into the final-verdict path with zero explanation, and must
    # NOT crash the job. A negative max_run_minutes puts budget_deadline in the past
    # deterministically (no race with real wall-clock time). Two distinct notifications are
    # expected here, mirroring run_cli's two separate deadline checks: iter_agent_stream raises
    # asyncio.TimeoutError immediately (mid-stream cutoff, now caught by the inner try/except
    # added alongside this fix), then the post-loop `elif budget_deadline...` check ALSO still
    # sees the deadline exceeded (nothing advanced the clock) and fires its own notification
    # right before run_completion_check. ---
    events = _run_scenario(None, expect_completion_check_called=True, extra_settings={"max_run_minutes": -1})
    deadline_events = [e for e in events if "max_run_minutes" in e.get("text", "")]
    assert len(deadline_events) >= 2, f"expected 2 deadline notifications (mid-stream + post-loop), got {events}"
    assert any("cutting the current turn short" in e["text"] for e in deadline_events), deadline_events
    assert any("finishing with whatever exists" in e["text"] for e in deadline_events), deadline_events

    # --- A genuinely unrecognized exception must still propagate (not silently swallowed). ---
    try:
        _run_scenario(ValueError("something else entirely"), expect_completion_check_called=False)
        raise AssertionError("an unrecognized exception must propagate, not be swallowed")
    except ValueError as e:
        assert str(e) == "something else entirely", e

    # --- Gap-E regression pin (2026-09-11 lifecycle-loop unification): before this, _run_research
    # only called run_state.save() on asyncio.CancelledError -- a generic unrecognized exception
    # (this exact ValueError/reraise=True path) reached _worker's `except Exception` with
    # _run_state.json never updated for that crash. run_agent_loop's own except-Exception wrapper
    # now saves before propagating, so this must fire here too. ---
    save_calls = _run_scenario(
        ValueError("gap-e regression"), expect_completion_check_called=False, expect_exception=True,
    )
    assert save_calls, "run_state.save() must be called on a generic exception before it propagates"

    asyncio.run(_queue_serialization_scenario())

    print("All api.py assertions passed.")


if __name__ == "__main__":
    main()
