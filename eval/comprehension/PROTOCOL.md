# Comprehension and writing diagnostic (pre-registered 2026-10-24, before any model output was seen)

Question: when the agent's sources are real full-text papers, does it fail at READING them, at WRITING from them, or both? The 2026-10-24 defect audit
could not say (most defects were attributed to the writing step, but reading and writing were never tested apart).

## Material
10 papers chosen by a fixed rule, not by me: for each of 10 fixed queries (select_papers.py) the first result, in the API's own rank order, that is
open access with a CC BY licence, published 2026 (after the model's stated knowledge cutoff of 2024-06, so memory cannot answer), and whose full text
is 3,000 to 8,000 words. 5 biomedical (Europe PMC), 5 other fields (OpenAlex). Paper text stays local (gitignored); only DOIs, my questions and keys,
and scores are committed.

## Key sheets (written by me after reading each paper completely, before running the model)
About 8 questions per paper, each with a short keyed answer and where it is in the paper. Types: N number with its unit, C comparison (what against
what, including pairs that are NOT comparable), L limitation or scope the authors state, D direction (causal vs correlational, increase vs decrease),
U unanswerable (the paper does not say; the right answer is "not stated").

## Test R (reading, ceiling)
The model (deepdelve-gpt-oss via the live config: temperature 0.2, num_ctx 65536, no thinking) gets the DocumentAnalyzer instructions, the whole paper in
context and one question at a time. In context, not through the 300-line read tool: this is the best case for comprehension. If reading is poor even
here, tool mechanics are not the main cause. Graded by me against the key: correct / partly / wrong / (U) correctly says not stated / invents an answer.

## Test W (writing, from perfect input)
A findings file written by me from the key (every entry true, with its source URL, plus two entries that must not be compared) goes to the Builder
instructions; the model writes the report. Every sentence of the report is labelled with the audit taxonomy (C1 unsupported, C3 overstated, C4
mismatched comparison, C2 wrong citation, Q1/Q2, padding S2). Any defect here is the writer's, because the input was perfect.
Limit: a single writer call, not the review and fix loop.

## Decision rules (fixed now)
* Reading is "a bottleneck" if more than 20 percent of keyed answers are wrong or invented, or more than 25 percent of U questions get an invented answer.
* Writing is "a bottleneck" if more than 10 percent of report sentences carry a defect of any type, or any C4 or C1 defect of severity 3 appears in
  more than 2 of 10 reports.
* Both can hold. Neither holding means the failures come from the loop or the long context, and the next test is the loop.
* No prompt, model or loop change is made on this evidence alone; it only says where to look.

## Limits
One model, one run per item, one labeller (me, key written before the run), 10 papers, 80 questions. Intervals will be wide; report Wilson 95 percent intervals.

## Addendum A (written 2026-10-24 after the key sheets, before any model output)
* Key sheets: 101 questions over the 10 papers (20 U, 81 N/C/L/D). Several papers contradict themselves (b1, b3, n3, n5) or reached the model garbled by PDF conversion (n1, n4: reversed table text and interleaved columns). Both are real conditions of the agent's inputs and are kept, not cleaned.
* Test R prompt, arm A: the DocumentAnalyzer role text from `src/prompts.py` with the tool workflow replaced by "the whole document is below", asking for the answer with an exact verbatim quote. It does NOT say what to do when the document lacks the answer (the real analyzer prompt does not either). Arm B repeats only the 20 U questions with one added sentence telling the model to answer "NOT STATED" if the document does not say. A vs B separates a missing instruction from a missing capability.
* Settings: deepdelve-gpt-oss through Ollama's native chat endpoint, think=false (as the agent sends it), temperature 0.2, num_ctx 65536, seed 42, one run per item.
* Grading: by me against the key, one of: correct / partly (right core, wrong or missing detail that matters) / wrong / invented (U only: gives an answer instead of NOT STATED) / correct-U. For items where the paper contradicts itself, "correct" means following the table the key names or flagging the conflict. Graded before looking at any aggregate.
* Model outputs contain paper text (CC BY, but the repo is public): outputs stay local (eval/comprehension/results/, gitignored); only the grades and scores are committed.
* Cost: about 100 calls, prefix-cached per paper, expected under 45 minutes of GPU.

## Addendum B (2026-10-24, after the gpt-oss R and W runs; before any other model was run)
The owner asked to repeat the tests with another model. Same keys, prompts, options (temperature 0.2, num_ctx 65536, seed 42, think=false) and grading; only the model changes. Candidates already on disk (no download): `ornith-1.5:9b` (thinking control already raw-tested 2026-10-03: think=false gives no thinking text) and `gemma4:e4b`. This is a diagnostic ceiling comparison, NOT a bake-off verdict under the Model Evaluation Standard (one run, one sampling setting, the other model's recommended sampling not used, no full-agent run); it informs where to look and decides nothing about the default model.

## Addendum C (2026-10-24, before any low-effort output)
Why: gpt-oss with think=false gets no reasoning instruction and runs at its default (medium), and 19 of 101 reading calls ended in a reasoning spiral with no answer. The project's own lever for this model is `settings.thinking_effort` (low/medium/high; raw sweep recorded 2026-09-30). Arm `gptoss_low`: same model, keys, prompts and options, only think="low". Decision rule, fixed now: low "helps" if empty answers fall to 5 or fewer of 101 AND correct answers do not fall by more than 5 compared with the default arm (74 correct, 3 partly); otherwise it does not. The writing step is repeated at low and labelled the same way. The live config is not changed; this is a measurement only.

## Result of Addendum C (2026-10-24, gpt-oss think="low", single run, one non-blind labeller)
* Reading: 0 empty (default 19). 66 correct + 19 right "not stated" = 85 (default 74), 6 partly (3), 8 wrong (5), 2 invented on unanswerable (0). Of the 19 former empties, 13 are now right. Rule met (empties <= 5, correct not down). Cost: wrong plus invented rose from 5 to 10 of 101 (10 percent, still under the 20 percent bottleneck line); the new errors cluster in the traps (false premises, self-contradicting papers) and in the two garbled papers (n4 invented a photon energy, n1 an agreement statistic). Speed: about 8 s per answer, no spirals.
* Writing: 71 of 349 units defective (20.3 percent; default 55 of 356, 15.4 percent), 0 severity 3, 14 severity 2 (13), 9 of 10 reports cite inline (7 of 10). Low effort does not improve writing; the defect rate is higher, mostly padding and invented "future work" lines, and labelling was not blind (done after the default set), so treat the gap as noise-to-worse, not as a verdict.
* Reading: low effort fixes the empties but not the ability to refuse false premises or catch self-contradictions. Writing is a bottleneck under both settings (rule: more than 10 percent).
* Files: grades_R_gptoss_low_A.json, labels_W_gptoss_low.json (texts stay local).

## Result: gemma4:e4b (2026-10-24, think=false, which is a real off switch for this model; single run, one non-blind labeller)
* Reading: 0 empty, 57 correct + 20 right "not stated" = 77, 13 partly, 11 wrong, 0 invented. About 9 s per answer. Weak spots: accepted 2 of 3 false premises, treated a non-significant AUC difference as significant, missed the paper's own inconsistencies, and on the garbled physics paper gave a wrong formula and said the metrics distinguish crystals from glass.
* Writing: 29 of 218 units defective (13.3 percent), 1 severity 3 (n5: says the LSTM ablation hurt less than the hierarchy ablation, the opposite of the numbers), 10 of 10 reports cite inline. Reports are shorter and stay close to the findings; defects are mostly puffery and wrong acronym expansions.
* Not a verdict: diagnostic only (Model Evaluation Standard not applied).
* Files: grades_R_gemma4_A.json, labels_W_gemma4.json.

## Result: gemma4:12b (2026-10-24, think=false, real off switch verified on the raw API; single run, one non-blind labeller)
* Reading: 0 empty, 70 correct + 21 right "not stated" = 91, 6 partly, 4 wrong, 0 invented. About 8 s per answer. Rejected the "twice as well" premise on both papers, caught the abstract-versus-results mismatch, named the LSTM ablation as worst and said the ring metrics do not separate crystals from glass. Wrong on 4 trap items (HIV vs school children significance, text/table quantization consistency, the 34.2 percent reward claim, the "all correlations under 0.8" claim).
* Writing: 25 of 213 units defective (11.7 percent, rule 10 percent), 0 severity 3, 10 of 10 reports cite inline. Defects mostly puffery, a few overstated strengths, one wrong acronym expansion (MMQR).
* Not a verdict: diagnostic only (Model Evaluation Standard not applied).
* Files: grades_R_gemma4_12b_A.json, labels_W_gemma4_12b.json.
