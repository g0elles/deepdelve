import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from engine.completion import (
    _is_citable_finding, _build_findings_source_material,
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
import asyncio as _asyncio
import config as _config
import contextvars
import tempfile

from engine.completion import (
    Ctx, run_completion_check, _facet_coverage, _findings_facet_coverage, check_report_underuses_findings,
    check_report_underuses_evidence,
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
    # --- check_report_underuses_findings (2026-07-22): Builder's own version of check_thin_
    # coverage, one stage downstream. Live-confirmed the SAME evidence-abandonment pattern this
    # project fixed for FindingsWriter earlier the same day recurs at Builder: a genuinely diverse
    # 15-entry findings.md (heuristic-algorithm papers AND Colombian cultural sources) produced a
    # final_report.md that only ever cited the Colombian cluster -- the heuristic-algorithms half,
    # present and citable in findings.md, never appeared in the report, and no existing grounding
    # check catches it (they all verify citations the report DOES make, never whether it used
    # enough of what was actually available). ---
    def _report_underuses_findings_scenario():
        from tools.core import tool_quotas_ctx as q_ctx

        _orig_ws11 = _config.cfg.get("settings", {}).get("workspace")
        _config.cfg["settings"]["workspace"] = {"type": "memory", "required_artifact": "final_report.md"}
        _orig_gc11 = _config.cfg.get("settings", {}).get("grounding_check")
        _config.cfg["settings"]["grounding_check"] = {"nli_verify": False, "topical_relevance_check": False}
        saved_fs = dict(_IN_MEMORY_FS)
        try:
            _IN_MEMORY_FS.clear()
            reset_fetched_urls()
            q_ctx.set({"delegate_tasks": {"used": 1, "limit": 5}})
            urls = [f"https://source{i}.example.co/page" for i in range(1, 6)]
            for i, u in enumerate(urls, 1):
                record_fetched_url(u, filename=f"sources/s{i}.md")
                _IN_MEMORY_FS[f"sources/s{i}.md"] = f"Source-URL: {u}\n\ndato numero {i} sobre el tema."
            findings_md = "\n\n".join(f"### [Fuente {i}]({u})\n- dato numero {i} sobre el tema." for i, u in enumerate(urls, 1))

            # (a) findings.md has 5 real distinct sources, report cites only 2 (ratio 0.4 < 0.5
            # threshold, 5 >= min_sources 3) -> fires, names the 3 neglected ones.
            with tempfile.TemporaryDirectory() as tmpdir_a:
                rs = RunState(tmpdir_a)
                run_state_ctx.set(rs)
                _IN_MEMORY_FS["findings.md"] = findings_md
                _IN_MEMORY_FS["final_report.md"] = (
                    f"- dato numero 1 sobre el tema. [Fuente 1]({urls[0]})\n"
                    f"- dato numero 2 sobre el tema. [Fuente 2]({urls[1]})"
                )
                msgs = []
                should_retry, _ = _asyncio.run(run_completion_check(
                    query="q", current_input="q", run_state=rs, notify=msgs.append))
                recorded = rs.data["completion_check_attempts"][-1]["problem"]
                assert recorded == "report_underuses_findings", (recorded, msgs)
                assert should_retry
                assert urls[2] in msgs[-1] and urls[3] in msgs[-1] and urls[4] in msgs[-1], msgs

            # (b) below min_sources (only 2 real findings.md sources) -> never fires, even at a
            # worse ratio (1 of 2 cited).
            with tempfile.TemporaryDirectory() as tmpdir_b:
                rs = RunState(tmpdir_b)
                run_state_ctx.set(rs)
                _IN_MEMORY_FS["findings.md"] = "\n\n".join(
                    f"### [Fuente {i}]({u})\n- dato numero {i} sobre el tema." for i, u in enumerate(urls[:2], 1))
                _IN_MEMORY_FS["final_report.md"] = f"- dato numero 1 sobre el tema. [Fuente 1]({urls[0]})"
                msgs = []
                should_retry, _ = _asyncio.run(run_completion_check(
                    query="q", current_input="q", run_state=rs, notify=msgs.append))
                recorded = rs.data["completion_check_attempts"][-1]["problem"]
                assert recorded != "report_underuses_findings", (
                    "too few real sources in findings.md for the ratio to mean anything", recorded, msgs)

            # (c) ratio exactly AT threshold (2 of 4 cited, 0.5) -> MUST fire (2026-08-20 live
            # incident: a ministral-3:8b run dropped an entire city -- 2 of 4 real findings.md
            # sources -- from a two-city comparison query, landing exactly on ratio == 0.5. The
            # old `>=` treated the boundary as a pass and let it through with no verdict at all;
            # `>` now fails closed on the boundary instead of silently accepting it.
            with tempfile.TemporaryDirectory() as tmpdir_c:
                rs = RunState(tmpdir_c)
                run_state_ctx.set(rs)
                _IN_MEMORY_FS["findings.md"] = "\n\n".join(
                    f"### [Fuente {i}]({u})\n- dato numero {i} sobre el tema." for i, u in enumerate(urls[:4], 1))
                _IN_MEMORY_FS["final_report.md"] = (
                    f"- dato numero 1 sobre el tema. [Fuente 1]({urls[0]})\n"
                    f"- dato numero 2 sobre el tema. [Fuente 2]({urls[1]})"
                )
                msgs = []
                should_retry, _ = _asyncio.run(run_completion_check(
                    query="q", current_input="q", run_state=rs, notify=msgs.append))
                recorded = rs.data["completion_check_attempts"][-1]["problem"]
                assert recorded == "report_underuses_findings", (
                    "ratio exactly AT threshold (0.5) must now fail closed, not pass", recorded, msgs)
                assert should_retry
                assert urls[2] in msgs[-1] and urls[3] in msgs[-1], msgs

            # (d) every real source cited -> clean, never fires.
            with tempfile.TemporaryDirectory() as tmpdir_d:
                rs = RunState(tmpdir_d)
                run_state_ctx.set(rs)
                _IN_MEMORY_FS["findings.md"] = findings_md
                _IN_MEMORY_FS["final_report.md"] = "\n".join(
                    f"- dato numero {i} sobre el tema. [Fuente {i}]({u})" for i, u in enumerate(urls, 1))
                msgs = []
                should_retry, _ = _asyncio.run(run_completion_check(
                    query="q", current_input="q", run_state=rs, notify=msgs.append))
                recorded = rs.data["completion_check_attempts"][-1]["problem"]
                assert recorded != "report_underuses_findings", (recorded, msgs)
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

    contextvars.copy_context().run(_report_underuses_findings_scenario)

    # --- null-summary exclusion (2026-08-01, RESEARCH.md Sec.16): a live 1800s-timeout run never
    # converged because findings.md listed URLs whose Analyzer dispatch came back empty-handed
    # ("No key findings extracted...") as real, must-be-cited evidence -- an unsatisfiable demand
    # (nothing to write about) that could only be "resolved" by re-failing the same check or
    # fabricating a claim. Both check_report_underuses_findings and _facet_coverage must exclude a
    # URL whose run_state.data["findings"] entries are ALL null-summary from counting as real
    # surviving evidence. ---
    def _null_summary_exclusion_scenario():
        from engine.completion import Ctx

        _orig_ws_null = _config.cfg.get("settings", {}).get("workspace")
        _config.cfg["settings"]["workspace"] = {"type": "memory", "required_artifact": "final_report.md"}
        saved_fs = dict(_IN_MEMORY_FS)
        try:
            _IN_MEMORY_FS.clear()
            urls = [f"https://nullsrc{i}.example.co/page" for i in range(1, 5)]
            # 3 of 4 sources have NO real content (findings.md still lists them under a heading,
            # same live shape as findings_by_writer's own faithful paraphrase); only url[0] has a
            # real finding. Report cites only url[0].
            findings_md = "\n\n".join(
                f"### [Fuente {i}]({u})\n- " + (
                    "dato numero 1 real sobre el tema, con una cifra concreta." if i == 1
                    else "No key findings extracted from this source during this research run."
                )
                for i, u in enumerate(urls, 1)
            )
            _IN_MEMORY_FS["findings.md"] = findings_md
            report_content = f"- dato numero 1 real sobre el tema. [Fuente 1]({urls[0]})"
            _IN_MEMORY_FS["final_report.md"] = report_content

            with tempfile.TemporaryDirectory() as tmpdir_null:
                rs = RunState(tmpdir_null)
                run_state_ctx.set(rs)
                for i, u in enumerate(urls, 1):
                    rs.add_finding(
                        u,
                        "dato numero 1 real sobre el tema, con una cifra concreta." if i == 1
                        else "No key findings extracted from this source during this research run.",
                        task_name=f"task_{i}", depth=1,
                    )
                ctx = Ctx(req_artifact="final_report.md", attempt=0, max_attempts=10, delegated=True,
                           files=["findings.md", "final_report.md"], content=report_content,
                           quotas=None, run_state=rs)
                # (a) check_report_underuses_findings: only 1 real source (the other 3 are
                # null-summary) -> below min_sources (3), must NOT fire even though the raw
                # findings.md text has 4 headed sections.
                v = check_report_underuses_findings(ctx)
                assert v is None, (
                    "3 of 4 findings.md sources are null-summary placeholders -- only 1 real "
                    "source remains, below min_sources, must not demand the other 3 be cited", v)

                # (b) _facet_coverage: task_2/3/4's only finding is null-summary -> they must NOT
                # appear in by_task at all (no real surviving evidence to be "dropped"), while
                # task_1 (real finding, cited in the report) is covered and also not dropped.
                by_task, dropped = _facet_coverage(ctx)
                assert "task_1" in by_task and urls[0].rstrip('/') in by_task["task_1"], by_task
                for i in (2, 3, 4):
                    assert f"task_{i}" not in by_task, (
                        f"task_{i}'s only finding is a null-summary placeholder -- it must not "
                        f"count as real surviving evidence", by_task)
                assert dropped == [], (
                    "no task with REAL evidence was dropped from the report", dropped)
        finally:
            _IN_MEMORY_FS.clear()
            _IN_MEMORY_FS.update(saved_fs)
            if _orig_ws_null is None:
                _config.cfg["settings"].pop("workspace", None)
            else:
                _config.cfg["settings"]["workspace"] = _orig_ws_null

    contextvars.copy_context().run(_null_summary_exclusion_scenario)

    # --- fabricated-finding exclusion (2026-08-16 live incident, sibling to the null-summary
    # exclusion above): a task whose ONLY finding carries a [SYSTEM VERIFICATION WARNING...]
    # marker (fabricated/off-topic content, NOT a null/empty extraction) must ALSO be excluded from
    # _facet_coverage/_findings_facet_coverage's notion of "real surviving evidence" -- the same
    # _is_citable_finding definition check_task_verification_flagged's own ledger already uses.
    # Confirmed live: without this, _facet_coverage told Builder a fabricated/quota-exhausted task
    # had "real surviving sources" to cite, directly contradicting check_task_verification_
    # flagged's own acknowledge-the-gap directive for the SAME task one attempt earlier. ---
    def _fabricated_finding_exclusion_scenario():
        from engine.completion import Ctx

        _orig_ws_fab = _config.cfg.get("settings", {}).get("workspace")
        _config.cfg["settings"]["workspace"] = {"type": "memory", "required_artifact": "final_report.md"}
        saved_fs = dict(_IN_MEMORY_FS)
        try:
            _IN_MEMORY_FS.clear()
            real_url = "https://real.example.co/visa-guide"
            fabricated_url = "https://fabricated.example.co/visa-guide"
            fabricated_summary = (
                "Minimum monthly income of €5,000 is required.\n\n"
                "[SYSTEM VERIFICATION WARNING: this summary cites a claim that does not match "
                "anything actually fetched this run (claim_unsupported).]"
            )
            findings_md = (
                f"### [Real Source]({real_url})\n- real finding, no warning marker.\n\n"
                f"### [Fabricated Source]({fabricated_url})\n{fabricated_summary}"
            )
            _IN_MEMORY_FS["findings.md"] = findings_md
            report_content = f"- real finding. [Real Source]({real_url})"
            _IN_MEMORY_FS["final_report.md"] = report_content

            with tempfile.TemporaryDirectory() as tmpdir_fab:
                rs = RunState(tmpdir_fab)
                run_state_ctx.set(rs)
                rs.add_finding(real_url, "real finding, no warning marker.",
                                task_name="task_real", depth=1)
                rs.add_finding(fabricated_url, fabricated_summary,
                                task_name="task_fabricated", depth=1)
                ctx = Ctx(req_artifact="final_report.md", attempt=0, max_attempts=10, delegated=True,
                           files=["findings.md", "final_report.md"], content=report_content,
                           quotas=None, run_state=rs)

                by_task, dropped = _facet_coverage(ctx)
                assert "task_real" in by_task and real_url.rstrip('/') in by_task["task_real"], by_task
                assert "task_fabricated" not in by_task, (
                    "a fabricated (SYSTEM WARNING-marked) finding must not count as real "
                    "surviving evidence Builder can be told to go cite", by_task)
                assert dropped == [], "no task with REAL evidence was dropped from the report"

                findings_by_task, findings_dropped = _findings_facet_coverage(ctx)
                assert "task_real" in findings_by_task, findings_by_task
                assert "task_fabricated" not in findings_by_task, (
                    "the same fabricated finding must not count as real evidence FindingsWriter "
                    "can be told it 'dropped' from findings.md either", findings_by_task)
        finally:
            _IN_MEMORY_FS.clear()
            _IN_MEMORY_FS.update(saved_fs)
            if _orig_ws_fab is None:
                _config.cfg["settings"].pop("workspace", None)
            else:
                _config.cfg["settings"]["workspace"] = _orig_ws_fab

    contextvars.copy_context().run(_fabricated_finding_exclusion_scenario)

    # --- null-finding evidence-blob crowding (2026-08-17, session_status 2026-08-16 item 3): a
    # busy task that fetches several sources but only extracts real content from one of them used
    # to have EVERY source -- including the "No key findings extracted" placeholders -- rendered
    # as an ordinary "### Source: ..." entry in _build_findings_source_material's evidence blob,
    # since a null-finding summary carries a real http URL and no cutoff/warning marker. Confirmed
    # live, repeatedly: FindingsWriter then wrote the placeholder text itself into findings.md
    # instead of just the real entry. Placeholders must be excluded from the evidence blob and
    # routed into the "nothing citable" note instead, same treatment as a fabricated/off-topic
    # finding above. ---
    def _null_finding_evidence_blob_scenario():
        reset_fetched_urls()
        with tempfile.TemporaryDirectory() as tmpdir_null_blob:
            rs = RunState(tmpdir_null_blob)
            run_state_ctx.set(rs)
            record_fetched_url("https://busy.example.co/real", filename="sources/real.md")
            record_fetched_url("https://busy.example.co/thin1", filename="sources/thin1.md")
            record_fetched_url("https://busy.example.co/thin2", filename="sources/thin2.md")
            rs.add_finding("https://busy.example.co/real",
                            "Rent in the target neighborhood averages €900/month per 2026 listings.",
                            task_name="busy_task", depth=1)
            rs.add_finding("https://busy.example.co/thin1",
                            "No key findings extracted from this source during this research run.",
                            task_name="busy_task", depth=1)
            rs.add_finding("https://busy.example.co/thin2",
                            "I was unable to find any relevant information on this page.",
                            task_name="busy_task", depth=1)
            material = _build_findings_source_material(rs)
            assert "€900/month" in material, material
            assert "No key findings extracted" not in material, material
            assert "unable to find any relevant information" not in material, material
        reset_fetched_urls()

    contextvars.copy_context().run(_null_finding_evidence_blob_scenario)

    # --- verification-warning URL scoping (2026-08-17 live incident, session_status item 3's own
    # long-open root cause): add_finding attaches the SAME shared synthesis text to every URL
    # fetched in one turn (_collapse_multi_url_task_findings's own docstring) -- a stub/unverified
    # flag about ONE of those co-fetched URLs must not wholesale-exclude the record for the OTHERS.
    # Confirmed live: a Mexico City rent synthesis covering a stub Blueground page AND a real
    # Rentberry price (MX$17,300/month) in one text got 'stub_source:...blueground...', and the old
    # wholesale check threw the real Rentberry price away too, along with mexicoinsider.mx (never
    # even named in the warning). ---
    def _is_citable_finding_url_scoped_scenario():

        stub_url = "https://stub.example.co/polanco-listing"
        real_url = "https://real.example.co/condesa-listing"
        untouched_url = "https://untouched.example.co/insider"
        shared_summary = (
            "Consolidated findings: Stub page price data was not captured (quota limits); "
            "Real page shows the lowest observed price is $17,300/month for a 1-bedroom.\n\n"
            f"[SYSTEM VERIFICATION WARNING: this summary attributes a claim to a source that does "
            f"not match anything actually fetched this run, or to something that isn't a real URL "
            f"at all (stub_source:{stub_url}). Do not treat the associated claim as sourced when "
            f"writing findings.md.]"
        )
        stub_f = {"source_url": stub_url, "summary": shared_summary}
        real_f = {"source_url": real_url, "summary": shared_summary}
        untouched_f = {"source_url": untouched_url, "summary": shared_summary}
        assert _is_citable_finding(stub_f) is False, "the URL the warning actually names must stay excluded"
        assert _is_citable_finding(real_f) is True, (
            "a co-cited URL the warning never named must NOT be swept up by another URL's "
            "stub/unverified flag")
        assert _is_citable_finding(untouched_f) is True, (
            "a third co-cited URL never mentioned anywhere in the warning must also stay citable")

        # unverified_urls: (multiple bad URLs) -- same scoping, more than one flagged URL.
        multi_bad_summary = (
            "Real content here.\n\n"
            "[SYSTEM VERIFICATION WARNING: this summary attributes a claim to a source that does "
            "not match anything actually fetched this run, or to something that isn't a real URL "
            "at all (unverified_urls:https://bad1.example.co/x, https://bad2.example.co/y). Do not "
            "treat the associated claim as sourced when writing findings.md.]"
        )
        assert _is_citable_finding({"source_url": "https://bad1.example.co/x", "summary": multi_bad_summary}) is False
        assert _is_citable_finding({"source_url": "https://bad2.example.co/y", "summary": multi_bad_summary}) is False
        assert _is_citable_finding({"source_url": "https://real.example.co/z", "summary": multi_bad_summary}) is True

        # A warning shape with no extractable URL (quote-identified, not URL-identified) falls back
        # to wholesale exclusion -- nothing to scope the exclusion to.
        no_url_summary = (
            "Some content.\n\n"
            "[SYSTEM VERIFICATION WARNING: this summary attributes a claim to a source that does "
            "not match anything actually fetched this run, or to something that isn't a real URL "
            "at all (quote_paraphrased:the exact wording was altered). Do not treat the associated "
            "claim as sourced when writing findings.md.]"
        )
        assert _is_citable_finding({"source_url": real_url, "summary": no_url_summary}) is False, (
            "a warning that identifies its problem by quoted text, not a URL, has nothing to scope "
            "to and must fall back to the prior wholesale exclusion")

        # The Analyzer-tier reconstructed-URL message also names the CORRECT reference URL inside
        # its own parenthetical -- that URL must never be swept up as if it were also flagged bad.
        reconstructed_summary = (
            "Some content.\n\n"
            "[SYSTEM VERIFICATION WARNING: this summary cites 'https://guessed.example.co/z', "
            "which does not match the source URL you were actually given to analyze "
            "(https://correct.example.co/real) — this looks like a reconstructed or guessed URL, "
            "not the real one. Do not treat the associated claim as sourced when writing "
            "findings.md.]"
        )
        assert _is_citable_finding({"source_url": "https://correct.example.co/real", "summary": reconstructed_summary}) is False, (
            "unlabeled parentheticals (no unverified_urls:/stub_source:/claim_unsupported: prefix) "
            "aren't scoped at all -- falls back to wholesale exclusion, same as the no-URL case, "
            "rather than risk sweeping the correct reference URL into the wrong bucket")

    _is_citable_finding_url_scoped_scenario()

    # --- [SYSTEM ATTRIBUTION WARNING] exclusion (2026-08-27): third case of the same bracketed-
    # marker convention -- always wholesale-excludes, since the marker is only ever attached to
    # the one URL it's actually about (unlike VERIFICATION warnings, which can name a DIFFERENT
    # co-cited URL and so need scoping). ---
    def _is_citable_finding_attribution_warning_scenario():

        flagged_summary = (
            "Consolidated findings about hospital habilitation requirements.\n\n"
            "[SYSTEM ATTRIBUTION WARNING: this source was fetched alongside others in one "
            "dispatch turn, but the synthesis above does not clearly reflect this specific "
            "source's own content -- do not treat this URL as the source for the claims above; "
            "see sources/itsitio_com_example.md for what this source actually says.]"
        )
        assert _is_citable_finding({"source_url": "https://itsitio.example.co/exports", "summary": flagged_summary}) is False
        clean_summary = "Consolidated findings about hospital habilitation requirements."
        assert _is_citable_finding({"source_url": "https://awnewscenter.example.co/habilitation", "summary": clean_summary}) is True

    _is_citable_finding_attribution_warning_scenario()

    # --- warning-marker stripped from a still-citable finding's rendered block (2026-08-17 live
    # incident, run6): _is_citable_finding's URL-scoping fix (same date) means a finding can now
    # correctly stay citable while its own summary still carries a [SYSTEM VERIFICATION WARNING...]
    # marker about a DIFFERENT co-cited URL that turn. Confirmed live: a real, correctly-citable
    # globallawexperts.com finding rendered that marker text VERBATIM into findings.md (via the
    # deterministic fallback), and the warning's own mentioned bad URL was then picked up by
    # findings.md-level grounding checks as if it were a real citation IN findings.md --
    # `findings_ungrounded` fired on content that was otherwise entirely real, reproducing
    # identically on every retry since the underlying research data never changed. ---
    def _warning_marker_stripped_from_citable_block_scenario():
        from engine.completion import _build_findings_source_material

        with tempfile.TemporaryDirectory() as tmpdir_marker_strip:
            rs = RunState(tmpdir_marker_strip)
            real_url = "https://globallawexperts.com/portugal-d8-visa-requirements-real"
            bad_url = "https://globallawexperts.com/family-reunification-fabricated"
            rs.data["fetched_urls"] = [{"url": real_url, "filename": "sources/real.md"}]
            rs.add_finding(
                real_url,
                "Two-step application process: first a residency visa abroad, then convert it "
                "in-person after arrival.\n\n"
                f"[SYSTEM VERIFICATION WARNING: this summary attributes a claim to a source that "
                f"does not match anything actually fetched this run, or to something that isn't a "
                f"real URL at all (unverified_urls:{bad_url}). Do not treat the associated claim "
                f"as sourced when writing findings.md.]",
                task_name="visa_requirements", depth=1,
            )
            material = _build_findings_source_material(rs)
            assert "SYSTEM VERIFICATION WARNING" not in material, (
                "the machine-readable marker must never be copied verbatim into the "
                "human-facing evidence blob / findings.md", material)
            assert bad_url not in material, (
                "the bad URL the warning names must not leak into findings.md either -- it would "
                "be picked up by findings.md-level grounding checks as if it were a real citation",
                material)
            assert "Two-step application process" in material, (
                "the real, genuinely-citable content must survive the strip", material)

    contextvars.copy_context().run(_warning_marker_stripped_from_citable_block_scenario)

    # --- 2026-07-31 (research finding, not a live incident): both check_report_underuses_findings
    # and check_report_underuses_evidence used to say "actually add sections" without ever naming
    # edit_workspace_file, the tool BUILDER_INSTRUCTIONS itself reserves for exactly this narrow-
    # scope operation -- see completion.py's own comment at the fix site for the full reasoning
    # (self-correction blind spot literature, arXiv:2406.01297). Pin: the directive text (.inject,
    # the model-facing field -- NOT .warning, which the other scenario's msgs assertions check)
    # must name edit_workspace_file on BOTH the first-occurrence and escalated branches for both
    # checks, so a future wording edit can't silently drop the tool-specific instruction again. ---
    def _underuses_edit_tool_directive_scenario():
        from engine.completion import check_report_underuses_evidence, Ctx

        _orig_ws17 = _config.cfg.get("settings", {}).get("workspace")
        _config.cfg["settings"]["workspace"] = {"type": "memory"}
        saved_fs = dict(_IN_MEMORY_FS)
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                # --- check_report_underuses_findings ---
                urls = [f"https://source{i}.example.co/page" for i in range(1, 6)]
                findings_md = "\n\n".join(f"### [Fuente {i}]({u})\n- dato numero {i}." for i, u in enumerate(urls, 1))
                _IN_MEMORY_FS.clear()
                _IN_MEMORY_FS["findings.md"] = findings_md
                thin_report = f"- dato numero 1. [Fuente 1]({urls[0]})\n- dato numero 2. [Fuente 2]({urls[1]})"

                rs = RunState(tmpdir)
                ctx_first = Ctx(req_artifact="final_report.md", attempt=0, max_attempts=8,
                                 delegated=True, files=["findings.md"], content=thin_report,
                                 quotas=None, run_state=rs)
                v = check_report_underuses_findings(ctx_first)
                assert v is not None and "edit_workspace_file" in v.inject, (
                    "first-occurrence directive must name edit_workspace_file", v)

                rs2 = RunState(tmpdir)
                rs2.data["completion_check_attempts"] = [{"problem": "report_underuses_findings"}]
                ctx_escalated = Ctx(req_artifact="final_report.md", attempt=1, max_attempts=8,
                                     delegated=True, files=["findings.md"], content=thin_report,
                                     quotas=None, run_state=rs2)
                v2 = check_report_underuses_findings(ctx_escalated)
                assert v2 is not None and "edit_workspace_file" in v2.inject, (
                    "escalated directive must name edit_workspace_file", v2)

                # --- check_report_underuses_evidence ---
                heur_urls = ["https://a.example.co/heur1", "https://a.example.co/heur2"]
                colo_urls = ["https://b.example.co/colo1", "https://b.example.co/colo2", "https://b.example.co/colo3"]
                _IN_MEMORY_FS["findings.md"] = "\n\n".join(
                    f"### [Src]({u})\n- real finding." for u in heur_urls + colo_urls)
                colombia_only_report = "\n".join(f"- dato. [Src]({u})" for u in colo_urls)

                rs3 = RunState(tmpdir)
                for u in heur_urls:
                    rs3.add_finding(u, "heuristic finding", task_name="heuristics", depth=1)
                for u in colo_urls:
                    rs3.add_finding(u, "colombia finding", task_name="colombia", depth=1)
                ctx3 = Ctx(req_artifact="final_report.md", attempt=0, max_attempts=8, delegated=True,
                           files=["findings.md", "final_report.md"], content=colombia_only_report,
                           quotas=None, run_state=rs3)
                v3 = check_report_underuses_evidence(ctx3)
                assert v3 is not None and "edit_workspace_file" in v3.inject, (
                    "first-occurrence evidence directive must name edit_workspace_file", v3)

                rs4 = RunState(tmpdir)
                for u in heur_urls:
                    rs4.add_finding(u, "heuristic finding", task_name="heuristics", depth=1)
                for u in colo_urls:
                    rs4.add_finding(u, "colombia finding", task_name="colombia", depth=1)
                rs4.data["completion_check_attempts"] = [{"problem": "report_underuses_evidence"}]
                ctx4 = Ctx(req_artifact="final_report.md", attempt=1, max_attempts=8, delegated=True,
                           files=["findings.md", "final_report.md"], content=colombia_only_report,
                           quotas=None, run_state=rs4)
                v4 = check_report_underuses_evidence(ctx4)
                assert v4 is not None and "edit_workspace_file" in v4.inject, (
                    "escalated evidence directive must name edit_workspace_file", v4)
        finally:
            _IN_MEMORY_FS.clear()
            _IN_MEMORY_FS.update(saved_fs)
            if _orig_ws17 is None:
                _config.cfg["settings"].pop("workspace", None)
            else:
                _config.cfg["settings"]["workspace"] = _orig_ws17

    contextvars.copy_context().run(_underuses_edit_tool_directive_scenario)

    # --- check_report_underuses_evidence (2026-07-29): check_findings_underuses_evidence's own
    # sibling one stage downstream -- findings.md can correctly cover every task (that check stays
    # silent) while final_report.md still cites only ONE task's sources by raw ratio, clearing
    # check_report_underuses_findings' flat threshold because the surviving task happened to have
    # more sources. Live case: RESEARCH.md Sec.14h, gpt-oss:20b dropped the heuristic-algorithms
    # task for an off-topic citation while still clearing the 50% ratio on Colombia's larger count.
    # Direct calls against the check function itself, same style as its sibling's own test. ---
    def _report_underuses_evidence_scenario():
        from utils.run_state import RunState
        from tools.fs import _IN_MEMORY_FS

        _orig_ws12 = _config.cfg.get("settings", {}).get("workspace")
        _config.cfg["settings"]["workspace"] = {"type": "memory"}
        saved_fs = dict(_IN_MEMORY_FS)
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                rs = RunState(tmpdir)
                # heuristics: 2 real URLs, both survive into findings.md. colombia: 3 real URLs,
                # both survive -- uneven counts, same shape as the live incident (bigger cluster
                # wins on raw ratio).
                heur_urls = ["https://a.example.co/heur1", "https://a.example.co/heur2"]
                colo_urls = ["https://b.example.co/colo1", "https://b.example.co/colo2", "https://b.example.co/colo3"]
                for u in heur_urls:
                    rs.add_finding(u, "heuristic finding", task_name="heuristics", depth=1)
                for u in colo_urls:
                    rs.add_finding(u, "colombia finding", task_name="colombia", depth=1)
                # Nested (depth>1) must be ignored, same convention as the sibling check.
                rs.add_finding("https://a.example.co/nested", "s", task_name="heuristics", depth=2)

                findings_md = "\n\n".join(
                    f"### [Src]({u})\n- real finding." for u in heur_urls + colo_urls)
                _IN_MEMORY_FS.clear()
                _IN_MEMORY_FS["findings.md"] = findings_md

                def _ctx(report_text):
                    return Ctx(req_artifact="final_report.md", attempt=0, max_attempts=8,
                               delegated=True, files=["findings.md", "final_report.md"],
                               content=report_text, quotas=None, run_state=rs)

                # (a) report cites ALL 3 colombia URLs (raw ratio 3/5 = 60%, clears
                # check_report_underuses_findings' 50% threshold) but ZERO heuristics URLs ->
                # this check fires even though the ratio check wouldn't.
                report_colombia_only = "\n".join(f"- dato. [Src]({u})" for u in colo_urls)
                verdict = check_report_underuses_evidence(_ctx(report_colombia_only))
                assert verdict is not None and verdict.problem == "report_underuses_evidence", verdict
                assert "heuristics" in verdict.warning and "colombia" not in verdict.warning, verdict.warning

                # (b) report cites at least one URL from EACH task -> no problem, even though
                # citation counts are uneven (not this check's job).
                report_both = "\n".join(f"- dato. [Src]({u})" for u in [heur_urls[0], colo_urls[0]])
                assert check_report_underuses_evidence(_ctx(report_both)) is None

                # (c) only 1 task's URLs actually reached findings.md -> min_tasks gate blocks it.
                rs2 = RunState(tmpdir)
                rs2.add_finding(heur_urls[0], "heuristic finding", task_name="heuristics", depth=1)
                _IN_MEMORY_FS["findings.md"] = f"### [Src]({heur_urls[0]})\n- real finding."
                ctx_one_task = Ctx(req_artifact="final_report.md", attempt=0, max_attempts=8,
                                    delegated=True, files=["findings.md", "final_report.md"],
                                    content="nothing cited", quotas=None, run_state=rs2)
                assert check_report_underuses_evidence(ctx_one_task) is None

                # (d) a task's real URLs exist in run_state but never reached findings.md at all ->
                # not this check's job (check_findings_underuses_evidence's), so it must stay silent
                # even though the report obviously can't cite what findings.md never gave it.
                _IN_MEMORY_FS["findings.md"] = findings_md
                rs3 = RunState(tmpdir)
                for u in heur_urls:
                    rs3.add_finding(u, "heuristic finding", task_name="heuristics", depth=1)
                rs3.add_finding("https://c.example.co/never-in-findings", "dropped upstream",
                                 task_name="dropped_upstream", depth=1)
                ctx_upstream_drop = Ctx(req_artifact="final_report.md", attempt=0, max_attempts=8,
                                         delegated=True, files=["findings.md", "final_report.md"],
                                         content=f"- dato. [Src]({heur_urls[0]})",
                                         quotas=None, run_state=rs3)
                assert check_report_underuses_evidence(ctx_upstream_drop) is None

                # (e) final_report.md missing entirely -> not this check's job.
                ctx_no_report = Ctx(req_artifact="final_report.md", attempt=0, max_attempts=8,
                                     delegated=True, files=["findings.md"], content=None,
                                     quotas=None, run_state=rs)
                assert check_report_underuses_evidence(ctx_no_report) is None
        finally:
            _IN_MEMORY_FS.clear()
            _IN_MEMORY_FS.update(saved_fs)
            if _orig_ws12 is None:
                _config.cfg["settings"].pop("workspace", None)
            else:
                _config.cfg["settings"]["workspace"] = _orig_ws12

    contextvars.copy_context().run(_report_underuses_evidence_scenario)

    # --- run_completion_check's starvation-yield for the report_underuses_findings/_evidence pair
    # must actually reach the starved check (2026-07-31 live incident, gpt-oss re-test after
    # tonight's earlier starvation fixes): the yield lambda tried check_report_underuses_findings
    # FIRST via `or` -- the SAME check already winning, so it short-circuited before
    # check_report_underuses_evidence (the actually-starved, more specific per-task check) ever ran.
    # Confirmed live: a run whose report dropped 4 whole tasks (raw ratio still cleared by one large
    # cluster) fired report_underuses_findings for 4+ consecutive attempts, crossing
    # _STARVATION_SKIP_THRESHOLD (2) multiple times, and never once yielded. Fixture: 2 tasks,
    # "heuristics" (3 URLs, all uncited) and "colombia" (5 URLs, 3 cited) -- report_underuses_
    # findings fires on raw ratio (3/8 = 0.375 < 0.5) AND report_underuses_evidence independently
    # fires (heuristics task has zero citations), same shape as the live incident. ---
    def _starvation_yield_prefers_evidence_scenario():
        from tools.core import tool_quotas_ctx as q_ctx

        _orig_ws16 = _config.cfg.get("settings", {}).get("workspace")
        _config.cfg["settings"]["workspace"] = {"type": "memory", "required_artifact": "final_report.md"}
        _orig_gc16 = _config.cfg.get("settings", {}).get("grounding_check")
        _config.cfg["settings"]["grounding_check"] = {"nli_verify": False, "topical_relevance_check": False}
        saved_fs = dict(_IN_MEMORY_FS)
        try:
            _IN_MEMORY_FS.clear()
            reset_fetched_urls()
            q_ctx.set({"delegate_tasks": {"used": 1, "limit": 5}})
            heur_urls = [f"https://heur{i}.example.co/page" for i in range(1, 4)]
            colo_urls = [f"https://colo{i}.example.co/page" for i in range(1, 6)]
            for u in heur_urls + colo_urls:
                record_fetched_url(u, filename=f"sources/{u.split('//')[1].split('.')[0]}.md")

            with tempfile.TemporaryDirectory() as tmpdir:
                rs = RunState(tmpdir)
                for u in heur_urls:
                    rs.add_finding(u, "heuristic finding", task_name="heuristics", depth=1)
                for u in colo_urls:
                    rs.add_finding(u, "colombia finding", task_name="colombia", depth=1)
                _IN_MEMORY_FS["findings.md"] = "\n\n".join(
                    f"### [Src]({u})\n- real finding." for u in heur_urls + colo_urls)
                _IN_MEMORY_FS["final_report.md"] = "\n".join(
                    f"- dato. [Src]({u})" for u in colo_urls[:3])
                # 2 prior consecutive report_underuses_findings occurrences -- crosses
                # _STARVATION_SKIP_THRESHOLD (2), the yield window is open on this attempt.
                rs.data["completion_check_attempts"] = [
                    {"attempt": 0, "problem": "report_underuses_findings"},
                    {"attempt": 1, "problem": "report_underuses_findings"},
                ]
                run_state_ctx.set(rs)
                msgs = []
                should_retry, _ = _asyncio.run(run_completion_check(
                    query="q", current_input="q", run_state=rs, notify=msgs.append))
                recorded = rs.data["completion_check_attempts"][-1]["problem"]
                assert recorded == "report_underuses_evidence", (
                    "once the starvation window opens, the starved check (report_underuses_"
                    "evidence) must actually win, not the same check that's already been firing",
                    recorded, msgs)
                assert "heuristics" in msgs[-1], msgs
        finally:
            _IN_MEMORY_FS.clear()
            _IN_MEMORY_FS.update(saved_fs)
            reset_fetched_urls()
            if _orig_ws16 is None:
                _config.cfg["settings"].pop("workspace", None)
            else:
                _config.cfg["settings"]["workspace"] = _orig_ws16
            if _orig_gc16 is None:
                _config.cfg["settings"].pop("grounding_check", None)
            else:
                _config.cfg["settings"]["grounding_check"] = _orig_gc16

    contextvars.copy_context().run(_starvation_yield_prefers_evidence_scenario)

    # --- cheap_grounding_problems / _other_grounding_problems / _with_other_grounding_addendum
    # (2026-07-29): real_grounding_problem's own ordered if-chain returns only its FIRST hit, so
    # every GROUNDING_CHECKS function keyed off the shared ctx.grounding_problem string can be
    # permanently shadowed by a persistently-recurring higher-priority one -- live-confirmed
    # against this session's OWN real run output: a saved final_report.md
    # (give_me_documentation_on_the_top_5_heuristic_algor_20260729_174715) had both a stub_source
    # citation AND 6 uncited-claims lines (verified by calling find_uncited_claim_lines directly
    # against the saved file); check_uncited_claims never got a turn across 3 attempts because
    # ctx.grounding_problem stayed "stub_source:..." the whole time, and the terminal "retry budget
    # exhausted" message reported only stub_source. This reproduces the same shape directly rather
    # than replaying the saved file (keeps the test self-contained and fast). ---
    def _other_grounding_problems_scenario():
        from engine.completion import (
            check_stub_source, cheap_grounding_problems, _other_grounding_problems,
            _with_other_grounding_addendum,
        )
        from utils.run_state import RunState, record_fetched_url, reset_fetched_urls

        with tempfile.TemporaryDirectory() as tmpdir:
            rs = RunState(tmpdir)
            reset_fetched_urls()
            try:
                stub_url = "https://news.example.co/paywalled-calendar"
                record_fetched_url(stub_url, filename="sources/stub.md", stub="paywall")
                # 6 figure-bearing lines, no citation on any of them, in their own heading section
                # with no URL anywhere in it (find_uncited_claim_lines is section-scoped) -- but the
                # document ALSO cites the stub URL, which real_grounding_problem's stub-detection
                # check (earlier in its own priority chain) matches FIRST.
                report = (
                    "## Findings\n"
                    "This report synthesizes recent findings on sales figures for the region overall.\n"
                    "Revenue rose to 200,016 million units in the most recent quarter under review.\n"
                    "Projected spending for next year is estimated near 198,161 million units total.\n"
                    "Long-term estimates place the 2027 figure around 203,930 million units total.\n"
                    "Long-term estimates place the 2028 figure around 210,660 million units total.\n"
                    "The overall trend suggests steady growth is expected to continue through 2029.\n"
                    "## Sources\n"
                    f"- [Calendar]({stub_url})\n"
                )
                gc_cfg = _config.cfg.get("settings", {}).get("grounding_check", {})
                from utils.run_state import get_fetched_urls
                raw = cheap_grounding_problems(report, gc_cfg, get_fetched_urls())
                names = [p.split(":", 1)[0] for p in raw]
                assert "stub_source" in names, names
                assert "uncited_claims" in names, names

                ctx = Ctx(req_artifact="final_report.md", attempt=6, max_attempts=8, delegated=True,
                          files=["findings.md", "final_report.md"], content=report,
                          quotas={}, run_state=rs)
                ctx.grounding_problem = f"stub_source:{stub_url}"
                primary = check_stub_source(ctx)
                assert primary is not None and primary.problem == "stub_source", primary

                # The bug this closes: re-running the sibling check against the SAME ctx can never
                # see the uncited-claims problem (the fact was never computed) -- confirms the fix
                # had to move to cheap_grounding_problems, not a GROUNDING_CHECKS re-walk.
                from engine.completion import check_uncited_claims
                assert check_uncited_claims(ctx) is None, (
                    "sanity check: a sibling check keyed off the same shared grounding_problem "
                    "string cannot independently reveal a second problem")

                others = _other_grounding_problems(ctx, primary.problem)
                assert any(o.startswith("uncited_claims:") for o in others), others

                augmented = _with_other_grounding_addendum(primary, ctx)
                assert "uncited_claims" in augmented.inject, augmented.inject
                assert augmented.warning == primary.warning, (
                    "only .inject (model-facing) gains the addendum, not .warning (user-facing "
                    "live progress notification) -- see _with_other_problems_addendum's docstring")

                # No secondary problem present -> byte-identical verdict, no addendum.
                clean_ctx = Ctx(req_artifact="final_report.md", attempt=0, max_attempts=8,
                                 delegated=True, files=["findings.md", "final_report.md"],
                                 content=f"Steady growth continued. [Calendar]({stub_url})",
                                 quotas={}, run_state=rs)
                clean_ctx.grounding_problem = f"stub_source:{stub_url}"
                clean_primary = check_stub_source(clean_ctx)
                clean_augmented = _with_other_grounding_addendum(clean_primary, clean_ctx)
                assert clean_augmented is clean_primary, clean_augmented
            finally:
                reset_fetched_urls()

    contextvars.copy_context().run(_other_grounding_problems_scenario)



if __name__ == "__main__":
    main()
    print("test_report_underuses OK")
