"""Compiled runs (settings.writer_mode "compiled"): a failing completion check is advisory. No dispatch, no Planner nudge, the problem is kept in
run_state.data["compiled_writer"]["check_notes"] for the reader notes, and the cycle ends through the final-verdict return."""
import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import config  # noqa: E402
from engine.completion import run_completion_check  # noqa: E402
from tools.fs import _IN_MEMORY_FS  # noqa: E402
from utils.run_state import RunState, record_fetched_url, reset_fetched_urls, run_state_ctx  # noqa: E402

SRC = "https://gov.example.co/page"


def _run(compiled: bool):
    calls = []

    async def dispatch_task(*a, **k):
        calls.append(a)
        raise AssertionError("no dispatch on a compiled run")

    with tempfile.TemporaryDirectory() as td:
        rs = RunState(td)
        rs.data["fetched_urls"] = [{"url": SRC, "filename": "sources/page.md"}]  # the pipeline seed records its sources, as a real compiled run has
        if compiled:
            rs.data["compiled_writer"] = {"units": 3}
        run_state_ctx.set(rs)
        msgs = []
        out = asyncio.run(run_completion_check(query="q", current_input="q", run_state=rs, notify=msgs.append, dispatch_task=dispatch_task))
        return out, rs, msgs, calls


def test_compiled_failing_check_is_advisory():
    old_ws = config.cfg.get("settings", {}).get("workspace")
    config.cfg["settings"]["workspace"] = {"type": "memory", "required_artifact": "final_report.md"}
    saved = dict(_IN_MEMORY_FS)
    try:
        _IN_MEMORY_FS.clear()
        reset_fetched_urls()
        record_fetched_url(SRC, filename="sources/page.md")
        _IN_MEMORY_FS["sources/page.md"] = "Source-URL: " + SRC + "\n\nReal text from the page about the topic, long enough to count."
        _IN_MEMORY_FS["findings.md"] = "- A real finding (" + SRC + ")"
        # final_report.md missing: a check fails (missing_artifact)
        (retry, new_input), rs, msgs, calls = _run(compiled=True)
        assert retry is False and new_input == "q" and calls == []
        notes = rs.data["compiled_writer"]["check_notes"]
        assert len(notes) == 1 and notes[0].startswith("missing_artifact: "), notes
        assert any("recorded as a reader note" in m for m in msgs), msgs
        # agents mode keeps today's behaviour: the same failure retries
        (retry2, _), _, _, _ = _run(compiled=False)
        assert retry2 is True
    finally:
        _IN_MEMORY_FS.clear()
        _IN_MEMORY_FS.update(saved)
        config.cfg["settings"]["workspace"] = old_ws


if __name__ == "__main__":
    test_compiled_failing_check_is_advisory()
    print("test_completion_compiled OK (1 test)")
