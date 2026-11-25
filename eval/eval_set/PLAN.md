# Fixed evaluation set (E1). Draft v6 2026-11-24 (REVIEW_1-5 applied), not frozen

Goal: one fixed instrument that says whether a pipeline/writer change helped, graded per query part against verified reference
answers, with noise and power measured. Priority 1 of the gap analysis (wiki Roadmap "Current obstacles"; Whitepaper §10).
All labels are LLM labels with user spot-checks; every result is provisional until full manual labelling (carry this wording into
CURRENT.md and the wiki page).

## User decisions (2026-11-24)
1. Run model: ornith-1.5:9b (digest recorded at freeze), its `settings.model_options` profile, informational as in the W series.
2. Labeller: Claude in fresh subagents (one context per pass), because manual labelling time is the bottleneck (Roadmap obstacle 7).
   Known ceiling: the developer's model family reads test reports; only labels flow back. The CLAUDE.md probe-first rule (2+ models)
   is about changes that rely on a model doing something new; here the gold sample is the validity check instead. Follow-up (user):
   a human grading tool (built or existing) so the user can later re-grade the same packets by hand; packets and labels are kept in a
   format that tool can read.
3. User effort accepted: ~40 gold labels + ~12 Phase 0 reference checks + ~36 new-set reference checks before freeze. A Phase 0 redo
   costs ~40 more labels plus new references.

## Why the current instruments fail
- Scorecard (`scripts/evaluate_run.py:score`) measures provenance (figs_unsupported, cites), not whether the question was answered.
- Probes graded parts ad hoc, on burned dev queries, 1 run per arm. W8b: two OFF runs of one frozen plan differed by 6 distinct papers (PLAN_W8b.md:55).

## Order of work
Phase 0 (grader validity, no GPU) -> write the 24 new queries and references (only after Phase 0 passes) -> freeze `evalset-v1` ->
Phase 1 (calibration, GPU) -> later A/B plans use the set. After the test split burns: draft 16 new test queries by the same procedure,
tag `evalset-v2`. Dev is for calibration and iteration only, never for decisions.

## Files (in `eval/eval_set/` unless stated)
- `PLAN.md`; `rubric.md` (decision table, labeller prompt; sha256 recorded).
- `queries.yaml`: `id, cat, split, q, parts[id, type(fact|figure|comparison), text, sides, measure, ref{statement, value(null for
  comparison), alt_values, url, quote, accessed, source_class, verified_by}]`. Tolerance lives in the rubric. Test queries and refs
  live at `~/deepdelve_evalset_test/queries_test.yaml` (outside the tree); only its sha256 is committed.
- `run.py` (new; job loop and log format copied from `eval/writer_defects/w8b_ab.py`, not imported). Arm = (commit, config path).
  Each arm runs from its own `git worktree add <tmp> <commit>`, removed only after runs.tsv is written. Per attempt:
  - `out = <repo>/research_output/e1_<set>_<q>_<arm>_r<k>_a<attempt>` (absolute `Path`); each arm config sets
    `settings.workspace` {type: disk, session_isolation: true, dir: absolute `<repo>/research_output`} (asserted on the raw arm
    YAML; without isolation every run writes the workspace root, `tui.py:2580`).
  - Driver: `sys.executable run.py --driver <wt> <cfg> <q> <out>` puts `<wt>/src` on the path with `--config <cfg>` in `sys.argv`
    before importing config, asserts `pr.__file__` is under `<wt>/src/` and loaded `config.cfg["api"]["openai_model"]` equals the run
    model, writes `<out>/arm_meta.json` {model_options(model), minimal_think(model) (Ollama up), pr_file}, calls
    `pr.run(q, model, out, 3, "ce", True)`. Driver rc != 0 = failed attempt, app.py skipped.
  - Writer: `[sys.executable, "<wt>/src/app.py", "--config", cfg, "--prompt", q, "--auto-approve", "--pipeline-evidence", out,
    "--writer-mode", "compiled", "--style", "standard"]`, cwd=<wt>, `q` byte-identical to the driver's; log `<out>.app.log`.
    report_dir = everything after `Run folder: ` to the end of the line, ANSI stripped (the repo path has a space); no
    `final_report.md` there = no report.
  - `searches`/`empty_searches` read from `<out>/metrics.json` `gather` with `.get(k, 0)`; searches == 0 = failed attempt.
  - CLI: `run.py --set dev|test --arms <commit>:<cfg> [<commit>:<cfg>] --k K` (one arm for Phase 1); resume key (query, arm, run,
    attempt); an attempt dir left by a crash is deleted before rerun; writes `eval/eval_set/runs.tsv` (header row).
- `score.py` (new): `packets` builds blind packets; `score` reads labels and runs the test and the simulation. `eval/writer_ab/blind.py`
  is not reused.
- Labels: `eval/eval_set/labels/<ab>_<pass>.jsonl` (test labels under `_private/`), a header record (rubric sha, refs sha, blind
  seed, labeller model, grader commit), then `report_id, part_id, label, evidence_quote, pass(1|2|gold)`. Packets and the private
  blind map go to `eval/eval_set/_private/` (gitignored; `.gitignore` committed with the plan).

## Set
- 24 new English queries; bank categories (`eval/pipeline_queries.yaml`); `spanish` excluded (English first).
- Quota (dev / test): comparison 3/5, legal 1/2, technical 1/2, scientific 1/2, historical 1/2, numeric 1/1, contested 0/1,
  news_recent 0/1. Stratified seeded draw by category.
- "Seen" = any run, any report read, or topic overlap with a retired or burned query (bank rule 3). Dev does not rotate under bank
  rule 5 while E1 is in use.
- 3-5 atomic parts per query, fixed before any run. Each `cat=comparison` query has exactly two comparison parts (two shared
  measures), each "X vs Y on measure M, or a reasoned not-comparable statement"; other categories have none. For comparison refs,
  `statement` holds the expected relation and `value` is null.
- Wording time-anchored ("in 2024"); a newer dated value is graded WRONG (wrong period).
- Refs found by web search (Claude) from primary or authoritative sources; `alt_values` filled while writing. A part with no locatable
  public answer is dropped and logged (known ceiling: the set favours findable answers).
- Reference check (new set and Phase 0): user checks every comparison part and a seeded 25% of the rest (URL loads, quote verbatim,
  value matches quote, source class allowed). A failed check fixes or drops the part.

## Rubric (decision table, frozen in rubric.md)
- Fact/figure ANSWERED: states the value/statement for the named entity and period, cites a saved source, and matches the ref within
  the ref's stated precision or an `alt_values` entry. A differing value also counts when the cited saved source states it verbatim,
  comes from an allowed source class, and uses the same definition and period.
- Comparison ANSWERED: both sides stated, each meeting the value rule, plus an explicit relation on M; or a not-comparable statement
  naming its reason (measure, period or population). A table row with both sides but no stated relation = MISSING.
- WRONG: contradicts the ref with no supporting saved source, or gives a wrong-period or wrong-entity figure as the answer.
- MISSING: absent, a disclosed gap, or a correct value with no citation.
- No figure auto-match; every part goes to the labeller.
- Blind packet (`score.py packets`): rubric sha, then per report: blind id, query text, its parts with refs (statement, value,
  alt_values) and allowed source classes, the stripped report (`evaluate_run._strip_notes`; title, style headings and run-metadata
  lines stripped, regexes listed in score.py; "Sources"/"References" heading normalised; [N] markers, source list, evidence quote
  bullets and the "Not covered" section kept), and source excerpts. Excerpts: URL taken verbatim from the report's `N. [title](url)`
  line (parsed to the last `)`) -> `pipeline.bridge.source_filename(url)` in the report dir (evidence dir only if missing there);
  each quote rendered under [N] located with `pipeline.run.quote_span`; +-500-character windows merged, capped at 3,000 characters
  per source; first 1,500 characters if no quote is found (Amendment 1, 2026-11-25, before any labelling: the +-1,500/6,000 rule
  gave ~70k-character packets, 43 contexts per pass vs ~20 planned; now 23). Shuffled with `random.Random(<blind seed>)`. Blindness fails if an arm
  changes the report format (state it in that A/B).
- All runs of one A/B (both arms) are labelled in one shuffled batch, one rubric version; each subagent context capped at 150,000
  characters of packets (~3 reports); a report is never split, one over the cap goes alone. Decisions use pass 1; pass 2 only in
  Phase 0. Earlier labels are never reused.

## Statistics (pinned)
- Target set: selected by part `type` through the script (developer cannot see test parts), never by part id. Fixed per A/B plan.
- Statistic T = (ANSWERED / target part-runs, arm B) minus (same, arm A).
- Permutation: whole runs relabelled within each query (2k runs per query), 10,000 draws, seed recorded,
  p = (1 + #{T* >= T}) / (1 + 10,000), one-sided B > A.
- Ship = p < 0.05 AND guard met AND observed T >= the A/B plan's practical floor. Reported: observed T, WRONG rate.
- Failed attempt = no report, app_rc != 0 or driver rc != 0. Max 2 attempts per slot, kept in its ABBA position. Attempt 2 also runs
  if attempt 1 has > 25% empty searches (W4 rule); attempt 2 is used as is. The last attempt is always the one used; still failed ->
  all its parts MISSING (intention to treat). Counts per arm logged; a redo-count gap > 1 between arms is flagged.
- Guard: median (pipeline + writer seconds) B <= A + 60, unless the A/B plan says otherwise. Pipeline seconds = wall time around the
  driver; writer seconds = wall time around the `app.py` subprocess (completion checks and PDF render included).
- A milestone = one pre-registered A/B decision, both arms in one session. Test burns after 2 A/B decisions, pass or fail
  (family-wise error up to ~0.10, accepted). Only aggregate test numbers reported; test spot-checks by the user only.
- Leakage check in `score.py`: fails if a test query string or full ref URL from the out-of-tree file (case and whitespace
  normalised) appears in `git diff evalset-v1 -- src/`, in `git diff evalset-v1 <arm commit> -- src/` for each arm, or in an arm's
  config. Paraphrase leakage is not detectable (stated).

## Power simulation (Phase 1, pinned)
- Generator: per rep, stratified query draw with replacement: N_comparison slots from the 3 dev comparison queries, 11 other slots
  from the 5 dev non-comparison queries (ceiling: the comparison MDE rests on 3 distinct queries, 6 parts). Within each drawn query,
  both arms resample whole observed dev runs (keeps part correlation and failed runs).
- Injection, arm B only: p_part = ANSWERED share of that part over the query's 4 dev runs (failed = MISSING); each non-ANSWERED target
  part flips to ANSWERED with probability min(1, delta / (1 - p_part)), skipped if p_part = 1. Realised delta per rep = arm B target
  ANSWERED share after injection minus before.
- Vectorised; 2,000 reps x 1,000 permutations, seeds recorded. Power = share of reps with p < 0.05.
- MDE = mean realised delta at the smallest nominal delta in {0.05, 0.10, ..., 0.40} with power >= 0.8, separately for "comparison
  parts" and "all parts", over N_comparison {5, 8, 12} x k {2, 3, 4}.
- Go/no-go on the empirical dev rates, with MDE x 1.15 (resampling 4 runs understates variance, sqrt(4/3)): ready when all-parts
  MDE x 1.15 <= 0.15 and comparison-parts MDE x 1.15 <= 0.30. Chosen design: smallest k in {2, 3, 4} at N_comparison = 5 meeting both;
  else k = 4 with the smallest N_comparison in {8, 12} (one round of added test comparison queries, test sha256 re-recorded at
  `evalset-v1.1` with the reason); else stop, report the MDE, and limit A/Bs to effects at or above it.
- Inside an A/B, permutation exchangeability replaces A/A pairs; update Whitepaper §10 at doc sync.

## Runs
- Planner free (not frozen). Order: query-major, ABBA (k=2), ABBAAB/BAABBA (k=3), ABBABAAB (k=4), alternating start by query. Never
  reuse a stored baseline.
- Cache check before Phase 1: grep all of `src/` for disk caches (including `rag_cache`, `~/.deepdelve/rag_cache.json`); confirm no
  plan or search file is reused across runs.
- `runs.tsv` fields: query, arm, run, attempt, model tag + digest, Ollama version, arm commit, grader HEAD + dirty flag, config sha256,
  resolved model_options, resolved think value (`minimal_think(model)`, once per run), `rag_cache.enabled`, `pdf_engine`, search
  backend (nondeterministic), start time, pipeline seconds, writer seconds, searches, empty_searches, evidence_dir (`out`),
  report_dir, sha256 of evidence.json and final_report.md, status. Run dirs stay local.
- Hardware: sequential runs; `rocm-smi` first; nothing else on the 16 GB card; `ollama stop` in a finally block;
  `DEEPDELVE_FORCE_VIRTUAL_DISPLAY=1`.
- Cost (~250 s per full run, PLAN_W8b.md:20; redos add up to ~10%; labelling contexts = reports / 3):

| Step | GPU h | Labelling contexts | User judgements |
|---|---|---|---|
| Phase 0 (~60 reports, 2 passes) | 0 | ~40 | ~40 gold + ~12 ref checks |
| New set refs | 0 | 0 | ~36 ref checks |
| Phase 1 | ~2.2 | ~11 | spot-check 10 |
| A/B, k=3, 16 queries | ~6.7 | ~32 | test spot-checks |
| A/B at ceiling, k=4, 23 queries | ~13 (worst case) | ~64 | test spot-checks |
| Test redraw (every 2 A/Bs) | 0 | 0 | 16 queries (~64 refs), ~23 checks |

## Seeds (values filled at freeze)
split draw, reference-check draw, gold draw, blind shuffle, permutation test, power simulation.

## Phase 0 (no GPU): grader validity
- Before any labelling: commit PLAN, rubric, labeller prompt (sha256, labeller model id) and the Phase 0 parts and references. Parts
  are written in the queries.yaml schema (comparison parts with sides and measure, at most two per query) before any label is seen.
- Report frame: report dirs resolved from the "Run folder:" line of `research_output/{cmp,cmp2,cmp2pres}_*_c{2,3}_r*.app.log`
  (c4 excluded: Spanish; `w8_c2_*` excluded) and the 36 W8b runs (`eval/writer_defects/w8b_runs.tsv` column 5). Mixed models
  (ornith, gemma4, qwen2.5) are deliberate for grader validity. A cmp2/cmp2pres pair shares evidence (writer-only replicate): at
  most one of each pair enters the gold draw. The 4 W8b `acad` reports are academic style. 2-part queries (vitd, pm25, minwage)
  allowed in Phase 0 only. >= 25 distinct parts; fallback queries named in the frozen commit.
- References written from web search WITHOUT opening the saved reports (recorded).
- A part-label = one (report_id, part_id). All frame reports are labelled in both passes. Gold: seeded draw of 40 part-labels,
  stratified by part type and query, exactly 15
  comparison parts, before any label is seen; the user labels them blind to all LLM labels (gold is collected before pass 1 is
  shown, from the same packets).
- Two independent LLM passes (fresh subagent each), blind order.
- Bar (pre-set, point estimates, binary ANSWERED vs not): labeller pass 1 vs user kappa >= 0.6 AND raw agreement >= 85% AND
  comparison-only agreement >= 13/15; pass 1 vs pass 2 kappa >= 0.7 over all Phase 0 labels (PABAK reported beside it). Reported:
  per-class counts and the number of distinct comparison parts behind the 15. If fewer than 3 comparison gold items are ANSWERED, the
  comparison grader is recorded as unvalidated on positives.
- Fail -> fix the rubric once, redo on the fallback queries; fail again -> stop and report.
- Also yields (provisional, reference-biased): answer and comparison rates on those queries, tie rate, share of stable parts.

## Phase 1 (GPU, needs user OK): calibration only
- Freeze first: commit plan, rubric, dev queries.yaml, test sha256, scripts, seeds, `.gitignore`; tag `evalset-v1`.
- 8 dev queries x 4 baseline runs = 32 runs (~2.2 h). The 32 reports are labelled as one shuffled batch (pass 1, capped contexts,
  rubric sha at freeze); the user spot-checks 10 part-labels. Output: power simulation results, MDE per target set, chosen k,
  go/no-go.
- No test run and no shipped change in this plan; fixing comparisons or retrieval are the next plans.
