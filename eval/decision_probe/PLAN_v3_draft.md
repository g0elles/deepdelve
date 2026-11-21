# Decision-model probe, round 3: scope (S) with one criteria question (CLOSED 2026-11-20 WITHOUT RUNNING, see PREREG Amendment 3; DRAFT v4 kept as the record)

Screening run. Revised after three EGM reviews (v1: 5 blockers / 9 majors; v2: 7 / 10; v3: 4 / 9) and three user decisions (2026-11-20): one short
question with criteria as the only arm; referent = request and facet questions; pass/fail at the pre-registered 0.5, best cut-off reported
without a bar.

## Why scope only
Task A (does a quote answer a facet) is not rerun. The round 1-2 cut-off sweep (`eval/decision_probe/notes_round12_sweep.txt`, post hoc)
found a cut-off meeting A's pair-level bars (precision >= 0.85, recall >= 0.80) for clef-flash, d3:4b and nimble-v3:9b, so A failed on
calibration at 0.5; its next step is a cut-off set on one labelled set and checked on a fresh one (needs new labels, separate decision).
For S no cut-off meets label-grade for any model; d3:9b is a near miss (30/44 out flagged at 77/498 = 15.5% not-out, limit 15%). The
question format is one untested variable for S.

## Terms and data
`eval/judge_scope/candidates.jsonl`, 542 rows, labels from the 2026-11-19 scope probe (`eval/judge_scope/probe_scope.py`): out = 44 (the
sentence reports on a different thing, place, period, climate or condition, or population than the request and facet questions name);
general = 136 (the sentence names nothing beyond what the request asks about); in = 362; not-out = general + in = 498. 442 English,
100 Spanish (`pipeline.run._is_spanish` imported by probe.py, applied to the request text). `disputed.json` is empty. States as `probe.rows("S")`: request, facet,
facet_questions, sentence.

## This is attempt 3 on this data
Rounds 1-2 used these rows. The wording below was informed by reading labelled rows (two reviews; the 44 out rows tagged by what differs:
subject about 24, place about 12, nearly all intercity routes naming Medellín and another city, climate or condition about 7, time 2,
group 0). It is frozen in one pass as PREREG Amendment 3 and committed before any model call. No round 4 on this data, whatever the
result, including a gate fail.

## Changes versus rounds 1-2
- A `criteria` field (one line each for true and false).
- The question rewritten into one short question asking what the sentence is "mainly about" (PREREG's question asked what it "reports on").
- What counts as different is now spelled out by the criteria: a narrower or broader version of something the request or facet questions
  name is not different; something of a kind they do not name is not different. Round 1-2's clause about sentences naming nothing is
  replaced by this.
- A pass earns only a fresh-set test (PREREG said "design revision").
- Two models, the two highest round-2 S AUROCs (nimble-v3:9b-q8_0 0.735, d3:9b-q8_0 0.852; source `probe.py --score` on `runs/`); no
  claim about clef-flash or d3:4b.
- Unchanged: rows, states, labels, the referent (request and facet questions, PREREG line 11), bars, threshold 0.5, "One question per
  request" (PREREG line 5), answer key `q`.

Hypothesis: one short question with true/false criteria, the documented noul shape, flags out rows well enough to meet the S bar on both
models.

## The question (literal; sent exactly; `probe.py` Q3)
```json
{"model": "<m>", "state": {"request": "...", "facet": "...", "facet_questions": ["..."], "sentence": "..."},
 "questions": {"q": {"type": "noul",
   "instructions": "Is the sentence mainly about a different subject, place, time period, climate or condition, or group of people than the request and facet questions name?",
   "criteria": {
     "true": "The sentence is mainly about a subject, place, period, climate or condition, or group that differs from one of the same kind the request or facet questions name.",
     "false": "The sentence is mainly about what the request or facet questions name, a narrower or broader version of it, or something of a kind they do not name."}}}}
```
Answer: `answers.q.noul`, a float in [0, 1]; anything else is an error. Out if >= 0.5.
"Mainly about" follows the labels: a sentence comparing Medellín with Cali's system is labelled in; an intercity route sentence is out.
Expected misses written in advance: rows where the difference is an implicit subject or near-synonym (gas boiler vs furnace, heavy trucks
for EV incentives, child benefit for parental leave, heat pump water heaters) and intercity rows naming only Medellín; reaching 30/44
needs 4 more out rows than round 2's best (26, d3:9b).

## Step 0: criteria gate (per model, before any labelled row; `probe.py` GATE)
Thirteen hand-written items, not from the labelled data or its topics. Facet = the request text verbatim, facet_questions = [request].
Expected answer in brackets.
1. "How much do bike lanes cost to build in Copenhagen?" / "In Amsterdam, a kilometre of bike lane costs about 1 million euros." [true, place]
2. same request / "In Copenhagen, a kilometre of protected bike lane costs about 1.5 million euros." [false]
3. "What are wheat yields in Kansas?" / "Corn yields in Kansas averaged 130 bushels per acre." [true, subject]
4. "What were coffee prices in 2010?" / "Coffee prices rose 20 percent in 1994 after a frost." [true, time]
5. "How warm do houses stay with wool insulation?" / "In Scotland, wool-insulated houses kept 18 degrees indoors." [false, request names no place]
6. "How do e-bikes affect commuting in Utrecht?" / "E-bike commuting rose 30 percent in Utrecht, more than in Rotterdam." [false, extra comparison value]
7. "What do dog vaccines cost in Texas?" / "In Austin, a rabies vaccine for a dog costs about 25 dollars." [false, narrower place]
8. "How effective is aspirin for adults over 65?" / "In children under 12, aspirin raises the risk of Reye's syndrome." [true, group]
9. "How much water do rice farms use in the dry season?" / "During the monsoon, rice paddies need little irrigation." [true, condition]
10. "What did the 2019 study find on sleep and memory in students?" / "According to a 2024 summary, the 2019 study found students who slept 8 hours recalled 20 percent more words." [false, publication year]
11. "How does the bus network in Lyon work?" / "Across France, city bus networks are run by regional transport authorities." [false, broader place]
12. "How does the metro in Lisbon work?" / "Buses from Porto to Faro run 12 times a day." [true, intercity]
13. "¿Cuánto cuesta el pasaje del metro de Santiago?" / "En Lima, el pasaje del metro cuesta 1,5 soles." [true, Spanish, place]
Arms: (a) as frozen; (c) true and false criteria swapped (diagnostic only, explains a fail); (b) no criteria (reported only). Gate passes
if arm (a) is on the expected side of 0.5 on >= 11 of 13 AND on all six expected-false items (2, 5, 6, 7, 10, 11), the ones only the
criteria decide. One try per call; an error in arm (a) counts as wrong. The gate runs once per model: its file
`eval/decision_probe/runs_v3/gate/<model>.jsonl` is created exclusively and never re-run or overwritten; a resume reads the stored verdict.

## Bars (PREREG) and outcomes
Fixed denominators 44 and 498, checked against `rows("S")`. A missing or errored row counts worst case (out row = not flagged; not-out row =
flagged); duplicate ids = no grade; 11 or more of 542 rows errored or missing = no verdict for that model. A model whose gate did not
pass gets no verdict, and its labelled rows are not run.
- Drop-grade: >= 30/44 out flagged AND <= 24/498 not-out flagged. Label-grade: >= 30/44 AND <= 74/498. Else fail.
- Baseline for scale: round-2 not-out rates 25.9% (nimble-v3:9b), 10.8% (d3:9b); lowest so far 4.0% (clef-flash, 19/44 out).

| Result | Outcome |
|---|---|
| Both models pass the gate, have a verdict, and are label-grade or better | Eligible for ONE separately pre-registered test on a fresh labelled set; nothing plugs into the pipeline before that |
| Anything else | Closed for this question on these two models, at the pre-registered cut-off rule; per-model numbers recorded. A d3:9b-only pass is not grounds for a round 4 |

## Reported (no bar)
Primary: Wilson 95% intervals for both bar rates (z 1.96, fixed denominators); AUROC on answered rows against the same model's round 2;
paired 2x2 per class (out, not-out) against round 2 at 0.5, errored round-3 rows counted worst case. Exploratory: the highest cut-off
flagging >= 30/44 out rows and its not-out rate (same rule as `notes_round12_sweep.txt`), to read a fail as ranking or calibration;
general rows flagged; rates by language (English question on Spanish rows is deliberate, as production would ask in English).
Exploratory lines use answered rows only and say so.

## Code and run
`eval/decision_probe/probe.py`: `--v3 --model <m>` runs the gate (once) and, if it passed, the labelled rows into
`eval/decision_probe/runs_v3/<model>_S.jsonl` (":" becomes "_"); `--gate` runs only the gate; `--v3 --score` runs `score3()`, which reads
only `runs_v3/*_S.jsonl`. Round 3 never writes to `runs/`. One try per row; a row is written once answered or errored. On a connection
error, or 3 errors in a row, the run stops without writing that row and can resume (resume skips written ids, never reruns the gate).
`--dry` prints the row-0 and gate-1 bodies with the same JSON encoding as the request, checked against this text before any GPU call.
One model at a time; rocm-smi shows no other model loaded first; publisher Modelfile context; 120 s timeout. Cost per model: 542 row calls
+ 39 gate calls, a few minutes. No downloads, no new labels.
