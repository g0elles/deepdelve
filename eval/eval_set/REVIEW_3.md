# Goldfish Review Report: E1 fixed evaluation set, review 3

**Target:** `eval/eval_set/PLAN.md` (L1-138, draft v3 2026-11-24, including the Runs think-line correction), checked against `REVIEW_2.md`

## Reviewer Status

| Reviewer | Lens | Status |
| --- | --- | --- |
| `egm-design` | power, decision rule, validity | completed |
| `egm-implementation-readiness` | runnability, code claims | completed |
| `egm-zoom-out` | scope, reuse, cost | completed |

**REVIEW_2 status:** 24 resolved, 6 partly, 0 not.
**New issues after deduplication:** 0 blockers, 10 majors, 24 minors.

The run model, the labeller and the user's effort are user decisions, so none of them is counted as a blocker. None of the new issues stops the plan from being coded or from producing a valid decision. The majors are gaps in the spec that would otherwise be decided ad hoc while coding, or that would bias the power go/no-go.

The synthesizer checked these claims directly in the code: the CLI flags, the run-dir layout, `_strip_notes`, `source_texts`, the think resolution, the `cmp*` frame and the config loading (M1).

---

## 1. REVIEW_2 findings

| ID | Status | Notes (PLAN v3 lines) |
| --- | --- | --- |
| NB1 go/no-go unreachable | resolved | L76-77: the decision rests on p alone and the floor only governs reporting. L94: MDE thresholds in rate units. Residue: no action is stated for "p < 0.05 but T below the floor" (minor 1). |
| NB2 test/sim not codable | partly | L73-75 pin the statistic, unit, estimator, seed and direction. Open: the simulation has two generators (Bernoulli at L88, bootstrapped runs at L93), parts are treated as independent within a run, and the baseline that governs the go/no-go is not named (M6, M7). |
| NB3 blind cut | partly | L34-36 and L64-67 specify new code and drop `blind.py`. But the source path is wrong (`sources/` does not exist in these runs), the "around the cited quote" window is undefined, and packet contents and size are unspecified (M4, M5). |
| NM1 comparison power | resolved | L46-47 give two comparison parts per comparison query. L92-93 simulate the two target sets separately. The grid's unit is ambiguous (minor 3). |
| NM2 unbounded no-go | partly | L94-96 set a ceiling, one re-freeze and a stop rule. Missing: the split the added queries go to, and the cost at the ceiling (M10). |
| NM3 run.py template | partly | L29-33 give the CLI, arm definition, redo logic and runs.tsv. Missing: how a commit or worktree arm executes (M2). Also, the pipeline does not read the arm config (M1). |
| NM4 failed runs | resolved | L78-79. The maximum attempts per slot are unstated (minor 5). |
| NM5 writer/config fixed | resolved | L100-101. Caveat M1. |
| NM6 latency guard | resolved | L81, L108. No measurement method is given (minor 15). |
| NM7 decision table | resolved | L59-62, L26. Minor 22. |
| NM8 labeller | resolved | L10-11, L34, L37, L68-69. The labels file has no path (minor 24). |
| NM9 Phase 0 frame | partly | L118-123 exclude c4, allow 2-part queries, cap gold comparison items and define a part-label. But the frame globs point at log files and evidence-only dirs, and they mix models (M3). |
| NM10 A/A role | resolved | L97. |
| Nm1 target by type | resolved | L72. Minor 2. |
| Nm2 cost | resolved | L111. The arithmetic checks out (32 x 250 s = 2.2 h; 16 x 6 x 250 s = 6.7 h). Scope is incomplete (M10). |
| Nm3 leakage hosts | resolved | L84-85. Scope residue in minor 8. |
| Nm4 ABBA order | resolved | L102. |
| Nm5 redo asymmetry | resolved | L79. |
| Nm6 ref schema | resolved | L26-27. |
| Nm7 sign test | resolved | L80. |
| Nm8 free prior | resolved | L128-129. See minor 23 on how independent the pairs are. |
| Nm9 user effort | partly | L12-13 give the before-freeze total. Not given: recurring effort per A/B (L49 `latest` re-verification, L83 spot-checks) and per test redraw (M10). |
| Nm10 after test burns | resolved | L21-22. The "re-run Phase 1 only if dev changed" clause can never fire, because dev does not rotate (L45) (minor 13). |
| Nm11 seeds | resolved | L113-114. |
| Nm12 provenance | resolved | L37, L108-109. Each run has two dirs (M3). |
| Nm13 hardware | resolved | L110-111. |
| Nm14 thinking | resolved | L105-108. **REVIEW_2's premise was wrong:** the pipeline does not ignore the thinking-off rule. `call_json` (`src/pipeline/llm.py:28-29`) resolves `"minimal"` through `utils/thinking.py:minimal_think` (L40-45). That returns `False` for boolean-control models such as ornith-1.5:9b (bool kind, `thinking.py:33-34`), the lowest level for level models (gpt-oss), and nothing for models without a control. The plan's corrected wording is accurate. Residue: minor 17. |
| Nm15 cache method | resolved | L104. Scope is too narrow (minor 19). |
| Nm16 kappa CI | resolved | L125-126. The bar uses the wrong label space (M8). |
| Nm17 quota | resolved | L42-43. Dev 3+1+1+1+1+1 = 8. Test 5+2+2+2+2+1+1+1 = 16. |

---

## 2. Code claims checked

| Claim (PLAN line) | Verdict | Evidence |
| --- | --- | --- |
| `--writer-mode compiled`, `--style standard` (L100) | true, but the parser is not in `src/app.py` | `app.py` has no argparse. `app.start()` calls `engine.tui.cli_main`. The flags are at `src/engine/tui.py:2866` (`--style` standard/academic/answer) and `tui.py:2880` (`--writer-mode` agents/compiled). w8b used both (`w8b_ab.py:40-41`). |
| `evaluate_run._strip_notes` (L64) | true | `scripts/evaluate_run.py:36-38` calls `utils.reader_notes.strip_block`. It removes only the reader-notes block, so the other strips must be new code, as L64-65 already say. |
| Source text in the run dir's `sources/` (L66) | **false for these runs** | Evidence-only runs store sources as `src_<sha1(url)[:8]>.md` at the root of both the pipeline dir (`src/pipeline/run.py:639`) and the report dir. `sources/` exists only in old free-form agent runs. `evaluate_run.source_texts` (L51-56) already reads both layouts, so reuse it. |
| `think="minimal"` at `run.py:183, 208, 223, 473` (L106) | true | These are the only think call sites in `src/pipeline`. `run.py:517` (`extract`) sends no think. The compiled writer has another site the plan omits (`tui.py:2164`, standalone `writer/compiled.py:451`). |
| Phase 0 frame `research_output/cmp_*`, `cmp2_*`, `cmp2pres_ornith_c2_*`, `c3_*` (L118) | **misdescribed** | Under those names, `research_output/` holds only `*.pipeline.log`/`*.app.log` files and evidence-only pipeline dirs (src_*.md, evidence.json, metrics.json, no `final_report.md`). The reports live in query-named timestamped dirs, named by the "Run folder:" line of each `.app.log`. The globs also catch the gemma4, qwen2.5 and c1 runs. `cmp2pres_ornith_*` loaded the `cmp2_ornith_*` evidence (`cmp2pres_ornith_c2_r1.app.log:10`). c4 is Spanish. |
| `w8b_runs.tsv` lists 36 runs (L119) | true | 36 rows. Column 4 is the evidence dir, column 5 the report dir. The 6 `acad` rows ran `--style academic`. |
| `w8b_ab.py:37` args, and `w8b_ab.py` does not import (L29, L101) | true | `pr.run(QUERY[q], MODEL, ..., 3, "ce", True)`. L21 reads `pr.scholar_search`, which no longer exists in `run.py`. |
| W4 redo rule | true | `w4_retrieval.py:53` (`empty_searches > 0.25*searches`), with the redo at `:65`. |
| `PLAN_W8b.md:20` 250 s/run, `:55` "differed by 6" (L17, L112) | true; the 6 needs a unit | ~2.5 h for 36 runs. The 6 is a count of distinct papers, not parts (minor 18). |
| Writer seconds recorded anywhere | no | `metrics.json` `wall_s` covers the pipeline only (`run.py:721`). w8b `secs` times `pr.run` only (`w8b_ab.py:36-38`). |
| Disk caches in `src/pipeline` | none | The `lru_cache` at `run.py:370` and `tune.py:14` `_cache` are in-memory. A disk cache does exist elsewhere: `rag_cache` at `~/.deepdelve/rag_cache.json` (minor 19). |
| `_private/` gitignored (L38) | not yet | `.gitignore` has no `eval_set` entry (minor 20). |

---

## 3. New issues

### Blockers
None.

### Majors

#### M1. The pipeline half of each run does not use the recorded config - Severity: Major
* **Reviewer Lens:** `egm-implementation-readiness` (verified by the synthesizer)
* **Affected Scope:** L30-31, L100-101, L105
* **Discrepancy Description:** `config._CONFIG_PATH` comes only from `--config`/`-c` in `sys.argv`, otherwise `~/.deepdelve/config.yaml` (`src/config.py:6-18`), and it is read once at import. `pipeline.llm.model_options` reads `config.cfg` (`llm.py:9-17`). w8b passes the config as the bare `sys.argv[1]` (`w8b_ab.py:56`), so the in-process `pr.run` used the default config's `model_options`, and only the writer subprocess got `--config`. The recorded config sha256 therefore describes only the writer. An arm that differs by config would differ only in the writer.
* **Rationale & Recommendation:** Before each arm, set `config._CONFIG_PATH = <arm cfg>` and call `config.load_config()`, or run the pipeline in a subprocess with `--config` (M2). After loading, record `model_options(model)` and `minimal_think(model)`. Assert that the config's `api.openai_model` equals the pipeline model, because the writer takes its model from the config (`tui.py:2162`).

#### M2. It is not said how a commit or worktree arm executes - Severity: Major
* **Reviewer Lens:** `egm-implementation-readiness`
* **Affected Scope:** L30-31
* **Discrepancy Description:** `from pipeline import run as pr` always binds to the checkout that `run.py` lives in, so "arm = commit or worktree" has no mechanism.
* **Rationale & Recommendation:** Specify the mechanism:
  - `git worktree add <tmp> <commit>`.
  - A small driver in `eval/eval_set` runs the pipeline in a subprocess. It puts `<wt>/src` on the path, includes `--config <cfg>` in `sys.argv` before importing config, calls `pr.run(q, model, out, 3, "ce", True)`, and writes `metrics.json`.
  - Then `<wt>/src/app.py` runs with cwd=<wt> and an absolute `--pipeline-evidence` path.
  - Record the worktree commit, not HEAD.
  - Zoom-out would drop the in-process patch arm entirely, since a subprocess arm covers every A/B (minor 14).

#### M3. Each run has two dirs, and the Phase 0 frame points at the wrong ones - Severity: Major
* **Reviewer Lens:** `egm-implementation-readiness`, synthesizer
* **Affected Scope:** L108-109 (`run_dir`), L118-120
* **Discrepancy Description:** The pipeline writes an evidence dir. `app.py` creates a separate report dir, which w8b finds by diffing `listdir` (`w8b_ab.py:39-45`). `evidence.json` sits in the first, and `final_report.md`/`_run_state.json` in the second. The `cmp*` names in L118 match only log files and evidence-only dirs, and they span three models.
* **Rationale & Recommendation:**
  - Split `run_dir` into `evidence_dir` and `report_dir` in runs.tsv, and hash each file from its own dir.
  - For Phase 0, resolve report dirs from each `<name>.app.log` "Run folder:" line. Do not use `eval/comparison_runs/runs_v2_ornith_presence1.5.tsv`, which names `cmp2_ornith_c*` dirs that were later overwritten.
  - State whether the frame is ornith-only, or deliberately includes gemma4/qwen2.5 reports. Mixed models are acceptable for grader validity, but say so.

#### M4. The source excerpts in the packet: wrong path, no window rule - Severity: Major
* **Reviewer Lens:** `egm-implementation-readiness`, `egm-zoom-out`
* **Affected Scope:** L64-66
* **Discrepancy Description:** `sources/` does not exist in these runs (§2), so packets built as written would carry no source text, and the "cited saved source states it verbatim" rule (L57-58) could not be applied. One [N] often carries several quotes (one checked report has 4 under [8]), so "around the cited quote" is ambiguous.
* **Rationale & Recommendation:**
  - Read sources via `evaluate_run.source_texts`: the report dir's `src_*.md` first, then the evidence dir as a fallback.
  - Map [N] to the Nth URL in the Sources/References list, then to `src_{sha1(url)[:8]}.md`.
  - Window: for each quote the report renders under [N], find its offset (from `evidence.json` `spans[facet][url]`, `run.py:709-710`, or a string search), merge ±1,500-character windows, cap at 6,000 characters, and use the first 6,000 characters when no quote is found.

#### M5. Packet contents and size are unspecified - Severity: Major
* **Reviewer Lens:** `egm-implementation-readiness`, `egm-zoom-out`
* **Affected Scope:** L64-69
* **Discrepancy Description:** To tell ANSWERED from WRONG, the labeller needs each query's parts and refs (statement, value, alt_values, `latest`/as_of) and the allowed source classes. None of these is listed. Packets are capped only at "≤ 10 reports". At about 6 to 15 cited sources x 6,000 characters, 10 reports can reach 360k characters or more (about 90k tokens), and labelling accuracy degrades well before the context limit.
* **Rationale & Recommendation:**
  - Packet = rubric sha, then per report: blind id, query text, parts with refs, the stripped report, and the source excerpts.
  - Keep the "Not covered" section, because a disclosed gap counts as MISSING, and keep the evidence quote bullets.
  - Cap each context by characters (for example ≤ 150k), not only by report count.
  - Test packets go only to `_private/`.

#### M6. The power simulation mixes two generators and ignores correlation within a run - Severity: Major
* **Reviewer Lens:** `egm-design`
* **Affected Scope:** L88, L90, L93
* **Discrepancy Description:**
  - Parts of one run share retrieval, and a failed run sets every part to MISSING. Independent per-part Bernoulli draws therefore understate the variance of T, so the MDE looks better than it is.
  - L88 (Bernoulli rates) and L93 (bootstrapped runs) are different generators.
  - Rates estimated from 4 runs sit on {0, 0.25, ..., 1}, so additive injection capped at 1 delivers less than the nominal delta.
* **Rationale & Recommendation:** Resample whole observed dev runs within each query, which keeps the correlation. Inject per run by flipping each non-ANSWERED target part to ANSWERED with probability delta / (1 - p_part). Report the realised mean delta and define the MDE on it. A parametric model with a run-level latent factor is the alternative.

#### M7. The go/no-go does not name its baseline - Severity: Major
* **Reviewer Lens:** `egm-design`
* **Affected Scope:** L89 vs L94
* **Discrepancy Description:** By a normal approximation, the comparison bar (≤ 0.30, 10 comparison parts) passes at k=3 with a 0.3 baseline (MDE ~0.29) and fails with a 0.5 baseline (~0.32). The all-parts bar (≤ 0.15) is reachable (~0.14 at k=2, ~0.12 at k=3). Because L89 runs three sensitivity baselines and L94 does not say which one governs, the same data can give either verdict.
* **Rationale & Recommendation:** The go/no-go uses the empirical dev rates, and 0.1/0.3/0.5 are reported as sensitivity only, run at the chosen design rather than the whole grid (minor 9). Alternatively, require the worse of the empirical rates and 0.5. State how a sensitivity scenario is built: target parts set to the scenario rate, the other parts left at their empirical rates.

#### M8. The Phase 0 kappa bar uses 3 classes while the decision is binary - Severity: Major
* **Reviewer Lens:** `egm-design`
* **Affected Scope:** L122-126
* **Discrepancy Description:**
  - T counts ANSWERED versus not (L73). Kappa over 3 classes can pass or fail on WRONG/MISSING disagreements that never affect T.
  - At the W4 floor (0/36 comparison answers), the up-to-15 comparison gold items may all be MISSING. Raw agreement would then clear 85% trivially, kappa would suffer the prevalence paradox, and the comparison grader would never be tested on positives.
* **Rationale & Recommendation:**
  - Make the primary bar binary: ANSWERED versus not, kappa plus raw agreement. Report 3-class kappa only.
  - Fix the comparison stratum at exactly 15 items, and add a floor on comparison-only binary agreement (for example ≥ 13/15).
  - If fewer than about 3 comparison gold items are ANSWERED, record that the comparison grader is unvalidated on positives.

#### M9. Phase 1 has no labelling step, but the simulation needs labels - Severity: Major
* **Reviewer Lens:** `egm-zoom-out`
* **Affected Scope:** L88 vs L131-134
* **Discrepancy Description:** The noise model takes part labels from all 32 dev runs (L88). Phase 1 lists only runs and outputs: no labeller, batch, rubric sha or spot-check. The A/A drift check (L97) needs the same labels.
* **Rationale & Recommendation:** Add to L133: "32 dev reports labelled as one shuffled batch (pass 1, char-capped contexts, rubric sha at freeze); the user spot-checks N."

#### M10. The cost is stated only for the cheapest case - Severity: Major
* **Reviewer Lens:** `egm-zoom-out`, `egm-design`
* **Affected Scope:** L21-22, L49, L82-83, L94-96, L111
* **Discrepancy Description:**
  - L111 gives k=3 on 16 queries (6.7 h). The L95 ceiling allows k=4 with 8 added queries: 24 x 8 x 250 s ≈ 13.3 h for one A/B. REVIEW_2 set that ceiling to keep an A/B under ~7 h, and v3 kept the ceiling but dropped the envelope.
  - Not costed: redo and re-run runs; labelling contexts (about 10 per A/B, 4 for Phase 1, 10 for Phase 0); `latest` re-verification per A/B; and each test redraw every 2 A/Bs (about 16 new references plus about 23 user checks).
  - The split the added queries go to is not named.
* **Rationale & Recommendation:**
  - Add one cost table. Rows: Phase 0, Phase 1, an A/B at the chosen k, an A/B at the ceiling, a redraw. Columns: GPU h, labelling contexts, user judgements.
  - Either pin the ceiling to "each A/B ≤ ~8 h sequential" or state 13.3 h as the worst case.
  - State that added queries are test comparison queries, so the test sha256 is re-recorded at `evalset-v1.1`.

### Minors

1. **Below-floor action (L76-77).** State it, for example "significant but below the practical floor: do not ship unless the A/B plan says otherwise". *(design)*
2. **Comparison parts are confined to comparison queries (L26, L46, L92).** Exactly 2 per `cat=comparison` query, so "N_comparison" and the part count agree. *(design)*
3. **Grid unit (L93).** Write "comparison queries (2 parts each): 5, 8, 12", with N_total = 16 + (N_comparison - 5). Reaching 12 uses 7 of the 8 added slots. *(design, zoom-out)*
4. **"Cheapest" vs "raise k first" (L95).** These can disagree: k=4 with N=5 is 128 runs, while k=3 with N=8 is 114 runs. Make the rule lexicographic (k up to 4, then queries), or count reference cost in "cheapest". *(design)*
5. **Maximum attempts per slot (L78 vs L103).** State how the W4 redo and the failed-run re-run combine, and that redos keep their ABBA slot. *(design)*
6. **Test burn (L82-83).** Two decisions at alpha 0.05 give family-wise error up to ~0.10. Accept and state it, or use 0.025. Say whether a no-go A/B counts toward the burn. *(design)*
7. **Bootstrap CI (L76).** Label it descriptive: it generalises over queries, while the permutation test is conditional on them. Report a one-sided 95% bound to match the test. *(design)*
8. **Leakage check (L84-85).** `evalset-v1..HEAD` misses the working tree and worktree-arm commits. Use `git diff evalset-v1 -- src/` plus `git diff evalset-v1 <arm commit> -- src/`, also scan each arm's config, and normalise case and whitespace. Paraphrased leakage is a stated ceiling. *(design, readiness)*
9. **Simulation size (L91-93).** The full grid is about 1.7e9 permutations. Say "vectorised", or use 1,000 permutations inside the simulation, and run the baseline sensitivity only at the chosen design. *(zoom-out)*
10. **Whitepaper §10 says future plans "should use more A/A pairs" (Whitepaper.md ~L344).** State why permutation exchangeability replaces A/A pairs inside A/Bs, or update §10 at doc sync. *(zoom-out)*
11. **Cite Roadmap obstacle 7** (the labelling bottleneck) at L10-13 as the reason for the Claude labeller. *(zoom-out)*
12. **Probe-first rule (CLAUDE.md).** It asks for 2 or more models. State that it does not apply to the labeller, or run pass 2 on a second labeller model. *(zoom-out)*
13. **Dev's role.** State in one line that dev is for calibration and iteration only, never for decisions. Delete the dead "re-run Phase 1 only if dev changed" clause (L22). *(zoom-out)*
14. **In-process patch arm (L30-31).** It is a second arm mechanism with its own restore risk. Drop it until a pipeline-only A/B needs it, or keep it only with M2's subprocess path as the default. *(zoom-out; readiness would keep it)*
15. **Seconds measurement (L81, L108).** Pipeline seconds = wall time around the pipeline call (or `metrics.json` `wall_s`). Writer seconds = wall time around the `app.py` subprocess, including completion checks and PDF render, as `eval/comparison_runs/runs.tsv` did. *(readiness)*
16. **Flag citation.** Cite `src/engine/tui.py:2866, 2880` rather than `src/app.py`. *(readiness)*
17. **Think sites (L106-108).** Add the compiled writer's site, `tui.py:2164`. The value is one call, `minimal_think(model)`, the same for every stage: record it once per run with the stage list, not per stage. *(readiness, synthesizer)*
18. **L17, "differed by 6".** The 6 counts distinct papers (`PLAN_W8b.md:55`), not parts. *(readiness)*
19. **Cache check (L104).** Grep all of `src/`, not only `src/pipeline`, because `rag_cache` (`~/.deepdelve/rag_cache.json`, `config_template.yaml:696-698`) sits outside the pipeline. Record `settings.rag_cache.enabled` per arm config. *(readiness)*
20. **`.gitignore`.** Add `eval/eval_set/_private/` before the first packet is written. Packets hold fetched web text. *(readiness)*
21. **`evaluate_run.score` for figs_unsupported (L80).** Pass it the report dir: `_run_state.json` exists only there. *(readiness)*
22. **Comparison refs (L26).** State that `statement` holds the expected relation (with `value: null`). *(readiness)*
23. **Phase 0 heterogeneity (L118-119, L128-129).** The 6 W8b `acad` reports are academic style ("References"). The `cmp2pres_ornith_*` reports reuse the `cmp2_ornith_*` evidence, so a cmp2/cmp2pres pair is a writer-only replicate. Do not use such pairs in the provisional variance estimate. *(readiness, synthesizer)*
24. **Labels path (L37).** Name the labels JSONL path, for example `eval/eval_set/labels/<ab>_<pass>.jsonl`, with test labels in `_private/`. *(zoom-out)*

---

## Net effect if applied
- **M1-M5:** run.py and score.py can be coded without guesswork. The arm config reaches the pipeline, arms run from their own commit, and packets carry the parts, refs and real source text.
- **M6-M8:** the go/no-go is no longer optimistic about variance or dependent on the choice of baseline, and Phase 0 validates the binary label the decision uses.
- **M9-M10:** labelling and cost are complete, with a worst-case A/B at about 13.3 h GPU unless the ceiling is tightened.
