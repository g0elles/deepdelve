# W4. Comparison asked for, never made: probe plan v3 (Phase 0, nothing built)

v2 2026-11-22 after EGM review 1 (1 blocker, 9 majors): per-query rubric, per-quote side/measure labels, per-query decision, pool check,
writer probe = production prompt and options with one decision per call, retrieval probe as a ceiling test.
v3 after review 2 (0 blockers, 5 majors): no pool/writer split (pool check kept as a diagnostic), sub-measures per query, matched
retrieval arms, floor 20, counting rules.

Graded defect (session_status/2026-11-20.md:42-65): none of 36 graded compiled reports (v1 12, v2 24 incl. 6 presence-1.5) makes a direct
comparison; best partial c2 ornith v1 r2 ("effects potentially greater than existing AUD meds"); c3 ornith r1 has a cited comparing
sentence without figures ("Iceland GDP growth relative to Ireland considerably higher [4]", session log :66), which fails the rubric below. User rule: a side-by-side table or two
per-side statements without a comparing sentence does not count.

## Saved data (offline; `eval/writer_defects/w4_survey.py`, 18 v2 sets of `eval/comparison_runs/runs.tsv`)
- Planner: c3 and c4 (12 sets) have one facet per side per measure, or one per side covering both measures. Questions are not always
  parallel: gemma c4 r1/r2 ask Mexico in Spanish and UK in English; ornith c3 r2 second questions differ. c2: 2 qwen sets have empty `entity`;
  ornith r1 planned a "semaglutide versus naltrexone" facet (`run.py:430` forbids it). A planner change alone is not the main lever.
- All 6 c4 sets were saved with `comparison: false` (before W1); with today's `COMPARISON_RE` all 18 are comparisons.
- Today's code on these sets: 15 have two sides (`compiled._sides`), 12 of them have no quote naming both sides (`_bridging`).
- Report line "no like-for-like comparison was verified" (`compiled.py:408-415`): present in 1 of 18 reports; c4 could never print it
  (flag false). It is printed only when no sentence cites quotes from two facets, so citing both sides counts as "compared". Fixed after
  the probe, keyed to the unit the probe validates.

Open question: per query, do the evidence sets hold a comparable pair (retrieval gap), or do they hold one that the reports never state
(writer gap)? Step A answers it without a model.

## Step A. Comparable-pair labels (offline, no GPU, no model)
- `eval/writer_defects/w4_pairs.py` dumps EVERY quote of the 18 sets (no figure/direction filter, so recall is not a filter's) with set id,
  facet, url, text, to `w4_quotes.jsonl`; model and run hidden behind a set hash.
- Labels per quote (not per facet: facets hold the other side's quotes, entity can be ""): `side` (A, B, both, none), `measure` (from the
  per-query list below, or `other`), `period_ok`, `human_trial` (c2 only).
- Per-query rubric (fixed now, before labelling). A PAIR is one side-A and one side-B quote with the same listed measure, both period_ok,
  both stated as figures, or both as directions with a size word:
  - c3: unemployment rate (same type on both sides: peak with peak, or the same year), real GDP growth (same year or span), or peak-to-trough
    output loss. period_ok = refers to 2008-2015. GDP level, emigration, 2026 monthly or quarterly figures are not.
  - c4, after that country's tax (Mexico from 2014, UK SDIL from April 2018), attributed to the tax. Sub-measures, same one on both sides:
    (a) volume of taxed drinks purchased or consumed; (b) sugar from taxed drinks purchased or consumed; (c) total dietary sugar intake.
    A reformulation-driven drop counts as (b) only when stated as sugar purchased, sold or consumed; sugar per 100 ml does not. Obesity,
    revenue, price are not.
  - c2, humans, randomised trial or meta-analysis of them. Sub-measures, same one on both sides: drinks per drinking day, heavy drinking
    days, total consumption (drinks or grams per week or month); a % reduction pairs only with a % reduction of the same sub-measure.
    DIRECT if head-to-head; INDIRECT if each against placebo in separate trials. Animal, EHR diagnosis rates, obesity-only cohorts are not.
    INDIRECT counts as a pair only for a sentence that says the trials were separate (enforced in the Step B rubric; user confirmed 2026-11-22).
  - A single quote labelled side `both` that states both sides' figures on one sub-measure is a PAIR (DIRECT).
- Pool diagnostic per PAIR (code, no model, not a decision input): were both quotes shown to one model call in production today, in the
  overview (`_overview_pool` with today's flag) or in a facet unit (a facet can hold both sides' quotes), after the `MAX_QUOTE_CHARS` cut in
  `write_unit`? A set counts as "shown" if any of its pairs was.
- Per-query decision (pre-registered), query = 6 sets: PAIR in < 2 sets -> retrieval gap (Step B-retrieval); PAIR in >= 2 sets -> writer
  gap (Step B-writer). Mixed results run each arm on its own queries only.
- Labeller: Claude, blind to model and run. Before labelling, the plan's sha256 goes into CURRENT.md (frozen). The user spot-checks every
  PAIR set plus any query within one set of its threshold.

## Step B-writer (queries with a writer gap only)
- Hypothesis: given one comparable pair, under the production writer prompt, the model writes a sentence that compares the two sides.
- One decision per call: write. Comparability is decided by the Step A labels, not the model; finding pairs in code is a separate later
  plan with its own probe against these labels (page `dates` are publish dates, not the period a figure refers to, so they cannot decide it).
- Cases: every Step A PAIR of the selected queries, deduplicated by quote pair; floor 20 (project rule), else the step reports
  "insufficient" and stops.
- Call: `_prompt(query, "the whole request", "How do A and B compare on <sub-measure>?", qmap, 1, 2, None)`, `_schema(sorted(qmap), 1, 2)`,
  then `check(sentence, qmap, query)` per sentence. `qmap` = {n: quote_a, m: quote_b}; same source (n == m) or a `both` quote: one entry,
  texts joined with a space as `write_unit` does. Stated deviations from production: first draft only (no repair, no verbatim fallback),
  a 2-quote pool, a question naming the sub-measure, 1-2 sentences (overview uses 2-4).
  `call_json(model, ..., think="minimal", timeout=180, seed=s)` with `settings.model_options`: ornith-1.5:9b T 0.6 top_p 0.95 top_k 20
  presence 0, ctx 262144; gemma4:12b T 1.0 top_p 0.95 top_k 64, ctx 262144 (`src/tools/config_template.yaml:35-36`, same in the live
  `~/.deepdelve/config.yaml`). 3 seeds (0, 1, 2)
  per case, since neither runs at T 0.
- Models: the two leading candidates (wiki Default-Model: no default designated, so these satisfy "default plus one other"), sequential,
  `rocm-smi` before each load. Before the run, raw API test per model of what reaches it: ctx 262144 loads, and reasoning length and
  eval_count for the think value the code sends vs the supported values (project thinking rule); Ollama version recorded.
- COMPARES rubric (fixed now): names both sides; states both figures (or both directions) as the cited quotes give them; a relation word
  (higher, lower, more, less, than, whereas, while, compared with) that is true by those figures, or for direction pairs, true by the size
  words; keeps the quotes' measure and period; INDIRECT pairs also say the evidence comes from separate trials. Labeller blind to model
  (rows shuffled, model stripped); user spot-checks 10 rows.
- Bar, per model, both must pass: >= 80% of case x seed rows where some sentence COMPARES and every sentence is `check` clean; 0 rows with
  a wrong relation direction. Exceptions and invalid JSON count as failed rows.
- Per-row output `w4_probe_<model>.jsonl`: case id, seed, raw output, check defects, label.
- Pass -> separate build plan (comparison unit fed by found pairs, report line keyed to it) after the pair-finding probe. Fail -> recorded.

## Step B-retrieval (queries with a retrieval gap only; a ceiling test, not a planner test)
- Hypothesis: per-side questions that name the measure and the period return pages holding a pair more often than the planner's questions.
- Arms, interleaved in one session, 2 runs each, matched: the same facets (the query's facets from its first saved ornith set, names and
  entities unchanged, since `select_ce` and the judge score against them, `run.py:496-544`) and the same number of questions per facet.
  (current) that set's saved questions; (new) hand-written questions naming side, sub-measure and period ("Iceland unemployment rate 2009
  2010 2011"). Hand-written questions bypass `_plan`, so `drop_years` (`run.py:415`, called `:443`) does not touch them. Round 1 only, as
  production builds it (incl. its q0 + name query, `run.py:652`); no gap or answer-gap rounds, so no `reformulate_question` call. No writer.
- The quote stage is the only model call: run with ornith-1.5:9b at its `model_options` (one model: the arm comparison is about the
  questions; the quote model is held fixed). Per-row output: query, arm, run, question, urls, `empty_searches`, quotes, then Step A labels.
  Fetching runs set `DEEPDELVE_FORCE_VIRTUAL_DISPLAY=1`. Quotes go to Step A labelling with arm and run stripped.
- Bar: the new arm has a PAIR in both runs for a query where the current arm has none in either run, for >= half of the selected queries.
- Pass shows a ceiling only. A planner that writes such questions contradicts its "no dates" rule (`run.py:427`) and is a second model
  hypothesis with its own Phase 0. Fail -> the comparison stays disclosed as not verified.

## Order and approvals
1. Step A (no GPU): script, freeze hash, labels, pool check, per-query decision. Results to CURRENT.md, then the wiki.
2. EGM review of the selected Step B arm(s) with Step A's numbers, before running. GPU and fetching need the user's OK.

## Step A result (2026-11-22, offline; `w4_pairs.py` dump of 478 quotes, labels + decision `w4_labels.py`)
Labelled against the frozen plan (sha256 7358442a...). Two labelling slips caught by the script before the decision and fixed: the
form rule (figure vs direction) was not enforced, and quarterly GDP growth was paired across different quarters.
- c3: PAIR in 5/6 sets (strict = lenient) -> writer gap. Every pair is the unemployment peak: Iceland 10.30% (June 2009, one Trading
  Economics sentence) against Ireland 16.1% (early 2012) or 15.1% (Feb 2012). Only 2 distinct quote pairs. No GDP pair: the two sides' growth
  figures never share a year or span. Pool diagnostic: both quotes shown to one model call in 2 of 5 sets (both gemma), and those reports
  still make no comparison (graded 2026-11-20); in 3 of 5 the pair never reached one unit.
- c2: PAIR 0/6 strict, 1/6 lenient (heavy-drinking NNT 4.3 semaglutide vs 10 naltrexone, both vs placebo, set 08d42833) -> retrieval gap
  either way. Semaglutide has trial figures on drinks per drinking day, heavy drinking days and total consumption; naltrexone has none on
  those sub-measures in any set (only NNTs, effect sizes g, days abstinent, relapse rates, or "modest").
- c4: PAIR 0/6 -> retrieval gap. Mexico: drink-volume % changes in every set. UK: no drink-volume or sugar-purchase change attributed to
  the tax; only obesity, reformulation, calories (6,600 kcal/yr), "billions of teaspoons", or "little impact" (Nielsen, unspecified measure).
- B-writer (c3): 2 distinct pairs < floor 20 -> "insufficient", stops as pre-registered.
- B-retrieval (c2, c4): selected. Needs EGM review with these numbers, then the user's OK (GPU + fetching).
- Spot-check for the user: every PAIR set (c3: 0b01db12, 358ed7fd, b467d1e4, 4de6c41e, 9542472c) and c2 (one set from its threshold under
  the lenient count).
