# Phase 3 comparison queries c2-c4 (written 2026-11-20, before any run)
Purpose: finish the model comparison (wiki Pipeline-Model-Comparison, Writer-Redesign-Plan row 3) on the current branch: evidence stage
(`python -m pipeline.run`) + compiled writer (`app.py --pipeline-evidence --writer-mode compiled`), same model for both stages.
Models: ornith-1.5:9b, gemma4:12b (enable_thinking false, config copies differing only in openai_model). Ollama, one model at a time.
Runs: 2 per cell (query x model), sequential, DEEPDELVE_FORCE_VIRTUAL_DISPLAY=1. 12 evidence + 12 writer runs.
Queries (fixed here; topics from the plan):
- c2: Does semaglutide reduce alcohol consumption in people with alcohol use disorder, and how does the evidence compare with naltrexone?
- c3: How did Iceland's recovery from the 2008 banking crisis compare with Ireland's on unemployment and GDP growth?
- c4: ¿Cómo se compara el efecto del impuesto a las bebidas azucaradas en México con el del Reino Unido sobre el consumo de azúcar?
Grading per report (before metrics): each query part answered in the written text (c2: effect of semaglutide on drinking; comparison with
naltrexone. c3: unemployment both countries; GDP growth both; the comparison. c4: Mexico effect; UK effect; comparison; written in Spanish),
cited figures checked against their sources, then scripts/evaluate_run.py scorecard. A model is not discarded on fewer than 2 runs.
Output: research_output/cmp_<model>_<c>_r<k> (evidence) + the app's report dir; per-run log in eval/comparison_runs/runs.tsv.
