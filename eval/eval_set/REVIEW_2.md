# Goldfish Review Report: E1 fixed evaluation set, review 2

**Target:** `eval/eval_set/PLAN.md` (L1-98, draft v2 2026-11-24), checked against `REVIEW_1.md`

## Reviewer Status

| Reviewer | Lens | Status |
| --- | --- | --- |
| `egm-design` | power, decision rule, validity | completed |
| `egm-implementation-readiness` | runnability, code claims, specs | completed |
| `egm-zoom-out` | scope, reuse, cost | completed |

**REVIEW_1 status:** 12 resolved, 9 partly resolved, 1 not resolved.
**New issues after deduplication:** 3 blockers, 10 majors, 17 minors.

Not counted as blockers, as the brief asked: the run model (L65-66) and the user's 40-label gold sample (L79-80).

I checked every code claim below against the source myself: `blind.py`, `w8b_ab.py`, `w8b_score.sign_p`, `evaluate_run.score`/`norm_fig`, `src/pipeline/run.py`, and `PLAN_W8b.md:20`.

---

## 1. REVIEW_1 findings

| ID | Status | Reason (PLAN v2 lines) |
| --- | --- | --- |
| B1 decision rule | partly | L54-58 adopt the permutation test, a minimum effect and the sign test as secondary only, and L31-32 add the part and comparison floors. Still missing: the statistic form, seed, p estimator, failed-run rule and the A/A false-positive check. The conjunction makes the test unpassable (NB1). |
| B2 labels | partly | L43-47 give three labels and L32-33 make the comparison its own part. The decision table has no row for comparison parts or for a correct value with no citation (NM7). |
| B3 Phase 0 validity | resolved | L76-84: commit before labelling, references written blind, a seeded gold draw, kappa bars. Residuals: the labeller is unnamed (NM8) and the kappa CI is wide (Nm16). |
| B4 Phase 1 test run | resolved | L60 and L94. |
| M1 norm_fig | resolved | L48. The description of `norm_fig` matches `evaluate_run.py:24, 46-48, 68-70`. |
| M2 k, n, power | partly | Phase 1 is calibration-only (L88-95), but the simulation cannot be coded (NB2) and the no-go branch has no bound (NM2). |
| M3 order, drift, planner, cache | partly | L67-69 are good. The cache check has no method, and ABBA is undefined for odd k and for the order across queries (Nm4, Nm15). |
| M4 pinning | partly | The L70-72 field list is complete. The run table has no file or format, and the writer mode, style and config are not fixed (NM5). |
| M5 leakage, split, seen | partly | L20-21, L29-31 and L60-62 are in place. But the LLM labeller (Claude, the developer's model) reads every test report (NM8), and the host check will fire falsely (Nm3). |
| M6 temporal drift | resolved | L28, L34-35. |
| M7 blind cut | **not** | L49 names `blind.py` and says it keeps the source list. `blind.py` deletes the source list (NB3). |
| M8 session confound | partly | L51 covers A/B batches. Phase 1 A/A labels from one session still serve as the reference for A/Bs labelled later (L91, L95) (NM10). |
| M9 files, reuse | partly | The plan is down to 5 files with schemas. But `blind.py` is not reusable, and `w8b_ab.py` does not import at HEAD (NB3, NM3). The labels, run-table and test-file paths and formats are not given. |
| m1 secondaries | resolved | L58-59. |
| m2 cost | partly | The budget uses pipeline-only seconds (Nm2). |
| m3 aggregation | resolved | L55. |
| m4 reference-check seed and checklist | resolved | L39-40. Seed values are due at freeze (Nm11). |
| m5 answerability ceiling | resolved | L37-38. |
| m6 dev rotation | resolved | L31. |
| m7 order of work | resolved | L13-14. |
| m8 category names | resolved | L27-28. |
| m9 provisional wording | resolved | L5-6. |

---

## 2. Can the permutation test and power simulation be implemented as written?

**No.** Specified: the pooled target-part statistic, within-query relabelling, 10,000 draws, one-sided, alpha 0.05, and a single primary test (so multiplicity does not arise).

Unspecified or contradictory:
- whether the statistic is a count or a rate;
- the unit of permutation (whole runs);
- the p estimator and the RNG seed;
- the failed-run rule;
- the meaning of "tied queries add 0";
- the A/A check;
- every element of the power simulation (NB1, NB2).

## 3. Code the plan cites: what is misdescribed

| Claim | Verdict | Evidence |
| --- | --- | --- |
| L23, L49: blind cut via `eval/writer_ab/blind.py`, which keeps [N] markers and the source list, strips headings and notes, and shuffles with a fixed seed | **wrong** | `blind.py:30` cuts everything from the `Sources/References` heading on. `blind.py:24` always shuffles with `random.Random(7)`; the CLI seed (L11) only selects `out/<model>/<arm>_s<seed>` dirs (L16-17). It reads only `eval/writer_ab/out/`, imports `run_arms`, and prepends a "FINDINGS (every entry is true ...)" block from `run_arms.KEYS` (L28, L33-34). That block would bias E1 labels. It does not strip the reader notes or style headings. It runs its whole body at import. `PLAN_W8b.md:29` records the same cut as "list and markers stripped". |
| L22: `run.py` generalises `w8b_ab.py` and takes an arm patch function | **wrong or incomplete** | `w8b_ab.py:21` reads `pr.scholar_search`, which no longer exists in `src/pipeline/run.py` (W8 reverted), so importing it raises AttributeError. L24-28 and L34 freeze the planner, which contradicts L67. Arms are in-process monkey-patches (L34-35), but the writer is a separate `src/app.py` subprocess (L40-43), so a patch function cannot express a writer change (L3 says the set covers writer changes). The job order at L60 is round-major (all queries' AB, then BA), not per-query ABBA. `secs` (L36-38) times `pr.run` only. It records none of the L70-72 fields and has no redo logic (that lives in `w4_retrieval.py:53,65`). |
| L58: `sign_p`, "16 untied needs 12 wins" | correct | `w8b_score.py:76-77`, upper tail of Bin(n, 0.5), n=0 returns 1.0. The caller drops ties. sign_p(12,16) = 0.038 and sign_p(11,16) = 0.105. Importing `w8b_score` drags in `w8_score` and edits sys.path; copying the one line is simpler (Nm7). |
| L9, L23: `evaluate_run.score` | correct | `evaluate_run.py:59`. It needs `_run_state.json` in the dir. |
| L10, L59, L68, L27-31 | correct | W8b A/A 9 vs 15 (`PLAN_W8b.md:55`). Bar 5 is +60 s (`PLAN_W8b.md:47`). The W4 redo rule is >25% (`PLAN_W4.md:142`). The bank categories and rules 3 and 5 match `pipeline_queries.yaml:3,7`. |
| L66: `enable_thinking=false` as part of the run profile | misleading | Pipeline calls hard-code `think="minimal"` (`run.py:183, 208, 223, 473`) and ignore `settings.enable_thinking` (Nm14). |
| L73, L90: median ~140 s per run | understated | 140 s is pipeline time only. `PLAN_W8b.md:20` gives ~2.5 h for 36 runs, about 250 s per run (Nm2). |

---

## 4. New issues

### Blockers

#### NB1. The go/no-go cannot pass at the effect it names, and the effect is given in two units
* **Lenses:** design, readiness, zoom-out
* **Scope:** L56-57, L91-93
* **Problem:**
  - L57 passes only when p < 0.05 AND the observed difference is at least +0.10. L93 requires power ≥ 0.8 at a true effect of +0.10. When the true effect equals the floor, the observed difference clears it only about half the time, so power is at most about 0.5 at any N or k. Phase 1 would always return no-go.
  - L91 also simulates "+1 target part", while L93 tests "+0.10 pooled rate". With one comparison part per comparison query, +1 part per run is +1.0 in rate, which gives trivially high power at the W4 floor (0/36).
* **Fix:** Separate the two numbers:
  - Keep +0.10 as the observed-effect floor.
  - Power the design at a larger effect to detect, δ\* (for example +0.20 on target parts), in rate units.
  - Phrase the go/no-go as "MDE at n=16, k=K for target set Y ≤ δ\*".
  - Alternatively, drop the conjunction and decide on p alone, with a CI reported.

#### NB2. The permutation test and power simulation are not specified tightly enough to code
* **Lenses:** design, readiness
* **Scope:** L55-57, L91-93
* **Fix:** Pin each element in the plan.
  - **Statistic.** T = (ANSWERED / target part-runs in B) − (the same for A), as a rate difference. Whole runs are relabelled within each query. Direction is B > A.
  - **p-value.** p = (1 + #{T\* ≥ T}) / (1 + 10,000), with a recorded seed. Delete "tied queries add 0": only queries whose 2k runs all have the same target count are constant under permutation.
  - **Noise model.** Use per-part Bernoulli rates taken from all 32 dev runs, plus the Phase 0 part rates as a prior, noting that W8b's frozen planner makes those rates understate noise. Resample queries with replacement up to the test size (5 comparison queries and 16 overall). Show sensitivity at baseline rates of 0.1, 0.3 and 0.5, because a rate at the floor gives zero variance.
  - **Injection.** Add δ to B's part rate, capped at 1. Pick either the additive or the logit form and state it.
  - **Reps.** 2,000 reps, each with 2,000 permutations, seeds recorded (Monte Carlo SE about 0.009 at power 0.8). Power is the share of reps that pass the full L57 rule.
  - **MDE.** The smallest δ on the grid {0.05, …, 0.40} with power ≥ 0.8.
  - **k.** The cheapest k in {2, 3, 4, 6} that reaches the target. Bootstrap runs, because 4 dev runs cannot give k > 2 without replacement.
  - **A/A.** Run the primary test once on a fixed split (runs 1,3 vs 2,4) as a drift check, or drop it. Under exchangeability the false-positive rate is at most alpha by construction.

#### NB3. The blind cut is attributed to code that does the opposite, and the packet cannot support the rubric
* **Lenses:** all three
* **Scope:** L23, L43-45, L49
* **Problem:** See §3. Followed literally, the plan strips the source list that ANSWERED depends on. "The cited saved source states it verbatim" (L44) also needs the saved source text, not just the list.
* **Fix:**
  - Specify new code: a `blind(report_md) -> str` function in `score.py`. It uses `evaluate_run._strip_notes`, strips the title, style headings and run-metadata lines (list the regexes), and keeps [N] markers and the source list.
  - Shuffle with `random.Random(<blind seed>)`.
  - Write packets plus a private map file to a named gitignored path.
  - The packet includes the cited sources' text from `src_*.md`/`sources/`, with a stated length cap. Otherwise, a script checks the verbatim match and the labeller only flags candidates.
  - State that `blind.py` is not reused.

### Majors

#### NM1. Comparison parts are underpowered, and the plan does not test that case
* **Lenses:** design, zoom-out
* **Scope:** L31, L55, L93, L98
* **Problem:**
  - The next plan is a comparison fix. Its targets are about 5-8 test comparison parts, which is 15-24 part-runs per arm at k=3.
  - At a baseline of 0.3, +0.10 gives z ≈ 0.6. 80% power needs δ ≈ 0.4. Across all parts (about 192 part-runs per arm), δ80 is about 0.13.
  - Dev holds at most 3 comparison queries.
* **Fix:**
  - Simulate "comparison parts only" and "all parts" separately, over the grid N_comparison {5, 8, 12} × k {3, 4, 6}.
  - A comparison A/B must name a δ\* no smaller than its δ80.
  - If that δ\* is implausibly large, add a second comparison-type part per comparison query before freeze.

#### NM2. The no-go branch is unbounded, and the N/k rule has no metric
* **Lenses:** zoom-out, design
* **Scope:** L93-94, L31
* **Problem:**
  - "Add queries (or raise k)" has no ceiling, no author, no split and no stop condition.
  - Adding test queries after the L89 freeze changes the test sha256.
  - The within-query test already removes between-query variance, so "raise k if within-query variance dominates" is backwards as a rule.
* **Fix:**
  - From the NB2 grid, pick the cheapest design (fewest total runs) that reaches the target. Raise k first, since it needs no new references.
  - Ceiling: k ≤ 4 and at most 8 added queries, keeping each A/B under about 7 h sequential.
  - Allow one round of additions, then re-freeze as `evalset-v1.1` with a recorded reason.
  - If power is still short: stop, report the MDE, and limit each A/B to effects at or above it.

#### NM3. The `run.py` template does not run, and the arm interface is undefined
* **Lenses:** readiness, design
* **Scope:** L22, L67-68
* **Fix:**
  - Build `run.py` from `w8b_ab.py` with these changes:
    - remove `scholar_search` and `frozen_plan`;
    - add the `w4_retrieval.py` redo logic;
    - write the L70-72 fields to a named TSV with a header row (for example `eval/eval_set/runs.tsv`), keeping resume-by-key;
    - order jobs query-major in ABBA/BABA, alternating by query.
  - Define an arm as (commit or worktree, CLI flags, config path).
  - Allow an in-process patch only for pipeline-only A/Bs, as a context manager that restores `pr.*` on exit.
  - Name the CLI.

#### NM4. Failed and missing runs have no rule
* **Lenses:** readiness, design
* **Scope:** L56, L68, L90
* **Problem:** The following are all unhandled:
  - NO REPORT (`w8b_score.py:84-85` only prints it);
  - `app_rc != 0`;
  - a run still over 25% empty searches after its redo.
* **Fix:**
  - Re-run once in the same session.
  - If the run still fails, count all its target parts as MISSING (intention-to-treat).
  - Apply the same rule in the permutation test and the simulation, and log the count per arm.

#### NM5. The instrument's writer and config are not fixed
* **Lens:** readiness
* **Scope:** L65-71
* **Fix:** State all of these:
  - `--writer-mode compiled`;
  - `--style` (standard or academic; commit 25d7685 made academic emit "References", and the blind cut must handle it);
  - the `pr.run` arguments (w8b used topk 3, `"ce"`, judge on; `w8b_ab.py:37`);
  - the config file path whose sha256 is recorded.

#### NM6. The latency guard and cost cover pipeline seconds only
* **Lenses:** design, zoom-out
* **Scope:** L59, L73, L90
* **Problem:** `secs` (`w8b_ab.py:36-38`) excludes the writer subprocess, so a writer A/B's latency regression would be invisible.
* **Fix:** Log pipeline seconds and writer seconds separately. The guard uses their sum unless the A/B plan says otherwise.

#### NM7. The decision table has no rows for comparison parts or uncited correct values
* **Lens:** readiness
* **Scope:** L43-47, L32-33
* **Fix:** Add these rows:
  - Comparison ANSWERED = both sides stated, each meeting the value rule, plus an explicit relation on M; or a not-comparable statement that names its reason (measure, period or population).
  - A table row with both sides but no stated relation = MISSING.
  - A correct value with no citation = MISSING.
  - For comparison refs, `statement` holds the expected relation and `value` is null.

#### NM8. The labelling mechanism and labeller are undefined, and the labeller sees the test set
* **Lenses:** readiness, zoom-out, synthesizer
* **Scope:** L23, L60-61, L76, L81
* **Problem:**
  - No model is named, and a Python script cannot call Claude.
  - If Claude labels the test reports, the developer's model reads every test report, which reopens the M5 leak that L61 closes for spot-checks.
  - Whether the title "Phase 0 (no GPU)" holds depends on which labeller is used.
* **Fix:**
  - Either name the labeller or add it to L65 as a user decision.
  - Have `score.py packets` build the packets and `score.py score` read the labels.
  - Do labelling in fresh subagents, one per pass, each reading `rubric.md` plus the packets and writing labels JSONL to a named path, with `pass` ∈ {1, 2, gold}.
  - For test packets, only labels flow back to the main session. State this as a known ceiling.
  - State the packet batch size per context, and which label set A/B decisions use (pass 1, or an adjudicated merge).

#### NM9. The Phase 0 report frame is undefined
* **Lens:** readiness
* **Scope:** L77-80
* **Problem:**
  - Candidate sets: `research_output/cmp_`, `cmp2_` and `cmp2pres_ornith_c{2,3,4}_r{1,2}` (18 runs), plus the 36 W8b reports.
  - c4 is in Spanish, and W8b's vitd, pm25 and minwage have only 2 parts each.
* **Fix:**
  - List the exact report dirs.
  - Decide whether c4 is kept or excluded.
  - Allow 2-part queries in Phase 0 explicitly.
  - Set the gold comparison cap and the seed.
  - Define a part-label as one (report_id, part_id) pair.

#### NM10. The role of A/A is undefined and confounded by labelling session
* **Lenses:** readiness, design
* **Scope:** L91, L95 vs L51
* **Problem:** REVIEW_1's A/A false-positive check was dropped. Phase 1 A/A labels from one session are an implicit reference for A/Bs labelled in later sessions.
* **Fix:** Rename A/A "simulation input and drift check", and never compare it with A/B labels. Or re-label the stored Phase 1 runs inside each A/B batch, which costs no GPU time.

### Minors

- **Nm1. Target parts by schema field (L54-55, L61).** The developer cannot see test parts, so target sets must be selected by `type` through a script, never by part id. *(design)*
- **Nm2. Cost (L73, L90).** Budget about 250 s per run (`PLAN_W8b.md:20`). Phase 1 is then about 2.2 h, and one A/B is 16 × 2k × 250 s, about 6.7 h at k=3. State this envelope. *(all)*
- **Nm3. Leakage check (L62).** Ref hosts such as who.int or oecd.org can appear legitimately in `src/`. Match query strings and full ref URLs only, and name the out-of-tree file path `score.py` reads. *(all)*
- **Nm4. ABBA order (L68).** Give the sequence for odd k (for example ABBAAB / BAABBA, alternating by query) and fix query-major order. *(design, readiness)*
- **Nm5. Redo asymmetry (L68).** Report redo counts per arm, and flag a gap larger than 1. *(design)*
- **Nm6. Ref schema bloat (L18-20).** With auto-matching dropped (L48), `value/unit/scale/period/entity/tol` serve no script. Fold them into `statement` and state the tolerance in the rubric, but keep `value: null` for comparison refs (NM7). *(zoom-out)*
- **Nm7. Sign test wording (L58).** Write "sign_p over untied queries (12/16 if none tie)". Copy the one-line function instead of importing `w8b_score`. *(design, readiness)*
- **Nm8. Free prior (L85-86).** The W8b OFF r1/r2 pairs (different plan per pair) and aa1/aa2 give a provisional variance and MDE before any GPU time is spent. Add this to the Phase 0 outputs, with the frozen-planner caveat. *(zoom-out, design)*
- **Nm9. User effort (L39-40, L79-80, L84).** State one total: about 40 gold labels, about 30 reference checks and the Phase 0 reference checks, roughly 70-80 judgements before freeze. A Phase 0 redo costs about 40 more labels plus new references (obstacle 7). *(zoom-out)*
- **Nm10. After the test burns (L60).** Add one line: draft 16 new test queries by the same procedure, tag `evalset-v2`, and re-run Phase 1 only if dev changed. *(zoom-out)*
- **Nm11. Seeds.** Put one "Seeds" line in the plan covering the split draw, the reference-check draw, the gold draw, blind, the permutation test and the simulation, with values filled at freeze. *(readiness)*
- **Nm12. Provenance fields (L24, L72).** Give the labels file a header record (rubric sha, blind seed). Name what is hashed per run (sha256 of `final_report.md` and `evidence.json`) and where (a run-table column). *(readiness)*
- **Nm13. Hardware (L64-73).** State: sequential runs, `rocm-smi` first, nothing else on the 16 GB card, `ollama stop` in a finally block, and `DEEPDELVE_FORCE_VIRTUAL_DISPLAY=1`. *(readiness)*
- **Nm14. Thinking (L66).** The pipeline sends `think="minimal"` regardless of `enable_thinking` (`run.py:183, 208, 223, 473`). Record the resolved think value per stage in the run table, and do not cite `enable_thinking=false` as the pipeline profile. *(readiness)*
- **Nm15. Cache check method (L69).** For example, grep `src/pipeline` for disk caches and confirm no plan or search file is reused across runs. The lru_cache on `_page_counts` is pure. *(readiness)*
- **Nm16. Kappa CI (L82).** With 40 items, kappa's 95% CI is about ±0.25. Report a bootstrap CI and per-class counts, and say the bar applies to the point estimate. Name the item set for the pass-1 vs pass-2 kappa (all Phase 0 labels). *(design, zoom-out)*
- **Nm17. Category quota (L27-31).** The 16 non-comparison queries do not divide evenly across 7 categories, and a plain seeded draw cannot guarantee "≥5 comparison in test" or "news_recent ≤ 1 per split". Write the quota table and make the draw stratified by category. *(synthesizer)*

---

## Net effect if applied
- Phase 1 then returns a computable decision: MDE and k per target set, against a δ\* that can actually be reached.
- The blind cut and `run.py` become specified new code, not reuse of tools that do something else.
- GPU estimate: Phase 1 about 2.2 h. Each A/B about 6.7 h at k=3, bounded by the NM2 ceiling.
