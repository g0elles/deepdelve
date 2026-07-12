"""Smallest thing that fails if the structural-check heuristics break.
Run: venv/Scripts/python test_structural_checks.py (no framework needed).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from engine.orchestrator import _extract_excluded_topics, _lacks_concrete_subject
from utils.grounding import find_non_url_citations, fully_ungrounded
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

    print("All structural-check assertions passed.")


if __name__ == "__main__":
    main()
