# Goldfish Review Report: E1 fixed evaluation set, review 5 (confirmation)

**Target:** `eval/eval_set/PLAN.md` (L1-168, draft v5 2026-11-24), checked against `REVIEW_4.md`

## Reviewer Status

| Reviewer | Lens | Status |
| --- | --- | --- |
| `egm-design` | power, decision rule, validity | completed |
| `egm-implementation-readiness` | runnability, code claims | completed |
| `egm-zoom-out` | scope, reuse, cuts | completed |

**REVIEW_4 status:**
- Blocker and majors: 7 of 7 resolved.
- Minors: 20 resolved, 1 partly (minor 1), and 1 needed no action (minor 22).
- Cuts: 13 of 13 applied.

**New issues:** 0 blockers, 2 majors, 5 minors.

Both majors are one-sentence fixes. Once they are applied, the plan can be implemented and can produce a valid go/no-go. v5 has added no scope since v4. The run model, the labeller and the user's effort are user decisions and are not counted.

The synthesizer checked these directly:
- `tui.py:2580-2583`: the per-run folder exists only if `session_isolation` is set (this is MJ-B).
- `config.py:55-58`: the default workspace type is `memory` and there is no `session_isolation` default.
- `tui.py:2827-2828`: the "Run folder:" line.
- `tui.py:2853-2884`: the flags.
- `run.py:88`: `quote_span`.
- `run.py:490-492, 721`: the `gather` keys.
- `bridge.py:12-14`: `source_filename`.
- `config.py:139-143`: workspace.dir is made absolute.

---

## 1. REVIEW_4 findings

| ID | Status | Evidence (PLAN v5) |
| --- | --- | --- |
| B1 app.py ran the interactive TUI | resolved | L38-39: `--prompt q --auto-approve`, `q` byte-identical. |
| N1 injection under-specified | resolved | L106-108: p_part, the min(1, ...) cap, skip at p_part = 1, arm B only, realised delta. L110 defines the MDE. |
| N2 resample not stratified | resolved | L103-104, with the 3-query ceiling stated. |
| N3 plug-in variance | resolved | L112: MDE x 1.15. |
| N4 Phase 0 comparison shape | resolved | L144-145 (schema), L157 (distinct comparison parts reported). |
| MJ1 outputs inside the worktree | resolved | L31-33, L40. Residue: MJ-B below. |
| MJ2 spans lookup | resolved | L76-79: `quote_span` on the report dir copy, evidence dir as fallback. |
| m1 cost counts | partly | L13 and L129-137 fixed. L138 still says "~16 refs" where ~64 is right (minor 1 below). |
| m2 report over the cap | resolved | L82 |
| m3 both passes | resolved | L152 |
| m4 acad count | resolved | L149 says 4. |
| m5 chosen design | resolved | L113-115 |
| m6 redo outcome | resolved | L91-93 |
| m7 k = 4 order | resolved | L119 |
| m8 ship rule | resolved | L90 |
| m9 PABAK | resolved | L156 |
| m10 bootstrap | resolved (cut) | |
| m11 URL to file | resolved | L76-77 |
| m12 driver contract | resolved | L34-37, L41 |
| m13 `Path`, `sys.executable` | resolved | L32, L34, L38 |
| m14 assert on loaded config | resolved | L35-36 |
| m15 frame globs | resolved | L146. Matches exactly 24 logs (8 cmp, 12 cmp2, 4 cmp2pres). |
| m16 commit fields | resolved | L123 |
| m17 `pdf_engine` | resolved | L124 |
| m18-20 sensitivity, A/A wording, sign_p | resolved (cut) | L112, L116, L90 |
| m21 `.gitignore` | resolved in the plan | L47, L163. Still uncommitted in the tree; it is committed at freeze. |
| m22 report-format blindness | no action needed | |
| Cuts 1-13 | all applied | Think parenthetical L124; Instrument bullet moved to L124-125; W4 repeat removed; sign_p and figs_unsupported cut (WRONG rate kept); bootstrap; sensitivity; A/A at L116; provisional MDE (L160 keeps rates only); Reported list trimmed; spans path; Out of scope; history parentheticals; `latest`/`as_of` (L58). |

---

## 2. Code claims checked

| Claim (PLAN line) | Verdict | Evidence |
| --- | --- | --- |
| `app.py --config --prompt --auto-approve --pipeline-evidence --writer-mode compiled --style standard` (L38) | true | `src/app.py:135-139` -> `sdk.py:18-20` -> `tui.cli_main`. Parser at `tui.py:2852-2884`. `--prompt` routes to `run_cli` (`:2947-2951`). `--auto-approve` is store_true and sets `config.AUTO_APPROVE` (`:2893`). |
| "Run folder:" line, ANSI stripped (L40, L146) | true, with conditions | `tui.py:2828`. Printed to stdout on the success path only. The exception path exits with 1 (`:2832-2841`), which the app_rc rule catches. All 24 frame logs contain the line. The path contains a space (minor 2). The line names the workspace root unless `session_isolation` is on (MJ-B). |
| `metrics.json` `gather.searches`/`empty_searches` (L41) | true | `run.py:721`, incremented in `search()` at `:490-492`. The keys are present only after at least one search (minor 4). |
| `source_filename(url)` (L77) | true | `bridge.py:12-14`: `src_<sha1[:8]>.md`. The same name is written at `run.py:639`, and the report dir holds copies. |
| `quote_span(quote, source)` (L78) | true | `run.py:88`. Returns None for a normalised quote under 20 characters; the plan's fallback to the first 6,000 characters covers this. |
| `pr.run(q, model, out, 3, "ce", True)` with `out` a Path (L37) | true | `run.py:602, 614` |
| `--config` in `sys.argv` before import; assert on loaded `openai_model` (L34-35) | true | `config.py:6-18, 213`. Env overlay at `:148-149`. |
| `settings.workspace.dir` absolute, asserted (L32-33) | works, but under-specified | `config.py:139-143` always runs abspath on the loaded value, so the assert must read the raw YAML (minor 3). The workspace type and isolation are not pinned (MJ-B). |
| Source list `N. [title](url)`, parse to the last `)` (L76) | true | `compiled.py:413-414`; an optional `, YYYY-MM-DD` follows. |
| `_strip_notes` (L74) | true | `evaluate_run.py:36-38` |
| W8b tsv column 5 = report dir (L147) | true | 36 rows, all with a report. The paths are repo-relative. |

---

## 3. New issues

### MJ-A. Gold labels are not stated as blind to the LLM labels - Severity: Major
* **Reviewer Lens:** `egm-design`
* **Affected Scope:** L152-156 (also L165, the Phase 1 spot-check)
* **Discrepancy Description:** L153 fixes when the gold items are drawn ("before any label is seen"). It does not say that the user labels them without seeing pass 1 (label and `evidence_quote`). A "confirm or correct" implementation anchors the user. That inflates kappa, raw agreement and the 13/15 comparison bar. This bar is the plan's only validity check (L11-12).
* **Rationale & Recommendation:** Add to L153: "The user labels the 40 gold items from the same blind packets, before seeing any LLM label. User labels are committed before pass-1 labels are joined."

### MJ-B. The arm config's workspace type and session isolation are not pinned - Severity: Major
* **Reviewer Lens:** `egm-implementation-readiness` (verified by synthesizer)
* **Affected Scope:** L32-33, L40
* **Discrepancy Description:**
  - `run_cli` creates a per-run folder only when `settings.workspace.session_isolation` is true (`tui.py:2580-2583`). Otherwise `_current_run_dir(None)` returns the workspace root (`tui.py:2262-2265`).
  - If isolation is off, every run writes `<repo>/research_output/final_report.md` and "Run folder:" prints the root. The plan's "no final_report.md there" check then passes on an overwritten file, and every runs.tsv row points at the last report. Nothing raises an error.
  - `_DEFAULTS` has `type: memory` and no `session_isolation` (`config.py:55-58`). Only `config_template.yaml:498-500` sets `disk`/`true`. The plan does not say which base the arm configs are copied from.
* **Rationale & Recommendation:** Add to L33: "Each arm config also sets `settings.workspace.type: disk` and `session_isolation: true`. The driver asserts both on the loaded `config.cfg`. run.py asserts that report_dir != workspace.dir and that report_dir is not already in runs.tsv."

### Minors (5)
1. **L138, redraw row.** Write "16 queries (~64 refs), ~23 checks". "~16 refs" understates the user's effort per burn about 4x. *(all three lenses)*
2. **L40, "Run folder:" parse.** The repo path contains a space. Take everything after `Run folder: ` to the end of the line, after stripping ANSI. Splitting on whitespace would make every attempt read as "no report". *(readiness)*
3. **L33, "asserted absolute".** `config.py:143` always makes the loaded value absolute, so an assert on `config.cfg` can never fail. Assert on the raw arm YAML. *(readiness, synthesizer)*
4. **L41, L91-92, empty searches.** Read the keys with `.get(k, 0)`. Treat searches == 0 as a failed attempt, so the 25% rule never divides by zero. *(readiness, synthesizer)*
5. **L42, CLI and resume.**
   - Give the arm form, e.g. `--arms <commit>:<cfg> [<commit>:<cfg>]`, with one arm for Phase 1. L30 defines an arm as (commit, config), but L42 passes only YAML files.
   - Name the resume key (query, arm, run, attempt).
   - Delete an attempt dir left by a crash before rerunning it. `out.mkdir(exist_ok=True)` (`run.py:614`) would otherwise mix old `src_*.md` files into the new attempt.
   *(zoom-out, readiness)*

Raised but not counted: the guard median when writer seconds are missing (design); no action stated for Phase 1 spot-check disagreements (design); the practical floor having no lower bound (design); copying `kappa` from `eval/writer_ab/human_compare.py:9-14` (zoom-out); "log format copied" at L30 being inaccurate (zoom-out); naming the Phase 0 evidence fallback dir (log path minus `.app.log`; W8b tsv column 4) (readiness); moving the driver contract into the run.py docstring after freeze (zoom-out).

---

## Net effect if applied
With MJ-A and MJ-B, the Phase 0 bar measures independent agreement, and every run row points at that run's own report. Every other code claim holds, and the decision rule, power, MDE and validity bar are fully specified. The plan is ready to freeze after those two sentences and minor 1.
