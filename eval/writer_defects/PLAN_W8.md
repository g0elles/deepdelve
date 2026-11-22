# W8. Scholarly search in the stage-graph pipeline (retrieval change; live A/B needs GPU + OK)

Found 2026-11-22 (academic run, `research_output/acad_ornith_chmx_r1`): a "what does peer-reviewed research report" query got 5 sources,
1 peer-reviewed; the Mexico evaluation (Social Science & Medicine 2026) was reached only through its research group's blog summary, and
the self-report study (IJBNPA 2024, in PMC) not at all. Cause: `src/pipeline/run.py:search` (:479) is DDGS web search only; the agent path
has `tools/scholar.py` (Europe PMC + OpenAlex, OA full text via `_full_text_epmc`), the pipeline never calls it.

## Probe (no GPU, 2026-11-22, saved facets of the 3 queries, query text `f"{q0} {name}"`, Europe PMC k=3)
Europe PMC answered every facet (OpenAlex fallback never needed; OpenAlex key is set in the live config). Chile facet: Taillie 2020
(PLOS Med, OA full text) at rank 2; Mexico facet: a 2026 SSB-intake-after-labelling paper (OA), not the SSM 2026 sales paper; c2: GLP-1/AUD
reviews and a naltrexone umbrella review; leave-law query (h20): only off-topic papers (sick leave, absenteeism costs). Some records have
no abstract and no DOI ("Abstracts from ... conference"): skipped by rule 2. So the leave guard is a real risk, not a formality.

## Change (v2, after EGM review 1: 0 blockers, 6 majors, 9 minors applied)
1. `src/pipeline/run.py:scholar_search(question, k)`: `tools.scholar.search_europepmc`; < 2 hits -> add `search_openalex` (agent-tool
   fallback, `scholar.py:252-258`). Called from `gather_round`, which catches every exception -> `stats["scholar_fail"] += 1`, run goes on
   (so a test can patch `scholar_search` to raise, review M3).
2. `scholar_text(rec) -> (url, text, title, date) | None`:
   - URL: OA + PMCID -> `_pmc_url(pmcid)`; else DOI -> `https://doi.org/<doi>`; else skip (no DOI, no PMCID: no stable key, review M2).
   - Text: OA + PMCID and `_full_text_epmc` succeeds -> "Content-Level: FULL TEXT" + "DOI: <doi>" lines + the JATS markdown (which
     starts with `# Title`). Otherwise, abstract present -> "Content-Level: ABSTRACT ONLY." + "DOI: <doi>" + "# <Title>" + abstract.
     No abstract and no full text -> skip. Header lines carry no sentence ending, and the title is a `#` heading, so `sentences()`
     never yields them as quotes (review M1: "(2020)." escapes `_BIB`; the agent path's "say in the report ..." instruction sentence is
     NOT copied).
   - Title for references: "<Title>. <Journal>" with authors capped at 3 + "et al." prefixed; date = year only (no double year, m6).
3. `run()` `gather_round`, round 1 only: ONE scholarly search per facet (`f"{f['questions'][0]} {f['name']}"`, k = topk), `scholar_text`
   run in the existing `ThreadPoolExecutor(4)` (m8). A scholarly URL already in `seen` with text is skipped; one already in `seen` with
   None (web fetch failed) is replaced by the scholarly text (m7). Scholarly searches do NOT go through the `search` seam (the existing
   round-1 count of 4 searches in `test_gap_rounds_only...` stays). Duplicate copies of the same paper reached by web search
   (publisher or old ncbi PMC URL) are left as-is (ponytail: dedupe by DOI if replays show double counting).
   `metrics.gather` gains `scholar_searches`, `scholar_sources`, `scholar_fulltext`, `scholar_fail`. Always on, no flag or config key.
   Parity: only `python -m pipeline.run` calls `run()`; TUI/CLI/API read the finished evidence dir (`tui.py:2124-2134`, `api.py:237`),
   so no surface change (m1).
4. Writer unchanged (abstract-only disclosure in the report is out of scope here).
5. Tests (`tests/test_pipeline_run.py`): `scholar_search`/`scholar_text` added to `patched()`'s default seam dict (returning []/None)
   so no existing test hits the network (M3); new: one scholarly search per facet in round 1, none in gap rounds; OA record -> FULL TEXT
   source at PMC URL; non-OA -> ABSTRACT ONLY at DOI URL; no DOI/PMCID or no text -> skipped; raising `scholar_search` -> run completes,
   `scholar_fail` counted; header/title lines never appear among `sentences()` of a stored scholarly source; a scholarly source reaches
   `evidence.json` through normal selection.

## Batch before the live A/B (review M5)
The 2026-11-22 academic run found two writer defects that Q-acad reports will show again: compiled writer ignores `--style academic`
(`src/writer/compiled.py`, no style handling) and a figure caption chosen as the answer-in-brief (`compiled.py:_overview_pool`). They get
their own plans (W9, W10) and are fixed BEFORE the A/B, so the 12 runs are done once.

## Acceptance (live; GPU + user OK; plan frozen before any run is read)
- ornith-1.5:9b at `settings.model_options` (informational, no default model designated). Runs strictly sequential, `rocm-smi` checked
  first, `ollama stop` after; estimate 12 x (~1.5 min pipeline + ~1 min compiled report) ~ 30-60 min.
- Queries (exact text):
  - Q-acad: "What does peer-reviewed research report about the effects of Chile's 2016 food labelling and advertising law and Mexico's
    2020 front-of-package warning labels on purchases of sugary drinks, and how do the two effects compare?"
  - c2: "Does semaglutide reduce alcohol consumption in people with alcohol use disorder, and how does the evidence compare with
    naltrexone?"
  - Q-leave: "What are the legal requirements for minimum paid annual leave in France, Brazil and South Korea, including how many days
    and how pay is calculated?"
- Arms: ON = new code; OFF = runner `eval/writer_defects/w8_ab.py` monkeypatching `pr.scholar_search` to return [] (m2). Pairs = (query,
  run index): run 1 ON vs run 1 OFF, run 2 ON vs run 2 OFF; order ON/OFF alternated per pair.
- Blinding (M4): part-answered labels on the report BODY with the Sources list and citation markers stripped, shuffled, arm hidden.
  Source-mix (a) is counted mechanically from the Sources list (not blind, not labelled).
- Measured: (a) peer-reviewed/official-evaluation share of cited sources; (b) query parts answered (rubric of the 2026-11-22
  evaluation; Q-acad against its verified reference in `session_status/CURRENT.md`); (c) figures not traceable to a saved source;
  (d) pipeline wall time.
- Bar (all): Q-acad and c2: ON raises (a) in both pairs and answers >= as many parts as OFF in every pair; Q-leave: ON answers >= as many
  parts as OFF in both pairs and no scholarly source is cited in either ON leave report (probe shows the hits are off-topic); (c) = 0 in
  every ON report; ON wall time <= OFF + 60 s median.
- Fail -> revert; record why here. Full test suite + ruff before commit.
