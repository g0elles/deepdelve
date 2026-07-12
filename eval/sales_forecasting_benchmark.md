# Sales-Forecasting / Heuristic-Algorithms Benchmark

A second benchmark case, structurally different from `colombia_b2b_benchmark.md`: an
academic/literature-review query instead of a market-research query. Scored against an
independently produced gold reference (`eval/reference/sales_forecasting_deepseek.md` — **never
feed that file to the agent**). This is the exact real query the user ran through
`deepdelve-mistral-nemo` on 2026-07-12 (see `research_output/i_want_documentation_on_heuristic_algoritms_for_de_20260712_131647/`),
which collapsed after 9 completion-check attempts with no accepted `findings.md` or
`final_report.md`.

## The prompt (reproduced verbatim, including the intake clarifications)

```
I want documentation on heuristic algoritms for deep learning, the idea is to have models that can
be used for predicting sales on multiple franchises of a company, based on the historical data, the
idea is to do the prediction based on patterns that can be identified and also based on the local
culture, something like holidays, usual paydays, cultural events

USER CLARIFICATIONS (answers to the intake questions above):
1. pick the common top 5
2. there's none for now, this is a simple research to scope
3. for now no, but adhere to the country of Colombia for cultural references, you'll have to
   research that
```

Run it (Linux):

```bash
~/.venvs/deepdelve/bin/python src/app.py --auto-approve --prompt "<paste the full prompt above, including clarifications>"
```

If/when the academic output mode ships (ROADMAP.md candidate), also run with `--style academic`
as a second variant — this query is the intended first live test for that feature, since the gold
reference itself uses `(Author, Year)` + numbered References, not inline `- **[Title](URL)**`
citations.

## Benchmark hygiene

Same rules as `colombia_b2b_benchmark.md`: disable `knowledge_cache`/`experience_cache` before each
round (n/a — both deleted, 929b987), run models sequentially (shared GPU), check `search_health` in
`_run_state.json` before scoring a bad run as a model failure rather than a provider throttle.

## Scoring rubric (manual, against the gold reference)

Score each tier 0–2 (0 = fail, 1 = partial, 2 = pass). Max 10.

1. **Structural integrity** (from `_run_state.json`, not the report's self-presentation): every
   URL cited in `final_report.md` appears in `fetched_urls` and is not flagged `stub`; `findings.md`
   exists and is grounded; run did not end in salvage or unresolved quarantine.
2. **Architecture coverage vs. reference**: the reference's 4 core architecture families found —
   Temporal Fusion Transformer, N-HiTS, Deep Reinforcement Learning (DQN), multimodal/LLM-based
   cultural-event encoding (EventCast-style). Overlap = strong signal of real literature search,
   not fabrication from parametric memory; a disjoint-but-grounded set of real architectures is not
   an automatic fail.
3. **Heuristic-optimization coverage**: at least one of PSO or GA (or a named hybrid) discussed as
   applied to hyperparameter tuning or architecture search — the query's actual noun phrase
   ("heuristic algorithms for deep learning") is specifically about this, not just "deep learning
   models" generically. A report that only covers architectures and never touches PSO/GA/heuristic
   optimization misses the query's central framing.
4. **Colombia cultural-context research — NOT covered by the gold reference, score independently**:
   the query explicitly required Colombia-specific holidays, payday cycles (Colombians are
   typically paid the 15th/30th-31st), and cultural events, adapted from the intake clarification.
   Nemo's failed run never got far enough to attempt this; any run that reaches a written report
   should be checked for real, cited Colombia-specific sources here, not generic "holidays boost
   retail sales" boilerplate.
5. **Quantitative grounding**: reported figures (RMSE/SMAPE/MAPE percentages, service-level
   improvements, etc.) are attributed to a real fetched source. The agent's numbers may legitimately
   differ from the reference's cited papers if it found different sources — verify against what IT
   fetched, not against the reference blindly. Reference anchors if useful for a fast spot-check:
   TFT R²=0.9875 (Punati et al. 2025); N-HiTS 8.2% RMSE reduction (IEEE 2026); multimodal 7.37%
   SMAPE improvement (Both et al. 2022); EventCast up to 86.9% MAE improvement (Hu et al. 2026);
   PSO 8.7% MAPE (MDPI 2026); GA-DQN service level 61%→94%.

Interpretation: 8–10 = pipeline-trustworthy at literature-review scale; 5–7 = usable with manual
verification; ≤4 = the nemo failure class (this query's own on-record "before" data point).

## Known baseline: nemo, 2026-07-12

`deepdelve-mistral-nemo` collapsed on this exact query: 9 completion-check attempts
(`missing_findings` x4, `missing_artifact` x3, `findings_ungrounded` x1), only 6 fetches (2 flagged
`stub`), and the run ended with no accepted `findings.md`/`final_report.md` — only a
`.rejected_attempt_6`. Consistent with nemo's documented 2/10 ceiling on the Colombia B2B
benchmark; not treated as a new bug. Score: 0/10 (never produced a scoreable artifact). Keep this
as the reference "before" run when comparing against `deepdelve-gpt-oss` or any future candidate
(e.g. Tongyi-DeepResearch-30B-A3B, pending the tool-call compatibility check in ROADMAP.md).

## Automated harness

Not yet registered in `eval/dataset.jsonl` — the query's exact wording (including the 3 intake
clarifications) doesn't map cleanly onto a single `llm_judge` criteria block the way the Colombia
B2B query does, since this run went through the TUI's `clarify_before_research` step rather than
headless. Register a dataset.jsonl row once the academic output-mode work (ROADMAP.md) is far
enough along to give this query a stable expected shape to judge against.
