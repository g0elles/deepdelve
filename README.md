# DeepDelve

A locally-run, multi-agent deep research assistant built on the **Microsoft Agent Framework** and the **Textual** TUI library, targeting local OpenAI-compatible model servers (defaults to **Ollama**, `http://localhost:11434/v1`).

A from-scratch rebuild of an earlier prototype, not an incremental patch — the prototype worked end-to-end but was unreliable beyond simple lookups. See [`ROADMAP.md`](ROADMAP.md) for what's done, what's open, and the history of bugs found and fixed.

## Architecture

```
Planner
  |
  +-- delegate_tasks, routed by research angle -------------------+
  |                                    |                          |
  v                                    v                          v
WebSearcher                  AcademicSearcher              PeerReviewer
(general web research)     (papers, citations,        (independent critique of
  |                          related work)              findings.md — fresh
  |                                    |                 context, no new research)
  +-- delegate_tasks, routed by content type -----+
  |                                               |
  v                                               v
DocumentAnalyzer                          DataAnalyzer
(prose/HTML extraction)              (tables, code, numbers, citations —
                                       verbatim pulls; also the only tool
                                       with extract_structured_data)
```

- **Planner**: plans in bounded, named slots (`background`/`comparison`/`related_work`/`verification` — never an open-ended task list), dispatches specialists, runs an adaptive planning loop (observe results, replan if something's missing or contradictory — recorded via the `replan_action` tool), and writes `final_report.md` in two passes: extract findings verbatim into `findings.md`, then self-critique (optionally delegating to `PeerReviewer` for deep/academic queries) before writing the final report.
- **WebSearcher / AcademicSearcher**: search and fetch. Specialist summaries are grounding-checked *before* they reach the Planner, not just at final-report time.
- **PeerReviewer**: Planner-tier delegate for an independent, fresh-context critique of `findings.md`.
- **DocumentAnalyzer / DataAnalyzer**: read/extract from downloaded files. `DataAnalyzer` also has `extract_structured_data` for tables/code/JSON blocks.

Tool access is withheld from each parent so it's structurally forced to delegate rather than short-circuit the chain — see each role's Delegation Routing block in `src/prompts.py`.

## Key structural fixes over the prototype

The full history (with live-test evidence for each) is in `ROADMAP.md`. The headline ones:

- **Real grounding check**: cross-references every cited URL against URLs actually fetched this run (`utils/grounding.py`), not a substring check. A second, content-level layer flags a citation whose source shares zero checkable facts with the claim next to it. Runs both on the final report and on each specialist's summary before it reaches the Planner.
- **`web_search` auto-fetches its top result's full content** — there's no snippet-only path left for a model to stop at (`settings.web_search.auto_fetch_top`), which was the single biggest lever on real answer quality.
- **Per-attempt quota top-up, artifact quarantine before nudging, and history-scanning salvage** for a narrated-but-never-written report — all structural fixes, not prompt tuning, for failure modes that prompt tuning alone didn't resolve in testing.
- **`RunState`** (`utils/run_state.py`) persists fetched URLs, findings, and completion-check attempts per run as `_run_state.json`, independent of the model's own narration.

## Setup

### 1. Environment

> **NTFS gotcha:** if this project directory is on an NTFS mount (`df -T .` shows `ntfs3`), `python3 -m venv venv` inside the project folder fails — NTFS doesn't support the symlinks venv needs. Create it elsewhere instead:
> ```bash
> python3 -m venv ~/.venvs/deepdelve
> ~/.venvs/deepdelve/bin/python3 -m pip install -e .
> ```

Otherwise:
```bash
python -m venv venv
source venv/bin/activate
pip install -e .
```

### 2. Model

Targets Ollama's OpenAI-compatible API by default. Default model: `deepdelve-mistral-nemo` (a `mistral-nemo:12b` derived tag — see below). Two things that will silently break tool-calling if skipped:

> **Tool-call support:** this agent is 100% tool-call driven — if a model never emits a structured `tool_calls` response, every agent just narrates instead of acting. Models from the official Ollama library ship with a maintainer-verified tool-call parser; `hf.co/...` GGUF imports often don't. Verify with:
> ```bash
> curl -s http://localhost:11434/v1/chat/completions -H "Content-Type: application/json" -d '{
>   "model": "<your-model>",
>   "messages": [{"role": "user", "content": "Search the web for the population of Tokyo."}],
>   "tools": [{"type": "function", "function": {"name": "web_search", "description": "Search the web.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}}]
> }' | python3 -c "import json,sys; print(json.load(sys.stdin)['choices'][0]['message'].get('tool_calls'))"
> ```
> A working model prints a populated list; a broken one prints `None`.

> **Context window:** Ollama-library models default to `num_ctx: 4096`, which is too small here. Create a derived tag with more headroom:
> ```bash
> ollama pull mistral-nemo:12b
> cat > Modelfile << 'EOF'
> FROM mistral-nemo:12b
> PARAMETER num_ctx 16384
> EOF
> ollama create deepdelve-mistral-nemo -f Modelfile
> ```
> Also set `OLLAMA_NUM_PARALLEL=1` in `/etc/systemd/system/ollama.service.d/override.conf` and restart — Ollama otherwise divides `num_ctx` across parallel request slots (often 4), silently giving each real request a quarter of the context you configured.

**Model choice**: `mistral-nemo:12b` and `devstral:24b` reliably emit correctly-shaped nested tool calls for this project's `delegate_tasks(tasks: [{task_name, instructions, agent_id: enum}])` schema; `mistral-nemo:12b` is the default since `devstral:24b` was less consistent across multiple live Planner-role trials (sometimes skipped tool calls entirely, or wrote placeholder text instead of real findings). Several other candidates (`hermes3:8b`, `qwen2.5-coder:14b-instruct`, `llama3-groq-tool-use:8b`, `mistral:7b-instruct-v0.3-q5_K_M`) were tried and rejected — see `ROADMAP.md` for the full trial history if evaluating a new model. **Passing an isolated tool-call test isn't sufficient evidence a model will behave reliably in the full multi-agent role** — test the actual Planner role with multiple independent trials.

### 3. Run

```bash
python src/app.py                                       # TUI
python src/app.py --prompt "..." --auto-approve          # headless
```

## Config highlights (`config_template.yaml`)

- `settings.quotas` / `settings.retry_quota_topup` — global, cumulative-across-all-agents tool-call budgets, with extra headroom on a completion-check retry.
- `settings.grounding_check` — `content_level_check`, `verify_specialist_output`, `live_http_verify`.
- `settings.knowledge_cache` / `settings.experience_cache` — persist verified `{question -> answer}` pairs and successful `{query_shape -> plan}` pairs across runs, deterministically (not agent tools the model has to remember to call).
- `settings.workspace.wiki_index` — maintain a persistent cross-run `index.md` at the workspace root.
- `settings.search_mode: heavy` — search deeper and auto-fetch more top results per call.
- `settings.human_in_the_loop` — require approval on the Planner's `write_todos` before research proceeds.
- `settings.mcp_servers` — wire in external MCP tools (e.g. Semantic Scholar, Brave Search), scoped per sub-agent. See the file's inline comments for ready-to-uncomment examples.

## Eval Harness

`eval/` is a headless-run + score harness. `dataset.jsonl` ships with 3 items: a simple factual lookup, a comparative query, and a paper-plus-related-work academic query.

```bash
python eval/evaluate.py --runs 3
python eval/results_viewer.py
```

## References

- Jiang, Yang, Cui, et al. *Deep Research in Physical Sciences: A Multi-Agent Framework and Comprehensive Benchmark* (DelveAgent / PhySciBench). [arXiv:2606.18648](https://arxiv.org/abs/2606.18648) — primary architecture source (Adaptive Planning Loop, Dual-Granularity Memory, Hierarchical Reflection).
- Huang, Chen, Zhang, et al. *Deep Research Agents: A Systematic Examination and Roadmap*. [arXiv:2506.18096](https://arxiv.org/abs/2506.18096)
- Xu, Peng. *A Comprehensive Survey of Deep Research: Systems, Methodologies, and Applications*. [arXiv:2506.12594](https://arxiv.org/abs/2506.12594)
- Xi, Lin, Xiao, et al. *A Survey of LLM-based Deep Search Agents*. [arXiv:2508.05668](https://arxiv.org/abs/2508.05668)
- [`kyuz0/deep-research-agent`](https://github.com/kyuz0/deep-research-agent) — base architecture this was forked from.
- [`CYC2002tommy/Deep-Research-Agent`](https://github.com/CYC2002tommy/Deep-Research-Agent) — source of the "full-text reading is mandatory" and content-level claim-grounding ideas.
- [`nashsu/llm_wiki`](https://github.com/nashsu/llm_wiki) — source of the `findings.md` → `final_report.md` two-pass pattern and the structured run-state idea.
