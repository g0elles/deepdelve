# Coverage probe: pre-registration (2026-11-19, written before any model output)
Question: can a local model find, for each part of the request, a kept quote that answers it, better than what the overview uses now?
Decision it feeds: per-part overview slots in writer/compiled.py:_overview_pool (and a per-part check of the final text) are built only if it passes.

Data: 5 saved English evidence dirs, every kept quote (deduplicated), parts split by hand (the splitter is out of scope here):
- research_output/cmp_ornith_c1 (heat pumps vs gas furnaces): lifetime cost; carbon emissions; performance in cold climates
- eval/heldout3_runs/h11 (parental leave): Germany duration+pay; Sweden duration+pay; Japan duration+pay
- eval/heldout3_runs/h12 (Raft/Paxos): leader election difference; log replication difference; production systems using Raft; ... using Paxos
- eval/heldout4_runs/h21 (LSM/B-tree): write amplification difference; read performance difference; databases using LSM trees; ... using B-trees
- eval/heldout4_runs/h25: {Portugal, Vietnam, Ghana} x {GDP per capita, internet penetration, fertility rate}  (9 parts)
23 parts.

Arms (each picks at most one quote per part):
- A answerability: pointwise YES/NO "does this passage state an answer to the question" per (part, quote); pick = YES quote with most word overlap with the part.
- B existing code: pipeline.run.facet_unanswered with questions = parts, passages in chunks of 20, first chunk that answers.
- C relevance baseline: pointwise YES/NO "is this passage relevant to the request" per quote; pick = YES quote with most word overlap with the part.
- D current, no model: the quote with most word overlap with request+part (writer/compiled.py _terms/_score).
Models: ornith-1.5:9b and gemma4:12b (wiki Default-Model leading candidates), think="minimal", temperature 0, production call_json.

Labelling rubric (written before labelling): a quote ANSWERS a part if it states, for the entity the part names, the attribute the part asks
(comparison parts: one side's value of that attribute or a direct comparison). Background, definitions, a related but different quantity, or
a different entity/country = NOT. An arm that abstains is a hit only if no quote in the dir answers the part (checked by reading all quotes).
Labels are blind to arm/model (picks pooled and shuffled per part).

Bar: A passes if on BOTH models hit rate >= 0.75, >= D + 0.15 and >= C + 0.10. If B is within 0.05 of A on both models, use B (no new prompt).
Otherwise: not built, reported as failed.
