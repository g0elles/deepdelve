# W9. Compiled writer ignores --style academic (code; parity gap)

Found 2026-11-22 (academic run): `--style academic` with `--writer-mode compiled` produced numeric `[n]` citations and a plain Sources
list; the completion check `report_style_violation` (`completion_checks_structural.py:996`) fired (advisory on compiled runs). Cause:
`src/writer/compiled.py:render` has no style handling; `tui.py:_compile_pipeline_report` (~:2151) calls `wc.render(c, written)` without
the style. Same gap on every surface that selects compiled + academic (CLI `--style`, TUI style setting, API style field).

## Decision (user, 2026-11-22, after EGM review 1: 1 blocker, 7 majors)
Author-year was rejected: on the motivating run only 11 of ~27 sources carry a date (both PLOS papers would read "(PLOS Medicine, n.d.)"),
title-derived organisation names are wrong ("Health News : NPR"), keys collide ("The Guardian"/"The Lancet" -> "the,2020"), and
malformed markers blind the grounding checks that read `[n]` (`grounding.py:642,715,1156`), verify_view (`_BULLET`, `_SOURCE`) and
reader notes. User chose a NUMBERED academic style (Vancouver-like): `[n]` markers stay, the list is a References list.

## Change (v2)
1. `src/writer/compiled.py:render(c, written, lang="en", style="standard")`: style "academic" -> the final section heading is
   LABELS["references"] ("References") instead of "Sources"; entries keep today's form `N. [Title](URL), date` (W8 scholarly titles already
   read "Authors. Title. Journal", date = year), so `_SOURCE`/`parse_numbered_sources` still parse them. Standard output byte-identical.
2. `src/engine/tui.py:_compile_pipeline_report`: `wc.render(c, written, style=config.get_setting("report_style", "standard"))`. CLI
   `--style`, TUI `/style`, API style all set `settings.report_style` (review: `tui.py:2899`, `:1069`, `api.py:133`), so one call site
   covers all three. "answer" style stays out of scope (renders as standard).
3. `src/utils/reader_notes.py:_SRC_HEAD` (:25): `Sources|References`.
4. `src/engine/completion_checks_structural.py:check_academic_citation_style_abandoned` (:996): on a compiled run
   (`run_state.data.get("compiled_writer")`, same signal as `completion.py:1112`) a numbered References list (`parse_numbered_sources`
   non-empty under a "References" heading) satisfies academic style -> None. Agent-written reports keep the (Author, Year) requirement.
   Verdict-matrix row added (`tests/test_completion_verdict_matrix.py`) + `tests/test_structural_checks.py` run (CLAUDE.md rule).
5. Tests (`tests/test_writer_compiled.py`): academic render has "## References", no "## Sources", same `[n]` markers and entries as
   standard; standard render unchanged. Reader-notes test: a References heading is found. Check: compiled + numbered References -> None;
   agent context (no compiled_writer) with the same text -> still `report_style_violation`.

## Acceptance (offline)
Re-render the academic run (`research_output/acad_ornith_chmx_r1` + its written sentences) in both styles; the academic one passes
`check_academic_citation_style_abandoned`, verify_view parses all source lines, reader notes find the list. Skeleton replay (standard)
45/45, 0 errors, no check fires. Full suite + ruff. Live: the W8 A/B Q-acad runs use --style academic.

## Round 2 (EGM: 0 blockers, 1 major, 6 minors; applied)
- M1 `scripts/evaluate_run.py:78` heading `Sources|References` (+ `tests/test_evaluate_run_gaps.py` case). Eval helpers accept it too:
  `eval/writer_ab/blind.py:30`, `eval/audit_runs/detectors.py:9`, `eval/writer_defects/credit.py:56`, `eval/claim_support/{build_set,extract_new}.py`.
- `LABELS["en"]["references"]` added. Check tests the heading text itself (`^#{1,3} References\b`), not only `parse_numbered_sources`.
- Matrix row `compiled_academic_numbered` (expected None, `compiled_writer` non-empty); the agent row still fires.
- Known gap (pre-existing, all compiled verdicts): `compiled_writer` is not in `_RESUME_CARRYOVER_KEYS`, so a `--resume-run` of a compiled
  run treats verdicts as Builder rewrites. Not changed here.
- Offline re-render: `render(..., style=...)` called directly (no `compiled.main --style`).

## Result (offline, 2026-11-22)
Academic run report with the References heading (academic render == standard with heading swapped, test-asserted):
`check_academic_citation_style_abandoned` None (was report_style_violation); agent context with the same text still fires; verify_view 5
source lines + 12 evidence bullets as before; reader notes identical; evaluate_run cited 5/5, listed 5, inline 18, same as standard.
