# Writer defects from the comparison batches: plan (v6; W1, W2, W3 BUILT 2026-11-20; results at the end)

Reviews: v1 not ready (4 blockers); v2 not ready (2 blockers, 10 majors); v3 not ready (2 blockers, 8 majors: corpus too small for
the W3 bar, strip_from_artifact writes to disk; W1/W2 judged ready). v4: pure in-memory strip, per-rule
flags and blind labelling in credit.py, exact separator/DOI/publisher rules, and W3 judged by unit tests plus 0 REAL removed (raw counts,
no rate bar, since the corpus is too small). v4 review: W1, W2 BUILD yes; W3 1 blocker (tests used figures the
extractor never sees), 8 majors; v5 fixes them (equality-only publisher match, audit agent-mode corpus added, module-constant switch).
Comparison-not-made (W4) is a separate planner plan by user decision (2026-11-20): a table without a comparing sentence does not count.

## Corpus and honesty notes
- Report dirs: column 7 of `eval/comparison_runs/runs.tsv` (18 v2 reports incl. the 6 ornith precise reruns),
  `runs_v2_ornith_presence1.5.tsv` (6), `runs_v1_16k_t0.tsv` (12). Each has `final_report.md`, `_run_state.json`, `src_*.md`.
- The 6 presence-1.5 reports' evidence dirs were overwritten by the precise rerun (same `cmp2_ornith_c*_r*` names): for those 6 use only
  the report dir; source numbers come from the report's own `## Sources` list; they are excluded from facet-dependent counts.
- No held-out set: the W3 junk examples ("Trading economics", "International monetary fund", "Reino unido") came from grading the v1
  reports, so the v1 12 are secondary, not unseen. Fresh evidence is the next live batch, whenever one runs. Raw counts are reported.
- One labeller; rubrics fixed below before labelling. Results drafted in `session_status/CURRENT.md`, published to the wiki.

## Offline credit procedure (all items; no model call, no GPU)
Script `eval/writer_defects/credit.py`, modelled on `eval/audit_runs/replay_checks.py:Run.activate`. Old code = commit 5dd7891; proof of
no change since the batch: `git diff b51661d~1 5dd7891 -- src/utils/grounding.py` is empty. Rules off reproduces old code exactly
(the :1496 whole-report substring test, `## Sources` included).
Corpora: v2 (18), pres1.5 (6), v1 (12) compiled-writer reports (column 7 of the three `eval/comparison_runs/*.tsv`), plus the agent-mode
audit corpus `eval/audit_runs/runs.py` RUNS (10, corpus "audit") and VAL_RUNS (8, "audit_val"): 54 reports.
1. Per report dir: set the fetched-URL ctx from `_run_state.json["fetched_urls"]` (flat; url/filename/title) and patch
   `grounding.get_workspace_file_content` to read the dir's `src_*.md` (as `eval/writer_ab/skeleton_replay.py:50-54`).
2. In memory only: cut `## Where sources differ` (to the next `## `) and apply `reader_notes.strip_block(text)`
   (`src/utils/reader_notes.py:68`).
   Never `strip_from_artifact` (writes to disk). Saved reports are never written.
3. Sanity (compiled-writer corpora): build a `Compiled` from the saved `## Sources` list and pass old-code hits through
   `compiled.add_disagreements` (`compiled.py:89-102`); compare its lines on (subject.lower(), figure, other_figure, n); an `m` mismatch is reported "order-dependent", not a fail with the saved
   lines parsed by `^- \*\*(.+?):\*\* (.+?) \[(\d+)\] but (.+?) \[(\d+)\];`. URLs are never normalised (`source_filename` =
   sha1(url)[:8], `bridge.py:12-14`); a URL with ")" or any mismatch = sanity fail, listed and excluded. No differ section = zero lines.
4. Switch: module constant `_XSRC_RULES = frozenset("abc")` in `grounding.py`, read by `cross_source_hits` at call time (never a default
   argument or import-time copy); credit.py and tests reassign it inside `orig = g._XSRC_RULES; try: ... finally: g._XSRC_RULES = orig`.
   No caller changes. Determinism: `grounding.py:1464` loops over `sorted(set(fetched.values()))` (was hash order, so `other_file` varied
   per process). Runs: {}, {a}, {b}, {c} (per-rule removals, reported) and cumulative {c}, {c,a}, {c,a,b} (`removed_by` = the first
   cumulative run whose key set drops the key). key = (subject.lower(), figure, other_figure). Assert new keys are a subset of old keys.
5. `eval/writer_defects/hits.jsonl`: union of old and new hits, `{run: report dir, corpus, sanity, subject, figure, other_figure,
   cited_files, other_file, other_url, segment, other_line, removed_by}`. `cross_source_hits` gains two optional dict keys, `segment` (the
   cited text around the figure) and `other_line` (the source line holding the other figure); existing consumers read named keys only
   (`compiled.py:96-100`, `grounding.py:1509-1511`); excluded from the key.
6. Labelling: export `hits_to_label.jsonl` without `removed_by` or provenance, one row per (key, other_file) (every other_file instance of
   a key rule (b) removes); I label it into `labels.jsonl` `{key, other_file, label, reason}` before the join. Partly blind only: I wrote the rules and some pairs reveal their rule (e.g. 11.400 vs 11,400); recorded.

## W1. Spanish comparison requests not detected (code only)
`src/pipeline/run.py:38 COMPARISON_RE` is English-only; `evidence["comparison"]` is false for c4 ("compara"), so `compiled.py`
`_sides` (:134), the bridging overview quotes (:162) and the comparison line (:409-416) are off for Spanish requests.
A word list is accepted here: the regex is already one, the alternative is a model call, and Spanish is the only language added
(English first, Spanish is the one other language in the eval set).
New regex: keep the English part unchanged; add `\bcompar(a|an|ar|ado|ada|ados|adas|ativa|aci[oó]n)\b`, `\ben comparaci[oó]n con\b`,
`\bdiferencias?\b`, `\bdifiere(n)?\b`. No "frente a" (means "facing", as in "la recuperación frente a la crisis"). No contra rule.
Test `test_comparison_re_spanish_and_english` in `tests/test_pipeline_plan_coverage.py` (next to :57-62): c4 query true; "¿Cómo fue la
recuperación frente a la crisis de 2008?" false; "ventajas y desventajas de X" false; existing English cases unchanged.
Bar (distinct queries, from `eval/comparison_runs/run_all.sh:5-8`, `eval/heldout3_runs/queries.txt` and every `metrics.json["query"]` in
the skeleton_replay globs): c4 becomes true; c2, c3, h10, h12 stay true; h17, h18 stay false; report the count of distinct queries that
changed. Not matched (noted in a comment): enclitic "compararlos", "comparativo". Known risk: a non-comparison query with "diferencias regionales" sets the flag (same breadth as English
`differences?`). Blast radius: only `compiled.py:86` reads the flag; `skeleton_replay.py:43` and `eval/coverage_probe/replay_slots.py:42`
recompute it (pick up the change automatically).

## W2. Duplicate section headings (code only)
Target: `research_output/does_semaglutide_reduce_alcohol_consumpt_579dac_20261120_191829/` (evidence `cmp2_ornith_c2_r2`), facets f1
(entity semaglutide) and f2 (entity naltrexone) both "Alcohol Use Disorder".
Code: extract `facet_headings(facets) -> list[str]` in `src/writer/compiled.py`, called by `render` (:389) and by the gap-disclosure
line (:406) so both show the same name. Collision = same text after the normalisation of `find_duplicate_heading_text`
(`completion_checks_grounding.py:360`, regex `^[0-9A-Za-z][.)]\s*` + lowercase), copied with a pointer comment (compiled.py does not
import the engine);
each colliding heading gets " (<entity>)", or " (<id>)" when entities are equal or empty. Never merge. `compiled.py:370` keeps passing the unsuffixed `f.name` to `write_unit` (the model sees the plain name), on purpose.
Test in `tests/test_writer_compiled.py` (module-level asserts): the f1/f2 pair gives "Alcohol Use Disorder (semaglutide)" / "(naltrexone)"; unique names unchanged.
Credit: substitute the k-th facet `## ` line (between `## Answer in brief` and the first label heading) in the saved report with
`facet_headings` output. Bar: `find_duplicate_heading_text(report) == []` (n = 1, cosmetic). `find_duplicate_report_sections`
(content overlap) reported separately.

## W3. "Where sources differ" false hits (code only, shared function)
`src/utils/grounding.py:cross_source_hits` (:1441). Callers, all unchanged: `tui.py:2168` (live, then `compiled.add_disagreements`),
`find_cross_source_contradictions` (:1507; completion check `completion.py:186`, body `completion_checks_grounding.py:1022-1041`,
builder_fixable, runs in every writer mode incl. agent mode), `eval/audit_runs/replay_checks.py:32`, `skeleton_replay.py:54,62`.
Every rule only removes hits.
(a) Same number, different separators. Parser: strip whitespace (incl. U+202F) and "%"; a `.` or `,` followed by exactly 3 digits and
    then a non-digit or end of token is a thousands separator, unless the integer part is "0"; any other is a decimal point. A token
    that still has more than one decimal point (e.g. "20.11.2026", "1.2.3") returns None and falls back to today's string comparison.
    Currency (`_figure_kind == "currency"`, the regex captures prefix plus one digit, :1100) keeps string comparison. At :1490 equal
    floats are not a hit. At :1496 the substring test stays; additionally a hit is skipped when a same-kind figure in the prose
    (excluding `## Sources`) parses to the other figure's float. `ponytail:` comment: "1.500" equals "1,500"; "11.400" reads as 11400.
(b) Source-name subject, host only. Normalise (lowercase, spaces and punctuation removed) the subject and the registrable host label
    (`data.worldbank.org` -> `worldbank`; public suffix = last label, or last two when the second-to-last is gov/gob/gouv/govt/com/co/
    org/net/ac/edu/or/ne). Sources: the hit's `cited_files` + `other_file`, non-stub `get_fetched_urls()` entries by filename. Drop when the
    subject EQUALS the host label. No title matching (a title segment is often the page topic: "Country profile - United Kingdom").
    `ponytail:` ceiling: a real hit whose subject equals a source's host label (incl. country-named government domains such as
    newzealand.govt.nz) is lost.
(c) DOI-shaped figure or other figure: `re.fullmatch(r"10\.\d{4,}", token_without_spaces)` and `_figure_kind == "decimal"`.
    `ponytail:` ceiling: a real 10.xxxx value with >= 4 decimals is dropped.
Rubric: REAL = the two sources give different values for the same quantity of the same thing at the same time; otherwise JUNK. Nothing
about who the subject is (a World Bank figure vs another source's figure for the same quantity is REAL).
Tests in `tests/test_grounding_checks_core.py`. `_run` (:127-141) is extended to take (url, body[, title]) and passes `title=` to
`record_fetched_url`; tests stay inside the memory-workspace contextvars wrapper (:180-188) and set `_XSRC_RULES`. Fixtures use
extractable figures (%, separator, year or currency), capitalised 2+-word subjects, a lowercase verb between subject and figure on both
the report line and the source line (`_PROPER_NOUN_PHRASE_RE`, grounding.py:299-300, swallows a dotted number that directly follows a
subject: "Sector Fintech 1.5%" gives subject "sector fintech 1.5"), as in the existing "Sector Fintech grew 12%" (:149), and shared
context words; every "no hit" test first asserts the hit exists with `_XSRC_RULES = frozenset()`.
- (a): "11.400" vs "11,400" no hit; "1.5%" vs "15%" hit; "11,4" vs "11.400" hit; "0.125%" vs "125.0%" hit; "20.11.2026" vs
  "20.11.2025" falls back, no crash, hit; parser unit test for each token.
- (b): "Trading Economics" on host tradingeconomics.com no hit; "New York" with title "Rents rise - The New York Times" on nytimes.com
  hit; "United Kingdom" with title "Country profile - United Kingdom" hit.
- (c): "10.1176" vs "1,2" no hit (the real semaglutide case); "110.12345" vs "1,2" hit.
Verdict matrix (`tests/test_completion_verdict_matrix.py`, 7-tuple `(name, True, files, None, None, "", sources)`): existing 12% vs 18%
row stays; add 11.400 vs 11,400 with expected_problem None.
Acceptance, decided in advance (the corpus is too small for a rate bar):
1. Unit tests above pass; precommit and `tests/test_structural_checks.py` pass.
2. 0 REAL hits removed across all 54 reports, reported with its denominator (old-code REAL hits per corpus, removals per rule from the
   single-rule runs). Rule X is "tested" only if at least one REAL-labelled old-code hit reached X's comparison and survived it ((a):
   both figures parse and are the same non-currency kind; (b): the subject was compared against a host label; (c): a decimal figure
   starts with "10."); otherwise "untested for rule X", not passed.
3. Per run: every run in RUNS (labels in `eval/audit_runs/labels/`) or VAL_RUNS (`labels_validation/`, read with
   `AUDIT_SET=validation`) that has a C5 label and on which the old code fires still fires with all rules on. Old hits removed in those
   runs are listed for manual review. (C5 labels are per-run defect records, not hits.)
Reported, no bar: hits old/new by `removed_by`, REAL/JUNK by key and hits by run, distinct queries per corpus. Known unremoved junk:
"International monetary fund" (other source en.wikipedia.org, different quantities), "Reino unido 1.000/9.000 vs 11,400". Historical
audit counts (e.g. `PHASE_C_VERDICT.md`) will not reproduce, recorded.
Side effect: count reports where the comparison line (`compiled.py:409-416`) would newly appear, cites from saved `[n]` markers, with and
without W1; if unreliable, drop the count and say so.

## Recorded, not in this batch
- W4 comparison: planner plan, one same-measure question per side (`run.py:426` forbids a comparison facet), Phase 0 probe first.
- Off-period figures: upstream (`run.py:423` "no dates", `drop_years` :411); planner/retrieval item, probe first. c4 "impact unknown until
  Apr 2018" is staleness (`compiled.py:255`).
- Comparator drop ("naltrexone slightly more effective" without "than acamprosate"), the one prose defect that cost points: count on
  saved reports first. Within-report conflict (Ireland peak 16.1% vs 15.1%). c4 UK retrieval gap. Grounded-but-wrong figures. qwen
  closing-turn `write_todos`.

## Order
1. `credit.py` + sanity reproduction. 2. W1, W2, W3 with tests; credit offline; label. 3. Precommit; commit with OK. No live rerun for
these (credit is code-only); the next live batch waits for the W4 planner change.

## Results (2026-11-20, offline, no model)
- W1: 22 distinct saved queries; only c4 changes (now comparison=true).
- W2: report 191829 duplicate headings ['Alcohol Use Disorder'] -> none ("(semaglutide)" / "(naltrexone)").
- W3 (`credit.py`, 54 reports): sanity 36/36 compiled reports reproduce their saved differ lines. Hits old 29 -> new 18 (removed: a 4,
  b 6, c 1). Blind labels (`labels.jsonl`): 29/29 JUNK, 0 REAL, so per the pre-set definition every rule is "untested" for REAL loss;
  audit agent-mode corpus (18 reports) has 0 hits old and new, so acceptance 3 is vacuous. Remaining 18 junk: metric-name subjects
  ("unemployment rate", "gdp growth rate" across different countries) and "international monetary fund" (different quantities).
  0 real disagreements in 36 compiled reports: next is a separate plan to drop the section or require an entity subject.
