# Overview slots replay: pre-registration (2026-11-19, written before the replay ran)
Question: does preferring the pipeline's answering quotes (evidence "answering", from facet_unanswered) make the overview pool carry a quote
that answers each facet more often than the shipped pool (bridging quotes first, then two per facet by word overlap)?
Why: the 2026-11-19 smoke showed the cost comparison came from bridging quotes, not from the change, and the change pulled in a wrong source
figure; the coverage probe's word-overlap baseline (0.22) ranked across all quotes, which is not what production does.
Data: the 33 saved evidence dirs (eval/writer_ab/skeleton_replay.DIRS). answering recomputed per dir with pipeline.run._compute_unanswered on
the saved quotes; models ornith-1.5:9b and gemma4:12b. No writer calls: the pool is deterministic given evidence + answering.
Unit: a facet. Hit = the overview pool holds a quote (of that facet, incl. its bridging quotes) that ANSWERS the facet's name as a question.
Rubric = eval/coverage_probe/PREREG.md (attribute asked, for the entity named; background, related-but-different quantity, other entity = NOT).
Labelling: blind to arm; quotes in both pools are labelled first, and a facet where a shared quote answers is a hit for both arms.
Pool coverage is an upper bound on the overview text (the writer uses 2-4 sentences from it).
Bar: keep the change only if, on BOTH models, facet hit rate with answering >= without + 0.10, and facets lost (hit without, miss with)
<= one third of facets gained. Otherwise revert src/pipeline/run.py + src/writer/compiled.py overview-slot edits.
