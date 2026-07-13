"""Smallest thing that fails if the structural-check heuristics break.
Run: venv/Scripts/python test_structural_checks.py (no framework needed).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from engine.orchestrator import _extract_excluded_topics, _lacks_concrete_subject
from utils.grounding import find_non_url_citations, fully_ungrounded, find_uncited_claim_lines, extract_cited_urls
from utils.run_state import record_fetched_url, reset_fetched_urls


def main():
    # --- excluded-topic extraction (live case: 4 excluded sectors researched anyway) ---
    topics = _extract_excluded_topics(
        "Do a market research of neglected markets in Colombia, excluding Agritech, "
        "HealthTech, EdTech and VR/AR-Education sectors."
    )
    assert "agritech" in topics and "healthtech" in topics and "edtech" in topics, topics
    assert any("vr/ar-education" in t for t in topics), topics
    assert _extract_excluded_topics("How to avoid mosquito bites in the tropics") == set()
    assert _extract_excluded_topics("Compare React and Vue") == set()

    # --- unresolved-referent detection (live case: 'its' resolved to Microsoft, not Python) ---
    assert _lacks_concrete_subject("Summarize its headline feature.")
    assert not _lacks_concrete_subject("Summarize Python 3.14's headline feature.")
    assert not _lacks_concrete_subject("Find the current stable version of Python.")
    assert not _lacks_concrete_subject("Find studies about coffee and how it affects sleep in Colombia.")
    # Long instructions are never flagged, whatever pronouns they use.
    assert not _lacks_concrete_subject("evaluate it against " + "criteria " * 30)

    # --- findings.md wholesale-fabrication gate ---
    reset_fetched_urls()
    assert fully_ungrounded("Findings: lots of prose, zero sources.") == "no_urls"
    assert fully_ungrounded("- claim (https://fake.example.com/x)") == "all_cited_urls_unverified"
    record_fetched_url("https://real.example.com/page", filename="real_page.md")
    # One real citation grounds the file, even alongside an unfetched snippet URL.
    assert fully_ungrounded(
        "- claim (https://real.example.com/page)\n- extra (https://never-fetched.example.com/y)"
    ) is None
    reset_fetched_urls()

    # --- non-URL citation label: pseudo-citations flagged, prose/headers not ---
    # (live case 2026-07-11: a heading with the word "Source" quarantined a grounded report)
    assert find_non_url_citations("Source: Expert opinion from a facility manager in Colombia")
    assert find_non_url_citations("- **Fuente:** Ministerio de Salud, informe interno")
    assert not find_non_url_citations("## Methodology & Source Quality Notes")
    assert not find_non_url_citations("No claims were made without source attribution.")
    assert not find_non_url_citations("**Sources:**\n- **[Title](https://x.org/a)**")

    # --- search-health counter (persists into _run_state.json via RunState) ---
    import contextvars
    import json
    import tempfile
    from utils.run_state import RunState, run_state_ctx, record_search_health, get_search_health

    with tempfile.TemporaryDirectory() as tmpdir:
        def _health_scenario():
            rs = RunState(tmpdir)
            rs.set_query("q")
            run_state_ctx.set(rs)
            record_search_health(ok=True)
            record_search_health(ok=False)
            record_search_health(ok=False)
            assert get_search_health() == {"calls": 3, "failures": 2}, get_search_health()
            # A fetch mid-run persists state immediately (crash forensics), atomically (no .tmp left)
            record_fetched_url("https://real.example.com/page", filename="real_page.md")

        contextvars.copy_context().run(_health_scenario)  # isolated so the ctx var doesn't leak
        assert get_search_health() == {"calls": 0, "failures": 0}  # no run state -> zeros, no crash

        state_path = os.path.join(tmpdir, "_run_state.json")
        assert os.path.exists(state_path), "state not persisted before run end"
        assert not os.path.exists(state_path + ".tmp"), "atomic-write temp file left behind"
        with open(state_path, encoding="utf-8") as f:
            persisted = json.load(f)
        assert persisted["search_health"] == {"calls": 3, "failures": 2}, persisted
        assert persisted["fetched_urls"][0]["url"] == "https://real.example.com/page", persisted
    reset_fetched_urls()

    # --- exclusion gate must not fire on a task's own restated exclusion clause ---
    # (live case 2026-07-11: a discovery task quoting "Exclude fintech, last-mile delivery..."
    # was skipped twice, burning delegate_tasks quota and turns)
    from engine.orchestrator import _EXCLUSION_CUE_RE
    excluded = _extract_excluded_topics("Find niches. Exclude fintech, last-mile delivery and legaltech.")
    assert "fintech" in excluded, excluded
    restated = _EXCLUSION_CUE_RE.sub(" ", "discover regulated niches in colombia. exclude fintech, last-mile delivery and legaltech.")
    assert not any(t in restated for t in excluded), restated
    on_topic = _EXCLUSION_CUE_RE.sub(" ", "research fintech opportunities for gig workers in colombia")
    assert any(t in on_topic for t in excluded), on_topic

    # --- bare-origin fetch must not prefix-ground fabricated deep links ---
    # (live case 2026-07-11, qwen3.6: fetching mercadolibre.com's root waved a fully fabricated
    # findings.md through fully_ungrounded via one reconstructed deep URL on that domain)
    record_fetched_url("https://www.mercadolibre.com/", filename="root.md")
    assert fully_ungrounded(
        "- claim (https://www.mercadolibre.com/mercado-software-b2b-colombia-2025-tamano-inversion/)"
    ) == "all_cited_urls_unverified"
    # A real deep-URL fetch still prefix-grounds its variants (query string, stripped chars).
    record_fetched_url("https://example.com/report/2026", filename="r.md")
    assert fully_ungrounded("- claim (https://example.com/report/2026?utm_source=x)") is None
    reset_fetched_urls()

    # --- quota refund on environmental failure ---
    from tools.core import refund_quota

    def _refund_scenario():
        from tools.core import tool_quotas_ctx as q_ctx, check_quota
        q_ctx.set({"web_search": {"used": 0, "limit": 2}})
        check_quota("web_search")
        refund_quota("web_search")
        assert q_ctx.get()["web_search"]["used"] == 0
        refund_quota("web_search")  # never goes negative
        assert q_ctx.get()["web_search"]["used"] == 0

    contextvars.copy_context().run(_refund_scenario)

    # --- malformed-tool-call recovery predicate (live case: gpt-oss bad escape -> Ollama 500) ---
    from engine.orchestrator import malformed_tool_call_nudge
    assert malformed_tool_call_nudge(Exception(
        "Error code: 500 - {'error': {'message': 'error parsing tool call: raw=...'}}"))
    assert malformed_tool_call_nudge(Exception("Connection error.")) is None

    # --- fetched files live under sources/ and carry their true URL as line 1 ---
    from tools.web import _fetched_filename, _save_fetched
    from tools.fs import _IN_MEMORY_FS
    import config as _config
    assert _fetched_filename("foo") == "sources/foo.md"
    assert _fetched_filename("sources/foo.md") == "sources/foo.md"
    _orig_ws = _config.cfg.get("settings", {}).get("workspace")
    _config.cfg.setdefault("settings", {})["workspace"] = {"type": "memory"}
    try:
        reset_fetched_urls()
        _save_fetched(["https://example.com/page"], "foo", "body text")
        assert _IN_MEMORY_FS["sources/foo.md"].startswith("Source-URL: https://example.com/page\n\n")
        from utils.run_state import get_fetched_urls
        assert get_fetched_urls()[0]["filename"] == "sources/foo.md"
    finally:
        if _orig_ws is None:
            _config.cfg["settings"].pop("workspace", None)
        else:
            _config.cfg["settings"]["workspace"] = _orig_ws
        reset_fetched_urls()

    # --- query-level scope warning (live case: Colombia task searching offshore wind turbines) ---
    from tools.web import _scope_warning
    from utils.run_state import scope_entities_ctx

    def _scope_scenario():
        scope_entities_ctx.set({"Colombia"})
        assert "SCOPE WARNING" in _scope_warning("predictive maintenance offshore wind turbine")
        assert _scope_warning("mantenimiento predictivo industrial colombia") == ""
        scope_entities_ctx.set(set())
        assert _scope_warning("anything at all") == ""  # no scope entities -> silent

    contextvars.copy_context().run(_scope_scenario)
    assert _scope_warning("anything") == ""  # outside any task -> silent

    # --- pre-run search health probe (patched ddgs, no network) ---
    import ddgs as _ddgs
    from tools.web import probe_search_health

    class _HealthyDDGS:
        def text(self, *a, **k): return [{"href": "https://x", "title": "t", "body": "b"}]

    class _ThrottledDDGS:
        def text(self, *a, **k): raise RuntimeError("202 Ratelimit")

    _real_ddgs = _ddgs.DDGS
    try:
        _ddgs.DDGS = _HealthyDDGS
        assert probe_search_health(retry_delay=0) is None
        _ddgs.DDGS = _ThrottledDDGS
        err = probe_search_health(retry_delay=0)
        assert err and "Ratelimit" in err, err
    finally:
        _ddgs.DDGS = _real_ddgs

    # --- run-resume helpers (--resume-run: reattach to an interrupted run instead of restarting) ---
    from engine.tui import load_resume_state, build_resume_input, _clarify_verdict
    from tools.fs import session_dir_ctx

    with tempfile.TemporaryDirectory() as tmpdir:
        run_dir = os.path.join(tmpdir, "my_interrupted_run")
        os.makedirs(os.path.join(run_dir, "sources"))
        prior = {
            "query": "Research X in Colombia",
            "fetched_urls": [{"url": "https://real.example.com/a", "filename": "sources/a.md", "timestamp": 1.0}],
            "findings": [],
        }
        with open(os.path.join(run_dir, "_run_state.json"), "w", encoding="utf-8") as f:
            json.dump(prior, f)
        with open(os.path.join(run_dir, "_todos.md"), "w", encoding="utf-8") as f:
            f.write("- [x] background\n- [ ] verification")
        with open(os.path.join(run_dir, "findings.md"), "w", encoding="utf-8") as f:
            f.write("## Findings so far\n- claim (https://real.example.com/a)")

        _orig_ws2 = _config.cfg.get("settings", {}).get("workspace")
        _config.cfg["settings"]["workspace"] = {"type": "disk", "dir": tmpdir, "session_isolation": True}
        try:
            name, state = load_resume_state(run_dir)  # full path accepted
            assert name == "my_interrupted_run" and state["query"] == "Research X in Colombia"
            name2, _ = load_resume_state("my_interrupted_run")  # bare folder name accepted
            assert name2 == name

            def _resume_scenario():
                session_dir_ctx.set(name)
                text = build_resume_input(state["query"], state)
                assert "RESUMED RUN" in text
                assert "Research X in Colombia" in text
                assert "https://real.example.com/a" in text
                assert "verification" in text          # _todos.md injected
                assert "Findings so far" in text       # findings.md injected

            contextvars.copy_context().run(_resume_scenario)
        finally:
            if _orig_ws2 is None:
                _config.cfg["settings"].pop("workspace", None)
            else:
                _config.cfg["settings"]["workspace"] = _orig_ws2

    # --- /resume-run TUI wiring: --resume-run existed in the headless CLI for a full session
    # before the TUI had any equivalent at all (caught live, 2026-07-12) ---
    import asyncio as _asyncio_tui

    async def _resume_run_tui_scenario():
        with tempfile.TemporaryDirectory() as tmpdir2:
            run_dir2 = os.path.join(tmpdir2, "my_interrupted_run")
            os.makedirs(os.path.join(run_dir2, "sources"))
            with open(os.path.join(run_dir2, "_run_state.json"), "w", encoding="utf-8") as f:
                json.dump({
                    "query": "Research X",
                    "fetched_urls": [{"url": "https://real.example.com/a", "filename": "sources/a.md", "timestamp": 1.0}],
                    "findings": [{"source_url": "https://real.example.com/a", "summary": "s"}],
                }, f)

            _orig_ws9 = _config.cfg.get("settings", {}).get("workspace")
            _config.cfg["settings"]["workspace"] = {"type": "disk", "dir": tmpdir2, "session_isolation": True}
            try:
                class _FakeBuilder:
                    name = "Planner"
                    instructions = "test"
                    tools = []
                    sub_agents = []

                app = BasicTuiAgent(_FakeBuilder())
                resume_calls = []

                async def _fake_run_agent(query, mount_user=True):
                    resume_calls.append({
                        "mount_user": mount_user,
                        "active_run_dir": app._active_run_dir,
                        "resuming_run": app._resuming_run,  # must be True DURING the call
                        "conv_fetched": list(app._conv_fetched or []),
                        "conv_run_state_query": app._conv_run_state.data.get("query") if app._conv_run_state else None,
                    })
                app.run_agent = _fake_run_agent

                async with app.run_test():
                    app._show_run_picker()
                    assert app._run_picker_active
                    assert app._filtered_cmds and app._filtered_cmds[0][0] == "my_interrupted_run"

                    await app._open_selected_run("my_interrupted_run")
                    assert len(resume_calls) == 1, resume_calls
                    call = resume_calls[0]
                    assert call["mount_user"] is False  # no giant resume-preamble bubble
                    assert call["active_run_dir"] == "my_interrupted_run"
                    assert call["resuming_run"] is True  # skip_completion_check's Q&A shortcut must be disarmed
                    assert call["conv_fetched"] == [
                        {"url": "https://real.example.com/a", "filename": "sources/a.md", "timestamp": 1.0}
                    ]
                    assert call["conv_run_state_query"] == "Research X"
                    # Reset back to False once run_agent returns (the `finally` in _resume_run).
                    assert app._resuming_run is False
            finally:
                if _orig_ws9 is None:
                    _config.cfg["settings"].pop("workspace", None)
                else:
                    _config.cfg["settings"]["workspace"] = _orig_ws9

    from engine.tui import BasicTuiAgent
    _asyncio_tui.run(_resume_run_tui_scenario())

    # --- B5: session log write throttling (2026-07-12) — _write_log serializes and rewrites the
    # WHOLE _session_events list every call; log_stream_content used to call it after EVERY
    # streamed event, so an N-event run paid O(N) per write summed over N writes = O(n²) total
    # (confirmed live: a 370-event killed run produced a 565KB session file). Throttled to at
    # most once per _LOG_WRITE_THROTTLE_SECONDS by default; force=True (every genuine checkpoint:
    # turn end, run end, before sys.exit) always writes regardless. ---
    def _write_log_throttle_scenario():
        # Local import, not the enclosing main()'s `time` (imported later, line ~987 as of this
        # writing) — Python's static per-function scoping would make a bare `time.sleep` here
        # resolve to that not-yet-assigned enclosing local and raise UnboundLocalError, the exact
        # bug class caught and fixed elsewhere in this file earlier this session.
        import time
        import engine.tui as _tui_mod
        with tempfile.TemporaryDirectory() as home_dir:
            _orig_home = os.environ.get("HOME")
            os.environ["HOME"] = home_dir
            _orig_persist = _config.cfg["settings"].get("enable_session_persistence")
            _config.cfg["settings"]["enable_session_persistence"] = True
            _orig_sid = _tui_mod._current_session_id
            _orig_events = _tui_mod._session_events
            _orig_last_write = _tui_mod._last_log_write
            try:
                _tui_mod._current_session_id = "throttle_test"
                _tui_mod._session_events = [{"a": 1}]
                _tui_mod._last_log_write = 0.0
                log_file = os.path.join(home_dir, f".{_config.APP_NAME}", "sessions", "session_throttle_test.json")

                _tui_mod._write_log()  # first call always writes (last_write starts at 0)
                assert os.path.exists(log_file), "first call must write"
                mtime1 = os.path.getmtime(log_file)

                time.sleep(0.05)
                _tui_mod._write_log()  # well within the throttle window
                assert os.path.getmtime(log_file) == mtime1, "throttled call must not rewrite"

                _tui_mod._write_log(force=True)  # bypasses the throttle unconditionally
                assert os.path.getmtime(log_file) > mtime1, "force=True must always write"
            finally:
                if _orig_home is None:
                    os.environ.pop("HOME", None)
                else:
                    os.environ["HOME"] = _orig_home
                if _orig_persist is None:
                    _config.cfg["settings"].pop("enable_session_persistence", None)
                else:
                    _config.cfg["settings"]["enable_session_persistence"] = _orig_persist
                _tui_mod._current_session_id = _orig_sid
                _tui_mod._session_events = _orig_events
                _tui_mod._last_log_write = _orig_last_write

    _write_log_throttle_scenario()

    # --- depth presets (--depth): quick/deep touch quotas+search_mode+retries, standard is a no-op ---
    from engine.tui import apply_depth_preset

    cfg = {"settings": {"quotas": {"web_search": 15, "read_workspace_file": {"limit": 30, "rules": {}}},
                        "search_mode": "light", "max_completion_check_attempts": 3}}
    apply_depth_preset(cfg, "standard")
    assert cfg["settings"]["quotas"]["web_search"] == 15  # untouched
    apply_depth_preset(cfg, "deep")
    assert cfg["settings"]["quotas"]["web_search"] == 30
    assert cfg["settings"]["search_mode"] == "heavy"
    assert cfg["settings"]["max_completion_check_attempts"] == 4
    apply_depth_preset(cfg, "quick")
    assert cfg["settings"]["quotas"]["web_search"] == 8
    assert cfg["settings"]["quotas"]["read_workspace_file"]["limit"] == 30  # dict quotas untouched

    # --- verdict matrix: one row per completion-check problem type, asserting the RECORDED
    # problem name AND a phrase distinctive to that branch's corrective nudge. This is the pin
    # against the swallowed-elif bug class (bd307f4, run 13) that motivated engine/completion.py:
    # a verdict carrying the right detail under the wrong label/nudge fails its row instantly.
    # Live-case rows: missing_findings (runs 10/11), regulation_unsupported (runs 12/13). ---
    import asyncio as _asyncio
    from engine.tui import run_completion_check

    _SRC = "https://gov.example.co/page"
    _STUB_SRC = "https://news.example.co/paywalled-article"
    _SOURCE_TEXT = ("Source-URL: " + _SRC + "\n\n"
                    + "Estrategia nacional de seguridad digital para infraestructura y sectores productivos. " * 3)
    _FINDINGS_OK = f"- hallado ({_SRC})"

    matrix = [
        # (row, delegated, workspace files, expected recorded problem, distinctive nudge phrase)
        ("not_delegated", False, {"final_report.md": f"- x [g]({_SRC})"},
         "not_delegated", "No `delegate_tasks` call was ever made"),
        ("findings_ungrounded", True, {"findings.md": "- todo de memoria, sin fuente alguna"},
         "findings_ungrounded", "fails the grounding check"),
        ("missing_findings", True, {"final_report.md": f"- x [g]({_SRC})"},
         "missing_findings", "was never written — the two-pass discipline was skipped"),
        ("missing_artifact", True, {"findings.md": _FINDINGS_OK},
         "missing_artifact", "is missing from the workspace"),
        ("claim_unsupported", True, {"findings.md": _FINDINGS_OK,
          "final_report.md": f"- Colombia exporto USD 3.5 mil millones en 2024 [gov]({_SRC})"},
         "claim_unsupported", "don't appear to come from that source's actual content"),
        ("no_urls", True, {"findings.md": _FINDINGS_OK,
          "final_report.md": "# Informe\nSin enlaces aqui."},
         "not_grounded", "zero hyperlinked sources"),
        ("regulation_unsupported", True, {"findings.md": _FINDINGS_OK,
          "final_report.md": f"| Ley 1906 de 2021 | [gov]({_SRC}) |"},
         "regulation_unsupported", "never mentions that regulation's number"),
        ("non_url_citation", True, {"findings.md": _FINDINGS_OK,
          "final_report.md": f"- dato uno [gov]({_SRC})\n- **Fuente:** Ministerio de Salud, informe interno"},
         "non_url_citation", "isn't a real URL"),
        # Live case run 14: citation to a really-fetched URL whose fetch was a 200 soft-404
        # (paywall shell) — hollow even though the URL gate sees a real fetch.
        ("stub_source", True, {"findings.md": _FINDINGS_OK,
          "final_report.md": f"- dato [news]({_STUB_SRC})"},
         "stub_source", "paywall/not-found stub"),
        # Live case run 14 (format half): claims as a figure table + detached Source URLs list —
        # every line-scoped check passes vacuously; nothing ties any figure to any source.
        ("uncited_claims", True, {"findings.md": _FINDINGS_OK,
          "final_report.md": ("| Sector | Valor estimado |\n"
                              "| Fintech | USD 3.5 mil millones en el mercado local en 2024 |\n"
                              "| Agro | 12% de crecimiento anual en exportaciones regionales |\n"
                              "| Salud | 2.300 empresas registradas en el sector durante 2023 |\n"
                              f"\n### Source URLs\n- {_SRC}\n")},
         "uncited_claims", "carry no citation of their own"),
        ("not_grounded", True, {"findings.md": _FINDINGS_OK,
          "final_report.md": "- x [g](https://never-fetched.example.com/y)"},
         "not_grounded", "was never actually fetched this run"),
        # Clean pass: grounded findings, report cites the fetched source, no checkable claim
        # contradicting it -> no problem recorded, no retry.
        ("clean_pass", True, {"findings.md": _FINDINGS_OK,
          "final_report.md": f"- el pais avanza de forma sostenida segun cifras oficiales [gov]({_SRC})"},
         None, None),
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        for _row_name, _delegated, _files, _expected, _phrase in matrix:
            def _matrix_row():
                from tools.fs import _IN_MEMORY_FS
                from tools.core import tool_quotas_ctx as q_ctx
                _orig_ws3 = _config.cfg.get("settings", {}).get("workspace")
                _config.cfg["settings"]["workspace"] = {"type": "memory", "required_artifact": "final_report.md"}
                saved_fs = dict(_IN_MEMORY_FS)
                try:
                    _IN_MEMORY_FS.clear()
                    reset_fetched_urls()
                    record_fetched_url(_SRC, filename="sources/page.md")
                    _IN_MEMORY_FS["sources/page.md"] = _SOURCE_TEXT
                    # A stub fetch is on record in EVERY row (rows that don't cite it must not
                    # trip over its mere existence); only the stub_source row cites it.
                    record_fetched_url(_STUB_SRC, filename="sources/stub.md", stub="paywall marker")
                    _IN_MEMORY_FS["sources/stub.md"] = "Source-URL: " + _STUB_SRC + "\n\nSUSCRÍBETE"
                    _IN_MEMORY_FS.update(_files)
                    q_ctx.set({"delegate_tasks": {"used": 1 if _delegated else 0, "limit": 5}})
                    rs = RunState(tmpdir)
                    run_state_ctx.set(rs)
                    msgs = []
                    should_retry, _ = _asyncio.run(run_completion_check(
                        query="q", current_input="q", run_state=rs, notify=msgs.append))
                    recorded = rs.data["completion_check_attempts"][-1]["problem"]
                    assert recorded == _expected, (_row_name, recorded, msgs)
                    assert should_retry == (_expected is not None), (_row_name, should_retry, msgs)
                    if _phrase:
                        assert _phrase in msgs[-1], (_row_name, _phrase, msgs)
                finally:
                    _IN_MEMORY_FS.clear()
                    _IN_MEMORY_FS.update(saved_fs)
                    reset_fetched_urls()
                    if _orig_ws3 is None:
                        _config.cfg["settings"].pop("workspace", None)
                    else:
                        _config.cfg["settings"]["workspace"] = _orig_ws3

            contextvars.copy_context().run(_matrix_row)

    # --- regulation-identifier grounding (live case run 12: 'Ley 1906 de 2021' cited to a real
    # fetched page that never mentions 1906 — passed both the URL gate and zero-overlap check) ---
    from utils.grounding import find_unsupported_regulation_ids

    def _regulation_scenario():
        from tools.fs import _IN_MEMORY_FS
        _orig_ws4 = _config.cfg.get("settings", {}).get("workspace")
        _config.cfg["settings"]["workspace"] = {"type": "memory"}
        saved_fs = dict(_IN_MEMORY_FS)
        try:
            _IN_MEMORY_FS.clear()
            reset_fetched_urls()
            record_fetched_url("https://mintic.example.gov.co/article", filename="sources/mintic.md")
            _IN_MEMORY_FS["sources/mintic.md"] = (
                "Source-URL: https://mintic.example.gov.co/article\n\n"
                "La Estrategia Nacional de Seguridad Digital 2025-2027 llega para proteger a Colombia. "
                "El Ministerio TIC presenta el plan de ciberseguridad nacional para infraestructura."
            )
            # Misattributed law number: page never says 1906 -> flagged
            bad = find_unsupported_regulation_ids(
                "| Ley 1906 de 2021 | [Mintic](https://mintic.example.gov.co/article) |")
            assert bad and "1906" in bad[0], bad
            # Supported identifier: page that DOES contain the number -> silent
            _IN_MEMORY_FS["sources/mintic.md"] += "\nTexto oficial de la Ley 1906 de 2021."
            assert find_unsupported_regulation_ids(
                "| Ley 1906 de 2021 | [Mintic](https://mintic.example.gov.co/article) |") == []
            # Identifier with an unfetched/no URL on the line -> other gates' job, silent here
            assert find_unsupported_regulation_ids("Decreto 9999/2015 obliga a todos.") == []
            assert find_unsupported_regulation_ids(
                "Decreto 9999/2015 ([x](https://never-fetched.example.com/y))") == []
            # Run 14's self-grounding case: the regulation number exists ONLY inside our own
            # injected Source-URL header line (the URL slug), not in the page content — the
            # check must strip that header before matching, or it verifies against itself.
            record_fetched_url("https://news.example.co/ley-1819-e-invoicing-dian",
                               filename="sources/eltiempo.md")
            _IN_MEMORY_FS["sources/eltiempo.md"] = (
                "Source-URL: https://news.example.co/ley-1819-e-invoicing-dian\n\n"
                "Suscríbete para leer el contenido completo de nuestras noticias y análisis del día."
            )
            bad2 = find_unsupported_regulation_ids(
                "| Ley 1819 de 2016 | [ET](https://news.example.co/ley-1819-e-invoicing-dian) |")
            assert bad2 and "1819" in bad2[0], (bad2, "Source-URL header slug must not self-ground")
        finally:
            _IN_MEMORY_FS.clear()
            _IN_MEMORY_FS.update(saved_fs)
            reset_fetched_urls()
            if _orig_ws4 is None:
                _config.cfg["settings"].pop("workspace", None)
            else:
                _config.cfg["settings"]["workspace"] = _orig_ws4

    contextvars.copy_context().run(_regulation_scenario)

    # --- academic-style (Author, Year) citation dialect (eval/sales_forecasting_benchmark.md,
    # ROADMAP.md "academic output mode") — same grounding guarantees as the default
    # `- **[Title](URL)**` format, resolved through a parsed References-section map ---
    from utils.grounding import parse_academic_references, real_grounding_problem as _rgp

    def _academic_citation_scenario():
        from tools.fs import _IN_MEMORY_FS
        _orig_ws6 = _config.cfg.get("settings", {}).get("workspace")
        _config.cfg["settings"]["workspace"] = {"type": "memory"}
        saved_fs = dict(_IN_MEMORY_FS)
        try:
            _IN_MEMORY_FS.clear()
            reset_fetched_urls()
            record_fetched_url("https://arxiv.org/abs/2511.00552", filename="sources/tft.md")
            _IN_MEMORY_FS["sources/tft.md"] = (
                "Source-URL: https://arxiv.org/abs/2511.00552\n\n"
                "Temporal Fusion Transformer achieves an R-squared of 0.9875 on 45 Walmart stores, "
                "integrating holiday, CPI, fuel price and temperature signals into a single model."
            )
            well_formed = (
                "## 3. Architectures\n\n"
                "TFT achieved R-squared of 0.9875 on Walmart data (Punati et al., 2025).\n\n"
                "## References\n\n"
                "1. Punati, S. B., et al. (2025). Temporal Fusion Transformer. "
                "https://arxiv.org/abs/2511.00552\n"
            )
            assert parse_academic_references(well_formed) == {
                "punati,2025": "https://arxiv.org/abs/2511.00552"
            }
            # Well-formed academic report: real citation, real fetch, term overlap -> passes clean.
            assert _asyncio.run(_rgp(well_formed)) is None, _asyncio.run(_rgp(well_formed))

            # Fabricated in-text citation with no matching References entry -> non_url_citation,
            # same failure class a bare (Org, Year) pseudo-citation already triggers.
            fabricated = (
                "## 3. Architectures\n\n"
                "A DQN model achieves the highest accuracy in FMCG forecasting (Nobody, 2099).\n\n"
                "## References\n\n"
                "1. Punati, S. B., et al. (2025). Temporal Fusion Transformer. "
                "https://arxiv.org/abs/2511.00552\n"
            )
            problem = _asyncio.run(_rgp(fabricated))
            assert problem and problem.startswith("non_url_citation"), problem

            # A References entry that cites a paper by title/arXiv-ID text alone, no real URL —
            # exactly how the DeepSeek gold reference itself writes some entries — must NOT
            # silently resolve; the in-text citation stays ungrounded until a URL is added.
            no_url_entry = (
                "## 3. Architectures\n\n"
                "GA-DQN raised service level from 61% to 94% (Various Authors, 2025).\n\n"
                "## References\n\n"
                "1. Various Authors. (2025). GA-DQN hybrid. *Supply Chain Analytics Journal*.\n"
            )
            assert parse_academic_references(no_url_entry) == {}
            # No http URL anywhere in the whole report (in-text citation is parenthetical, and
            # the one References entry has none either) -> the hard "no_urls" gate fires first,
            # before non_url_citation_check is ever reached — even more direct than that check.
            problem2 = _asyncio.run(_rgp(no_url_entry))
            assert problem2 == "no_urls", problem2

            # Two citations on one line: an earlier REAL one must not mask a later FABRICATED
            # one — the exact class of bug caught while building this feature (only the first
            # regex match on a line was being resolved before this fix).
            two_on_one_line = (
                "TFT hit R-squared 0.9875 (Punati et al., 2025); a rival model claims higher "
                "accuracy still (Nobody, 2099).\n\n"
                "## References\n\n"
                "1. Punati, S. B., et al. (2025). Temporal Fusion Transformer. "
                "https://arxiv.org/abs/2511.00552\n"
            )
            assert find_non_url_citations(two_on_one_line), "unresolved 2nd citation must be caught"

            # Fresh audit, 2026-07-12: _PARENTHETICAL_CITATION_RE originally required every
            # token before the comma to start with an ASCII capital, so it silently failed to
            # even DETECT "et al."/"&"/"and"/accented-surname citations at all -- not a
            # false-positive, a total miss that broke grounding in both directions (a fabricated
            # multi-author citation went undetected; a genuinely well-formed one was wrongly
            # quarantined). Pin every form the academic-mode prompt actually tells the model to
            # use (prompts.py ACADEMIC_CITATION_FORMAT_INSTRUCTIONS: "et al. for 3+ authors").
            record_fetched_url("https://example.com/drl", filename="sources/drl.md")
            record_fetched_url("https://example.com/pso", filename="sources/pso.md")
            record_fetched_url("https://example.com/rbfnn", filename="sources/rbfnn.md")
            multi_author_forms = (
                "DRL achieves the highest accuracy for FMCG demand forecasting "
                "(Urgenc et al., 2025). PSO cut MAPE by 23 percent versus Transformer "
                "(Smith and Jones, 2024). RBFNN generalization improved significantly "
                "(Chen & Patel, 2020).\n\n"
                "## References\n\n"
                "1. Urgenc, S., et al. (2025). DRL Demand Forecasting. https://example.com/drl\n"
                "2. Smith, J., and Jones, B. (2024). PSO Attention. https://example.com/pso\n"
                "3. Chen, L., & Patel, R. (2020). RBFNN Hybrid. https://example.com/rbfnn\n"
            )
            assert find_non_url_citations(multi_author_forms) == [], (
                "well-formed et al./and/& citations must all resolve, not be flagged")
            assert find_uncited_claim_lines(multi_author_forms) == [], (
                "sections carrying only et al./and/& citations must be exempted, same as http")
            assert _asyncio.run(_rgp(multi_author_forms)) is None, _asyncio.run(_rgp(multi_author_forms))

            # A fabricated multi-author citation with no matching References entry must still be
            # caught now that the detector actually sees "et al." citations at all.
            fabricated_multi_author = (
                "A DQN model achieves the highest accuracy in FMCG forecasting "
                "(Nobody et al., 2099).\n\n"
                "## References\n\n"
                "1. Urgenc, S., et al. (2025). DRL Demand Forecasting. https://example.com/drl\n"
            )
            assert find_non_url_citations(fabricated_multi_author), (
                "fabricated et al. citation with no matching reference must be flagged")

            # Fresh audit, 2026-07-12: _academic_citation_key tried the unanchored in-text regex
            # BEFORE the anchored reference-entry regex, so a numbered reference whose own TITLE
            # happens to contain a (Word, YYYY)-shaped substring got mis-keyed to that inner
            # parenthetical instead of its real leading author/year.
            title_collision = (
                "## References\n\n"
                "1. Urgenc, S., et al. (2025). A study of trends (Preliminary, 1998) in demand "
                "forecasting. https://example.com/drl\n"
            )
            assert parse_academic_references(title_collision) == {
                "urgenc,2025": "https://example.com/drl"
            }, parse_academic_references(title_collision)
        finally:
            _IN_MEMORY_FS.clear()
            _IN_MEMORY_FS.update(saved_fs)
            reset_fetched_urls()
            if _orig_ws6 is None:
                _config.cfg["settings"].pop("workspace", None)
            else:
                _config.cfg["settings"]["workspace"] = _orig_ws6

    contextvars.copy_context().run(_academic_citation_scenario)

    # --- answer mode (ROADMAP.md candidate from dzhng/deep-research's writeFinalAnswer,
    # 2026-07-12): a short direct-answer report shape, `(Source: [Title](URL))` inline citations.
    # Deliberately requires ZERO grounding.py changes — the format is just a different PLACEMENT
    # of the same `[Title](URL)` markdown link syntax the standard style already uses, and every
    # check here extracts URLs format-agnostically. This pins that compatibility claim.
    from prompts import PLANNER_INSTRUCTIONS, ANSWER_REPORT_STYLE_INSTRUCTIONS, ANSWER_CITATION_FORMAT_INSTRUCTIONS

    class _AnswerModeSafeDict(dict):
        def __missing__(self, key):
            return '{' + key + '}'

    _rendered = PLANNER_INSTRUCTIONS.format_map(_AnswerModeSafeDict(
        date="2026-07-12", workspace_dir="/tmp/ws", delegation_instructions="[DELEGATION BLOCK]",
        report_style_instructions=ANSWER_REPORT_STYLE_INSTRUCTIONS,
        citation_format_instructions=ANSWER_CITATION_FORMAT_INSTRUCTIONS,
        delegate_tasks_quota=10, write_workspace_file_quota=10, write_todos_quota=5,
    ))
    assert "{report_style_instructions}" not in _rendered
    assert "{citation_format_instructions}" not in _rendered

    _answer_text = (
        "Guido van Rossum created Python in 1991 "
        "(Source: [Wikipedia](https://en.wikipedia.org/wiki/Guido_van_Rossum))."
    )
    assert extract_cited_urls(_answer_text) == ["https://en.wikipedia.org/wiki/Guido_van_Rossum"]
    assert find_non_url_citations(_answer_text) == []
    assert find_uncited_claim_lines(_answer_text) == []

    # --- stub-fetch detection (live case run 14: a model-invented URL answered by a 200
    # soft-404 — 5KB of subscription chrome — was recorded as a real fetch and passed the
    # hard URL gate) ---
    from tools.web import _stub_reason
    from utils.grounding import real_grounding_problem

    chrome = "\n".join(["[SUSCRÍBETE](https://news.example.co/sub)", "Inicia sesión",
                        "Noticias", "Deportes", "Política"] * 20)
    assert _stub_reason(chrome), "paywall chrome must flag as stub"
    assert _stub_reason("") == "empty page"
    assert _stub_reason("Página no encontrada\n\nError 404"), "tiny not-found page must flag"
    assert _stub_reason("Just a title\n\nAnd one short line."), "near-zero prose must flag"
    _para = ("Colombian exporters shipped record volumes of coffee and flowers this quarter "
             "according to the trade ministry figures released on Tuesday, with analysts "
             "noting sustained demand across European and North American markets overall.")
    real_article = "\n\n".join([_para] * 6 + ["Subscribe to our newsletter for updates"])
    assert _stub_reason(real_article) is None, "real prose mentioning 'subscribe' must NOT flag"

    def _stub_gate_scenario():
        from tools.fs import _IN_MEMORY_FS
        _orig_ws5 = _config.cfg.get("settings", {}).get("workspace")
        _orig_gc = _config.cfg.get("settings", {}).get("grounding_check")
        _config.cfg["settings"]["workspace"] = {"type": "memory"}
        _config.cfg["settings"]["grounding_check"] = {"stub_detection": True, "live_http_verify": False}
        saved_fs = dict(_IN_MEMORY_FS)
        try:
            _IN_MEMORY_FS.clear()
            reset_fetched_urls()
            record_fetched_url("https://news.example.co/paywalled", filename="sources/stub.md",
                               stub="paywall marker")
            _IN_MEMORY_FS["sources/stub.md"] = "Source-URL: https://news.example.co/paywalled\n\nSUSCRÍBETE"
            report = "- dato [news](https://news.example.co/paywalled)"
            problem = _asyncio.run(real_grounding_problem(report))
            assert problem and problem.startswith("stub_source"), problem
            # Flag off -> the stub gate stands down (stub content still can't ground claims,
            # via _fetched_url_files' exclusion).
            _config.cfg["settings"]["grounding_check"]["stub_detection"] = False
            assert _asyncio.run(real_grounding_problem(report)) is None
            _config.cfg["settings"]["grounding_check"]["stub_detection"] = True
            # Same URL later fetched for real (retry got the actual page) -> citation valid.
            record_fetched_url("https://news.example.co/paywalled", filename="sources/real.md")
            _IN_MEMORY_FS["sources/real.md"] = "Source-URL: https://news.example.co/paywalled\n\n" + _para
            problem2 = _asyncio.run(real_grounding_problem(report))
            assert not (problem2 or "").startswith("stub_source"), problem2
        finally:
            _IN_MEMORY_FS.clear()
            _IN_MEMORY_FS.update(saved_fs)
            reset_fetched_urls()
            if _orig_ws5 is None:
                _config.cfg["settings"].pop("workspace", None)
            else:
                _config.cfg["settings"]["workspace"] = _orig_ws5
            if _orig_gc is None:
                _config.cfg["settings"].pop("grounding_check", None)
            else:
                _config.cfg["settings"]["grounding_check"] = _orig_gc

    contextvars.copy_context().run(_stub_gate_scenario)

    # --- URL prefix-match boundary (2026-07-12 audit G1: a genuinely fetched .../article
    # grounded an invented .../article-fake-2024 via bare string-prefixing) ---
    from utils.grounding import _urls_prefix_match

    assert not _urls_prefix_match("https://real.com/article-fake-2024", "https://real.com/article")
    assert _urls_prefix_match("https://real.com/article?utm=1", "https://real.com/article")
    assert _urls_prefix_match("https://real.com/article#s2", "https://real.com/article")
    assert _urls_prefix_match("https://real.com/article/annex", "https://real.com/article")
    # Bare-origin rule unchanged: a domain root never prefix-grounds a deep link.
    assert not _urls_prefix_match("https://real.com/deep/link", "https://real.com")

    # --- grounding_check.enabled master switch honored (2026-07-12 audit G2: the template
    # shipped it but nothing read it — an unhonored kill switch) ---
    def _enabled_off_scenario():
        from tools.fs import _IN_MEMORY_FS
        from tools.core import tool_quotas_ctx as q_ctx
        _orig_ws6 = _config.cfg.get("settings", {}).get("workspace")
        _orig_gc2 = _config.cfg.get("settings", {}).get("grounding_check")
        _config.cfg["settings"]["workspace"] = {"type": "memory", "required_artifact": "final_report.md"}
        _config.cfg["settings"]["grounding_check"] = {"enabled": False}
        saved_fs = dict(_IN_MEMORY_FS)
        try:
            _IN_MEMORY_FS.clear()
            reset_fetched_urls()
            # findings.md fabricated AND the report cites a never-fetched URL — with the master
            # switch off, neither grounding gate may fire (structural checks still pass: the
            # run delegated and both artifacts exist).
            _IN_MEMORY_FS["findings.md"] = "- todo de memoria, sin fuente"
            _IN_MEMORY_FS["final_report.md"] = "- x [g](https://never-fetched.example.com/y)"
            q_ctx.set({"delegate_tasks": {"used": 1, "limit": 5}})
            rs = RunState(tempfile.gettempdir())
            run_state_ctx.set(rs)
            msgs = []
            should_retry, _ = _asyncio.run(run_completion_check(
                query="q", current_input="q", run_state=rs, notify=msgs.append))
            recorded = rs.data["completion_check_attempts"][-1]["problem"]
            assert recorded is None and not should_retry, (recorded, should_retry, msgs)
        finally:
            _IN_MEMORY_FS.clear()
            _IN_MEMORY_FS.update(saved_fs)
            reset_fetched_urls()
            if _orig_ws6 is None:
                _config.cfg["settings"].pop("workspace", None)
            else:
                _config.cfg["settings"]["workspace"] = _orig_ws6
            if _orig_gc2 is None:
                _config.cfg["settings"].pop("grounding_check", None)
            else:
                _config.cfg["settings"]["grounding_check"] = _orig_gc2

    contextvars.copy_context().run(_enabled_off_scenario)

    # --- A3 citation-format layer (run 14's format half: table + detached '### Source URLs') ---
    from utils.grounding import split_prose_from_sources

    # The detached-source-section heading variants must be stripped from prose...
    for heading in ("### Source URLs", "## Sources", "**Fuentes:**", "## Fuentes consultadas",
                    "References used:"):
        rep = f"- claim line\n{heading}\nhttps://x.co/a"
        assert "x.co" not in split_prose_from_sources(rep), heading
    # ...but a real content heading that merely starts with the word must NOT be.
    kept = split_prose_from_sources("## Sources of growth in Colombia\nhttps://x.co/a")
    assert "x.co" in kept

    _table_report = ("| Sector | Valor |\n"
                     "| Fintech | USD 3.5 mil millones en el mercado local en 2024 |\n"
                     "| Agro | 12% de crecimiento anual en exportaciones regionales |\n"
                     "| Salud | 2.300 empresas registradas en el sector durante 2023 |\n"
                     "\n### Source URLs\n- https://gov.example.co/page\n")
    assert len(find_uncited_claim_lines(_table_report)) >= 3
    # Properly formatted claim+citation lines never count, nor do short/heading/separator lines.
    _good_report = ("# Informe\n"
                    "- **[Fintech](https://gov.example.co/page)** USD 3.5 mil millones en 2024\n"
                    "- **[Agro](https://gov.example.co/page)** 12% de crecimiento anual\n"
                    "|---|---|\n")
    assert find_uncited_claim_lines(_good_report) == []
    # Run 15's live FALSE POSITIVE (2026-07-12): a per-niche '#### Sources' block under each
    # h3 section ties that section's claims to sources — held a correctly-grounded report
    # through 3 nudges. h4+ blocks must survive split_prose_from_sources, and a section
    # containing a URL exempts its own figure lines.
    _sectioned_report = (
        "## Research Objective\n"
        "Identify B2B technology opportunities in Colombia for 2026 where a small team could generate revenue.\n\n"
        "### 1. Cattle Traceability\n"
        "| **Regulation** | Ley 2585 de 2026 — trazabilidad ganadera obligatoria |\n"
        "| **Compliance Deadline** | Enacted 4 June 2026; implementation required by that date |\n"
        "| **Market Size** | 2.300 productores registrados en el sistema en 2025 |\n"
        "#### Sources\n"
        "- **[Ley 2585 de 2026](https://sidn.example.gov.co/ley_2585)**\n")
    assert "sidn.example.gov.co" in split_prose_from_sources(_sectioned_report)
    _hits = find_uncited_claim_lines(_sectioned_report)
    assert len(_hits) < 3 and not any("2585" in h for h in _hits), _hits

    # --- context-budget guard: stream char accounting (settings.context_budget_chars) ---
    from engine.orchestrator import stream_content_chars, get_context_budget

    class _C:
        def __init__(self, **kw): [setattr(self, k, v) for k, v in kw.items()]
    class _U:
        def __init__(self, contents): self.contents = contents

    assert stream_content_chars(_U([_C(text="abcde")])) == 5
    assert stream_content_chars(_U([_C(arguments='{"q":1}'), _C(result="xyz")])) == 10
    assert stream_content_chars(_U([_C(result=12345)])) == 5   # non-str result stringified
    assert stream_content_chars(_U([])) == 0
    _orig_cb = _config.cfg.get("settings", {}).get("context_budget_chars")
    try:
        _config.cfg["settings"]["context_budget_chars"] = 50000
        assert get_context_budget() == 50000
        _config.cfg["settings"]["context_budget_chars"] = 0
        assert get_context_budget() == 0
        _config.cfg["settings"].pop("context_budget_chars")
        assert get_context_budget() == 0  # absent = off
    finally:
        if _orig_cb is None:
            _config.cfg["settings"].pop("context_budget_chars", None)
        else:
            _config.cfg["settings"]["context_budget_chars"] = _orig_cb

    # --- C8 charset handling (run 14: the flagship 750KB DIAN law text was saved as mojibake —
    # 'Resolución'/'número' could never string-match, silently gutting every Spanish-term check) ---
    from tools.web import _decode_html_bytes, _strip_boilerplate_html, _meta_declared_encoding

    _latin_body = "<html><body><p>Resolución número 000042 de la DIAN sobre facturación electrónica en Colombia y sus efectos.</p></body></html>"
    # Charset only in the HTTP header
    assert "Resolución número" in _decode_html_bytes(_latin_body.encode("latin-1"), "iso-8859-1")
    # Charset only in the document's own meta tag
    _latin_meta = ('<html><head><meta charset="iso-8859-1"></head><body>'
                   "<p>Resolución número 000042 de la DIAN.</p></body></html>").encode("latin-1")
    assert _meta_declared_encoding(_latin_meta) == "iso-8859-1"
    assert "Resolución número" in _decode_html_bytes(_latin_meta, None)
    # A LYING latin-1 header must not corrupt a UTF-8 page (strict UTF-8 self-validates first)
    assert "Resolución número" in _decode_html_bytes("<p>Resolución número</p>".encode("utf-8"), "iso-8859-1")
    # No declaration anywhere: cp1252 fallback still yields the accents, never mojibake
    assert "Resolución" in _decode_html_bytes("<p>Resolución</p>".encode("latin-1"), None)
    # The stale meta tag must not survive into the cleaned UTF-8 bytes (markitdown would honor
    # it and re-mojibake the content), and the full markitdown round trip must keep the accents.
    _cleaned = _strip_boilerplate_html(_decode_html_bytes(_latin_meta, None))
    assert b"iso-8859-1" not in _cleaned and _cleaned.startswith(b'<meta charset="utf-8">')
    from utils.parsers import convert_to_markdown
    with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="wb") as _tmp_html:
        _tmp_html.write(_strip_boilerplate_html(_decode_html_bytes(_latin_body.encode("latin-1"), "iso-8859-1")))
        _tmp_html_path = _tmp_html.name
    try:
        _md = convert_to_markdown(_tmp_html_path)
        assert _md and "Resolución número 000042" in _md, _md
    finally:
        os.unlink(_tmp_html_path)

    # --- line-scoped claim grounding (review #2 item 4): the old WHOLE-report term overlap let
    # generic shared terms mask per-claim fabrication — run 12's flagship figure was absent from
    # its cited source but passed because other lines shared terms with that same source. ---
    from utils.grounding import claim_grounding_problem

    def _line_claim_scenario():
        from tools.fs import _IN_MEMORY_FS
        _orig_ws8 = _config.cfg.get("settings", {}).get("workspace")
        _config.cfg["settings"]["workspace"] = {"type": "memory"}
        saved_fs = dict(_IN_MEMORY_FS)
        try:
            _IN_MEMORY_FS.clear()
            reset_fetched_urls()
            record_fetched_url("https://gov.example.co/exportaciones", filename="sources/exp.md")
            _IN_MEMORY_FS["sources/exp.md"] = (
                "Source-URL: https://gov.example.co/exportaciones\n\n"
                "Las exportaciones de cacao de Colombia alcanzaron USD 265.1 millones en 2024, "
                "segun cifras oficiales de la entidad nacional de estadistica.")
            supported_line = "- Cacao: USD 265.1 millones en 2024 [gov](https://gov.example.co/exportaciones)"
            # A line whose own figure appears in its cited source -> silent
            assert claim_grounding_problem(supported_line) is None
            # THE masking case: a supported line + a fabricated figure citing the SAME source.
            # The whole-report version passed this (265.1/2024 overlapped report-wide).
            problem = claim_grounding_problem(
                supported_line
                + "\n- Software: USD 3.5 mil millones en 2023 [gov](https://gov.example.co/exportaciones)")
            assert problem and problem.startswith("claim_unsupported"), problem
            # A line with no checkable terms of its own -> skipped, never flagged
            assert claim_grounding_problem(
                "- el sector crece de forma sostenida [gov](https://gov.example.co/exportaciones)") is None
            # Unfetched citation -> the hard URL gate's job, silent here
            assert claim_grounding_problem(
                "- USD 9.9 mil millones [x](https://never-fetched.example.com/a)") is None
        finally:
            _IN_MEMORY_FS.clear()
            _IN_MEMORY_FS.update(saved_fs)
            reset_fetched_urls()
            if _orig_ws8 is None:
                _config.cfg["settings"].pop("workspace", None)
            else:
                _config.cfg["settings"]["workspace"] = _orig_ws8

    contextvars.copy_context().run(_line_claim_scenario)

    # --- quarantined-draft restore beats narration salvage (runs 11/13's endgame) ---
    from engine.tui import _restore_quarantined_draft

    with tempfile.TemporaryDirectory() as tmpdir:
        def _restore_scenario():
            _orig_ws6 = _config.cfg.get("settings", {}).get("workspace")
            _config.cfg["settings"]["workspace"] = {"type": "disk", "dir": tmpdir}
            try:
                draft_path = os.path.join(tmpdir, "final_report.md.rejected_attempt_1")
                with open(draft_path, "w", encoding="utf-8") as f:
                    f.write("# Real Report\nActual researched content.")
                assert _restore_quarantined_draft("final_report.md", "regulation_unsupported")
                restored = open(os.path.join(tmpdir, "final_report.md"), encoding="utf-8").read()
                assert "QUARANTINED DRAFT" in restored and "regulation_unsupported" in restored
                assert "Actual researched content." in restored
                # No-op when the artifact already exists (never clobber a real report)
                assert not _restore_quarantined_draft("final_report.md", "x")
            finally:
                if _orig_ws6 is None:
                    _config.cfg["settings"].pop("workspace", None)
                else:
                    _config.cfg["settings"]["workspace"] = _orig_ws6

        contextvars.copy_context().run(_restore_scenario)

    # --- _get_safe_path Windows escape (review #2 finding 1: os.path.join discards the base
    # for drive-qualified/drive-relative names, letting write_workspace_file leave the workspace) ---
    with tempfile.TemporaryDirectory() as tmpdir:
        def _safe_path_scenario():
            from tools.fs import _get_safe_path
            _orig_ws7 = _config.cfg.get("settings", {}).get("workspace")
            _config.cfg["settings"]["workspace"] = {"type": "disk", "dir": tmpdir}
            try:
                assert _get_safe_path("C:\\evil.md") == ""       # drive-qualified
                assert _get_safe_path("C:evil.md") == ""          # drive-relative
                assert _get_safe_path("..\\evil.md") == ""        # traversal (pre-existing guard)
                ok = _get_safe_path("notes/sub.md")
                assert ok and os.path.commonpath([os.path.abspath(tmpdir), ok]) == os.path.abspath(tmpdir), ok
            finally:
                if _orig_ws7 is None:
                    _config.cfg["settings"].pop("workspace", None)
                else:
                    _config.cfg["settings"]["workspace"] = _orig_ws7

        contextvars.copy_context().run(_safe_path_scenario)

    # --- structural eval scorer (review #2 item 5: rubric tier 1 from _run_state.json, which no
    # other scorer reads — an LLM judge only ever sees the report's self-presentation) ---
    import importlib.util as _ilu
    import json as _json
    _spec = _ilu.spec_from_file_location(
        "eval_evaluate", os.path.join(os.path.dirname(os.path.abspath(__file__)), "eval", "evaluate.py"))
    _ev = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_ev)

    with tempfile.TemporaryDirectory() as tmpdir:
        _u = "https://gov.example.co/page"
        def _write_run(report, findings, state):
            for name, content in (("final_report.md", report), ("findings.md", findings)):
                path = os.path.join(tmpdir, name)
                if content is None:
                    if os.path.exists(path): os.remove(path)
                else:
                    with open(path, "w", encoding="utf-8") as f: f.write(content)
            with open(os.path.join(tmpdir, "_run_state.json"), "w", encoding="utf-8") as f:
                _json.dump(state, f)

        good_state = {"fetched_urls": [{"url": _u, "filename": "sources/p.md"}],
                      "completion_check_attempts": [{"attempt": 0, "problem": None}]}
        # Clean run: report + findings both cite the real fetch, no unresolved problem -> 4/4
        _write_run(f"- x [g]({_u})", f"- f ({_u})", good_state)
        assert _ev.score_structural(tmpdir, "final_report.md") == 1.0
        # Salvaged report citing an unfetched URL, no findings, unresolved problem -> 0/4
        _write_run("> **AUTO-RECOVERED DRAFT** —\n- x [g](https://fake.example.com/a)", None,
                   {"fetched_urls": [{"url": _u, "filename": "sources/p.md"}],
                    "completion_check_attempts": [{"attempt": 2, "problem": "missing_artifact"}]})
        assert _ev.score_structural(tmpdir, "final_report.md") == 0.0
        # Honest partial: clean report + grounded findings, but the run ended unresolved -> 3/4
        _write_run(f"- x [g]({_u})", f"- f ({_u})",
                   {"fetched_urls": [{"url": _u, "filename": "sources/p.md"}],
                    "completion_check_attempts": [{"attempt": 3, "problem": "claim_unsupported"}]})
        assert _ev.score_structural(tmpdir, "final_report.md") == 0.75
        assert _ev.score_structural(None, "final_report.md") == 0.0

    # --- intake verdict parsing (fail-open: the clarifier can never block research) ---
    assert _clarify_verdict("CLEAR") is None
    assert _clarify_verdict("  clear\n") is None
    assert _clarify_verdict("") is None
    assert _clarify_verdict(None) is None
    assert _clarify_verdict("x" * 700) is None  # rambling => proceed
    q = "1. Which country?\n2. What timeframe?"
    assert _clarify_verdict(q) == q

    # --- max_run_minutes wall-clock cutoff actually fires even when the stream goes silent ---
    # (live bug, 2026-07-12): the old `async for update in stream: if deadline exceeded: break`
    # only checked the deadline when an update actually arrived. A real run against
    # deepdelve-tongyi blew 6+ minutes past its configured max_run_minutes=60 with the GPU still
    # actively generating one silent multi-minute <think> block and zero cutoff message. Fixed in
    # run_cli (engine/tui.py) by manually driving __anext__() through asyncio.wait_for(...,
    # timeout=remaining) instead of a plain `async for` — this proves that exact mechanism cuts
    # off a stream that goes silent past its deadline, on a real wall-clock timer, independent of
    # whether the stream ever yields again.
    import time

    class _SlowStream:
        def __aiter__(self):
            return self
        async def __anext__(self):
            if not hasattr(self, "_given"):
                self._given = True
                return "first update"
            await _asyncio.sleep(10)  # simulates a long silent <think> block past the deadline
            return "never reached"

    async def _cutoff_scenario():
        deadline = time.monotonic() + 0.2
        it = _SlowStream().__aiter__()
        received = []
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return received, "pre_check"
            try:
                update = await _asyncio.wait_for(it.__anext__(), timeout=remaining)
            except _asyncio.TimeoutError:
                return received, "timeout"
            received.append(update)

    _start = time.monotonic()
    _received, _how = _asyncio.run(_cutoff_scenario())
    _elapsed = time.monotonic() - _start
    assert _received == ["first update"], _received
    assert _how == "timeout", _how
    assert _elapsed < 2, f"cutoff must fire near the 0.2s deadline, not wait for the 10s stall ({_elapsed}s)"

    # --- fuzzy filename fallback: a sub-agent's garbled/reconstructed filename should still
    # resolve if it clearly maps to one real file ---
    # (live bug, 2026-07-12): sub-agents handed a filename second-hand (not the one they fetched
    # themselves) reconstruct it from memory and get it wrong — 'sources/nixtaverse_nixta?',
    # 'sources/arxiv_org_metaheuristic_analysis?', 'sources/Arxiv????' were all observed live,
    # each burning a full turn + quota unit on a doomed read_workspace_file/grep_workspace_file
    # call. Measured on that one run: 16% of read/grep calls failed "not found", 7/12 of those
    # visibly garbled with a literal '?'.
    from tools.fs import resolve_fuzzy_filename, read_workspace_file, grep_workspace_file, _IN_MEMORY_FS

    def _fuzzy_filename_scenario():
        _orig_ws7 = _config.cfg.get("settings", {}).get("workspace")
        _config.cfg["settings"]["workspace"] = {"type": "memory"}
        saved_fs = dict(_IN_MEMORY_FS)
        try:
            _IN_MEMORY_FS.clear()
            _IN_MEMORY_FS["sources/nixtlaverse_nixtla_exogenous_features_a1b2c3d4.md"] = (
                "Source-URL: https://nixtlaverse.nixtla.io/mlforecast/docs/how-to-guides/exogenous_features.html\n\n"
                "Nixtla exogenous features documentation content here."
            )
            _IN_MEMORY_FS["sources/arxiv_org_genetic_algorithm_deep_learning_sales_e5f6a7b8.md"] = (
                "Source-URL: https://arxiv.org/abs/2410.15047\n\nGenetic algorithm paper content."
            )
            # A garbled request with a literal '?' (exactly the observed live shape) resolves to
            # the one real file it clearly maps to.
            assert resolve_fuzzy_filename("sources/nixtaverse_nixta?") == \
                "sources/nixtlaverse_nixtla_exogenous_features_a1b2c3d4.md"
            # '.' vs '_' mismatch (arxiv.org vs the real arxiv_org slug) also resolves.
            assert resolve_fuzzy_filename("sources/arxiv.org_genetic_algorithm_deep_l") == \
                "sources/arxiv_org_genetic_algorithm_deep_learning_sales_e5f6a7b8.md"
            # Too short / no real overlap -> no confident auto-resolve, stays None.
            assert resolve_fuzzy_filename("sources/xyz?") is None
            assert resolve_fuzzy_filename("sources/completely_unrelated_name_here") is None
            # (In real production flow, resolve_fuzzy_filename is only ever called AFTER an exact
            # get_workspace_file_content lookup already missed — the tools never call it for a
            # name that already resolved. Calling it directly with an exact name is just a
            # 1.0-ratio match to itself, which is correct, not a special case to guard against.)

            # End-to-end through the actual tools: a garbled filename still returns real content
            # (and the response shows the corrected filename, not the garbled one).
            result = read_workspace_file("sources/nixtaverse_nixta?")
            assert "Nixtla exogenous features documentation" in result
            assert "sources/nixtlaverse_nixtla_exogenous_features_a1b2c3d4.md" in result
            assert "not found" not in result

            grep_result = grep_workspace_file("sources/arxiv_org_genetic_algo_deep?", "Genetic")
            assert "genetic algorithm" in grep_result.lower() or "Genetic" in grep_result
            assert "not found — searched" in grep_result

            # A genuinely unresolvable filename still fails cleanly, no false-positive resolve.
            assert "not found" in read_workspace_file("sources/nothing_like_this_exists_at_all.md")
            # Too short/generic even after cleaning ('Arxiv????' -> just 'arxiv') to safely
            # auto-resolve on its own — conservative by design, matches this project's posture.
            assert resolve_fuzzy_filename("sources/Arxiv????") is None
        finally:
            _IN_MEMORY_FS.clear()
            _IN_MEMORY_FS.update(saved_fs)
            if _orig_ws7 is None:
                _config.cfg["settings"].pop("workspace", None)
            else:
                _config.cfg["settings"]["workspace"] = _orig_ws7

    contextvars.copy_context().run(_fuzzy_filename_scenario)

    # --- structured run-state diagnostics (2026-07-12): answer "why did each attempt fail",
    # "how many tool calls errored", and "was this task re-delegated" from _run_state.json alone,
    # without hand-parsing the raw session-event JSON ---
    with tempfile.TemporaryDirectory() as _tmpdir2:
        _rs = RunState(_tmpdir2)

        # record_attempt's new `detail` param persists the full verdict text, not just the label.
        _rs.record_attempt(0, "claim_unsupported", 5, detail="claim_unsupported:https://x.org/a")
        assert _rs.data["completion_check_attempts"][-1]["detail"] == "claim_unsupported:https://x.org/a"
        _rs.record_attempt(1, None, 5)  # detail defaults to None, not required
        assert _rs.data["completion_check_attempts"][-1]["detail"] is None

        # record_tool_error counts + samples (capped at 10 so a constantly-erroring run doesn't
        # bloat _run_state.json).
        for i in range(15):
            _rs.record_tool_error(f"[Agent] Error: sample {i}")
        assert _rs.data["tool_error_count"] == 15
        assert len(_rs.data["tool_error_samples"]) == 10

        # next_subagent_label: first dispatch unchanged, repeats get disambiguated.
        assert _rs.next_subagent_label("SubAgent_background") == "SubAgent_background"
        assert _rs.next_subagent_label("SubAgent_background") == "SubAgent_background#2"
        assert _rs.next_subagent_label("SubAgent_background") == "SubAgent_background#3"
        assert _rs.data["subagent_invocations"]["SubAgent_background"] == 3

        # Collision guard: if a task was genuinely (if implausibly) named to match what the
        # auto-disambiguator would generate for a DIFFERENT task, the generator must not silently
        # collide with it — this is the exact scenario raised when reviewing this feature.
        _rs2 = RunState(_tmpdir2)
        assert _rs2.next_subagent_label("SubAgent_x") == "SubAgent_x"                 # 1st real "x"
        assert _rs2.next_subagent_label("SubAgent_x#2") == "SubAgent_x#2"             # a DIFFERENT
        #                                                                                real task
        #                                                                                literally
        #                                                                                named "x#2"
        assert _rs2.next_subagent_label("SubAgent_x") == "SubAgent_x#3", (
            "2nd real dispatch of 'x' must skip the already-claimed '#2' and land on '#3', "
            "not silently collide with the unrelated task literally named 'x#2'"
        )
        assert len({"SubAgent_x", "SubAgent_x#2", "SubAgent_x#3"}) == 3  # all distinct

    # --- TUI tool-call widget: an error RESULT must not render a green success checkmark ---
    # (live bug, 2026-07-12): a read_workspace_file call that failed with 'Error: Requested
    # function "read_workspace..." not found.' still showed a checkmark, because ToolCallWidget's
    # set_result unconditionally used the success marker regardless of what the result text
    # actually said — this project's tools return formatted error strings instead of raising, so
    # "the call returned" and "the call succeeded" are NOT the same thing.
    from engine.tui import _looks_like_tool_error
    assert _looks_like_tool_error('Error: Requested function "read_workspace..." not found.')
    assert _looks_like_tool_error("Error: 'foo.md' not found.")
    assert _looks_like_tool_error("CRITICAL TOOL EXECUTION ERROR: web_search failed internally.")
    assert _looks_like_tool_error("## Error for Analyze paper\nTask forcefully aborted: timeout\n---")
    assert not _looks_like_tool_error("Wrote 'final_report.md' to disk.")
    assert not _looks_like_tool_error("## Result for background\n**Findings**\n\n- real content")
    assert not _looks_like_tool_error("")
    assert not _looks_like_tool_error(None)

    print("All structural-check assertions passed.")


if __name__ == "__main__":
    main()
