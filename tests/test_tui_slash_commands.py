"""Characterization tests for BasicTuiAgent.on_input_submitted: every slash command's observable effect (config /
queued state / chat message) and the free-text routing (running-guard, clarify intake, run_agent). Written before
turning the handler's if/elif chain into a dispatch table, so the table is checked against what the chain did.
Textual's Pilot drives the real app; the config writer, the session log and the agent entry points are stubbed so
nothing touches the user's ~/.deepdelve or starts a research run."""
import asyncio
import copy
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
os.environ["HOME"] = tempfile.mkdtemp()  # Path.home() based paths (/sessions, persistence) land in a scratch dir

import config  # noqa: E402
import engine.tui as T  # noqa: E402
from textual.widgets import Input  # noqa: E402


def texts(app):
    out = []
    for w in app.query_one("#chat-container").children:
        r = getattr(w, "renderable", None) or getattr(w, "content", None)
        out.append(getattr(r, "markup", None) or str(r))
    return out


def last(app):
    return texts(app)[-1]


async def submit(app, pilot, value):
    await app.on_input_submitted(Input.Submitted(app.query_one("#prompt-input"), value))
    await pilot.pause()


def with_app(coro_fn):
    """Run `coro_fn(app, pilot)` inside a live app with config, save_config and _write_log isolated."""
    async def _run():
        saved_cfg = copy.deepcopy(config.cfg)
        saved_writer, config.save_config = config.save_config, (lambda: None)
        saved_log, T._write_log = T._write_log, (lambda *a, **k: None)
        try:
            app = T.BasicTuiAgent(builder=None)
            calls = {"run_agent": [], "clarify": [], "exit": 0}
            app.run_agent = lambda q, *a, **k: calls["run_agent"].append(q)
            app.clarify_query = lambda q, *a, **k: calls["clarify"].append(q)
            app.exit = lambda *a, **k: calls.__setitem__("exit", calls["exit"] + 1)
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                await coro_fn(app, pilot, calls)
        finally:
            config.save_config, T._write_log = saved_writer, saved_log
            config.cfg.clear()
            config.cfg.update(saved_cfg)
    asyncio.run(_run())


def test_depth_and_style():
    async def body(app, pilot, calls):
        await submit(app, pilot, "/depth bogus")
        assert "Usage: `/depth quick|standard|deep` (got `bogus`)" in last(app)
        await submit(app, pilot, "/depth")
        assert "(got `(none)`)" in last(app)
        await submit(app, pilot, "/depth quick")
        assert "Research depth set to **quick** for the next run." in last(app)
        await submit(app, pilot, "/style bogus")
        assert "Usage: `/style standard|academic|answer` (got `bogus`)" in last(app)
        await submit(app, pilot, "/style academic")
        assert config.cfg["settings"]["report_style"] == "academic"
        assert "Report style set to **academic**" in last(app)
        assert calls["run_agent"] == [] and calls["clarify"] == []
    with_app(body)


def test_seed_url_seed_doc_pipeline_evidence():
    async def body(app, pilot, calls):
        await submit(app, pilot, "/seed-url")
        assert "Usage: `/seed-url <url>`" in last(app) and app._pending_seed_urls == []
        await submit(app, pilot, "/seed-url https://a.example/x")
        await submit(app, pilot, "/seed-url https://b.example/y")
        assert app._pending_seed_urls == ["https://a.example/x", "https://b.example/y"]
        assert "Queued seed URL (2 pending): https://b.example/y" in last(app)

        await submit(app, pilot, "/seed-doc")
        assert "Usage: `/seed-doc <path>`" in last(app)
        await submit(app, pilot, "/seed-doc /no/such/file.txt")
        assert "Not a file: `/no/such/file.txt`" in last(app) and app._pending_seed_docs == []
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "doc.txt"
            f.write_text("x")
            await submit(app, pilot, f"/seed-doc {f}")
            assert app._pending_seed_docs == [str(f)]
            assert "Queued seed document (1 pending)" in last(app)

            await submit(app, pilot, "/pipeline-evidence")
            assert "Usage: `/pipeline-evidence <dir>`" in last(app) and app._pending_pipeline_evidence is None
            await submit(app, pilot, f"/pipeline-evidence {td}")
            assert f"No evidence.json in: `{td}`" in last(app) and app._pending_pipeline_evidence is None
            (Path(td) / "evidence.json").write_text("{}")
            await submit(app, pilot, f"/pipeline-evidence {td}")
            assert app._pending_pipeline_evidence == td and f"Queued pipeline evidence: {td}" in last(app)
        assert calls["run_agent"] == [] and calls["clarify"] == []
    with_app(body)


def test_toggles_and_config():
    async def body(app, pilot, calls):
        before = config.cfg["settings"]["enable_thinking"]
        await submit(app, pilot, "/toggle_thinking")
        assert config.cfg["settings"]["enable_thinking"] is (not before)
        assert f"Thinking capability is now **{'ON' if not before else 'OFF'}**" in last(app)

        hf = config.cfg["settings"].get("fetch", {}).get("headless_fallback", True)
        await submit(app, pilot, "/toggle_headless_fetch")
        assert config.cfg["settings"]["fetch"]["headless_fallback"] is (not hf)
        assert f"Headless-browser fetch fallback is now **{'ON' if not hf else 'OFF'}**" in last(app)

        pers = config.cfg["settings"].get("enable_session_persistence", True)
        await submit(app, pilot, "/toggle_persistence")
        assert config.cfg["settings"]["enable_session_persistence"] is (not pers)
        assert f"Session persistence is now **{'ON' if not pers else 'OFF'}**." in last(app)
        if not pers:
            assert "Saving to:" in last(app)

        await submit(app, pilot, "/config")
        assert last(app).startswith("**System Configuration (Loaded from:")
        assert "### Settings" in last(app) and "### Api" in last(app)
    with_app(body)


def test_stop_and_new_reset_state():
    async def body(app, pilot, calls):
        app._is_agent_running = True
        await submit(app, pilot, "/stop")
        assert app._is_agent_running is False and last(app) == "**System:**\nStopped."

        app._pending_clarify, app._clarify_done = "q", True
        app._active_run_dir, app._conv_fetched, app._conv_run_state = "d", [1], object()
        old_sid = T._current_session_id
        T._session_events.append({"x": 1})
        await submit(app, pilot, "/new")
        assert app._pending_clarify is None and app._clarify_done is False
        assert app._active_run_dir is None and app._conv_fetched is None and app._conv_run_state is None
        assert T._current_session_id != old_sid and T._session_events == []
        chat_children = app.query_one("#chat-container").children
        assert len(chat_children) == 1 and chat_children[0].id == "banner", "chat must be cleared down to a fresh banner"
    with_app(body)


def test_pickers_exit_and_sessions_listing():
    async def body(app, pilot, calls):
        seen = []
        app._show_session_picker = lambda: seen.append("session")
        app._show_run_picker = lambda: seen.append("run")
        await submit(app, pilot, "/resume")
        await submit(app, pilot, "/resume-run")
        assert seen == ["session", "run"]
        await submit(app, pilot, "/exit")
        assert calls["exit"] == 1
        await submit(app, pilot, "/sessions")
        assert last(app) == "**System:**\nNo sessions found."
        d = Path.home() / f".{config.APP_NAME}" / "sessions"
        d.mkdir(parents=True)
        (d / "session_abc.json").write_text('{"timestamp": "2026-10-21", "session_id": "abc"}')
        (d / "session_bad.json").write_text("not json")
        await submit(app, pilot, "/sessions")
        assert "**ID:** `abc` (Date: 2026-10-21)" in last(app) and "Invalid session file: `session_bad.json`" in last(app)
        assert calls["run_agent"] == [] and calls["clarify"] == []
    with_app(body)


def test_free_text_routing():
    async def body(app, pilot, calls):
        config.cfg["settings"]["clarify_before_research"] = True
        await submit(app, pilot, "what is x")
        assert calls["clarify"] == ["what is x"] and calls["run_agent"] == []

        app._pending_clarify = "original q"
        await submit(app, pilot, "my answers")
        assert calls["run_agent"] == ["original q\n\nUSER CLARIFICATIONS (answers to the intake questions above):\nmy answers"]
        assert app._pending_clarify is None

        app._clarify_done = True
        await submit(app, pilot, "follow up")
        assert calls["run_agent"][-1] == "follow up"

        # an unrecognised slash command is NOT rejected: it falls through as a research prompt
        await submit(app, pilot, "/bogus-command")
        assert calls["run_agent"][-1] == "/bogus-command"

        # while a run is active, non-slash text is refused, slash commands still work
        n = len(calls["run_agent"])
        app._is_agent_running = True
        await submit(app, pilot, "another question")
        assert "Operation running. Please type `/stop` first" in last(app) and len(calls["run_agent"]) == n
        await submit(app, pilot, "/depth deep")
        assert "Research depth set to **deep**" in last(app)

        # blank input does nothing
        app._is_agent_running = False
        await submit(app, pilot, "   ")
        assert len(calls["run_agent"]) == n
    with_app(body)


def test_every_advertised_command_has_a_handler():
    """SLASH_COMMANDS feeds the popup, ghost text and command palette; a command listed there with no handler would
    silently be sent to the model as a research prompt."""
    handled = set(T.BasicTuiAgent._EXACT_COMMANDS) | {p for p, _ in T.BasicTuiAgent._PREFIX_COMMANDS} | {"/files"}
    advertised = {c for c, _ in T.BasicTuiAgent.SLASH_COMMANDS}
    assert advertised <= handled, advertised - handled
    for h in list(T.BasicTuiAgent._EXACT_COMMANDS.values()) + [h for _, h in T.BasicTuiAgent._PREFIX_COMMANDS]:
        assert asyncio.iscoroutinefunction(getattr(T.BasicTuiAgent, h)), h


if __name__ == "__main__":
    _tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    assert _tests, "no tests found"
    for _n, _f in _tests:
        _f()
    print(f"test_tui_slash_commands OK ({len(_tests)} tests)")
