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
