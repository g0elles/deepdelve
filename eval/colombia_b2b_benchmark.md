# Colombia B2B Niches Benchmark

A realistic, market-research-scale test for comparing models on the workload where
`mistral-nemo` actually collapsed. Scored against an independently produced gold reference
(`eval/reference/colombia_b2b_niches_2026_reference.md` — **never feed that file to the agent**).
Also registered as the last item in `eval/dataset.jsonl` for the automated harness.

## The prompt (variant A — English, primary)

```
Research niche B2B technology opportunities in Colombia for 2026 where a small software team
could realistically generate revenue. Focus only on niches where (a) a recent Colombian or EU
regulation forces a specific customer segment to adopt technology, and (b) that segment has
demonstrated real capacity to pay, and (c) local competition is weak or nonexistent. Exclude
fintech, last-mile delivery and routing apps, construction ERP software, and generic legaltech —
those markets are saturated. Identify 4 to 6 niches. For EACH niche state: the exact regulation
creating the obligation (name/number and compliance deadline), who the paying customer is and
evidence of their capacity to pay, at least one existing local or international competitor, and a
market-size or export figure. Many of the best sources are in Spanish — search in Spanish as well
as English. Every claim must cite the real source URL you fetched.
```

Run it:

```powershell
.\venv\Scripts\python.exe src/app.py --auto-approve --prompt "<paste variant A>"
```

## Variant B — Spanish (optional, tests language robustness)

```
Investiga nichos de oportunidad tecnológica B2B en Colombia para 2026 donde un equipo pequeño de
software pueda generar ingresos de forma realista. Enfócate solo en nichos donde (a) una
regulación reciente colombiana o de la UE obligue a un segmento de clientes a digitalizarse, (b)
ese segmento demuestre capacidad real de pago, y (c) la competencia local sea débil o inexistente.
Excluye fintech, apps de última milla y ruteo, ERP de construcción y legaltech genérico — están
saturados. Identifica de 4 a 6 nichos. Para CADA nicho indica: la norma exacta que crea la
obligación (nombre/número y fecha límite), quién es el cliente que paga y evidencia de su
capacidad de pago, al menos un competidor existente, y una cifra de tamaño de mercado o
exportaciones. Cada afirmación debe citar la URL real de la fuente que consultaste.
```

## Benchmark hygiene (learned the hard way, 2026-07-11)

- **Disable `knowledge_cache` and `experience_cache` and delete their JSON files before each
  round.** Any model's PASSING run saves its answer to the cache; every later model then gets a
  "previously-verified answer exists, write it as-is" note and produces a derivative report —
  confirmed live: a qwen run reproduced a gpt-oss report almost verbatim (same niches, same
  figures, same sources) while looking like independent research.
- **Space rounds out or verify `search_health` in `_run_state.json`** — provider throttling after
  a search-heavy day makes failures look like model fabrication. With `search_backend: auto`
  (engine rotation) this is largely mitigated but still worth checking per run.
- **Run models sequentially, never concurrently** — they share search egress and (locally) GPU.

## Scoring rubric (manual, against the gold reference)

Score each tier 0–2 (0 = fail, 1 = partial, 2 = pass). Max 10.

1. **Structural integrity** (from `research_output/<run>/_run_state.json`, not the report's
   self-presentation): every URL cited in `final_report.md` appears in `fetched_urls`;
   `findings.md` exists and is grounded; run did not end in salvage.
2. **Exclusion compliance**: none of the 4 excluded verticals (fintech, last-mile, construction
   ERP, generic legaltech) appears as a recommended niche. The new `delegate_tasks` exclusion gate
   should show `## Skipped` entries if the Planner tried.
3. **Niche overlap with reference**: ≥2 of the reference's real niches found independently
   (EUDR traceability, SAGRILAFT/SARLAFT, RIPS/glosas, RNDC freight, energy communities, retail
   forecasting, predictive maintenance, SME anomaly cybersecurity). Overlap = strong signal the
   research was real; a disjoint-but-grounded set is not an automatic fail — verify its sources.
4. **Regulatory precision**: named regulations are real and correctly dated. Fast spot-checks
   against the reference: EUDR 2023/1115 deadline 30-dec-2026/jun-2027; Res. 2275/2023 (RIPS);
   Res. 2328/2025 (transport SARLAFT); Res. 20243040058015/2024 (RNDC); Ley 1715 / Decreto
   2236/2023 (energy communities).
5. **Quantitative grounding**: market figures are attributed to fetched sources and roughly match
   reality (reference anchors: cacao exports US$265.1M 2024; cybersecurity USD 1.03B 2024;
   apparel USD 5.79B 2025; 2,295 startups / US$857M invested per Colombia Tech Report 2026).
   The agent's numbers may legitimately differ if it fetched different sources — verify against
   what IT fetched, not against the reference blindly.

Interpretation: 8–10 = pipeline-trustworthy at market-research scale; 5–7 = usable with manual
verification; ≤4 = the mistral-nemo failure class.

## Automated harness

The same query is the last line of `eval/dataset.jsonl` (llm_judge). Judge model in
`eval/eval_config.yaml` is `deepdelve-gpt-oss`. Note the judge only sees the report text — the
structural tier (fetched-URL cross-check) is what the harness can't judge; always check
`_run_state.json` for runs that matter.
