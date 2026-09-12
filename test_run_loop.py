"""Smallest thing that fails if engine/run_loop.py's shared run-lifecycle loop breaks.

Backs the run_cli/run_agent/_run_research lifecycle-loop unification (see
~/.claude/plans/polymorphic-wibbling-fiddle.md and session_status/CURRENT.md, 2026-09-11).
These tests pin run_agent_loop's own contract directly, independent of any surface, using fakes
for the agent/stream (same convention test_api.py already uses for iter_agent_stream's contract)
and a real RunState against a tempdir workspace.

Scenarios, one per axis this module's docstring calls out as load-bearing:
  1. normal completion (no retries, no budget, one turn)
  2. malformed-tool-call retry then give-up (run_state.attempt forced, completion check still runs)
  3. QuotaAbortException falls through into the completion check instead of `break`ing past it
  4. context-budget nudge-then-cutoff (one wrap-up turn, then force final verdict)
  5. wall-clock budget_deadline expiry (cuts the turn, then forces final verdict)
  6. crash-save: a genuinely unrecognized exception still calls run_state.save() before
     propagating -- the fix for the confirmed gap _run_research had (only CancelledError saved).
  7. on_malformed_give_up suppresses the default reraise (run_agent's own deliberate,
     pre-existing "never crash on an unrecognized exception" behavior, Phase 3) while
     asyncio.CancelledError still propagates regardless (needed for /stop and /cancel).

Run: ~/.venvs/deepdelve/bin/python test_run_loop.py (no framework, same convention as
test_api.py/test_tools.py).
"""
import asyncio
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from utils.run_state import RunState


class _FakeContent:
    def __init__(self, type="text", text=None, function_call=None, arguments=None, result=None):
        self.type = type
        self.text = text
        self.function_call = function_call
        self.arguments = arguments
        self.result = result


class _FakeUpdate:
    def __init__(self, contents=None, user_input_requests=None):
        self.contents = contents or []
        self.user_input_requests = user_input_requests or []


class _FakeStream:
    """Minimal stand-in for the agent-framework stream: iter_agent_stream drives it via
    stream.__aiter__().__anext__(), wrapped in asyncio.wait_for -- a plain async generator-shaped
    object satisfies that contract without the real SDK's stream class (same convention as
    test_api.py's _FakeStream)."""

    def __init__(self, updates=None, raise_exc=None):
        self._updates = list(updates or [])
        self._raise_exc = raise_exc
        self._raised = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._updates:
            return self._updates.pop(0)
        if self._raise_exc is not None and not self._raised:
            self._raised = True
            raise self._raise_exc
        raise StopAsyncIteration


class _FakeAgent:
    def __init__(self, streams):
        self._streams = list(streams)
        self.call_count = 0

    def run(self, current_input, session=None, stream=True):
        self.call_count += 1
        if self._streams:
            return self._streams.pop(0)
        return _FakeStream(updates=[])


def _make_run_state(tmpdir):
    return RunState(tmpdir)


def _make_surface(run_state, context_budget=None, budget_deadline=None, skip_completion_check=False):
    import engine.run_loop as run_loop

    recorded_content = []
    notified = []

    async def on_stream_content(content):
        recorded_content.append(content)

    def notify(msg):
        notified.append(msg)

    async def handle_approvals(requests):
        raise AssertionError("handle_approvals should not be called in these scenarios")

    surface = run_loop.RunLoopSurface(
        on_stream_content=on_stream_content,
        notify=notify,
        handle_approvals=handle_approvals,
        skip_completion_check=skip_completion_check,
        context_budget=context_budget,
        budget_deadline=budget_deadline,
        query="test query",
        dispatch_task=None,
        find_substantial_text=lambda: "",
        run_state=run_state,
    )
    return surface, recorded_content, notified


def _patch_completion_check(fn):
    import engine.run_loop as run_loop

    orig = run_loop.run_completion_check
    run_loop.run_completion_check = fn
    return orig


def _restore_completion_check(orig):
    import engine.run_loop as run_loop

    run_loop.run_completion_check = orig


def scenario_normal_completion():
    import engine.run_loop as run_loop

    with tempfile.TemporaryDirectory() as tmpdir:
        run_state = _make_run_state(tmpdir)
        surface, recorded, notified = _make_surface(run_state)

        calls = []

        async def fake_completion_check(**kwargs):
            calls.append(kwargs)
            return False, kwargs["current_input"]

        orig = _patch_completion_check(fake_completion_check)
        try:
            update = _FakeUpdate(contents=[_FakeContent(type="text", text="hello")])
            agent = _FakeAgent([_FakeStream(updates=[update])])
            asyncio.run(run_loop.run_agent_loop(agent, None, "prompt", surface))
        finally:
            _restore_completion_check(orig)

        assert agent.call_count == 1, agent.call_count
        assert len(recorded) == 1 and recorded[0].text == "hello", recorded
        assert len(calls) == 1, calls
        assert run_state.attempt == 0, run_state.attempt
    print("scenario_normal_completion: OK")


def scenario_malformed_retry_then_give_up():
    import engine.run_loop as run_loop

    with tempfile.TemporaryDirectory() as tmpdir:
        run_state = _make_run_state(tmpdir)
        surface, recorded, notified = _make_surface(run_state)

        calls = []

        async def fake_completion_check(**kwargs):
            calls.append(kwargs)
            return False, kwargs["current_input"]

        orig = _patch_completion_check(fake_completion_check)
        try:
            exc = Exception("error parsing tool call: bad json")
            # max_retries defaults to 2 inside classify_malformed_retry: 2 retries, then give up.
            agent = _FakeAgent([
                _FakeStream(raise_exc=exc),
                _FakeStream(raise_exc=exc),
                _FakeStream(raise_exc=exc),
            ])
            asyncio.run(run_loop.run_agent_loop(agent, None, "prompt", surface))
        finally:
            _restore_completion_check(orig)

        assert agent.call_count == 3, agent.call_count
        assert run_state.attempt == 10**6, run_state.attempt
        assert len(calls) == 1, calls
        give_up = [m for m in notified if "giving up on this turn" in m]
        assert give_up, notified
    print("scenario_malformed_retry_then_give_up: OK")


def scenario_quota_abort_falls_through():
    import engine.run_loop as run_loop
    from tools import QuotaAbortException

    with tempfile.TemporaryDirectory() as tmpdir:
        run_state = _make_run_state(tmpdir)
        surface, recorded, notified = _make_surface(run_state)

        calls = []

        async def fake_completion_check(**kwargs):
            calls.append(kwargs)
            return False, kwargs["current_input"]

        orig = _patch_completion_check(fake_completion_check)
        try:
            agent = _FakeAgent([_FakeStream(raise_exc=QuotaAbortException("quota exhausted"))])
            asyncio.run(run_loop.run_agent_loop(agent, None, "prompt", surface))
        finally:
            _restore_completion_check(orig)

        # The whole point of the 2026-08-27 run_cli fix this transcribes: a QuotaAbortException
        # must NOT skip the completion check (no `break` out of the outer loop).
        assert len(calls) == 1, f"QuotaAbortException must still reach run_completion_check, got {calls}"
        assert run_state.attempt == 10**6, run_state.attempt
        assert any("forcefully aborted" in m for m in notified), notified
    print("scenario_quota_abort_falls_through: OK")


def scenario_context_budget_nudge_then_cutoff():
    import engine.run_loop as run_loop

    with tempfile.TemporaryDirectory() as tmpdir:
        run_state = _make_run_state(tmpdir)
        surface, recorded, notified = _make_surface(run_state, context_budget=10)

        calls = []

        async def fake_completion_check(**kwargs):
            calls.append(kwargs)
            return False, kwargs["current_input"]

        orig = _patch_completion_check(fake_completion_check)
        try:
            big_text = "x" * 20
            update1 = _FakeUpdate(contents=[_FakeContent(type="text", text=big_text)])
            update2 = _FakeUpdate(contents=[_FakeContent(type="text", text=big_text)])
            agent = _FakeAgent([
                _FakeStream(updates=[update1]),
                _FakeStream(updates=[update2]),
            ])
            asyncio.run(run_loop.run_agent_loop(agent, None, "prompt", surface))
        finally:
            _restore_completion_check(orig)

        # First overshoot: one wrap-up turn, no completion check yet, no attempt-forcing.
        # Second overshoot: attempt forced to 10**6, completion check finally runs.
        assert agent.call_count == 2, agent.call_count
        assert len(calls) == 1, calls
        assert run_state.attempt == 10**6, run_state.attempt
        wrap_up = [m for m in notified if "wrap-up turn" in m]
        assert wrap_up, notified
        # The overshot update's content must NOT have been rendered (break happens before the
        # render loop in the turn that exceeds the budget) -- exactly run_cli's existing behavior.
        assert recorded == [], recorded
    print("scenario_context_budget_nudge_then_cutoff: OK")


def scenario_wall_clock_deadline_expiry():
    import engine.run_loop as run_loop

    with tempfile.TemporaryDirectory() as tmpdir:
        run_state = _make_run_state(tmpdir)
        past_deadline = time.monotonic() - 1
        surface, recorded, notified = _make_surface(run_state, budget_deadline=past_deadline)

        calls = []

        async def fake_completion_check(**kwargs):
            calls.append(kwargs)
            return False, kwargs["current_input"]

        orig = _patch_completion_check(fake_completion_check)
        try:
            agent = _FakeAgent([_FakeStream(updates=[])])
            asyncio.run(run_loop.run_agent_loop(agent, None, "prompt", surface))
        finally:
            _restore_completion_check(orig)

        assert agent.call_count == 1, agent.call_count
        assert len(calls) == 1, calls
        assert run_state.attempt == 10**6, run_state.attempt
        assert any("cutting the current turn short" in m for m in notified), notified
        assert any("no more retries" in m for m in notified), notified
    print("scenario_wall_clock_deadline_expiry: OK")


def scenario_crash_save_on_generic_exception():
    """Gap-E regression pin: a genuinely unrecognized exception (classify_malformed_retry's
    reraise=True path) must still call run_state.save() before propagating -- this is the actual
    fix for _run_research's confirmed gap (it previously only saved on asyncio.CancelledError)."""
    import engine.run_loop as run_loop

    with tempfile.TemporaryDirectory() as tmpdir:
        run_state = _make_run_state(tmpdir)
        surface, recorded, notified = _make_surface(run_state)

        save_calls = []
        orig_save = run_state.save
        orig_sync = run_state.sync_fetched_urls

        def spy_save():
            save_calls.append(True)
            orig_save()

        run_state.save = spy_save
        run_state.sync_fetched_urls = lambda: orig_sync()

        agent = _FakeAgent([_FakeStream(raise_exc=ValueError("totally unrecognized"))])
        raised = None
        try:
            asyncio.run(run_loop.run_agent_loop(agent, None, "prompt", surface))
        except ValueError as e:
            raised = e

        assert raised is not None and str(raised) == "totally unrecognized", raised
        assert save_calls, "run_state.save() must be called on a generic exception before it propagates"
    print("scenario_crash_save_on_generic_exception: OK")


def scenario_on_malformed_give_up_suppresses_reraise():
    """run_agent's own deliberate, pre-existing behavior (never crash on an unrecognized
    exception -- see RunLoopSurface.on_malformed_give_up's docstring): with the hook set, a
    generic exception must NOT propagate, must render via the hook instead of the default
    notify, and must not force run_state.attempt (force_final_verdict is False for this case).
    A recognized-but-retry-exhausted exception DOES still force attempt via the hook path.
    asyncio.CancelledError must propagate regardless of the hook (needed for /stop/ /cancel)."""
    import engine.run_loop as run_loop

    # --- Unrecognized exception: suppressed, not forced, hook called instead of default notify.
    with tempfile.TemporaryDirectory() as tmpdir:
        run_state = _make_run_state(tmpdir)
        surface, recorded, notified = _make_surface(run_state)
        give_up_calls = []
        surface.on_malformed_give_up = lambda e, result: give_up_calls.append((e, result))

        calls = []

        async def fake_completion_check(**kwargs):
            calls.append(kwargs)
            return False, kwargs["current_input"]

        orig = _patch_completion_check(fake_completion_check)
        try:
            exc = ValueError("totally unrecognized")
            agent = _FakeAgent([_FakeStream(raise_exc=exc)])
            asyncio.run(run_loop.run_agent_loop(agent, None, "prompt", surface))
        finally:
            _restore_completion_check(orig)

        assert len(give_up_calls) == 1, give_up_calls
        assert give_up_calls[0][0] is exc, give_up_calls
        assert give_up_calls[0][1].reraise is True, give_up_calls
        assert not notified, f"default notify must not fire when on_malformed_give_up is set: {notified}"
        assert run_state.attempt == 0, "an unrecognized exception must not force final-verdict here"
        assert len(calls) == 1, "completion check must still run normally (not forced)"

    # --- Recognized-but-exhausted: hook still called, but force_final_verdict fires attempt.
    with tempfile.TemporaryDirectory() as tmpdir:
        run_state = _make_run_state(tmpdir)
        surface, recorded, notified = _make_surface(run_state)
        give_up_calls = []
        surface.on_malformed_give_up = lambda e, result: give_up_calls.append((e, result))

        calls = []

        async def fake_completion_check(**kwargs):
            calls.append(kwargs)
            return False, kwargs["current_input"]

        orig = _patch_completion_check(fake_completion_check)
        try:
            exc = Exception("error parsing tool call: bad json")
            agent = _FakeAgent([_FakeStream(raise_exc=exc) for _ in range(3)])
            asyncio.run(run_loop.run_agent_loop(agent, None, "prompt", surface))
        finally:
            _restore_completion_check(orig)

        assert len(give_up_calls) == 1, give_up_calls
        assert give_up_calls[0][1].force_final_verdict is True, give_up_calls
        assert run_state.attempt == 10**6, run_state.attempt
        assert len(calls) == 1, calls

    # --- CancelledError must propagate regardless of on_malformed_give_up being set.
    with tempfile.TemporaryDirectory() as tmpdir:
        run_state = _make_run_state(tmpdir)
        surface, recorded, notified = _make_surface(run_state)
        give_up_calls = []
        surface.on_malformed_give_up = lambda e, result: give_up_calls.append((e, result))

        agent = _FakeAgent([_FakeStream(raise_exc=asyncio.CancelledError())])
        raised = None
        try:
            asyncio.run(run_loop.run_agent_loop(agent, None, "prompt", surface))
        except asyncio.CancelledError as e:
            raised = e

        assert raised is not None, "CancelledError must propagate even when on_malformed_give_up is set"
        assert not give_up_calls, "on_malformed_give_up must not be called for CancelledError"
    print("scenario_on_malformed_give_up_suppresses_reraise: OK")


def main():
    scenario_normal_completion()
    scenario_malformed_retry_then_give_up()
    scenario_quota_abort_falls_through()
    scenario_context_budget_nudge_then_cutoff()
    scenario_wall_clock_deadline_expiry()
    scenario_crash_save_on_generic_exception()
    scenario_on_malformed_give_up_suppresses_reraise()
    print("All test_run_loop.py scenarios passed.")


if __name__ == "__main__":
    main()
