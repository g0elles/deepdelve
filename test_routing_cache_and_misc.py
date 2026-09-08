import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from engine.completion import (
    _with_wait_prefix, _strip_other_problems_addendum,
)
from utils.run_state import record_fetched_url, reset_fetched_urls

# noqa: F401 -- common test-infra names available to every split file below, regardless of
# whether a given file's own retained sections happen to use all of them (ruff prunes genuinely
# unused ones per file). Split 2026-09-07 out of the former single 10,701-line
# test_structural_checks.py (session_status/CURRENT.md carried-forward TODO) -- see
# test_structural_checks.py's own new header for the full split rationale and the file-to-topic
# map. Pure move: every assertion below is byte-identical to its prior body, just regrouped by
# topic into its own main(), all still called in original order from the new thin
# test_structural_checks.py orchestrator.
import config as _config
import tempfile

from engine.completion import (
    Ctx, Verdict, COMPLETION_CHECKS,
)
from tools.fs import _IN_MEMORY_FS
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
    # --- Non-generative routing classifier for delegate_tasks (RESEARCH.md §6, ROADMAP.md
    # "Planned", 2026-07-20) — pins _agent_routing_rejection_reason's pure decision logic (the
    # decided "reject-and-nudge" policy) with a fake prediction, no real classifier artifact or
    # contextvar/async machinery needed. ---
    def _agent_routing_rejection_scenario():
        from engine.orchestrator import _agent_routing_rejection_reason

        caller_roles = frozenset({"WebSearcher", "AcademicSearcher"})

        # 1. No prediction available (classifier disabled, or roster doesn't overlap its known
        # classes) -> must never reject, regardless of what the declared agent_id looks like.
        assert _agent_routing_rejection_reason("WebSearcher", caller_roles, None, 0.6) is None
        assert _agent_routing_rejection_reason("searcher", caller_roles, None, 0.6) is None, (
            "with no prediction, even an obviously-wrong agent_id must not be rejected here -- "
            "that's the pre-existing exact-string check in _run_single_task's own job")

        # 2. Declared agent_id isn't one of the CALLER's real roles at all (the exact real
        # hallucination pattern: "searcher" lowercase, invented role names) -> always rejected,
        # regardless of confidence, since it can never resolve to a real sub-agent anyway.
        reason = _agent_routing_rejection_reason("searcher", caller_roles, ("WebSearcher", 0.66), 0.6)
        assert reason is not None and "not a valid specialist" in reason, reason
        assert "WebSearcher" in reason and "0.66" in reason, reason

        # 3. Declared agent_id IS a real role for this caller, but the classifier strongly
        # disagrees (confidence >= min_confidence, different class) -> rejected as "looks wrong",
        # a different message than the unknown-role case.
        reason = _agent_routing_rejection_reason(
            "AcademicSearcher", caller_roles, ("WebSearcher", 0.8), 0.6)
        assert reason is not None and "looks wrong" in reason, reason

        # 4. Declared agent_id IS a real role and the classifier AGREES -> never rejected (the
        # common case must be a true no-op).
        assert _agent_routing_rejection_reason(
            "WebSearcher", caller_roles, ("WebSearcher", 0.9), 0.6) is None

        # 5. Declared agent_id IS a real role, classifier disagrees but BELOW min_confidence ->
        # must abstain (same "none apply" pattern ATLAS/AdaMAST uses, RESEARCH.md §1) rather than
        # force a low-confidence guess into a rejection.
        assert _agent_routing_rejection_reason(
            "AcademicSearcher", caller_roles, ("WebSearcher", 0.5), 0.6) is None, (
            "a low-confidence disagreement must abstain, not reject")

    _agent_routing_rejection_scenario()

    # --- RAG findings cache (ROADMAP.md "Strategic options" item 5, RESEARCH.md §8, 2026-07-20) —
    # replaces the deleted knowledge_cache/experience_cache (929b987). Two scenarios: the pure
    # write-gate decision logic (no embeddings/network needed), and the real embedding-based
    # lookup/save round-trip (real tiny all-MiniLM-L6-v2 embeddings, same approach
    # agent_routing.py's own self-test uses -- no mock needed, the model is small and local). ---
    def _rag_cache_write_gate_scenario():
        from engine.orchestrator import _should_cache_finding

        real_finding = "- **[Rust Releases](https://releases.rs/)**: Rust 1.97.1 is current stable."
        narration_only = "I'll search for authoritative information about the latest stable version."

        # 1. Disabled -> never cache, regardless of how clean the finding is.
        assert _should_cache_finding("", [{"url": "https://example.com"}], False, real_finding) is False

        # 2. Enabled, clean (no warnings), real new URL, real markdown-link finding -> cache.
        assert _should_cache_finding("", [{"url": "https://example.com"}], True, real_finding) is True

        # 3. Enabled, but a verification/relevance warning is present -> never cache, even with a
        # real new URL. A cache entry must never be less verified than a same-run finding.
        assert _should_cache_finding(
            "\n\n[SYSTEM VERIFICATION WARNING: ...]", [{"url": "https://example.com"}], True, real_finding
        ) is False

        # 4. Enabled, clean, but no real new URL (task-name-only fallback finding) -> never cache,
        # there's no real source to cite.
        assert _should_cache_finding("", [], True, real_finding) is False

        # 5. Enabled, clean, real new URL, but the text is pre-delegation NARRATION rather than a
        # real consolidated finding (no markdown-link citation) -> never cache. Found live 2026-07-20:
        # a Searcher's own "I'll search for..." narration can carry a real new_urls entry and zero
        # verification warnings, yet contain no actual finding at all.
        assert _should_cache_finding("", [{"url": "https://example.com"}], True, narration_only) is False

    _rag_cache_write_gate_scenario()

    def _rag_cache_lookup_scenario():
        import time as _time
        import tempfile as _tempfile
        import config as _config
        from utils import rag_cache as _rag_cache

        with _tempfile.TemporaryDirectory() as tmp_dir:
            cache_path = os.path.join(tmp_dir, "rag_cache_test.json")
            _config.cfg["settings"]["rag_cache"] = {
                "enabled": True, "path": cache_path, "max_age_days": 7,
                "min_similarity": 0.75, "top_k": 3,
            }
            # Force a clean in-memory state -- this module-level singleton persists across scenario
            # functions in the same test process, same caution as agent_routing's own self-test.
            _rag_cache._entries = None
            _rag_cache._matrix = None

            _rag_cache.save(
                "current stable version of Rust programming language",
                "https://releases.rs/", "Rust 1.97.1 is the current stable release.", "test-model",
            )

            # A near-duplicate query must hit the cached entry above the configured threshold.
            hits = _rag_cache.lookup("what is the latest stable Rust release", min_similarity=0.5)
            assert hits, "a semantically similar query must return the cached entry"
            assert hits[0]["source_url"] == "https://releases.rs/"
            assert "1.97.1" in hits[0]["summary"]

            # A genuinely unrelated query must NOT match at the configured threshold.
            no_hits = _rag_cache.lookup(
                "history of the Roman aqueduct system", min_similarity=0.75
            )
            assert no_hits == [], "an unrelated query must not return the Rust finding"

            # A stale entry (older than max_age_days) must be excluded even with a perfect query.
            _rag_cache._entries[0]["timestamp"] = _time.time() - (8 * 86400)
            _rag_cache._matrix = None  # force rebuild so the mutated timestamp is picked up
            stale_hits = _rag_cache.lookup(
                "current stable version of Rust programming language",
                min_similarity=0.5, max_age_days=7,
            )
            assert stale_hits == [], "an entry older than max_age_days must be excluded"

            _rag_cache._entries = None
            _rag_cache._matrix = None

    _rag_cache_lookup_scenario()

    # --- edit_workspace_file (2026-07-28, added after a live stall): Builder/FindingsWriter had
    # only write_workspace_file for corrections, forcing a full-document regeneration for even a
    # one-line fix (drop a bad citation, correct a figure) -- confirmed live to be a real capacity
    # edge for a small model (repeated attempts producing nothing usable on a "drop 3 citations,
    # keep everything else" correction). edit_workspace_file does a targeted old_string/new_string
    # replacement instead, same shape as this project's own editing tool, so a small model only
    # has to get the ONE changed span right, not regenerate the whole file from memory. ---
    from tools.fs import edit_workspace_file, write_workspace_file, get_workspace_file_content

    def _edit_workspace_file_scenario():
        import config as _config
        _orig_ws_type = _config.cfg.get("settings", {}).get("workspace", {}).get("type")
        _config.cfg.setdefault("settings", {}).setdefault("workspace", {})["type"] = "memory"
        try:
            write_workspace_file(filename="doc.md", content="alpha\nbeta\ngamma\n")

            # Unique match -> replaced cleanly.
            result = edit_workspace_file(filename="doc.md", old_string="beta", new_string="BETA")
            assert "Edited" in result and "1 replacement" in result, result
            assert get_workspace_file_content("doc.md") == "alpha\nBETA\ngamma\n"

            # old_string not present at all -> clear error, file unchanged.
            miss = edit_workspace_file(filename="doc.md", old_string="not-there", new_string="x")
            assert miss.startswith("Error:") and "not found" in miss, miss
            assert get_workspace_file_content("doc.md") == "alpha\nBETA\ngamma\n"

            # old_string matches more than once without replace_all -> refuses rather than
            # guessing which occurrence was meant.
            write_workspace_file(filename="dup.md", content="x\nx\ny\n")
            ambiguous = edit_workspace_file(filename="dup.md", old_string="x", new_string="z")
            assert ambiguous.startswith("Error:") and "2 times" in ambiguous, ambiguous
            assert get_workspace_file_content("dup.md") == "x\nx\ny\n"

            # replace_all=True replaces every occurrence.
            all_result = edit_workspace_file(filename="dup.md", old_string="x", new_string="z", replace_all=True)
            assert "2 replacements" in all_result, all_result
            assert get_workspace_file_content("dup.md") == "z\nz\ny\n"
        finally:
            _IN_MEMORY_FS.clear()
            if _orig_ws_type is None:
                _config.cfg.get("settings", {}).get("workspace", {}).pop("type", None)
            else:
                _config.cfg["settings"]["workspace"]["type"] = _orig_ws_type
    _edit_workspace_file_scenario()

    # Both writer roles (Builder, FindingsWriter) must actually have the new tool wired in --
    # app.py's own SubAgentConfig tool lists, not just defined in tools/fs.py and never attached.
    import app as _app_mod_check
    assert edit_workspace_file in _app_mod_check.builder_agent.tools, (
        "builder_agent must have edit_workspace_file in its tools list")
    assert edit_workspace_file in _app_mod_check.findings_writer_agent.tools, (
        "findings_writer_agent must have edit_workspace_file in its tools list")

    # --- api.backend pluggable serving endpoint (2026-07-28): _build_client and
    # _get_default_options must both branch on api.backend, and the "ollama" branch must use
    # OllamaChatClient's own `think` option rather than the OpenAI-only chat_template_kwargs/
    # extra_body shape -- confirmed live (RESEARCH.md §14e) that shape is what leaks reasoning
    # back into tool-calling turns on Ollama's OpenAI-compat endpoint even with
    # enable_thinking:false, and the whole point of the native OllamaChatClient branch is to avoid
    # it entirely. Source-inspection, same "not easily unit-testable in isolation" precedent as
    # the resume-carryover tuple assertions above -- these functions build real network clients,
    # not pure functions worth mocking for a structural pin. ---
    import inspect as _inspect2
    import engine.orchestrator as _orch_mod_check
    _build_client_src = _inspect2.getsource(_orch_mod_check._build_client)
    assert 'api_cfg.get("backend"' in _build_client_src or 'get("backend"' in _build_client_src, (
        "_build_client must branch on api.backend")
    assert "OllamaChatClient" in _build_client_src, (
        "_build_client's ollama branch must use agent_framework.ollama.OllamaChatClient")

    _default_options_src = _inspect2.getsource(_orch_mod_check._get_default_options)
    assert 'get("backend"' in _default_options_src, (
        "_get_default_options must branch on api.backend")
    # Split on the ollama branch's own early return so the two shapes can't accidentally bleed
    # into each other -- the ollama branch must never carry the OpenAI-only extra_body dance, and
    # vice versa doesn't matter (the openai branch predates and is unaffected by this feature).
    _ollama_branch_src = _default_options_src.split('== "ollama"', 1)[1].split("return options", 1)[0]
    assert "chat_template_kwargs" not in _ollama_branch_src, (
        "_get_default_options' ollama branch must not use the OpenAI-only chat_template_kwargs shape")
    assert '"think"' in _ollama_branch_src, (
        "_get_default_options' ollama branch must set the native `think` option")

    # --- Standing audit: every non-self-resolving check in COMPLETION_CHECKS/GROUNDING_CHECKS
    # must call _capped (2026-07-31, the actual payoff of the night's structural fix -- see
    # ARCHITECTURE.md). A check that returns a Verdict for a problem NOT in _BUILDER_FIXABLE_
    # PROBLEMS/_FINDINGS_WRITER_FIXABLE_PROBLEMS never dispatches a real Builder/FindingsWriter
    # fix on its own -- if it also never caps its own firing, it can win first-match on EVERY
    # attempt for as long as its condition holds, permanently starving every check below it. Six
    # live incidents and two more found by this exact audit (check_propagated_ungrounded_content,
    # check_report_underuses_evidence) happened before this test existed. A future check that
    # skips _capped now fails here immediately instead of five sessions from now at 2am.
    #
    # Exemption is either (a) the check's own returned problem name is Builder/FindingsWriter-
    # fixable -- it dispatches its own real recovery every time, converging rather than starving
    # anything -- or (b) an explicit, justified allowlist: check_not_delegated self-clears the
    # moment ctx.delegated flips true (not a persistent-condition risk); check_untracked_
    # delegation has its own STRICTER "fires at most once ever" gate, deliberately never
    # escalating; check_uneven_task_investment is gated behind BOTH findings.md and req_artifact
    # already existing, so it structurally cannot starve missing_findings/missing_artifact, and
    # its own docstring documents relying on run_completion_check's generic force_whole_rebuild/
    # final-exhaustion escalation as its bound instead.
    def _starvation_audit_scenario():
        import re as _re
        import engine.completion as _comp
        _EXEMPT_FUNCTION_NAMES = {
            "check_not_delegated", "check_untracked_delegation", "check_uneven_task_investment",
        }
        _self_resolving = set(_comp._BUILDER_FIXABLE_PROBLEMS) | set(_comp._FINDINGS_WRITER_FIXABLE_PROBLEMS)
        violations = []
        for check_fn in _comp.COMPLETION_CHECKS + _comp.GROUNDING_CHECKS:
            name = check_fn.__name__
            if name in _EXEMPT_FUNCTION_NAMES:
                continue
            src = _inspect2.getsource(check_fn)
            problems = set(_re.findall(r'Verdict\(\s*\n?\s*"([a-z_]+)"', src))
            if problems & _self_resolving:
                continue  # self-resolving: dispatches its own real fix, doesn't need a cap
            if "_capped(" not in src:
                violations.append(name)
        assert not violations, (
            "these checks are not Builder/FindingsWriter-fixable, not on the explicit exempt "
            "allowlist, and don't call _capped -- they can permanently starve every check below "
            "them in COMPLETION_CHECKS/GROUNDING_CHECKS: " + ", ".join(violations)
        )

    _starvation_audit_scenario()

    # --- _with_wait_prefix (2026-08-25, Tsui self-correction blind-spot finding, COLM 2026,
    # arXiv:2507.02778, fully read): a short explicit self-reflection cue prepended to a retry
    # nudge ONLY when this exact problem already fired on a preceding attempt -- adapted from a
    # finding that models often fail to fix an error framed as their own prior turn, even when
    # they can fix the identical error framed as external input. Pure-function test, no model
    # dependency. ---
    def _wait_prefix_scenario():
        with tempfile.TemporaryDirectory() as tmpdir_wp:
            rs = RunState(tmpdir_wp)
            v = Verdict("thin_coverage", "w", "i")
            # First occurrence ever (no prior attempts recorded) -> no prefix.
            assert _with_wait_prefix(v, rs).inject == "i", "must not prefix on a problem's first occurrence"
            rs.data["completion_check_attempts"] = [{"problem": "thin_coverage"}]
            # Same problem fired on the immediately preceding attempt -> prefixed.
            assert _with_wait_prefix(v, rs).inject == "Wait. i", (
                "must prefix when this exact problem already fired last attempt")
            # A DIFFERENT problem on the preceding attempt -> no prefix (this is this problem's
            # own first occurrence, nothing yet to have blind-spotted).
            v2 = Verdict("missing_artifact", "w", "i")
            assert _with_wait_prefix(v2, rs).inject == "i", (
                "must not prefix when the PRECEDING attempt was a different problem")
            # Ablation switch suppresses it entirely.
            _orig_abl = _config.cfg.get("settings", {}).get("ablation")
            _config.cfg["settings"]["ablation"] = {"disable_wait_prefix": True}
            try:
                assert _with_wait_prefix(v, rs).inject == "i", "ablation switch must fully disable this"
            finally:
                if _orig_abl is None:
                    _config.cfg["settings"].pop("ablation", None)
                else:
                    _config.cfg["settings"]["ablation"] = _orig_abl

    _wait_prefix_scenario()

    # --- _strip_other_problems_addendum + disable_other_problems_addendum ablation (2026-08-25,
    # ReflexGrad arXiv:2511.14584 finding, fully read): their own ablation found merging multiple
    # simultaneous corrective signals into one instruction "produced incoherent guidance" -- the
    # same shape as DeepDelve's _with_other_problems_addendum bundling a lower-priority secondary
    # problem onto a primary nudge. force_whole_rebuild's own ONE expensive full-rewrite attempt
    # must not be diluted this way, so it strips the addendum back off before building its
    # instruction; the ablation switch lets the addendum mechanism itself be A/B tested. ---
    def _strip_addendum_scenario():
        clean = "SYSTEM WARNING: fix the thing."
        addendum = " ALSO currently true (lower priority than the above -- do not undo it while fixing the above): other_problem: some warning"
        assert _strip_other_problems_addendum(clean + addendum) == clean, (
            "must remove exactly the addendum tail, leaving the primary text untouched")
        assert _strip_other_problems_addendum(clean) == clean, (
            "must be a no-op when no addendum is present")
        # The GROUNDING_CHECKS sibling's slightly different wording shares the same leading marker.
        grounding_addendum = " ALSO currently true in the same document (lower priority than the above -- do not undo it while fixing the above): other_problem"
        assert _strip_other_problems_addendum(clean + grounding_addendum) == clean, (
            "must also strip the GROUNDING_CHECKS-flavored addendum wording")

    _strip_addendum_scenario()

    # --- disable_other_problems_addendum ablation, exercised through the real functions (not
    # just the string-stripping helper above) -- confirms the gate actually short-circuits
    # _with_other_problems_addendum/_with_other_grounding_addendum before they compute anything. ---
    def _other_problems_addendum_ablation_scenario():
        from tools.fs import _IN_MEMORY_FS
        from engine.completion_starvation import _with_other_problems_addendum, _with_other_grounding_addendum
        _orig_ws15 = _config.cfg.get("settings", {}).get("workspace")
        _config.cfg["settings"]["workspace"] = {"type": "memory", "required_artifact": "final_report.md"}
        _orig_abl2 = _config.cfg.get("settings", {}).get("ablation")
        saved_fs = dict(_IN_MEMORY_FS)
        try:
            _IN_MEMORY_FS.clear()
            reset_fetched_urls()
            record_fetched_url(_SRC, filename="sources/page.md")
            _IN_MEMORY_FS["sources/page.md"] = _SOURCE_TEXT
            with tempfile.TemporaryDirectory() as tmpdir_ab:
                rs = RunState(tmpdir_ab)
                ctx = Ctx(req_artifact="final_report.md", attempt=0, max_attempts=3, delegated=True,
                          files=["final_report.md"], content="- x [g](" + _SRC + ")", quotas=None,
                          run_state=rs)
                v = Verdict("missing_artifact", "w", "i")
                _config.cfg["settings"]["ablation"] = {"disable_other_problems_addendum": True}
                v_gated = _with_other_problems_addendum(v, ctx, COMPLETION_CHECKS)
                assert v_gated.inject == "i", "ablation must short-circuit before computing anything"
                v_gated2 = _with_other_grounding_addendum(v, ctx)
                assert v_gated2.inject == "i", "ablation must short-circuit the grounding sibling too"
        finally:
            _IN_MEMORY_FS.clear()
            _IN_MEMORY_FS.update(saved_fs)
            reset_fetched_urls()
            if _orig_ws15 is None:
                _config.cfg["settings"].pop("workspace", None)
            else:
                _config.cfg["settings"]["workspace"] = _orig_ws15
            if _orig_abl2 is None:
                _config.cfg["settings"].pop("ablation", None)
            else:
                _config.cfg["settings"]["ablation"] = _orig_abl2

    _other_problems_addendum_ablation_scenario()

    # --- TUI command palette (2026-08-20, ROADMAP QoE item): Textual's built-in command palette
    # (ctrl+p, ENABLE_COMMAND_PALETTE default True, never overridden by this app) now surfaces
    # BasicTuiAgent's own SLASH_COMMANDS via a registered Provider, live-verified end to end with
    # Textual's own Pilot test harness -- a zero-arg command (/toggle_thinking) must actually
    # execute on selection, an arg-taking one (/depth) must fill the prompt and focus it without
    # auto-submitting (no UI to collect the argument in the palette itself). ---
    def _command_palette_scenario():
        import asyncio as _asyncio_cp
        import engine.tui as _tui_cp
        from textual.command import CommandPalette as _CommandPalette

        async def _run():
            app = _tui_cp.BasicTuiAgent(builder=None)
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                await pilot.press("ctrl+p")
                await pilot.pause()
                assert any(isinstance(s, _CommandPalette) for s in app.screen_stack), (
                    "ctrl+p did not open the command palette"
                )
                await pilot.press("escape")
                await pilot.pause()

                provider = _tui_cp.SlashCommandProvider(app.screen)
                before = _config.cfg["settings"].get("enable_thinking")
                thinking_hits = [h async for h in provider.search("toggle_thinking")]
                assert thinking_hits, "expected /toggle_thinking to fuzzy-match"
                await thinking_hits[0].command()
                await pilot.pause()
                after = _config.cfg["settings"].get("enable_thinking")
                assert before != after, "palette-selected zero-arg command did not execute"
                _config.cfg["settings"]["enable_thinking"] = before  # restore

                prompt = app.query_one("#prompt-input")
                depth_hits = [h async for h in provider.search("depth")]
                assert depth_hits, "expected /depth to fuzzy-match"
                await depth_hits[0].command()
                await pilot.pause()
                assert prompt.value == "/depth ", (
                    "an arg-taking command must fill, not auto-submit: " + repr(prompt.value)
                )
                assert app.focused is prompt

        _asyncio_cp.run(_run())

    _command_palette_scenario()

    # --- academic_citation_existence_problem / real_grounding_problem wiring (2026-08-22):
    # a real, fetched URL whose academic (Author, Year) attribution doesn't correspond to any real
    # paper -- the "real URL, fabricated attribution" mashup pattern URL-presence checks alone
    # cannot see. The real Semantic Scholar API isn't called in this fast suite -- mocked at
    # utils.grounding._semantic_scholar_lookup to test WIRING correctness (config opt-in gate ->
    # academic_citation_existence_problem -> real_grounding_problem ordering), same boundary this
    # project already draws for nli_unsupported_problem/topical_relevance_problem above. ---
    def _academic_citation_verify_scenario():
        from tools.fs import _IN_MEMORY_FS
        from unittest.mock import patch
        import asyncio as _asyncio_ac
        import config as _config_ac
        import utils.grounding as _grounding_mod_ac

        gov_url = "https://gov.example.co/ac-page"
        ref_url = "https://example.com/fabricated-paper"
        content = (
            f"- The pilot program launched in 2020 (Smith, 2020) [gov]({gov_url})\n\n"
            "## References\n"
            f"1. Smith, J. (2020). A Fabricated Paper. {ref_url}\n"
        )
        source_text = ("Source-URL: " + gov_url + "\n\n"
                        + "The pilot program launched in 2020 after extensive review. " * 3)

        _orig_gc_ac = _config_ac.cfg.get("settings", {}).get("grounding_check")
        saved_fs = dict(_IN_MEMORY_FS)
        try:
            _IN_MEMORY_FS.clear()
            reset_fetched_urls()
            record_fetched_url(gov_url, filename="sources/ac_page.md")
            record_fetched_url(ref_url, filename="sources/ac_ref.md")
            _IN_MEMORY_FS["sources/ac_page.md"] = source_text
            _IN_MEMORY_FS["sources/ac_ref.md"] = "Source-URL: " + ref_url + "\n\nUnrelated placeholder content."

            # Default off: even with a mocked NOT_FOUND lookup, the check must never run unless
            # explicitly enabled -- opt-in default regression guard.
            _config_ac.cfg["settings"]["grounding_check"] = {"nli_verify": False, "topical_relevance_check": False}
            with patch.object(_grounding_mod_ac, "_semantic_scholar_lookup", return_value="NOT_FOUND"):
                result = _asyncio_ac.run(_grounding_mod_ac.real_grounding_problem(content))
                assert result is None, ("academic_citation_verify must default off", result)

            # Enabled + NOT_FOUND -> flags, URL-scoped detail.
            _config_ac.cfg["settings"]["grounding_check"] = {
                "nli_verify": False, "topical_relevance_check": False, "academic_citation_verify": True,
            }
            with patch.object(_grounding_mod_ac, "_semantic_scholar_lookup", return_value="NOT_FOUND"):
                result = _asyncio_ac.run(_grounding_mod_ac.real_grounding_problem(content))
                assert result == f"academic_citation_unverified:{ref_url}", result
                assert _grounding_mod_ac.academic_citation_existence_problem(content) == f"academic_citation_unverified:{ref_url}"

            # Enabled + VERIFIED -> clean pass, confirming the new check doesn't regress the
            # existing clean-pass path once wired in.
            with patch.object(_grounding_mod_ac, "_semantic_scholar_lookup", return_value="VERIFIED"):
                result = _asyncio_ac.run(_grounding_mod_ac.real_grounding_problem(content))
                assert result is None, result

            # Fail-open: the real lookup raising/timing out must never manufacture a flag.
            with patch.object(_grounding_mod_ac, "_semantic_scholar_lookup", return_value=None):
                result = _asyncio_ac.run(_grounding_mod_ac.real_grounding_problem(content))
                assert result is None, result
        finally:
            _IN_MEMORY_FS.clear()
            _IN_MEMORY_FS.update(saved_fs)
            reset_fetched_urls()
            if _orig_gc_ac is None:
                _config_ac.cfg["settings"].pop("grounding_check", None)
            else:
                _config_ac.cfg["settings"]["grounding_check"] = _orig_gc_ac

    _academic_citation_verify_scenario()



if __name__ == "__main__":
    main()
    print("test_routing_cache_and_misc OK")
