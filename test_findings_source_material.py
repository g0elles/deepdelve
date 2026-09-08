import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from engine.completion import (
    _build_findings_source_material,
)

# noqa: F401 -- common test-infra names available to every split file below, regardless of
# whether a given file's own retained sections happen to use all of them (ruff prunes genuinely
# unused ones per file). Split 2026-09-07 out of the former single 10,701-line
# test_structural_checks.py (session_status/CURRENT.md carried-forward TODO) -- see
# test_structural_checks.py's own new header for the full split rationale and the file-to-topic
# map. Pure move: every assertion below is byte-identical to its prior body, just regrouped by
# topic into its own main(), all still called in original order from the new thin
# test_structural_checks.py orchestrator.
import config as _config
import contextvars
import tempfile

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
    # --- _build_findings_source_material must dedupe exact (source_url, summary) repeats before
    # serializing into FindingsWriter's prompt (2026-07-14 live finding: every completion-check
    # retry that re-delegates the same task_name re-adds a finding without removing the stale one,
    # so a real run accumulated 25 entries for ~8-10 distinct pieces of research, with some summaries
    # appearing identically 5 times — genuine content was getting diluted/dropped by FindingsWriter
    # under the bloat rather than something Colombia-specific). Distinct summaries for the SAME
    # source_url (a legitimately different retry result) must both survive. ---
    def _findings_dedup_scenario():

        with tempfile.TemporaryDirectory() as tmpdir:
            rs = RunState(tmpdir)
            rs.add_finding(_SRC, "same summary text", task_name="background", depth=1)
            rs.add_finding(_SRC, "same summary text", task_name="background", depth=1)  # exact repeat
            rs.add_finding(_SRC, "same summary text", task_name="background", depth=1)  # exact repeat
            rs.add_finding(_SRC, "a genuinely different summary", task_name="background", depth=1)
            material = _build_findings_source_material(rs)
            assert material.count("same summary text") == 1, (
                "exact-duplicate findings must be collapsed to one entry", material)
            assert "a genuinely different summary" in material

    contextvars.copy_context().run(_findings_dedup_scenario)

    # --- _collapse_multi_url_task_findings (2026-07-22): a Searcher task that fetches N URLs in
    # one turn calls add_finding once per URL but with the SAME task-level summary attached every
    # time (orchestrator.py's _run_single_task) -- different source_url, identical body text, so
    # _findings_dedup_scenario's exact (source_url, summary) dedup above does NOT collapse these.
    # Confirmed live: one 3-task run had 16 of 25 "citable findings" made of just 3 near-identical
    # blobs, diluting the evidence base and letting whichever task fetched the most URLs dominate
    # _reorder_findings_for_position_bias's front/back edges by fetch count alone. The body text
    # must now be kept ONCE per (task_name, summary) group, with every additional real URL still
    # named (never silently dropped) so FindingsWriter can still write a separate entry for each. ---
    def _findings_multi_url_collapse_scenario():

        with tempfile.TemporaryDirectory() as tmpdir:
            rs = RunState(tmpdir)
            shared_summary = "**Consolidated Findings** – same synthesis text for every URL fetched this turn."
            rs.add_finding("https://example.com/a", shared_summary, task_name="multi_fetch_task", depth=1)
            rs.add_finding("https://example.com/b", shared_summary, task_name="multi_fetch_task", depth=1)
            rs.add_finding("https://example.com/c", shared_summary, task_name="multi_fetch_task", depth=1)
            rs.add_finding(_SRC, "a distinct single-source finding", task_name="other_task", depth=1)
            material = _build_findings_source_material(rs)
            assert material.count("**Consolidated Findings**") == 1, (
                "same-task, same-summary findings sharing N URLs must render the body ONCE, "
                "not once per URL", material)
            for url in ("https://example.com/a", "https://example.com/b", "https://example.com/c"):
                assert url in material, (
                    "every real URL from the collapsed group must still be named so "
                    "FindingsWriter can cite each one, not just the first", url, material)
            assert "a distinct single-source finding" in material, material

    contextvars.copy_context().run(_findings_multi_url_collapse_scenario)

    # --- _build_findings_source_material must show each finding's REAL saved filename alongside
    # its URL (2026-07-19, user-proposed extension of the same-day delegate_tasks filename fix) —
    # FINDINGS_WRITER_INSTRUCTIONS' own Workflow step 2 already claimed "path is given alongside
    # its URL", which was FALSE before this fix (only the separate fetched_block cross-reference
    # list at the bottom had it) — this makes that existing prompt claim true. A finding whose
    # source_url isn't a real fetch (the coverage() task_name fallback for an uncovered task) must
    # NOT get a fabricated filename annotation. ---
    def _findings_filename_scenario():

        with tempfile.TemporaryDirectory() as tmpdir:
            rs = RunState(tmpdir)
            rs.data["fetched_urls"] = [{"url": _SRC, "filename": "sources/page.md"}]
            rs.add_finding(_SRC, "a real finding", task_name="t1", depth=1)
            rs.add_finding("uncovered_task", "", task_name="t2", depth=1)  # no real fetch at all
            material = _build_findings_source_material(rs)
            assert f"### Source: {_SRC} (saved as sources/page.md)" in material, material
            # The no-real-fetch entry gets no filename annotation, and (2026-07-21 fabrication
            # fix) no "### Source: ..." heading at all -- see _findings_uncited_fallback_scenario.
            assert "### Source: uncovered_task" not in material, material

    contextvars.copy_context().run(_findings_filename_scenario)

    # --- task_names scoping (_dispatch_per_facet_findings_writer_fix) must also pull in a nested
    # Analyzer's own finding via top_level_task_name, not just an exact task_name match (2026-08-17
    # ledger-rollup sibling fix) -- otherwise a per-facet FindingsWriter retry scoped to the facet
    # that actually has the real content would silently drop it, the same evidence-crowding shape
    # the scoping feature exists to prevent, just from the opposite direction. ---
    def _findings_scoping_rollup_scenario():

        with tempfile.TemporaryDirectory() as tmpdir:
            rs = RunState(tmpdir)
            rs.data["fetched_urls"] = [{"url": _SRC, "filename": "sources/page.md"}]
            rs.add_finding(_SRC, "real nested analysis", task_name="Analyze page.md", depth=2,
                            top_level_task_name="facet_a")
            rs.add_finding("https://other.example.co/x", "unrelated facet's own content",
                            task_name="facet_b", depth=1)
            material = _build_findings_source_material(rs, task_names={"facet_a"})
            assert f"### Source: {_SRC}" in material, material
            assert "unrelated facet's own content" not in material, material

    contextvars.copy_context().run(_findings_scoping_rollup_scenario)

    # --- A finding whose source_url is the add_finding task_name fallback (no real fetched or
    # reference URL at all) must NEVER be rendered as a "### Source: ..." entry -- that shape is
    # what lets the engine treat an entry as citable, and a bare task_name string wearing it was
    # silently cited as a fake URL by a real qwen3:8b run (5/19 findings fabricated,
    # 2026-07-21). It must instead be named in a separate, explicitly non-citable list. ---
    def _findings_uncited_fallback_scenario():

        with tempfile.TemporaryDirectory() as tmpdir:
            rs = RunState(tmpdir)
            rs.add_finding(_SRC, "a real finding", task_name="t1", depth=1)
            rs.add_finding("orphan_task", "some narration with no real source", task_name="orphan_task", depth=2)
            material = _build_findings_source_material(rs)
            assert "### Source: orphan_task" not in material, (
                "a task_name fallback must never be rendered as a citable Source heading", material)
            assert "orphan_task" in material, (
                "the task must still be named so the model can acknowledge the gap", material)
            assert "invent" in material.lower() or "fabricat" in material.lower(), (
                "the model must be explicitly told not to fabricate a source for it", material)

    contextvars.copy_context().run(_findings_uncited_fallback_scenario)

    # --- _build_findings_source_material: TRUE total (findings_block + both notes + boilerplate
    # + fetched_block) must never exceed context_budget_chars (2026-07-23, live regression: a
    # real run's findings_block correctly capped at 48,308 chars under a 50,000 budget, but
    # fetched_block (10,368 chars, unbudgeted) got appended afterward anyway for a true total of
    # 58,676 -- FindingsWriter then produced empty output 4 times in a row, plausibly out of
    # context budget to actually write anything after ingesting the oversized prompt). Many
    # findings AND many fetched URLs, deliberately sized so both sections individually would fit
    # under a naive per-section cap but NOT together. ---
    def _findings_budget_sharing_scenario():

        _orig_cb = _config.cfg.get("settings", {}).get("context_budget_chars")
        _config.cfg.setdefault("settings", {})["context_budget_chars"] = 2000
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                rs = RunState(tmpdir)
                # 30 findings entries, ~80 chars of summary each -> ~2400+ chars before headings.
                for i in range(30):
                    rs.add_finding(f"https://example.co/{i}", "x" * 80, task_name=f"task_{i}", depth=1)
                # 30 fetched URLs -- present in fetched_urls even for findings not added above,
                # simulating a run where more was fetched than ended up citable.
                rs.data["fetched_urls"] = [
                    {"url": f"https://example.co/extra/{i}", "filename": f"sources/extra_{i}.md"}
                    for i in range(30)
                ]
                material = _build_findings_source_material(rs)
                assert len(material) <= 2000, (
                    "TRUE total must respect context_budget_chars -- this is the exact "
                    "2026-07-23 live regression", len(material))
        finally:
            if _orig_cb is None:
                _config.cfg["settings"].pop("context_budget_chars", None)
            else:
                _config.cfg["settings"]["context_budget_chars"] = _orig_cb

    contextvars.copy_context().run(_findings_budget_sharing_scenario)

    # --- _build_findings_source_material: fetched_block alone must never be allowed to consume
    # the ENTIRE budget and zero out every finding -- degenerate case, many more fetched URLs
    # than findings entries. Never observed live, but the fix's own fetched_cap guard (half the
    # budget) exists specifically to prevent this; pin it directly. ---
    def _findings_budget_degenerate_scenario():

        _orig_cb = _config.cfg.get("settings", {}).get("context_budget_chars")
        _config.cfg.setdefault("settings", {})["context_budget_chars"] = 1000
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                rs = RunState(tmpdir)
                rs.add_finding(_SRC, "a real finding that must survive", task_name="t1", depth=1)
                # 200 fetched URLs -- alone, at ~50 chars/line, comfortably exceeds a 1000-char budget.
                rs.data["fetched_urls"] = [
                    {"url": f"https://example.co/many/{i}", "filename": f"sources/m{i}.md"}
                    for i in range(200)
                ]
                material = _build_findings_source_material(rs)
                assert "a real finding that must survive" in material, (
                    "fetched_block overflow must never zero out every finding", material)
                assert len(material) <= 1000 + 200, (  # small slack for the omitted-count note text
                    "true total must still respect budget in the degenerate case", len(material))
        finally:
            if _orig_cb is None:
                _config.cfg["settings"].pop("context_budget_chars", None)
            else:
                _config.cfg["settings"]["context_budget_chars"] = _orig_cb

    contextvars.copy_context().run(_findings_budget_degenerate_scenario)

    # --- _build_findings_source_material: uncited_task_names/omitted_task_names must be
    # deduplicated before rendering -- the SAME task_name redispatched across multiple retries
    # must appear once in the note, not once per occurrence (2026-07-23 live regression: a real
    # run's uncited_note listed 'background' 10 times, pure wasted budget and misleading noise —
    # looked like 10 distinct failed tasks, was 1 task redispatched 10 times). ---
    def _findings_note_dedup_scenario():

        with tempfile.TemporaryDirectory() as tmpdir:
            rs = RunState(tmpdir)
            rs.add_finding(_SRC, "a real citable finding", task_name="real_task", depth=1)
            for _ in range(10):
                rs.add_finding("background", "narration, no real source", task_name="background", depth=1)
            material = _build_findings_source_material(rs)
            assert material.count("'background'") == 1, (
                "the same uncited task_name repeated across retries must be named ONCE in the "
                "note, not once per occurrence", material)

    contextvars.copy_context().run(_findings_note_dedup_scenario)

    # --- writer_gate_ctx structural gate (2026-07-22): a prompt-only reorder of
    # FINDINGS_WRITER_INSTRUCTIONS asking the model to write from the evidence base before reading
    # raw source files did NOT change live behavior (gpt-oss:20b's first call was still
    # read_workspace_file) -- this is the backing structural check. Armed, read/grep are blocked
    # until the first write_workspace_file call; unarmed (None, e.g. Builder's dispatch), both work
    # exactly as before -- must never regress Builder's own required read-findings.md-first flow. ---
    def _writer_gate_scenario():
        from tools import writer_gate_ctx
        from tools.fs import read_workspace_file, write_workspace_file, grep_workspace_file

        token = writer_gate_ctx.set({"write_done": False})
        try:
            blocked = read_workspace_file("anything.md")
            assert blocked.startswith("Error:") and "write_workspace_file" in blocked, blocked
            blocked_grep = grep_workspace_file("anything.md", "x")
            assert blocked_grep.startswith("Error:"), blocked_grep

            result = write_workspace_file("findings.md", "### [T](https://x.com)\n- a finding")
            assert "Wrote" in result, result

            unblocked = read_workspace_file("findings.md")
            assert "a finding" in unblocked, unblocked
        finally:
            writer_gate_ctx.reset(token)

        # Gate not armed (default None, e.g. Builder's dispatch) -- unaffected.
        assert "a finding" in read_workspace_file("findings.md")

    contextvars.copy_context().run(_writer_gate_scenario)

    # --- writer_gate_ctx: edit_workspace_file also satisfies the gate (2026-08-16 live incident):
    # _dispatch_per_facet_findings_writer_fix's corrective passes arm this SAME gate (it's shared
    # by every FindingsWriter dispatch) but their own instructions explicitly say "use
    # edit_workspace_file ... do not rewrite or touch any other part of the file" -- findings.md
    # already exists at that point, so trying to read it first (to find an edit anchor) is
    # legitimate. With only write_workspace_file satisfying the gate, that read got rejected with
    # wording that told the model to call write_workspace_file instead -- steering it toward a
    # full-file overwrite that silently destroyed facets a PRIOR per-facet round had already
    # correctly added. Confirmed live: this exact error fired 20 times in one 45-minute run that
    # never once converged on a stable findings.md. ---
    def _writer_gate_edit_satisfies_scenario():
        from tools import writer_gate_ctx
        from tools.fs import read_workspace_file, write_workspace_file, edit_workspace_file

        write_workspace_file("findings.md", "### [T](https://x.com)\n- a finding")
        token = writer_gate_ctx.set({"write_done": False})
        try:
            blocked = read_workspace_file("findings.md")
            assert blocked.startswith("Error:") and "write_workspace_file" in blocked, blocked

            result = edit_workspace_file("findings.md", "- a finding", "- a finding\n- another finding")
            assert "Error" not in result, result

            unblocked = read_workspace_file("findings.md")
            assert "another finding" in unblocked, unblocked
        finally:
            writer_gate_ctx.reset(token)

    contextvars.copy_context().run(_writer_gate_edit_satisfies_scenario)

    # --- writer_gate_ctx: recommended_tool controls the block message's own wording (2026-08-16
    # follow-up incident): fixing WHICH tool satisfies the gate wasn't enough on its own -- the
    # block message the model actually SEES still hardcoded "call write_workspace_file now" even
    # for a per-facet dispatch whose own instructions said to use edit_workspace_file. Confirmed
    # live: rather than following either the (wrong) block message or its own (right) instructions,
    # the model just kept retrying blocked reads a few times, then gave up with NOTHING written for
    # that facet -- worse than the original overwrite risk. The block message must name whichever
    # tool THIS dispatch's own instructions actually told the model to use. ---
    def _writer_gate_recommended_tool_scenario():
        from tools import writer_gate_ctx
        from tools.fs import read_workspace_file, write_workspace_file

        write_workspace_file("findings.md", "### [T](https://x.com)\n- a finding")

        # Default (no recommended_tool set) -- classic two-pass full-write case, unchanged wording.
        token = writer_gate_ctx.set({"write_done": False})
        try:
            blocked = read_workspace_file("findings.md")
            assert "write_workspace_file" in blocked, blocked
        finally:
            writer_gate_ctx.reset(token)

        # Per-facet case -- block message must name edit_workspace_file, not write_workspace_file.
        token2 = writer_gate_ctx.set({"write_done": False, "recommended_tool": "edit_workspace_file"})
        try:
            blocked2 = read_workspace_file("findings.md")
            assert "edit_workspace_file" in blocked2, blocked2
            assert "call write_workspace_file now" not in blocked2, (
                "a per-facet dispatch's own block message must not point the model at the tool "
                "its own instructions told it NOT to use", blocked2)
        finally:
            writer_gate_ctx.reset(token2)

    contextvars.copy_context().run(_writer_gate_recommended_tool_scenario)

    # --- writer_gate_ctx: reading the gate's OWN target file is exempt from the block entirely
    # (2026-08-16 follow-up incident, the actual deadlock root cause): edit_workspace_file requires
    # an EXACT existing substring as its anchor, but a per-facet dispatch's fresh context has never
    # seen findings.md's current content -- it structurally CANNOT produce a valid old_string
    # without reading the file first, and the gate (even after the recommended_tool fix above)
    # still refused that read, since the block applied to ALL reads regardless of which file. This
    # was live-confirmed as a real dead end, not a theoretical one: three separate per-facet
    # dispatches in one run each tried read_workspace_file a few times, got blocked every time,
    # and gave up with NOTHING written for their facet -- not even a failed edit attempt, just
    # silence. Reading the target file must be allowed; reading anything ELSE (a raw source under
    # sources/) must still block exactly as before, or this reopens the ORIGINAL 2026-07-22 bug
    # this gate exists to prevent (abandoning the compiled evidence to go hand-read sources). ---
    def _writer_gate_target_file_exempt_scenario():
        from tools import writer_gate_ctx
        from tools.fs import read_workspace_file, write_workspace_file, edit_workspace_file

        write_workspace_file("findings.md", "### [Existing](https://x.com)\n- existing finding")
        write_workspace_file("sources/other_source.md", "raw source content, not the compiled evidence")

        token = writer_gate_ctx.set({
            "write_done": False, "recommended_tool": "edit_workspace_file", "target_file": "findings.md",
        })
        try:
            # Reading a DIFFERENT file (a raw source, not the target) must still block, BEFORE any
            # write/edit has happened -- the exemption is scoped to the target file only, checked
            # first since satisfying the gate below would unblock everything afterward anyway.
            other_blocked = read_workspace_file("sources/other_source.md")
            assert other_blocked.startswith("Error:") and "edit_workspace_file" in other_blocked, other_blocked

            # Reading the target file (to find a real edit anchor) must be allowed.
            unblocked = read_workspace_file("findings.md")
            assert "Error" not in unblocked or "not found" not in unblocked.lower(), unblocked
            assert "existing finding" in unblocked, unblocked

            # A subsequent real edit, anchored on content only visible via that now-permitted
            # read, must succeed -- this is the actual deadlock this fix resolves.
            result = edit_workspace_file(
                "findings.md", "- existing finding", "- existing finding\n- a NEW facet's finding")
            assert "Error" not in result, result
        finally:
            writer_gate_ctx.reset(token)

    contextvars.copy_context().run(_writer_gate_target_file_exempt_scenario)

    # --- low-novelty search-streak backstop (2026-07-22): a real live run showed one WebSearcher
    # task fire 14 near-duplicate web_search calls (each worded differently, so the existing
    # exact-repeat <Anti-Looping> rule never caught it), fetching only 3 URLs the whole time.
    # FIRST VERSION of this check reset on any successful fetch -- live-disconfirmed immediately
    # (web_search auto-fetches its top result on nearly every call, so that reset fired almost
    # every time regardless of query repetition, and the streak never reached threshold across a
    # real 15-query run). Rebuilt around actual query novelty instead. ---
    def _search_streak_scenario():
        from tools.core import tool_quotas_ctx as q_ctx
        from utils.run_state import task_id_ctx
        from tools.web import _note_search_streak, _SEARCH_STREAK_WARN_AT

        q_ctx.set({})
        task_id_ctx.set("task-a")
        # Same shape as the real live transcript: near-duplicate rephrasings of one request.
        near_dup_queries = [
            "heuristic algorithms deep learning sales forecasting holidays payday events",  # seeds the term set, novelty always 1.0 here
            "heuristic approach deep learning sales forecasting",
            "heuristic rules sales forecasting deep learning",
            "holiday effect sales forecasting deep learning",
            "payday effect sales forecast deep learning",
            "sales forecast deep learning approach effect",
        ]
        assert len(near_dup_queries) == _SEARCH_STREAK_WARN_AT + 1
        notes = [_note_search_streak(q) for q in near_dup_queries]
        assert all(n == "" for n in notes[:-1]), notes
        assert notes[-1] and "web_search" in notes[-1] and "rephrasing" in notes[-1], notes

        # A genuinely novel query (new terms, no overlap with anything seen) resets the streak.
        assert _note_search_streak("giraffe migration patterns antarctica penguins") == ""

        # Different task_id -> independent counter, one task's streak never bleeds into another's.
        task_id_ctx.set("task-b")
        assert _note_search_streak("heuristic algorithms deep learning sales forecasting") == ""

    contextvars.copy_context().run(_search_streak_scenario)

    # --- _build_findings_source_material must cap total size against context_budget_chars instead
    # of handing an unbounded string to a fresh dispatch for the model backend to silently truncate
    # (2026-07-19 QA audit, "real grounded content silently vanishes during synthesis" investigation
    # -- this was a genuinely unguarded injection point, unlike the Planner's stream and a sub-
    # agent's own generation, which both already had a context_budget_chars-style guard). Whole
    # entries only (never truncate one mid-way), earliest-first, and the omitted task names must be
    # named explicitly so the model can acknowledge the gap instead of silently dropping it. ---
    def _findings_budget_scenario():
        _orig_budget = _config.cfg.get("settings", {}).get("context_budget_chars")
        try:
            # 300 (this test's original value) no longer leaves room for even one entry once
            # the 2026-07-23 fix's fixed overhead (intro/outro boilerplate + the omitted_note
            # size reserve) is honestly accounted for -- that overhead was ALWAYS real, the
            # original test just never had to pay it (the pre-fix code left intro/outro
            # completely unbounded, so this test's small budget only ever constrained
            # findings_block in isolation, never the true total). 1000 keeps the same "only the
            # first entry survives" demonstration this test is actually for.
            _config.cfg.setdefault("settings", {})["context_budget_chars"] = 1000
            with tempfile.TemporaryDirectory() as tmpdir:
                rs = RunState(tmpdir)
                rs.add_finding("https://a.example.com", "x" * 150, task_name="first_task", depth=1)
                rs.add_finding("https://b.example.com", "y" * 150, task_name="second_task", depth=1)
                rs.add_finding("https://c.example.com", "z" * 150, task_name="third_task", depth=1)
                material = _build_findings_source_material(rs)
                assert "first_task" not in material or "x" * 150 in material, (
                    "an entry must never be truncated mid-way -- it's either whole or fully omitted", material)
                assert "z" * 150 not in material, (
                    "later entries beyond the budget must be omitted, not silently included anyway", material)
                assert "omitted" in material.lower(), (
                    "an omission must be explicitly named to the model, not silent", material)
                assert "third_task" in material, (
                    "the omitted task's name must be named so the model can acknowledge the gap", material)

            # context_budget_chars == 0 (off) must disable the cap entirely, same "0 = off"
            # convention as get_context_budget elsewhere.
            _config.cfg["settings"]["context_budget_chars"] = 0
            with tempfile.TemporaryDirectory() as tmpdir2:
                rs2 = RunState(tmpdir2)
                rs2.add_finding("https://a.example.com", "x" * 150, task_name="first_task", depth=1)
                rs2.add_finding("https://b.example.com", "y" * 150, task_name="second_task", depth=1)
                rs2.add_finding("https://c.example.com", "z" * 150, task_name="third_task", depth=1)
                material2 = _build_findings_source_material(rs2)
                assert "z" * 150 in material2, "context_budget_chars=0 must disable the cap entirely"
        finally:
            if _orig_budget is None:
                _config.cfg["settings"].pop("context_budget_chars", None)
            else:
                _config.cfg["settings"]["context_budget_chars"] = _orig_budget

    contextvars.copy_context().run(_findings_budget_scenario)



if __name__ == "__main__":
    main()
    print("test_findings_source_material OK")
