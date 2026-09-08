import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from utils.run_state import record_fetched_url, reset_fetched_urls

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
    run_completion_check,
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
    # --- find_cross_source_contradictions: citation-only lines must never be treated as claims.
    # Live-confirmed false positive (2026-07-14, real Iceland-population TUI run): an agency name
    # ("Statistics Iceland") appearing ONLY inside a `- Source: [Title - Statistics Iceland](url)`
    # citation attribution in the report, and dozens of times across a long fetched Wikipedia
    # article as bare source attribution / image captions / reference-list entries, got paired
    # with unrelated nearby years by _extract_figure_claims's nearest-figure heuristic -- firing a
    # phantom cross_source_contradiction on every single Builder rewrite (report never actually
    # said anything wrong), an unfixable, non-converging retry loop. ---
    def _cross_source_citation_line_scenario():
        from utils.grounding import find_cross_source_contradictions, _is_citation_only_line

        assert _is_citation_only_line(
            "- Source: [The population on 1 January 2025 - Statistics Iceland]"
            "(https://statice.is/publications/news-archive/inhabitants/the-population-on-1-january-2025/)"
        )
        assert _is_citation_only_line('2. [↑](#cite_ref-2) ["Population by origin"](https://example.com).')
        # Genuine prose must NOT be classified as citation-only, even with a link or a subject
        # name inside it -- only bibliographic/attribution-only lines are excluded.
        assert not _is_citation_only_line(
            "The population of Iceland from 1703 to 2017, using data from Statistics Iceland."
        )
        assert not _is_citation_only_line(
            "There is a slight discrepancy between the annual growth rate indicated by the "
            "primary Statistics Iceland data (~1.5%) and the trajectory suggested by the "
            "Wikipedia projection (~394,530)."
        )

        def _fake_scenario():
            saved_fs = dict(_IN_MEMORY_FS)
            try:
                _IN_MEMORY_FS.clear()
                reset_fetched_urls()
                report = (
                    "As of January 1, 2025, the official population of Iceland was **389,444**.\n"
                    "- Source: [The population on 1 January 2025 - Statistics Iceland]"
                    "(https://statice.is/pop-2025)\n"
                )
                record_fetched_url("https://statice.is/pop-2025", filename="sources/statice.md")
                _IN_MEMORY_FS["sources/statice.md"] = (
                    "Source-URL: https://statice.is/pop-2025\n\n"
                    "The population on 1 January 2025 was 389,444."
                )
                record_fetched_url("https://en.wikipedia.org/wiki/Demographics_of_Iceland", filename="sources/wiki.md")
                # Real-shape reproduction: "Statistics Iceland" as bare attribution in a caption
                # (2017, unrelated to any population figure) plus a numbered reference-list entry
                # citing "Statistics Iceland" again (2024) -- neither is a genuine competing claim.
                _IN_MEMORY_FS["sources/wiki.md"] = (
                    "Source-URL: https://en.wikipedia.org/wiki/Demographics_of_Iceland\n\n"
                    "The population of Iceland from 1703 to 2017, using data from Statistics Iceland.\n\n"
                    '2. [↑](#cite_ref-2) ["Population by origin"](https://example.com). '
                    "*Statistics Iceland*. Retrieved 2024-01-01."
                )
                hits = find_cross_source_contradictions(report)
                assert hits == [], hits
            finally:
                _IN_MEMORY_FS.clear()
                _IN_MEMORY_FS.update(saved_fs)
                reset_fetched_urls()

        _orig_ws_csc = _config.cfg.get("settings", {}).get("workspace")
        _config.cfg["settings"]["workspace"] = {"type": "memory", "required_artifact": "final_report.md"}
        try:
            contextvars.copy_context().run(_fake_scenario)
        finally:
            if _orig_ws_csc is None:
                _config.cfg["settings"].pop("workspace", None)
            else:
                _config.cfg["settings"]["workspace"] = _orig_ws_csc

    _cross_source_citation_line_scenario()

    # --- NLI grounding verification (live case 2026-07-12, NVIDIA NIM gpt-oss-20b benchmark run):
    # a citation to a real, fetched source whose claim shares terms with it (passes
    # content_level_check) but is actually contradicted by the source's real content (a paper
    # title quoted with one word swapped). The real NLI model isn't loaded in this fast suite —
    # mocked at utils.grounding._get_nli_model to test WIRING correctness (config toggle ->
    # ordering after content_level_check -> Verdict routing -> quarantine -> nudge phrase), same
    # boundary this project already draws elsewhere (e.g. live_http_verify is a real network call,
    # never exercised by the fast suite either). ---
    def _nli_verify_scenario():
        from tools.core import tool_quotas_ctx as q_ctx
        from unittest.mock import patch
        import utils.grounding as _grounding_mod

        class _FakeScore:
            def __init__(self, idx):
                self._idx = idx
            def argmax(self):
                return self._idx

        class _FakeModel:
            def __init__(self, idx):
                self._idx = idx
            def predict(self, pairs):
                return [_FakeScore(self._idx) for _ in pairs]

        _orig_ws6 = _config.cfg.get("settings", {}).get("workspace")
        _config.cfg["settings"]["workspace"] = {"type": "memory", "required_artifact": "final_report.md"}
        # nli_verify stays on (that's what this scenario tests), but topical_relevance_check must
        # be off -- otherwise the entailment/neutral sub-case below (NLI returns None) falls
        # through to a REAL, unmocked topical-relevance model load, same anti-pattern the matrix's
        # own nli_verify:False guard exists to prevent (see its comment above).
        _orig_gc6 = _config.cfg.get("settings", {}).get("grounding_check")
        _config.cfg["settings"]["grounding_check"] = {"topical_relevance_check": False}
        saved_fs = dict(_IN_MEMORY_FS)
        try:
            _IN_MEMORY_FS.clear()
            reset_fetched_urls()
            # Dedicated source/claim pair, NOT the shared _SRC/_SOURCE_TEXT fixture: that fixture
            # deliberately has zero numbers/capitalized phrases (so claim_grounding_problem's OTHER
            # matrix rows can test "no checkable terms -> skipped" vs. "checkable terms -> zero
            # overlap -> flagged"). This scenario needs a claim that DOES share a term with its
            # source (so content_level_check passes and execution actually reaches
            # nli_unsupported_problem) -- the shared multi-word proper-noun phrase "National Cyber
            # Strategy", NOT a shared bare year: a 2026-08-25 fix made _grounded_claim_pairs skip
            # any claim anchored ONLY by a bare year (too weak/ubiquitous an anchor -- see that
            # fix's own comment), so this fixture was updated to use a real distinguishing term
            # instead, preserving genuine wiring coverage rather than relying on the now-excluded
            # year-only match.
            _nli_src = "https://gov.example.co/nli-test-page"
            _nli_source_text = ("Source-URL: " + _nli_src + "\n\n"
                                 + "The National Cyber Strategy was formally adopted in 2020 "
                                   "following extensive review. " * 3)
            record_fetched_url(_nli_src, filename="sources/nli_page.md")
            _IN_MEMORY_FS["sources/nli_page.md"] = _nli_source_text
            _IN_MEMORY_FS["findings.md"] = f"- hallado ({_nli_src})"
            claim_line = f"- The National Cyber Strategy launched in 2020 under a different name [gov]({_nli_src})"
            _IN_MEMORY_FS["final_report.md"] = claim_line
            q_ctx.set({"delegate_tasks": {"used": 1, "limit": 5}})

            # Contradiction mocked -> nli_unsupported verdict, quarantined, distinctive nudge.
            with patch.object(_grounding_mod, "_get_nli_model", return_value=_FakeModel(0)):
                with tempfile.TemporaryDirectory() as tmpdir5:
                    rs = RunState(tmpdir5)
                    run_state_ctx.set(rs)
                    msgs = []
                    should_retry, _ = _asyncio.run(run_completion_check(
                        query="q", current_input="q", run_state=rs, notify=msgs.append))
                    recorded = rs.data["completion_check_attempts"][-1]["problem"]
                    assert recorded == "nli_unsupported", (recorded, msgs)
                    assert should_retry
                    assert "isn't actually entailed" in msgs[-1] or "NOT actually supported" in msgs[-1], msgs

            # Entailment/neutral mocked (never contradiction) -> clean pass, confirming the new
            # check doesn't regress the existing clean-pass path once wired in.
            with patch.object(_grounding_mod, "_get_nli_model", return_value=_FakeModel(2)):
                with tempfile.TemporaryDirectory() as tmpdir6:
                    rs = RunState(tmpdir6)
                    run_state_ctx.set(rs)
                    msgs = []
                    should_retry, _ = _asyncio.run(run_completion_check(
                        query="q", current_input="q", run_state=rs, notify=msgs.append))
                    recorded = rs.data["completion_check_attempts"][-1]["problem"]
                    assert recorded is None, (recorded, msgs)
                    assert not should_retry
        finally:
            _IN_MEMORY_FS.clear()
            _IN_MEMORY_FS.update(saved_fs)
            reset_fetched_urls()
            if _orig_ws6 is None:
                _config.cfg["settings"].pop("workspace", None)
            else:
                _config.cfg["settings"]["workspace"] = _orig_ws6
            if _orig_gc6 is None:
                _config.cfg["settings"].pop("grounding_check", None)
            else:
                _config.cfg["settings"]["grounding_check"] = _orig_gc6

    contextvars.copy_context().run(_nli_verify_scenario)

    # --- Editorializing MiniCheck classifier check (2026-08-29/30, RAGTruth-informed whack-a-mole
    # root-cause fix): fifth grounding layer, opt-in (editorial_detection_check default False).
    # Real minicheck model isn't loaded in this fast suite -- mocked at
    # utils.grounding._get_editorial_detector to test WIRING correctness (config toggle -> ordering
    # after nli_verify/topical_relevance_check -> Verdict routing -> quarantine -> nudge phrase,
    # AND the _HEDGE_MARKER_RE exception), same boundary this project already draws for
    # nli_unsupported_problem/topical_relevance_problem above. ---
    def _editorializing_scenario():
        from tools.core import tool_quotas_ctx as q_ctx
        from unittest.mock import patch
        import utils.grounding as _grounding_mod

        class _FakeDetector:
            def __init__(self, labels):
                self._labels = labels
            def score(self, docs, claims):
                return list(self._labels), [0.0] * len(claims), None, None

        _orig_ws7 = _config.cfg.get("settings", {}).get("workspace")
        _config.cfg["settings"]["workspace"] = {"type": "memory", "required_artifact": "final_report.md"}
        # editorial_detection_check must be explicitly on (opt-in, default False); nli_verify/
        # topical_relevance_check must be off so execution actually reaches editorializing_problem
        # instead of falling through to a REAL, unmocked model load first, same anti-pattern the
        # nli_verify scenario's own topical_relevance_check:False guard exists to prevent.
        _orig_gc7 = _config.cfg.get("settings", {}).get("grounding_check")
        _config.cfg["settings"]["grounding_check"] = {
            "nli_verify": False, "topical_relevance_check": False,
            "editorial_detection_check": True,
        }
        saved_fs = dict(_IN_MEMORY_FS)
        try:
            _IN_MEMORY_FS.clear()
            reset_fetched_urls()
            _edt_src = "https://gov.example.co/editorial-test-page"
            _edt_source_text = ("Source-URL: " + _edt_src + "\n\n"
                                 + "The National Cyber Strategy was formally adopted in 2020 "
                                   "following extensive review. " * 3)
            record_fetched_url(_edt_src, filename="sources/editorial_page.md")
            _IN_MEMORY_FS["sources/editorial_page.md"] = _edt_source_text
            _IN_MEMORY_FS["findings.md"] = f"- hallado ({_edt_src})"
            claim_line = f"- The National Cyber Strategy launched in 2020, signaling a decisive shift toward offensive cyber posture [gov]({_edt_src})"
            _IN_MEMORY_FS["final_report.md"] = claim_line
            q_ctx.set({"delegate_tasks": {"used": 1, "limit": 5}})

            # Detector labels the claim unsupported (0) -> editorializing verdict, quarantined,
            # distinctive nudge.
            with patch.object(_grounding_mod, "_get_editorial_detector", return_value=_FakeDetector([0])):
                with tempfile.TemporaryDirectory() as tmpdir7:
                    rs = RunState(tmpdir7)
                    run_state_ctx.set(rs)
                    msgs = []
                    should_retry, _ = _asyncio.run(run_completion_check(
                        query="q", current_input="q", run_state=rs, notify=msgs.append))
                    recorded = rs.data["completion_check_attempts"][-1]["problem"]
                    assert recorded == "editorializing", (recorded, msgs)
                    assert should_retry
                    assert "own added interpretation" in msgs[-1] or "does NOT actually say" in msgs[-1], msgs

            # Detector labels the claim supported (1) -> clean pass, confirming the new check
            # doesn't regress the existing clean-pass path once wired in.
            with patch.object(_grounding_mod, "_get_editorial_detector", return_value=_FakeDetector([1])):
                with tempfile.TemporaryDirectory() as tmpdir8:
                    rs = RunState(tmpdir8)
                    run_state_ctx.set(rs)
                    msgs = []
                    should_retry, _ = _asyncio.run(run_completion_check(
                        query="q", current_input="q", run_state=rs, notify=msgs.append))
                    recorded = rs.data["completion_check_attempts"][-1]["problem"]
                    assert recorded is None, (recorded, msgs)
                    assert not should_retry

            # Detector unavailable (fails open) -> clean pass, never crashes the run.
            with patch.object(_grounding_mod, "_get_editorial_detector", return_value=None):
                with tempfile.TemporaryDirectory() as tmpdir9:
                    rs = RunState(tmpdir9)
                    run_state_ctx.set(rs)
                    msgs = []
                    should_retry, _ = _asyncio.run(run_completion_check(
                        query="q", current_input="q", run_state=rs, notify=msgs.append))
                    recorded = rs.data["completion_check_attempts"][-1]["problem"]
                    assert recorded is None, (recorded, msgs)
                    assert not should_retry

            # Hedge exception (2026-08-30 fix): detector labels the claim unsupported (0), but the
            # claim itself carries an HONEST hedge marker ("no subsequent source confirms") --
            # must NOT be flagged. This is the false-positive pattern found live during real
            # MiniCheck calibration, not a hypothetical case.
            _hedge_claim_line = f"- The National Cyber Strategy launched in 2020; no subsequent source confirms this. [gov]({_edt_src})"
            _IN_MEMORY_FS["final_report.md"] = _hedge_claim_line
            with patch.object(_grounding_mod, "_get_editorial_detector", return_value=_FakeDetector([0])):
                with tempfile.TemporaryDirectory() as tmpdir10:
                    rs = RunState(tmpdir10)
                    run_state_ctx.set(rs)
                    msgs = []
                    should_retry, _ = _asyncio.run(run_completion_check(
                        query="q", current_input="q", run_state=rs, notify=msgs.append))
                    recorded = rs.data["completion_check_attempts"][-1]["problem"]
                    assert recorded is None, (recorded, msgs)
                    assert not should_retry
        finally:
            _IN_MEMORY_FS.clear()
            _IN_MEMORY_FS.update(saved_fs)
            reset_fetched_urls()
            if _orig_ws7 is None:
                _config.cfg["settings"].pop("workspace", None)
            else:
                _config.cfg["settings"]["workspace"] = _orig_ws7
            if _orig_gc7 is None:
                _config.cfg["settings"].pop("grounding_check", None)
            else:
                _config.cfg["settings"]["grounding_check"] = _orig_gc7

    contextvars.copy_context().run(_editorializing_scenario)

    # --- extract_salient_terms: bare-integer-percent branch was silently unmatchable
    # (2026-08-25, found while building an unrelated test fixture). `\b\d+%\b` requires a
    # word/non-word transition on BOTH sides of the match, but "%" is itself non-word, so "12% "
    # (percent followed by whitespace/punctuation -- the overwhelmingly common case in real
    # prose) has non-word characters on both sides of that final boundary position and it can
    # never form. A decimal percentage like "12.5%" was unaffected (matched by the OTHER
    # alternative, which needs no trailing boundary after its own digits). Fixed by dropping the
    # trailing \b -- the literal "%" is itself non-word, so `\d+%` can never accidentally swallow
    # a following word character into the same match, no boundary assertion needed. ---
    from utils.grounding import extract_salient_terms
    assert "12%" in extract_salient_terms("improved by 12% overall"), (
        "a bare-integer percentage followed by whitespace must be captured as a salient term")
    assert "8%" in extract_salient_terms("grew 8%, according to the report"), (
        "a bare-integer percentage followed by punctuation must be captured as a salient term")
    assert extract_salient_terms("reached 80.5 % on GLUE") == {"80.5"}, (
        "a decimal percentage must still match via the decimal-number branch, unaffected by "
        "this fix (no trailing % needed in the captured term for that branch)")

    # --- ROADMAP Phase 4: topical-relevance cross-encoder reranker (the GOA-algorithm vs.
    # Goa-the-Indian-state acronym collision from ROADMAP "Findings from live testing" — term
    # overlap passes (a shared "12.5%" figure) and NLI wouldn't contradict it (an EV-policy sentence
    # doesn't CONTRADICT an algorithm claim, it's just unrelated), so only a topical-relevance
    # judgment catches it. Real BAAI/bge-reranker-v2-m3 isn't loaded in this fast suite -- mocked
    # at utils.grounding._get_topical_relevance_model to test WIRING correctness, same boundary as
    # _nli_verify_scenario above. Fixture uses a shared FIGURE, not a shared bare year, since a
    # 2026-08-25 fix made _grounded_claim_pairs skip any claim anchored only by a bare year (too
    # weak/ubiquitous an anchor -- see that fix's own comment); "2024" alone no longer reaches
    # this check at all. ---
    def _topical_relevance_scenario():
        from tools.core import tool_quotas_ctx as q_ctx
        from unittest.mock import patch
        import utils.grounding as _grounding_mod

        class _FakeRerankerModel:
            def __init__(self, score):
                self._score = score
            def predict(self, pairs):
                return [self._score for _ in pairs]

        _orig_ws7 = _config.cfg.get("settings", {}).get("workspace")
        _config.cfg["settings"]["workspace"] = {"type": "memory", "required_artifact": "final_report.md"}
        # nli_verify off (this scenario isn't testing NLI wiring, and leaving it on would call the
        # real NLI model unmocked -- same anti-pattern the matrix's own guard exists to prevent).
        _orig_gc7 = _config.cfg.get("settings", {}).get("grounding_check")
        _config.cfg["settings"]["grounding_check"] = {"nli_verify": False}
        saved_fs = dict(_IN_MEMORY_FS)
        try:
            _IN_MEMORY_FS.clear()
            reset_fetched_urls()
            _goa_src = "https://goa.example.co/ev-policy"
            _goa_source_text = ("Source-URL: " + _goa_src + "\n\n"
                                 "Goa announced new electric vehicle incentives for residents, "
                                 "cutting registration costs by 12.5% under the state's broader "
                                 "transport policy. " * 3)
            record_fetched_url(_goa_src, filename="sources/goa_page.md")
            _IN_MEMORY_FS["sources/goa_page.md"] = _goa_source_text
            _IN_MEMORY_FS["findings.md"] = f"- hallado ({_goa_src})"
            # Shares the checkable term '12.5' with the source, so claim_grounding_problem's
            # term-overlap passes outright -- exactly the failure shape this check exists for.
            claim_line = f"- The GOA algorithm improved convergence results by 12.5% [source]({_goa_src})"
            _IN_MEMORY_FS["final_report.md"] = claim_line
            q_ctx.set({"delegate_tasks": {"used": 1, "limit": 5}})

            # Low relevance score mocked -> topical_mismatch verdict, quarantined, distinctive nudge.
            with patch.object(_grounding_mod, "_get_topical_relevance_model", return_value=_FakeRerankerModel(0.01)):
                with tempfile.TemporaryDirectory() as tmpdir7a:
                    rs = RunState(tmpdir7a)
                    run_state_ctx.set(rs)
                    msgs = []
                    should_retry, _ = _asyncio.run(run_completion_check(
                        query="q", current_input="q", run_state=rs, notify=msgs.append))
                    recorded = rs.data["completion_check_attempts"][-1]["problem"]
                    assert recorded == "topical_mismatch", (recorded, msgs)
                    assert should_retry
                    assert "different subject" in msgs[-1] or "DIFFERENT SUBJECT" in msgs[-1], msgs

            # High relevance score mocked -> clean pass, confirming the new check doesn't regress
            # the existing clean-pass path once wired in.
            with patch.object(_grounding_mod, "_get_topical_relevance_model", return_value=_FakeRerankerModel(0.95)):
                with tempfile.TemporaryDirectory() as tmpdir7b:
                    rs = RunState(tmpdir7b)
                    run_state_ctx.set(rs)
                    msgs = []
                    should_retry, _ = _asyncio.run(run_completion_check(
                        query="q", current_input="q", run_state=rs, notify=msgs.append))
                    recorded = rs.data["completion_check_attempts"][-1]["problem"]
                    assert recorded is None, (recorded, msgs)
                    assert not should_retry
        finally:
            _IN_MEMORY_FS.clear()
            _IN_MEMORY_FS.update(saved_fs)
            reset_fetched_urls()
            if _orig_ws7 is None:
                _config.cfg["settings"].pop("workspace", None)
            else:
                _config.cfg["settings"]["workspace"] = _orig_ws7
            if _orig_gc7 is None:
                _config.cfg["settings"].pop("grounding_check", None)
            else:
                _config.cfg["settings"]["grounding_check"] = _orig_gc7

    contextvars.copy_context().run(_topical_relevance_scenario)



if __name__ == "__main__":
    main()
    print("test_grounding_checks_core OK")
