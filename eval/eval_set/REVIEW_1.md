# Goldfish Review Report: E1 fixed evaluation set, review 1

**Target:** `eval/eval_set/PLAN.md` (L1-58, draft 2026-11-24, not frozen)

## Reviewer Status

| Reviewer | Lens | Status |
| --- | --- | --- |
| `egm-design` | power, decision rule, confounds | completed |
| `egm-implementation-readiness` | grading spec, Phase 0 validity, reproducibility, leakage | completed |
| `egm-zoom-out` | over-build, cost vs value | completed |

Totals after deduplication: **4 blockers, 9 majors, 9 minors.** Lens tags show which reviewers raised each finding.

A correction to the review brief: a one-sided sign test at p < 0.05 with 16 untied queries needs **12** wins (P(X>=12) = 0.038), not 13. With 7 untied it needs 7/7. With 4 or fewer untied, no result can pass.

---

## Blockers

### B1. The decision rule cannot pass for the effects this set exists to measure
* **Lens:** design, readiness, zoom-out
* **Scope:** L35-37, L13-15, L30
* **Problem:**
  - (a) Wrong statistic for the threshold. The rule requires the median per-query difference to exceed the 90th percentile of single-query A/A |difference|. That percentile is about 1.6 σ_d, so the typical query would have to gain about as much as the noisiest A/A query moved. A shift that big implies a win probability of about 0.95 per query, which makes the sign test redundant. For a 4-part query with 2 coin-flip parts, σ_d ≈ 0.14, so the threshold is about 0.24: roughly one extra answered part on the median query. W8b's own numbers (9/10 untied wins, p = 0.011, median +2 against A/A |diff| of 1 and 6) would come out inconclusive again under this rule.
  - (b) Dilution. The first obstacle is comparisons, but a comparison fix moves only the comparison parts, and only about 5 of the 16 test queries would be comparison queries. The other 11 or more differences are noise or 0, so the median stays near 0.
  - (c) Ties. With 2-5 parts and floor effects (W4: 0/36 reports compared), many queries tie exactly. The plan does not say whether the median is taken over all 16 queries or only the untied ones. A tie rate above 50% pushes the untied count to a size where the sign test cannot pass at all.
  - (d) The threshold itself is undefined. The plan gives no unit, no quantile method (a 90th percentile of 8 values is essentially the maximum), and no planner-freezing regime. It also does not say whether a threshold measured on dev queries applies to test queries with different part counts.
* **Fix:**
  - Replace L36-37 with a within-query permutation test on a pooled statistic: total ANSWERED part-runs, B minus A, over the **target parts pre-registered in each A/B plan** (comparison parts for a comparison fix, all parts otherwise).
  - Null distribution: permute arm labels among each query's 2k runs (C(6,3) = 20 per query), 10,000 Monte Carlo draws, one-sided p < 0.05. Tied queries then add 0 instead of shrinking n.
  - Pre-register a minimum effect, for example +0.10 pooled answer rate on the target parts.
  - Keep the sign test (12/16) as a secondary report only.
  - Demote A/A to a false-positive check: the same test run on A/A data must give p > 0.05.
  - Require at least 3 parts per query. State how the 8 comparison queries split between dev and test (at least 5 in test).
  - Rough power: +1 compared part per run on 5 test comparison queries gives z ≈ 2.6. The current rule has about 0 power for the same effect.

### B2. Labels are undefined at the boundaries that move the primary metric
* **Lens:** readiness, zoom-out, design
* **Scope:** L18, L22-23, L30, L42
* **Problem:**
  - None of these are defined: PARTIAL, "equally sourced value", a value backed by a different valid source (another year, definition or release), a correct statement with no citation, a disclosed gap (W8b counted it as not answered), and a right value for the wrong period.
  - COMPARED duplicates the part label. ANSWERED with COMPARED=no is possible, and COMPARED needs its own 90% bar.
  - The plan also does not say whether COMPARED requires both sides to be answered, whether a reasoned "not comparable" counts (W8b allowed it), or whether a table row counts.
  - PARTIAL scores 0 in L30 and never enters a decision. It only adds a fuzzy boundary for labellers to disagree on.
* **Fix:**
  - Use three labels: ANSWERED / WRONG / MISSING. Keep WRONG, since it tracks obstacle 4.
  - Make "the comparison" its own part, as in W8b: "X vs Y on measure M, or a reasoned not-comparable statement". Comparison rate = answer rate on comparison-type parts. Drop the COMPARED label and its bar.
  - Freeze a decision table in `rubric.md`:
    - ANSWERED = states the value for the named entity and period, cites a saved source, and either matches the reference within tolerance or matches an `alt_values` entry. A differing value also counts when the cited saved source states it verbatim, comes from a listed source class, and uses the same definition and period.
    - WRONG = contradicts the reference with no supporting saved source, or gives a wrong-period figure as current.
    - MISSING = absent, or a disclosed gap.
  - Fill `alt_values` per part while writing the references, so most disputes are settled before any report is read.

### B3. Phase 0 tests labeller self-consistency, not grader validity, and its bar is not verifiably pre-set
* **Lens:** readiness, design
* **Scope:** L39-44, L47
* **Problem:**
  - Two passes of the same LLM share its biases. This is the self-preference risk Whitepaper §9 already concedes.
  - Raw 85% agreement is weak when one class dominates: at an 80/20 split, chance agreement is already about 68%.
  - The only validity check, a 20-part user spot-check, has no sampler.
  - The labeller model is not named.
  - The freeze happens only at Phase 1 (L47), so the "pre-set" Phase 0 bar cannot be verified (obstacle 6).
  - The references are circular. c2, c3, c4 and the W8b reports have already been read, so new references can anchor on what those reports say.
  - The sample is thin: about 6 queries x 3 parts is about 18 distinct parts, about 6 of them comparisons. The roughly 40 reports are nested inside those parts.
* **Fix:**
  - Commit PLAN, rubric, labeller prompt (with sha256 and model id) and Phase 0 references **before** any Phase 0 labelling.
  - Write the Phase 0 references from web search only, without opening the saved reports, and record that.
  - Use at least 25 distinct parts: add burned queries that have saved reports. Name the 3 fallback queries for the L43 re-run now.
  - Bar: labeller vs user on a gold sample drawn by a seeded script before any label is seen (for example 40 part-labels, stratified by part type and query, with every comparison part up to a cap).
  - Pass if Cohen's kappa >= 0.6 and raw agreement >= 85% against the user, and kappa >= 0.7 between the two passes. Report the base rate and per-part agreement.
  - Optional: a second labeller from another model family. That needs the GPU, so drop the "no GPU" title if it is adopted.
  - Mark the Phase 0 "free result" (L44) as provisional and reference-biased.

### B4. The Phase 1 test run spends half the test set's life and decides nothing
* **Lens:** zoom-out, design
* **Scope:** L14, L49
* **Problem:**
  - Test burns after 2 milestones, and L49 says no change ships, so this run uses up one of the two milestones for nothing.
  - The baseline cannot be reused. A later A/B has to run its own baseline at its own commit, Ollama version and date, because DDGS results and sampled model calls drift over time.
  - The run costs about 2.5 h of GPU for a number that the dev baseline and Phase 0 already give.
* **Fix:**
  - Delete L49.
  - Define a milestone as one pre-registered A/B decision, with both arms run in the same session.
  - State "test burns after 2 A/B decisions".

---

## Majors

### M1. `norm_fig` cannot auto-match figures
* **Lens:** readiness, design
* **Scope:** L24 (`scripts/evaluate_run.py:46-48`, `FIG`, containment at L68-70)
* **Problem:** `norm_fig` is a string rewrite used for substring containment. `FIG` captures percentages only. There is no number parsing, tolerance, unit or scale handling, rounding or ranges. Matching is also position-blind: a reference figure stated for the wrong side or year still matches. That case is exactly obstacle 4.
* **Fix:** Drop the script step and put tolerance in the rubric. Or have the script only *propose* matches, and still send every figure part to the labeller. Either way, give each figure part `value, unit, scale, period, entity, tol` fields. Default tolerance: the reference's stated precision.

### M2. k and n are asserted, not derived, and Phase 1 produces no decision
* **Lens:** zoom-out, design
* **Scope:** L14, L34-35, L48
* **Problem:** k = 3 and 16 test queries are fixed before any variance is known. A/A on every A/B adds 48 runs (about 2.5 h or more) to each one.
* **Fix:**
  - Make Phase 1 calibration-only: N = 4 baseline runs per dev query (32 runs).
  - Get the A/A distribution from all 2+2 splits.
  - Simulate the B1 test with an injected +1 target part to estimate power.
  - Pre-set the go/no-go: power below 0.8 means add queries (between-query variation dominates) before any test run.
  - Record the minimum detectable effect.
  - From the saved reports, Phase 0 can estimate the tie rate and the share of stable parts at no GPU cost.
  - Re-run A/A only when the instrument changes (model, Ollama version, a major stage).

### M3. Run order, web drift, planner freezing and caches are unspecified
* **Lens:** design, zoom-out
* **Scope:** L34-37, L48
* **Problem:**
  - Blocks of one arm confound arm with drift: DDGS `backend="auto"`, throttling, and changing web results.
  - Nothing forbids reusing a stored baseline.
  - The plan does not say whether the planner is frozen per pair (W8b froze it). A frozen planner cannot test planner changes, and a free planner adds noise.
  - Any on-disk fetch or search cache would carry state from one run to the next.
* **Fix:**
  - Interleave each query's runs in ABBA/BABA blocks, the A/A too.
  - Never reuse a stored baseline.
  - State the planner choice. Free is probably right, since the calibration then measures the noise that matters.
  - Carry over W4's empty-search redo rule (more than 25% empty searches means one redo) and log `empty_searches`.
  - Verify that no cache persists across runs.

### M4. Reproducibility pinning is incomplete and the model is not named
* **Lens:** readiness
* **Scope:** L34, L52
* **Problem:** The plan records only "model, Ollama version, commit". "One model" breaks the CLAUDE.md rule against unnamed or fallback models. `w8b_ab.py`, the likely template, records none of these.
* **Fix:**
  - Run table: model tag plus digest, Ollama version, commit plus dirty flag, config sha256, resolved `model_options` (including `enable_thinking=false`), writer style, search backend (marked nondeterministic), start time, wall seconds, `run_dir`, and seed or "unseeded".
  - Grading side: labeller model, prompt sha, rubric sha, refs sha, blind seed, grader commit.
  - Keep run dirs locally (they quote paper text) and commit their hashes.
  - Name the model (for example ornith-1.5:9b with digest, "informational, as in the W series"), or make the choice a user decision before freeze.

### M5. Leakage, the split procedure and "seen" are undefined
* **Lens:** readiness, design
* **Scope:** L12-14, L19, L52
* **Problem:**
  - Claude writes the queries and references and also develops the pipeline. Test references kept in the tree can steer tuning.
  - Spot-checks put test reports in front of the developer, which is how test_used and heldout2 burned.
  - The plan has no dev/test assignment procedure and no definition of "never seen".
* **Fix:**
  - Define "seen" as: any run, any report read, or topic overlap with a retired or burned query (bank rule 3).
  - Draft candidates to the category quota, then assign splits with a seeded draw recorded in the plan.
  - Keep test queries and references outside the working tree. Commit only their sha256 at freeze.
  - Report only aggregate test numbers. Test spot-checks go to the user only.
  - Add a check in score.py that fails if a test reference host or query string appears in `git diff evalset-v1..HEAD -- src/`.

### M6. References go stale (temporal drift)
* **Lens:** readiness
* **Scope:** L13, L16
* **Problem:** In the recent and numeric categories, a later run may find a newer, correct figure and be graded WRONG.
* **Fix:**
  - Time-anchor query wording ("in 2024").
  - Give each reference `as_of` and `latest` fields. For `latest` parts, a newer dated value backed by a saved source counts as ANSWERED.
  - Re-verify `latest` references before each milestone and commit the diff.
  - Cap recent queries at 1-2 per split.

### M7. The blind re-render conflicts with source checking
* **Lens:** readiness
* **Scope:** L25
* **Problem:** If the source list and [N] markers are stripped, as in W8b, the labeller cannot apply the citation requirement or the "equally sourced" rule. The plan also does not say which tool re-renders.
* **Fix:**
  - Reuse `eval/writer_ab/blind.py`.
  - Keep neutral numbered markers and the source list. Strip only arm-revealing metadata (style headings, reader-notes block).
  - Shuffle with a fixed seed.
  - Note that blindness fails if an arm changes the report format.

### M8. Labelling sessions are confounded
* **Lens:** design
* **Scope:** L25, L35
* **Problem:** Whitepaper §6.1 shows the same baseline at 10.9% vs 24.9% across sessions, cause unknown. If the A/A labels from Phase 1 are compared with A/B labels from a later session, the session effect enters the comparison.
* **Fix:** Label all runs of one A/B (both arms, plus any A/A) in one shuffled blind batch, with one rubric version. Never reuse earlier labels.

### M9. Seven files, most of which already exist
* **Lens:** zoom-out, readiness
* **Scope:** L52
* **Problem:**
  - `run.py` would re-implement `eval/writer_defects/w8b_ab.py`.
  - `score.py` would copy `w8b_score.sign_p`, `evaluate_run.score` and `norm_fig`.
  - The blind cut already exists in `eval/writer_ab/blind.py`.
  - Keeping `refs.jsonl` separate from `queries.yaml` gives two hashes to freeze.
  - `grade.py` and `score.py` are a handoff with no other purpose.
  - The files have no schemas.
* **Fix:**
  - Five files: PLAN.md; queries.yaml (query, parts and references together); rubric.md; run.py (a generalised `w8b_ab.py` that reads yaml and takes an arm patch function, plus the M4 fields); score.py (grade plus score, importing the existing helpers).
  - Write the schemas into the plan:
    - queries: `id, cat, split, q, parts[id, type, text, sides, measure, ref{value, unit, scale, period, tol, alt_values, statement, url, quote, accessed, as_of, latest, source_class, verified_by}]`
    - labels: `report_id, part_id, label, evidence_quote, labeller, pass`

---

## Minors

- **m1. Secondary metrics decide nothing (L27, L31).** Either say "logged, not decided on", or make latency an explicit guard like W8b bar 5 (+60 s), since latency is what failed W8b. Drop `cited`. *(zoom-out)*
- **m2. The cost estimate is low (L48-49).** W8b runs took 45-490 s, median about 140 s, and 4-8 min on leave, minwage and pm25. As written, one A/B plus A/A is 144 runs, about 5.5-8 h. Budget from the measured median. *(design)*
- **m3. Aggregation is not stated (L30).** Pre-register equal weight per query or per part-run. Under B1 it is per part-run. *(design, readiness)*
- **m4. The reference check has no seed or checklist (L19).** Seed the 25% draw. Checklist: URL loads, quote is verbatim, value matches the quote, source class is allowed. Say what a failure triggers. *(readiness)*
- **m5. The answerability filter biases the set (L17).** Dropping parts with no public answer tilts the set toward findable answers. State this as a known ceiling and log the dropped parts. *(readiness)*
- **m6. Dev refresh conflicts with a fixed dev split.** Bank rule 5 replaces dev queries about every 2 weeks. Say whether E1's dev split rotates and how the calibration follows it. *(readiness)*
- **m7. The order of work (L12-19 vs L39-44).** Write the 24 new queries' parts and references only after Phase 0 passes, since a rubric fix can change how parts must be worded. *(zoom-out)*
- **m8. Category names (L13).** The plan says "recent", but the bank uses `news_recent`. Align with the bank, and say that the bank's `spanish` category is excluded. *(synthesizer)*
- **m9. Provisional wording (L26, L53).** Carry "provisional until full manual labelling" into the wiki page and CURRENT.md drafts, as Whitepaper §9 does. *(readiness)*

---

## Net effect if applied
- **GPU:** about 5 h drops to about 1.3-1.6 h (Phase 1 is calibration only, with no test run). Each future A/B goes from about 7 h to about 4-5 h, because A/A is calibrated once.
- **Phase 1** then outputs a decision: k, power, and the minimum detectable effect at n = 16.
- **Phase 0** then tests grader validity against the user, with a bar committed before labelling.
