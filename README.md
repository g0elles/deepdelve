# DeepDelve

A locally-run, multi-agent deep research assistant built on the **Microsoft Agent Framework** and the **Textual** TUI library, targeting local OpenAI-compatible model servers (defaults to **Ollama**, `http://localhost:11434/v1`).

This is a from-scratch rebuild of an earlier prototype (`../deep-research`), not an incremental patch. That prototype worked end-to-end but was unreliable on anything beyond simple lookups — see "Design rationale" below for the specific bugs found in its retry/grounding logic and what changed here.

## Architecture

Three tiers, same depth as most reference implementations in this space, but tiers 2 and 3 are small **named panels** rather than one monolithic agent each — specialized by task type, not just pipeline stage:

```
Planner
  |
  +-- delegate_tasks, routed by research angle --+
  |                                               |
  v                                               v
WebSearcher                              AcademicSearcher
(general web research)                   (papers, citations, related work)
  |                                               |
  +-- delegate_tasks, routed by content type -----+
  |                                               |
  v                                               v
DocumentAnalyzer                          DataAnalyzer
(prose/HTML extraction)              (tables, code, numbers, citations — verbatim pulls)
```

- **Planner**: plans research in bounded, named slots (`background`/`comparison`/`related_work`/`verification` — never an open-ended task list), dispatches specialists, runs an **adaptive planning loop** (observe results, replan if something's missing or contradictory, don't just proceed linearly), and writes `final_report.md` in **two passes**: extract findings verbatim into `findings.md` first, then a self-critique pass before writing the final report from it.
- **WebSearcher / AcademicSearcher**: search and fetch. `AcademicSearcher` exists because "research this paper + find related work" was the exact query class that used to exhaust its retry budget and write nothing — see rationale below.
- **DocumentAnalyzer / DataAnalyzer**: read/extract from downloaded files. `DataAnalyzer` is tuned for verbatim structured pulls (exact numbers, titles, citations) instead of paraphrased summaries.

Tool access is withheld from each parent so it is structurally forced to delegate rather than short-circuit the chain (unchanged from the reference pattern this was built from — see Delegation Routing in each role's prompt in `src/prompts.py`).

## Design rationale (what changed from the prototype, and why)

The full reasoning — including the exact code-level bugs found in the prototype's retry loop, the papers consulted, and which architectural claims are paper-backed vs. our own inference — is long enough that it lives in the original planning doc, not repeated here. The short version:

1. **Retry quota starvation (fixed)**: the prototype's completion-check retries shared the *same* tool-call quota pool as the failed attempt they were correcting — a complex query could burn its entire budget before the first retry even started. Fixed via `settings.retry_quota_topup` in `config_template.yaml` (`engine/orchestrator.py::topup_quota_pool`).
2. **Weak grounding check (fixed)**: the prototype only checked whether `final_report.md` contained the literal substring `"http"` — a fully hallucinated URL passed. Now the engine deterministically logs every URL actually passed to `fetch_url_to_workspace` (`utils/run_state.py`) and cross-references every cited URL in the report against that log, with an optional live HTTP re-check (`settings.grounding_check` in config).
3. **Context self-poisoning on retry (fixed)**: the prototype nudged "fix it" while leaving the model's own bad prior draft visible in the workspace. Now a failed grounding check quarantines the bad artifact (renames it aside) before nudging, so the model isn't re-conditioning on its own wrong output.
4. **Domain specialization (added)**: tiers 2 and 3 became named panels instead of one-size-fits-all agents, informed by a real paper on multi-agent deep research (DelveAgent, arXiv:2606.18648) and cross-checked against three independent field surveys before being adopted — not taken from a single source.
5. **Dual-granularity memory (added)**: `utils/knowledge_cache.py` now persists both verified Q&A facts *and* successful past plans (keyed by a coarse query shape), so a structurally similar future query can seed its plan instead of starting from zero.
6. **Two real interface bugs fixed while porting the TUI** (`engine/tui.py`): the interactive TUI path never reset its `session_dir_ctx` token (only the headless path did), and the ~150-line completion-check retry logic was duplicated near-verbatim between the TUI and headless entry points — any future fix had to be hand-applied twice. Both are now one shared `run_completion_check()` used by both paths.

## Setup Instructions

### 1. Create the Environment & Install

> **NTFS gotcha:** if this project directory is on an NTFS mount (`df -T .` shows `ntfs3`), `python3 -m venv venv` **inside the project folder will fail** — NTFS doesn't support the symlinks venv needs. Create the venv on a native filesystem instead:
> ```bash
> python3 -m venv ~/.venvs/deepdelve
> ~/.venvs/deepdelve/bin/python3 -m pip install -e .
> # then run with ~/.venvs/deepdelve/bin/python3 src/app.py instead of venv/bin/python3
> ```

If your project directory is on a normal native filesystem:
```bash
python -m venv venv
source venv/bin/activate
pip install -e .
```

### 2. Configure Endpoints & Model

By default, targets Ollama's OpenAI-compatible API (`http://localhost:11434/v1`).

> **Model compatibility gotcha:** this agent is 100% tool-call driven — if the model never emits a *structured* `tool_calls` response, every agent just narrates instead of acting. Models from the official Ollama library ship with a maintainer-verified tool-call parser; `hf.co/...` GGUF imports often don't. Verify any model with:
> ```bash
> curl -s http://localhost:11434/v1/chat/completions -H "Content-Type: application/json" -d '{
>   "model": "<your-model>",
>   "messages": [{"role": "user", "content": "Search the web for the population of Tokyo."}],
>   "tools": [{"type": "function", "function": {"name": "web_search", "description": "Search the web.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}}]
> }' | python3 -c "import json,sys; print(json.load(sys.stdin)['choices'][0]['message'].get('tool_calls'))"
> ```
> A working model prints a populated list; a broken one prints `None`.

> **Context window gotcha:** Ollama-library models default to `num_ctx: 4096`. Create a derived tag:
> ```bash
> ollama pull mistral-nemo:12b
> cat > Modelfile << 'EOF'
> FROM mistral-nemo:12b
> PARAMETER num_ctx 16384
> EOF
> ollama create deepdelve-mistral-nemo -f Modelfile
> ```

> **Parallel-slot gotcha:** Ollama divides `num_ctx` across `OLLAMA_NUM_PARALLEL` parallel slots (often auto-set to 4), silently giving each real request a quarter of the context you configured. Verify via `journalctl -u ollama -f | grep n_ctx_slot` while sending a request. If it's smaller than expected, set `OLLAMA_NUM_PARALLEL=1` in `/etc/systemd/system/ollama.service.d/override.conf` and restart.

> **Model choice — do not assume the old ranking holds.** The domain-specialized prompts here have different tool-call shapes than the prototype's single generic Searcher. Re-run the reliability curl test above against each candidate model before picking a default; results will be logged here once re-tested.

### 3. Run

```bash
python src/app.py                                                        # TUI
python src/app.py --prompt "..." --auto-approve                          # headless
```

## Knowledge & Experience Cache

`src/utils/knowledge_cache.py` persists two things across runs, both deterministically engine-driven (not agent tools the model has to remember to call):
- **Knowledge store**: verified `{question -> answer}` pairs, reused if a repeat question comes in within `max_age_days`.
- **Experience store**: successful `{query_shape -> plan}` pairs, offered as a seeding hint (not a forced override) for structurally similar future queries.

## Tool Quotas

Same global, cumulative-across-all-agents quota model as the reference pattern (`settings.quotas` in `config_template.yaml`), plus `settings.retry_quota_topup` — see Design Rationale point 1.

## Eval Harness

`eval/` is a generic headless-run + score harness (`evaluate.py` runs each `eval/dataset.jsonl` item against the agent, `results_viewer.py` renders an HTML report). `dataset.jsonl` ships with 3 items matching the query classes that used to fail in the prototype: a simple factual lookup, a comparative query, and a paper-plus-related-work academic query.

```bash
python eval/evaluate.py --runs 3
python eval/results_viewer.py
```
