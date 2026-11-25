# Goldfish Review Report: E1 fixed evaluation set, review 4

**Target:** `eval/eval_set/PLAN.md` (L1-175, draft v4 2026-11-24), checked against `REVIEW_3.md`

## Reviewer Status

| Reviewer | Lens | Status |
| --- | --- | --- |
| `egm-design` | power, decision rule, validity | completed |
| `egm-implementation-readiness` | runnability, code claims | completed |
| `egm-zoom-out` | scope, reuse, cost, cuts | completed |

**REVIEW_3 status:** 29 resolved, 5 partly, 0 not.
**New issues after deduplication:** 1 blocker, 6 majors, 22 minors, plus a cut list (section 5).

The run model, the labeller and the user's effort are user decisions and are not counted. The blocker is a one-line fix. The majors are four sentences in "Power simulation", one in Phase 0, and two in Files/Runs. Two of the majors (MJ2, the S3 part of N3) are fixed by deleting text.

The synthesizer verified these directly: `config.py:6-18` (the `--config` read at import), `llm.py:9-31` (`model_options`, how "minimal" resolves), `thinking.py:40-45` (`minimal_think`, which queries Ollama `/api/show` and needs no config), `run.py:183/208/223/473/602` (think sites, `run` signature), `tui.py:2164` (writer think site), `tui.py:2828` ("Run folder:" line with ANSI codes), `tui.py:2947-2953` (B1), `w8b_runs.tsv` (4 `acad` rows).

---

## 1. REVIEW_3 findings

| ID | Status | Notes (PLAN v4 lines) |
| --- | --- | --- |
| M1 pipeline ignores arm config | resolved | L32-34. Residue: assert on the loaded `config.cfg`, not the raw YAML (minor 14). |
| M2 commit/worktree arm mechanism | partly | L31-36 give the worktree, the driver subprocess and the cwd. The app.py command cannot run headless (B1). Where runs are written is not fixed (MJ1). The driver's name and output channel are not given (minor 12). |
| M3 two dirs, Phase 0 frame | resolved | L128-130, L149-151. The frame globs should be named now (minor 15). |
| M4 source path, window rule | resolved | L73-76. The spans lookup it adopted breaks for overwritten evidence (MJ2, new). |
| M5 packet contents and size | resolved | L70-79. A report over the cap is not handled (minor 2). |
| M6 two generators, correlation | partly | L103-105 use one generator. p_part, the cap, the arm and the realised delta are undefined (N1). The resample is unstratified (N2) and biased (N3). |
| M7 baseline | resolved | L109-110. |
| M8 kappa label space | resolved | L159-162. Minor 9 (PABAK). |
| M9 Phase 1 labelling | resolved | L169-170. |
| M10 cost | partly | Table L133-142, ceiling stated as worst case. The labelling-context and user-judgement figures are 3-4x too low, and `latest` re-verification has no row (minor 1). |
| m1 below-floor action | resolved | L87-88. |
| m2 comparison parts confined | resolved | L51-53. |
| m3 grid unit | resolved | L108. |
| m4 k first, then queries | resolved | L110-111. The selection rule is implicit (minor 5). |
| m5 attempts per slot | partly | L89-90. The redo rule is repeated at L121, and an attempt 2 that still has >25% empty searches has no stated outcome (minor 6). |
| m6 test burn | resolved | L95-96. |
| m7 bootstrap label | resolved | L86-87 (or cut it, section 5). |
| m8 leakage scope | resolved | L98-100. |
| m9 simulation size | resolved | L106, L110. |
| m10 Whitepaper §10 | resolved | L112-113. |
| m11 obstacle 7 | resolved | L10-11. |
| m12 probe-first rule | resolved | L11-12. |
| m13 dev role, dead clause | resolved | L22-23. |
| m14 in-process patch arm | resolved | L36. |
| m15 seconds measurement | resolved | L93-94. |
| m16 flag citation | resolved | L117 (parser at `tui.py:2852/2866/2880/2884`). |
| m17 think sites | resolved | L125-128 (but this whole paragraph can be cut, section 5). |
| m18 "differed by 6" | resolved | L18. |
| m19 cache scope | resolved | L118-119, L122-123. |
| m20 `.gitignore` | resolved | `.gitignore:33` has `eval/eval_set/_private/`, but the change is not committed yet (` M .gitignore`). Commit it with the plan. |
| m21 `evaluate_run.score` input | resolved | L130. |
| m22 comparison refs | resolved | L53. |
| m23 Phase 0 heterogeneity | partly | L151-153. "The 6 W8b `acad` reports" is wrong: `w8b_runs.tsv` has **4** `acad` rows (lines 1, 2, 17, 18). REVIEW_3 introduced the 6. |
| m24 labels path | resolved | L41-42. |

---

## 2. Code claims checked

| Claim (PLAN line) | Verdict | Evidence |
| --- | --- | --- |
| `--config` in `sys.argv` before importing config (L32-33) | true | `config.py:6-10,18`; `load_config()` runs at import (`:213`). The env overlay `OPENAI_MODEL` applies after the file (`:148-149`). |
| `model_options(model)`, `minimal_think(model)` recordable in driver (L34) | true | `llm.py:9-17` needs the loaded config. `thinking.py:40-45` needs Ollama up at localhost:11434. |
| `pr.run(q, model, out, 3, "ce", True)` (L35) | true, but `out` must be a `Path` | `run.py:602`; `out.mkdir` at `:614`. |
| `pr.run` writes `src_*.md`, `evidence.json` (spans), `metrics.json` into `out` | true | `run.py:639, 709-710, 724-728`. `metrics.json` has `wall_s` and `gather.empty_searches`. |
| app.py command (L35-36) | **false as written** | Without `--prompt`, `tui.py:2947-2953` starts the interactive TUI (B1). |
| `python <wt>/src/app.py` with cwd=<wt> runs the worktree's code | true | The script dir goes first on `sys.path`. Needs `sys.executable` (venv). |
| Report dir location | **unfixed** | `<workspace.dir>/<slug>_<sha6>_<ts>` (`tui.py:2041-2057`). `workspace.dir` is `./research_output`, resolved against the cwd (`config.py:143`), so with cwd=<wt> the reports land inside the worktree (MJ1). |
| `evaluate_run.source_texts` gives report-dir-first, evidence fallback, URL->file (L74) | partly | `evaluate_run.py:51-56` takes one dir and returns `{basename: text}`. There is no URL mapping. That means two calls, plus `pipeline.bridge.source_filename(url)` (`bridge.py:12-14`) (minor 11). |
| `evidence.json` spans give quote offsets (L75) | true, but unreliable for Phase 0 | `spans[facet][url][raw_quote]`. The overwritten `cmp2_ornith_*` evidence does not belong to the reports that cite it, and spans are keyed by the raw quote while the report renders `_PAGE_REF`-stripped text (`writer/compiled.py:79,399`) (MJ2). |
| `_strip_notes`, `norm_fig`, `score` needs `_run_state.json` (L69, L71, L130) | true | `evaluate_run.py:36-38, 24/46-48, 41-43`. |
| Think sites `run.py:183,208,223,473`, writer `tui.py:2164` (L125-128) | true | `:517` (extract) sends no think. The writer model is `config.cfg["api"]["openai_model"]` (`tui.py:2162`). |
| `settings.rag_cache.enabled` (L118-119) | true | `config_template.yaml:696-698`. |
| `w8b_ab.py` does not import at HEAD; `:37` args; W4 redo rule | true | `w8b_ab.py:21` (`pr.scholar_search` gone), `w4_retrieval.py:53,65`. |
| `w8b_runs.tsv` column 5 = report dir (L151) | true | Column 4 = evidence dir. |
| 6 W8b `acad` reports (L153) | **false** | 4 rows. |
| Worktree lacks a needed gitignored file | no | No dotenv, models in HF cache, venv outside the tree, browser env-driven. The only hazard is the relative workspace dir (MJ1). |
| Cost GPU hours (L139-141) | true | 32, 96 and 192 runs x 250 s = 2.2, 6.7 and 13.3 h (about 2.4, 7.3 and 14.7 h with redos). |

---

## 3. New issues

### Blocker

#### B1. The app.py command runs the interactive TUI, not a headless run - Severity: Blocker
* **Reviewer Lens:** `egm-implementation-readiness` (verified by synthesizer)
* **Affected Scope:** L35-36
* **Discrepancy Description:** `tui.py:2947-2953` takes the headless path (`run_cli`) only when `--prompt`, `--prompt-file` or `--resume-run` is given. Otherwise it starts `BasicTuiAgent(...).run()`, which under a subprocess hangs or crashes and writes no report. The prompt also sets the report title and the dir slug (`tui.py:2163`). w8b passed `--prompt` and `--auto-approve` (`w8b_ab.py:40-41`).
* **Rationale & Recommendation:** `[sys.executable, "<wt>/src/app.py", "--config", cfg, "--prompt", q, "--auto-approve", "--pipeline-evidence", str(out), "--writer-mode", "compiled", "--style", "standard"]`, with `q` byte-identical to the `q` given to `pr.run`.

### Majors

#### N1. The injection is under-specified - Severity: Major
* **Reviewer Lens:** `egm-design`
* **Affected Scope:** L104-105, L107-108
* **Discrepancy Description:**
  - `p_part` is undefined.
  - `delta / (1 - p_part)` exceeds 1 whenever p_part > 1 - delta (p_part = 0.75 with delta ≥ 0.30). It is undefined at p_part = 1.
  - The arm that gets the injection is not named.
  - "Smallest realised delta on {0.05, ...}" mixes the nominal grid with a continuous outcome.
  - A coder who uses the nominal delta, or who leaves the probability uncapped, gets a different MDE near the 0.30 comparison bar.
* **Rationale & Recommendation:** "p_part = ANSWERED share of that part over the query's 4 dev runs (failed runs = MISSING). Arm B resamples only. Flip probability = min(1, delta / (1 - p_part)); skip if p_part = 1. Realised delta per rep = arm B target ANSWERED share after injection minus before. MDE = mean realised delta at the smallest nominal grid point with power ≥ 0.8."

#### N2. The MDE at N_comparison 8 and 12 cannot be computed as written - Severity: Major
* **Reviewer Lens:** `egm-design`
* **Affected Scope:** L103-104, L108
* **Discrepancy Description:** "Queries resampled with replacement to the test size" is unstratified. Dev has 3 comparison queries out of 8, so a simulated set holds Binomial(16, 3/8) comparison queries, never a fixed 5, 8 or 12.
* **Rationale & Recommendation:**
  - "Stratified: N_comparison slots drawn from the 3 dev comparison queries, the 16 + N_comparison - 5 - N_comparison = 11 other slots from the 5 dev non-comparison queries."
  - State the ceiling: the comparison MDE rests on 3 distinct queries (6 parts).

#### N3. The run resample understates variance near the bars - Severity: Major
* **Reviewer Lens:** `egm-design`
* **Affected Scope:** L103, L109
* **Discrepancy Description:** Resampling runs with replacement from 4 observed runs uses the plug-in variance ((n-1)/n = 0.75 of the sample variance). The SE of T is therefore about 13% too small, and so is the MDE. Parts observed at 0/4 or 4/4 also look deterministic. Near the bars, whether the plan reads go or no-go depends on this.
* **Rationale & Recommendation:** One sentence: "Go/no-go applies to MDE x 1.15 (sqrt(4/3)), i.e. bars ≈ 0.13 all-parts, 0.26 comparison." The alternative is to read any MDE within 15% of a bar as a no-go.

#### N4. Phase 0 comparison parts are not in the shape the rubric grades - Severity: Major
* **Reviewer Lens:** `egm-design`
* **Affected Scope:** L65-66, L148, L151-157
* **Discrepancy Description:** The W8b comparison parts (`PLAN_W8b.md:37-41`) are "comparison of the evidence", with no sides or measure M. The rubric needs "X vs Y on measure M" (L52-53, L65). The plan never says that the Phase 0 parts are rewritten into the `queries.yaml` schema. If they are not, the 13/15 bar validates a different rule from the one A/Bs use.
* **Rationale & Recommendation:**
  - Add to L148: "Phase 0 parts are written in the queries.yaml schema (comparison parts with sides and measure, at most two per query) and committed with the Phase 0 references, before any label is seen."
  - Report the number of distinct comparison parts next to 13/15. There are about 5-7, so the 15 items are clustered.

#### MJ1. Run outputs land inside a temporary worktree - Severity: Major
* **Reviewer Lens:** `egm-implementation-readiness`
* **Affected Scope:** L35, L129
* **Discrepancy Description:**
  - `out` is not fixed.
  - `workspace.dir: ./research_output` resolves against cwd=<wt> (`config.py:143`), so every report dir lands in `<wt>/research_output/`. Removing the worktree deletes the reports that packets, hashes and re-labelling depend on.
  - The report dir has to be found somehow: the w8b listdir diff, or the log.
* **Rationale & Recommendation:**
  - (a) Each arm config sets `settings.workspace.dir` to the absolute `<repo>/research_output`, and run.py asserts that it is absolute.
  - (b) `out = <repo>/research_output/e1_<set>_<q>_<arm>_r<k>_a<attempt>`, absolute, with the app log at `<out>.app.log`.
  - (c) report_dir = the "Run folder:" path from that log after stripping ANSI (`\x1b\[[0-9;]*m`). If there is no `final_report.md` there, the attempt counts as "no report".
  - (d) Worktrees are removed only after runs.tsv is written.

#### MJ2. The spans lookup shows the wrong text for Phase 0 reports - Severity: Major
* **Reviewer Lens:** `egm-implementation-readiness`
* **Affected Scope:** L74-75
* **Discrepancy Description:**
  - The `cmp2_ornith_c*` evidence dirs were overwritten, and the `cmp2pres_ornith_*` reports loaded them. For those reports the evidence dir's spans and `src_*.md` belong to a different run. The excerpt windows would silently show text the report never saw, which corrupts the "cited saved source states it verbatim" rule.
  - Spans are also keyed by the raw quote, not the rendered one.
* **Rationale & Recommendation:** This is a cut. Drop the `evidence.json` spans path and locate each rendered quote with the existing `pipeline.run.quote_span(quote, src_text)` (`run.py:88`) in the report dir's copy of `src_*.md`. Use the evidence dir only if the report dir lacks the file. Packets then no longer need the evidence dir.

### Minors

1. **Cost table counts (L13, L133-142).**
   - Labelling contexts: one report packet is about 50k characters (≈10 cited sources, up to 6k each), so about 3 reports fit per 150k context. That gives Phase 0 ≈ 40 contexts (60 reports x 2 passes), Phase 1 ≈ 11, A/B k=3 ≈ 32 and the ceiling ≈ 64, against ~10/4/10/15 in the table. Derive the count as reports / 3 and state that it follows from the fixed 6k rule. Trimming excerpts to fit fewer contexts is not allowed.
   - User judgements: new-set reference checks are ≈ 36 (16 comparison + 25% of ≈ 80 others), not 30. Phase 0 reference checks ≈ 12, and the plan should state that L58 applies to them. The before-freeze total is ≈ 85-90.
   - Redraw: "16 queries (~64 refs)", not "~16 refs".
   - `latest` re-verification (L55) has no cost row. Add one, or cut the mechanism (section 5).
   *(zoom-out)*
2. **A report over the 150k cap (L78).** Add: "a report is never split; one over the cap goes alone, excerpts unchanged." *(zoom-out)*
3. **Phase 0 labelling scope (L149-160).** State that all frame reports are labelled in both passes, as "over all Phase 0 labels" (L160) implies. *(zoom-out)*
4. **Acad count (L153).** 4, not 6. *(design, synthesizer)*
5. **Chosen design (L109).** "The smallest k in {2,3,4} at N_comparison = 5 meeting both bars; else k = 4 with the smallest N_comparison in {8,12}; else stop." *(design)*
6. **Redo outcome (L89, L121).** "Failed" = no report or app_rc ≠ 0. An attempt 2 that still has >25% empty searches is labelled as is. The last attempt is always the one used. Delete L121's repeat. *(design)*
7. **k = 4 order (L120).** "ABBA for even k" does not fix k = 4. Write ABBABAAB. *(design)*
8. **Ship rule (L86-94).** Write it as one line: "Ship = p < 0.05 AND guard met AND T ≥ the A/B plan's floor." *(design)*
9. **Pass 1 vs pass 2 kappa (L160).** At a low ANSWERED rate, binary kappa can fail at about 95% raw agreement and burn the one rubric redo. Report PABAK or positive agreement next to it. *(design)*
10. **Bootstrap (L86).** If kept: "runs resampled within query and arm, 10,000 reps." It is cut in section 5. *(design)*
11. **URL to file (L73-74).** Use `pipeline.bridge.source_filename(url)` on the URL taken verbatim from the report's `N. [title](url)` line, parsed to the last `)`. Do not use `evaluate_run`'s `num2url`: its `.rstrip("/")` (`evaluate_run.py:86`) changes the sha1. Alternatively, read the ordered "(saved as src_….md)" lines in `findings.md` (`compiled.py:426-427`). *(readiness)*
12. **Driver contract (L32-35).**
    - Name the entry point (e.g. `run.py --driver <wt> <cfg> <q> <out>`).
    - The driver writes `<out>/arm_meta.json` = {model_options, minimal_think, pr_file} and asserts that `pr.__file__` is under `<wt>/src/`.
    - Driver rc ≠ 0 counts as a failed attempt, and app.py is skipped.
    - The parent reads `searches`/`empty_searches` from `<out>/metrics.json` `gather`.
    - Ollama must be up when `minimal_think` is recorded.
    *(readiness)*
13. **`Path(out)`, `sys.executable` (L35).** `pr.run` crashes on a str (`run.py:614`). Both subprocesses use the venv interpreter. *(readiness)*
14. **Model assert (L34).** Assert on the loaded `config.cfg["api"]["openai_model"]`, which includes the `OPENAI_MODEL` env overlay that also reaches the writer, not on the YAML. *(readiness)*
15. **Phase 0 frame globs (L149-151).** "The c2/c3 comparison runs" matches 24 `cmp_*`/`cmp2_*`/`cmp2pres_*` logs plus 4 `w8_c2_*` logs. Name the globs in the plan, so the frame is not picked while looking at outputs. *(readiness)*
16. **Commit fields (L124).** A worktree is always clean. Record the arm commit, plus the grader repo's HEAD and dirty flag separately. *(readiness)*
17. **`settings.pdf_engine` (L118-119).** PDF render time is inside writer seconds (the user config has weasyprint). Record it alongside `rag_cache.enabled`. *(readiness)*
18. **Sensitivity scenarios break the run correlation (L110).** Fixed-rate targets are independent draws again. Cut them (section 5). *(design)*
19. **Stale A/A wording (L114).** "Simulation input" is no longer true, because the generator uses all 4 runs. Cut it (section 5). *(design)*
20. **sign_p parenthetical (L92).** "12/16 if none tie" is wrong for the comparison target set (5 queries). Cut it (section 5). *(design)*
21. **`.gitignore` not committed.** Commit `.gitignore:33` with the plan. *(readiness)*
22. **Report-format blindness (L76-77).** Unchanged from v3, fine. No action needed; listed only so it is not re-raised. *(synthesizer)*

---

## 4. Code-claim errors in the plan, summary
- L35-36: app.py command lacks `--prompt`/`--auto-approve` (B1).
- L74: `source_texts` does no URL mapping and no fallback (minor 11).
- L153: 4 acad reports, not 6.
- Everything else checked holds (section 2).

---

## 5. Cuts (no effect on the decision, the go/no-go or the Phase 0 bar)
1. **L125-128**, the think-resolution parenthetical. Replace it with "resolved think value (`minimal_think(model)`, once per run)". The code documents the semantics.
2. **L117-119**, the Instrument bullet. It repeats L35-36. Move `rag_cache.enabled` (and `pdf_engine`) into the runs.tsv fields.
3. **L121**, the W4 redo sentence. It repeats L89-90. Add `empty_searches` to the runs.tsv fields.
4. **L92**, secondary metrics:
   - Cut `sign_p`, a weaker test on the same data, together with L38-39 ("Copies `sign_p`...").
   - Cut `figs_unsupported`, which is outside E1's goal, together with L130.
   - Keep the WRONG rate, which comes free from the same labels.
5. **L86-87**, the descriptive bootstrap bound: a second resampling path with no role in the decision. Report observed T.
6. **L110-111**, the sensitivity scenarios. The go/no-go uses the empirical rates (minor 18).
7. **L114**, the A/A drift check: reported, never compared, and no decision depends on it. Shorten L112-113 to "permutation exchangeability replaces A/A pairs; update Whitepaper §10 at doc sync."
8. **L164-165**, the provisional variance/MDE from the W8b frozen-planner pairs. Phase 1 replaces it, and L165 admits the bias. Keep the answer, comparison and tie rates.
9. **L160-161**, the Phase 0 "Reported:" list. Keep the per-class counts and the distinct comparison parts (N4). Drop the bootstrap CIs, 3-class kappa and per-part agreement.
10. **L74-75**, the `evidence.json` spans path (MJ2).
11. **L174-175**, "Out of scope". Spanish is at L46, model selection is user decision 1, and "no shipped change" is at L172.
12. **Historical parentheticals:**
    - L30-31 "which no longer imports at HEAD because…" (keep "copy, do not import").
    - L33 "(w8b passed the config as a bare argument…)".
    - L39-40, the reasons `blind.py` is not reused.
    - L69, the `norm_fig` gloss.
13. **Optional: L54-55, the `latest` mechanism.** Time-anchored wording already fixes the answer. Drop `latest` and `as_of` (and their mentions at L28/L72), and grade a newer dated value as WRONG-period. Only news_recent (0/1 query) depends on it, and it also removes minor 1's missing cost row.

These cuts remove about 25-30 lines.

---

## Net effect if applied
- **B1, MJ1, MJ2, minors 11-16:** run.py and score.py can be coded and run headless. Reports persist outside worktrees, and packets show each report's own source text.
- **N1-N3:** the power go/no-go is computable at every grid point and no longer optimistic about variance.
- **N4:** Phase 0 validates the same comparison rule that A/Bs use.
- **Cuts:** the plan is shorter by roughly a sixth, with no loss to any decision.
