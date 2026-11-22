# W5. Retire `cross_source_contradiction` and "Where sources differ" (code only, no model, no GPU)

User decision 2026-11-22: retire the check in every writer mode and the compiled section; keep the detector for offline eval.

## Evidence
- `eval/writer_defects/credit.py` (PLAN.md Results): 54 saved reports (36 compiled, 18 agent-mode audit). Old code 29 hits, new 18,
  all 29 blind-labelled JUNK, 0 REAL. Agent-mode audit corpus: 0 hits old and new; `eval/audit_runs/VALIDATION_VERDICT.md:17` "No firings,
  no labelled instance of its class".
- Remaining 18: 7 metric-name subjects ("unemployment rate", "gdp growth rate" across Ireland/Iceland); 11 entity subjects
  ("reino unido", "international monetary fund") pairing different quantities (bailout total vs one tranche, sugar % vs obesity rank).
  A subject rule cannot separate these: the subject is right, the attribute differs. Live 2026-09-30 (wiki Roadmap): 12 of 13 hits junk,
  never converging.
- On compiled runs a failing check is advisory (`completion.py:1111`, reader note), so keeping the check without the section would put
  the junk in reader notes; on agent runs it dispatches a Builder rewrite (`builder_fixable=True`).

## Changes
1. `src/engine/completion.py`: delete the `_CHECK_SPECS` row (:186) and the import (:65). All routing sets derive from `_CHECK_SPECS`.
2. `src/engine/completion_checks_grounding.py`: delete `check_cross_source_contradiction` (:1022-1041) and the
   `find_cross_source_contradictions` import (:28).
3. `src/writer/compiled.py`: delete `add_disagreements` (:89-102), `Compiled.disagreements` (:64), the render block (:420-424),
   the `differ`/`differ_line` keys in `LABELS["en"]` (only language), and `and not c.disagreements` in the comparison-line condition (:431).
4. `src/engine/tui.py:2167-2170` (`_compile_pipeline_report`): delete the hits/add_disagreements/re-render lines. Parity: this function
   is the single compiled path for CLI, TUI and API (README: `--writer-mode`, `/writer-mode`, `writer_mode`); no surface-specific code.
5. Kept: `utils/grounding.py:cross_source_hits`, `find_cross_source_contradictions`, `_XSRC_RULES` and their unit tests in
   `tests/test_grounding_checks_core.py` (detector for offline replays: `eval/audit_runs/replay_checks.py`, `skeleton_replay.py:62`).
   Docstrings of both say "offline detector, retired as a completion check 2026-11-22 (eval/writer_defects/PLAN_W5.md)".
6. Tests:
   - `tests/test_completion_verdict_matrix.py:391`: keep the 12%/18% fixture, rename the row `cross_source_retired`, expected problem
     and phrase -> None (pins the retirement);
     delete the `cross_source_same_number_separators` row (:401, rule a is unit-tested in test_grounding_checks_core).
   - `tests/test_writer_compiled.py:123-130`: delete the add_disagreements block.
   - `tests/test_dispatch_ablation_and_salvage.py:865` uses the name as plain history data: unchanged (old `completion_check_attempts`
     entries are strings; no lookup keyed on a registered problem, `completion.py:232` maps func -> problem only).
7. Eval: `eval/writer_ab/skeleton_replay.py:54-55` delete (would raise); `:62-63` delete (retired check must not count as fired).
   The replay rewrites `eval/writer_ab/skeleton_matrix.json` (untracked, so nothing to commit). `eval/writer_defects/credit.py` frozen; reproduces at 8502167
   (note in its docstring).
8. Docs: README:75 drop "flags cross-source contradictions". Wiki: `Roadmap.md:162,262-268` (Phase 2 FEVER item) and
   `Writer-Redesign-Plan.md` marked retired with this evidence (other pages are history, unchanged). `Architecture.md`: add a
   "retiring a check" row to the §6 quick reference (delete spec row, keep a matrix row expecting None, update offline replays, old
   names in carried-over attempts are harmless) and fix :209 (verdict matrix lives in `tests/test_completion_verdict_matrix.py`).
   Unchanged on purpose: PeerReviewer prose on unresolved contradictions (`prompts.py:840`), comment at `findings_evidence.py:393`.
9. Resume: `completion_check_attempts` carries over (`run_state.py:559`); old names are compared by equality/membership only, no keyed
   lookup. `compiled_writer` is not carried over. A `final_report.md` written before the change keeps its old section.

## Acceptance
- `~/.venvs/deepdelve/bin/python -m pytest tests -q` (incl. test_structural_checks, verdict matrix, test_writer_compiled,
  test_grounding_checks_core): pass; `ruff check src tests eval/writer_ab` clean.
- `grep -rn "add_disagreements\|check_cross_source_contradiction\|cross_source_contradiction\|Where sources differ" src tests eval/writer_ab scripts`
  -> only the kept detector, the retirement matrix row, history data in test_dispatch_ablation_and_salvage, and `minimal_pairs.py`'s heading skip.
- Offline: `skeleton_replay.py` : zero `error` rows, same number of reports as the committed skeleton_matrix.json, no "## Where sources differ".
- No live run needed: the change removes a section and a check, no model behaviour changes (phase smoke test: one offline replay).

## Result (2026-11-22, offline)
- Skeleton replay before and after: 45 reports, 0 errors, 0 checks fired; after: 0 of 45 reports contain "Where sources differ".
- Acceptance grep: only the kept detector, history comments, the retirement matrix row and the test history data.
