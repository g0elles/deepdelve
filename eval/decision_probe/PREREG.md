# Decision-model probe: pre-registration (2026-11-20, written before any decision model was downloaded or run)
Question: can an Ollama decision model (/v1/systemone, typed questions -> calibrated probability, one forward pass, no generated text)
make the two single-decision judgements that failed on general models: (S) is a kept sentence out of the request's scope, (A) does a
quote answer a facet. API read in full: docs.ollama.com/api/systemone, ollama.com/blog/decision-models (2026-09-29).
Models (Apache 2.0, GGUF q8_0): d3:4b-q8_0 (4B) and clef-flash (9B). G2: two models. One question per request. (Amended 2026-11-20 before any
run: pplx-decider-flash was the planned 4B model but Ollama ships it only in MLX formats, which do not run on this Linux/ROCm machine.)
No production code depends on this probe; a pass leads to a design revision, not a build.

S, scope. Data: eval/judge_scope/candidates.jsonl, 542 labelled rows (44 out, 136 general, 362 in; 442 English, 100 Spanish), the
labels used by the 2026-11-19 scope probe, minus rows marked disputed there. State = request, facet, facet questions, sentence.
Question (noul): "Does the sentence report on a different thing or product, place, period, climate or condition, or population or group
than the request and facet questions name? A dimension the request does not name cannot differ. Answer false if the sentence only
names nothing beyond what the request asks about." Flag = noul >= 0.5 (the API returns calibrated probabilities; threshold fixed here).
Bar = the scope probe's Goal 1 bar on all rows (decision models do not need a judge-kept set): drop-grade = flags >= 2/3 of out rows AND
<= 5% of not-out rows; label-grade = >= 2/3 of out AND <= 15% of not-out; else fail. Reported, no bar: AUROC, rates by language.

A, answerability. Data: eval/coverage_probe/runs/slots_to_label.json + slots_labels.json, 486 (facet, quote) pairs over 128 facets,
348 labelled answering (rubric eval/coverage_probe/PREREG.md, labelled blind 2026-11-19). State = facet name as a question + quote.
Question (noul): "Does the passage state an answer, fully or in part, to the question: it states the attribute the question asks about,
for the entity the question names? Background, a definition, a related but different quantity, or a different entity does NOT answer."
Bar (both): (1) pair-level at 0.5: precision >= 0.85 AND recall >= 0.80; (2) pick: over facets with >= 1 answering quote, the
highest-noul quote answers in >= 0.90 of facets. Reported: AUROC, abstention (max noul < 0.5) on facets with no answering quote.

Decision rule: S label-grade or better, or A passing both lines, on BOTH models -> design revision for that judgement (where it plugs
in, fresh test set, Spanish check). One model only -> recorded, no change. Fail -> closed for these models, numbers recorded.
Per-row output: eval/decision_probe/runs/<model>_{S,A}.jsonl. Hardware: one model at a time, rocm-smi check first.

Amendment 2 (2026-11-20, after the first two models failed, before any run of the new ones): same tasks, data, questions, thresholds and
decision rule, applied to nimble-v3:9b-q8_0 (Bespoke Labs, Qwen3.5-9B base, CC BY-NC 4.0; JevBench intelligence 56.5) and d3:9b-q8_0
(vLLM-SR, Apache 2.0; unranked, its 4B scored 50.8 and 27B 72.9 on JevBench). Chosen as the highest-ranked decision models that run as
GGUF in Ollama within 16 GB (benchmarkheaven.com/jev-models, 2026-11-19). Each model at its publisher Modelfile context; raw
/v1/systemone check first. Reported per model and against the earlier two.

Amendment 3 (2026-11-20): round 3 for scope (one short question with true/false criteria, the documented noul shape) was planned in
`PLAN_v3_draft.md` and CLOSED WITHOUT ANY MODEL CALL after four EGM reviews. Reason: on these labels, a model that follows the criteria
cannot reach the bar. The final wording ("mainly about"; a narrower or broader version of what the request or facet questions name is not
different) exempts about 15 of the 44 out rows (candidates.jsonl lines: heat pump vs gas boiler 44, 118, 154, 164, 196, 210; heat pump vs
oil or electric furnace 7, 82; heat-pump water heaters 9, 12; Netherlands truck and commercial charging 242, 248, 250; "bus vs train or
plane" 466, 467), capping a criteria-faithful model at about 29/44, below the 30/44 bar; d3:9b's round-2 misses fall mostly in these
classes. Narrowing the exemption to places and periods raises the estimated ceiling only to about 30-36. The labels are also inconsistent
on the comparator class (some heat-pump-vs-oil-furnace sentences out, near-identical ones in or general), so no short single rule matches
them. Scope by decision model is closed on these labels; the next step, if any, is re-labelling the contested comparator rows under a
written rule, then a fresh pre-registration. Round-3 code in probe.py (`--v3`, `--gate`, `score3`) was never run; known unfixed issues from
the fourth review (an outage during the gate records a FAIL; HTTP errors are treated as outages; the gate file is written only on
completion) must be fixed before any reuse. Answerability: calibration miss, see notes_round12_sweep.txt; next step is a cut-off set on one
labelled set and checked on a fresh one.
