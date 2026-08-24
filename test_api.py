"""Smallest thing that fails if src/api.py's run-lifecycle loop breaks. api.py had ZERO test
coverage before this file (confirmed via codegraph before this fix) -- this pins the one new
piece of logic added 2026-08-24: malformed-tool-call retry + QuotaAbortException handling in
_run_research's stream-consumption loop, mirroring run_cli/run_agent's own already-tested copies
of this exact pattern (engine/tui.py). Does not exercise the rest of _run_research (job queueing,
SSE draining, file uploads) -- those are unchanged by this fix and still uncovered; a fuller
api.py test harness is a separate, larger undertaking than this one targeted pin.
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


def _run_scenario(first_call_exc, expect_completion_check_called):
    import api

    with tempfile.TemporaryDirectory() as tmpdir:
        orig_ws = config.cfg.get("settings", {}).get("workspace")
        config.cfg.setdefault("settings", {})["workspace"] = {"type": "disk", "dir": tmpdir}

        orig_create_local_agent = api.create_local_agent
        orig_run_completion_check = api.run_completion_check
        orig_write_bib = api._write_bibliography
        orig_export_pdf = api._export_pdf

        completion_check_calls = []

        def _fake_create_local_agent(builder, subagent_callback=None, session_data=None):
            return _FakeAgent(first_call_exc), None, None

        async def _fake_run_completion_check(**kwargs):
            completion_check_calls.append(kwargs)
            return False, kwargs["current_input"]

        api.create_local_agent = _fake_create_local_agent
        api.run_completion_check = _fake_run_completion_check
        api._write_bibliography = lambda run_state: None
        api._export_pdf = lambda run_id: (None, None)

        try:
            events = asyncio.Queue()

            class _FakeApp:
                class state:
                    builder = None
            api.app = _FakeApp()

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
            api.run_completion_check = orig_run_completion_check
            api._write_bibliography = orig_write_bib
            api._export_pdf = orig_export_pdf
            if orig_ws is None:
                config.cfg["settings"].pop("workspace", None)
            else:
                config.cfg["settings"]["workspace"] = orig_ws


def main():
    # --- Malformed tool call: must retry once (via classify_malformed_retry), not crash the job.
    # Before this fix, this exact exception propagated straight out of _run_research uncaught. ---
    events = _run_scenario(Exception("error parsing tool call: bad json"), expect_completion_check_called=True)
    retry_events = [e for e in events if "malformed tool call" in e.get("text", "")]
    assert retry_events, f"expected a retry notification, got {events}"
    assert "1/2" in retry_events[0]["text"], retry_events[0]

    # --- QuotaAbortException: must abort cleanly with a system event, not crash the job. ---
    from tools import QuotaAbortException
    events = _run_scenario(QuotaAbortException("stuck in a loop"), expect_completion_check_called=False)
    abort_events = [e for e in events if "forcefully aborted" in e.get("text", "")]
    assert abort_events, f"expected a forced-abort notification, got {events}"
    assert "stuck in a loop" in abort_events[0]["text"], abort_events[0]

    # --- A genuinely unrecognized exception must still propagate (not silently swallowed). ---
    try:
        _run_scenario(ValueError("something else entirely"), expect_completion_check_called=False)
        raise AssertionError("an unrecognized exception must propagate, not be swallowed")
    except ValueError as e:
        assert str(e) == "something else entirely", e

    print("All api.py assertions passed.")


if __name__ == "__main__":
    main()
