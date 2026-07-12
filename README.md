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

- **Planner**: plans in bounded, named slots (`background`/`comparison`/`related_work`/`verification` — never an open-ended task list), dispatches specialists, runs an adaptive planning loop (observe results, replan if something's missing or contradictory), and writes `final_report.md` in two passes: extract findings verbatim into `findings.md`, then self-critique (optionally delegating to `PeerReviewer` for deep/academic queries) before writing the final report.
- **WebSearcher / AcademicSearcher**: search and fetch. Specialist summaries are grounding-checked *before* they reach the Planner, not just at final-report time.
- **PeerReviewer**: Planner-tier delegate for an independent, fresh-context critique of `findings.md`.
- **DocumentAnalyzer / DataAnalyzer**: read/extract from downloaded files. `DataAnalyzer` also has `extract_structured_data` for tables/code/JSON blocks.

Tool access is withheld from each parent so it's structurally forced to delegate rather than short-circuit the chain — see each role's Delegation Routing block in `src/prompts.py`.

## Key structural fixes over the prototype

The full history (with live-test evidence for each) is in `ROADMAP.md`. The headline ones:

- **Real grounding check**: cross-references every cited URL against URLs actually fetched this run (`utils/grounding.py`), not a substring check — with a path-segment boundary, so a fetched `.../article` can't ground a decorated fabrication like `.../article-fake-2024`. A second, content-level layer flags a citation whose source shares zero checkable facts with the claim next to it. A third layer catches a claim attributed to something that isn't a URL at all (a bare `(DANE, 2020)` parenthetical, a `Source: <prose>` line) — unverifiable in exactly the same way a fabricated URL is, but invisible to a check that only looks for `https?://`. A fourth catches a regulation identifier ("Ley 1906 de 2021") cited to a genuinely-fetched source whose content never mentions that number. A fifth refuses citations to **stub fetches** — a URL that answered HTTP 200 with a paywall/not-found shell is recorded as `stub` at fetch time and can neither pass the URL gate nor support any claim (closes the invented-URL-plus-soft-404 hole found live in run 14). A sixth (`uncited_claims`) catches claims structurally decoupled from citations — a table of figures plus a detached "Source URLs" list passes every line-scoped check vacuously, so ≥3 figure-bearing lines in a section with no URL fail the check even when every listed URL is real. Runs both on the final report and on each specialist's summary before it reaches the Planner. `findings.md` (Pass 1) is gated too: it must exist before `final_report.md` is accepted, and a wholesale-fabricated one (zero real citations) is quarantined. The verdict logic lives in `src/engine/completion.py` as an ordered check list, pinned by `test_structural_checks.py`'s verdict matrix.
- **Fetched pages decoded by their real charset** (strict UTF-8 → HTTP header → meta tag → cp1252 fallback, stale charset meta tags scrubbed before markdown conversion) — mojibake had silently gutted every accent-bearing Spanish term match in the grounding checks on the benchmark's flagship language.
- **Fetched files carry provenance**: everything a run fetches lands in the run folder's `sources/` subdirectory with `Source-URL: <true url>` as line 1, so a cited claim can be traced to the exact bytes it came from; the run root holds only `final_report.md`, `findings.md`, `_todos.md`, and `_run_state.json`.
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

### 2. Model & Endpoint

DeepDelve talks to any **OpenAI-compatible chat-completions endpoint** — it isn't Ollama-specific, that's just the default. Three ways to point it elsewhere, in order of precedence (later overrides earlier):

1. **Edit `~/.deepdelve/config.yaml`** (created on first run from `src/config_template.yaml`):
   ```yaml
   api:
     openai_base_url: https://api.openai.com/v1   # or any other OpenAI-compatible URL
     openai_model: gpt-4.1                          # or your provider's model name
   ```
2. **Environment variables** (override the config file, no edit needed):
   ```bash
   export OPENAI_API_BASE="https://api.openai.com/v1"
   export OPENAI_MODEL="gpt-4.1"
   export OPENAI_API_KEY="sk-..."     # required by real providers; defaults to "dummy" for
                                       # unauthenticated local servers (Ollama, LM Studio, vLLM, etc.)
   python src/app.py
   ```
3. **A separate config file entirely** via `--config`/`-c`:
   ```bash
   python src/app.py --config /path/to/other-config.yaml
   ```

This works for any local server that speaks the OpenAI chat-completions API (Ollama, LM Studio, vLLM, llama.cpp's server, text-generation-webui) or any hosted provider that does (OpenAI itself, OpenRouter, Together, Groq, etc.) — just set the base URL, model name, and API key accordingly. The one hard requirement, regardless of provider, is real structured tool-calling support (see below) — this agent is 100% tool-call driven, and a model/endpoint that only narrates JSON as text will not work.

The rest of this section documents the **Ollama default** and its specific gotchas — skip it if you're pointing at a different provider.

Default model: `deepdelve-gpt-oss` (a `gpt-oss:20b` derived tag — see below). Two things that will silently break tool-calling if skipped:

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
> ollama pull gpt-oss:20b
> cat > Modelfile << 'EOF'
> FROM gpt-oss:20b
> PARAMETER num_ctx 16384
> EOF
> ollama create deepdelve-gpt-oss -f Modelfile
> ```
> Also set `OLLAMA_NUM_PARALLEL=1` in `/etc/systemd/system/ollama.service.d/override.conf` and restart — Ollama otherwise divides `num_ctx` across parallel request slots (often 4), silently giving each real request a quarter of the context you configured.

**Model choice** (13-run Colombia B2B benchmark, 2026-07-11, manual rubric vs a gold reference — protocol in `eval/colombia_b2b_benchmark.md`):

| Model | Best score | Verdict |
|---|---|---|
| `gpt-oss:20b` | **7/10** | **Default.** The only model in the "usable with verification" band. High run-to-run variance, but its bad runs are honest-empty, not fabricated. ~15-20 min/run. |
| `qwen3.6` | 1/10 | Researches well, synthesizes disastrously at research scale (reconstructed 22/22 cited URLs from filenames). |
| `mistral-nemo:12b` | 2/10 | Passes the isolated `delegate_tasks(tasks: [{task_name, instructions, agent_id: enum}])` schema test 3/3, but ceilings at 2/10 on the full rubric — fabrication is caught and labeled by the gates rather than passing silently. |
| hosted (NVIDIA NIM free tier) | n/a | Best discovery quality of anything tried, but a multi-agent run generates hundreds of completions and the free-tier quota wall kills every run at ~10 min. Needs a paid endpoint; this project is local-only for now. |

Earlier candidates (`devstral:24b`, `hermes3:8b`, `qwen2.5-coder:14b-instruct`, `llama3-groq-tool-use:8b`, `mistral:7b-instruct-v0.3-q5_K_M`) were rejected at the tool-call-schema stage — see `ROADMAP.md` for that trial history. **Passing an isolated tool-call test isn't sufficient evidence a model will behave reliably in the full multi-agent role** (nemo is the proof) — test the actual Planner role with multiple independent trials. The meta-result of the benchmark: across all runs and models, no fabricated report got past the gates unlabeled — the defense layer is the validated product; model quality determines how often it has to fire.

### 3. Run

```bash
python src/app.py                                        # TUI
python src/app.py --prompt "..." --auto-approve          # headless
python src/app.py --prompt "..." --depth deep            # quota/search/retry presets: quick|standard|deep
python src/app.py --prompt "..." --seed-url https://...  # pre-fetch known-good sources (repeatable)
python src/app.py --resume-run <run_folder>              # reattach an interrupted run, fresh budget
python src/app.py --list-runs                            # workspace runs + report status
```

Headless runs are honest about failure: a pre-run search-health probe aborts in seconds with `ENVIRONMENT UNHEALTHY` (exit 1) instead of burning a doomed 20-minute run; a crashed run exits 1 and still saves forensics; every run ends with a finish-line summary (`Report: <path>` or `NOT WRITTEN`, sources fetched, search failures). `_run_state.json` is written from run start and updated on every fetch/search event, so even a killed run leaves a scoreable record. `settings.max_run_minutes` (default 45) cuts a runaway run at the turn boundary — labeling and salvage still run, so it ends with an explicit outcome.

In the TUI, the first message of a conversation gets a one-shot intake check (`clarify_before_research`): the model either replies CLEAR and proceeds or asks up to 3 scoping questions first — fail-open, and headless runs never ask. Follow-up messages in the same conversation reuse the run's workspace and fetched-URL state; once a report exists, follow-ups skip the completion check (Q&A mode).

## Config highlights (`config_template.yaml`)

- `settings.quotas` / `settings.retry_quota_topup` — global, cumulative-across-all-agents tool-call budgets, with extra headroom on a completion-check retry.
- `settings.grounding_check` — `content_level_check`, `non_url_citation_check`, `regulation_id_check`, `check_findings`, `verify_specialist_output`, `verify_scope_relevance`, `live_http_verify`.
- `settings.search_mode: heavy` — search deeper and auto-fetch more top results per call.
- `settings.search_backend` — `auto` rotates/falls back across ddgs's 10+ engines; a pinned single engine is a single point of failure (live-confirmed: DDG throttling made whole runs look like model fabrication).
- `settings.max_run_minutes` — wall-clock budget for headless runs; on expiry the completion check jumps to its final verdict instead of hard-killing.
- `settings.human_in_the_loop` — require approval on the Planner's `write_todos` before research proceeds.
- `settings.permissions` — per-tool approval gate (`<tool_name>: require_approval`). Defaults to gating `remove_workspace_file`, since deleting a file is the one destructive workspace action.
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
- [`dzhng/deep-research`](https://github.com/dzhng/deep-research) — source of the schema-forced FOLLOW-UP DIRECTIONS idea (Searchers must return next-round research leads for the Planner) and the information-density rule for findings (entities, exact metrics, dates). Its structural iterative-deepening loop (learnings-conditioned query generation with geometric narrowing) is a ROADMAP candidate.
- [`Alibaba-NLP/DeepResearch`](https://github.com/Alibaba-NLP/DeepResearch) (Tongyi DeepResearch) — source of the heavy search mode (test-time scaling, credited in `tools/web.py`) and the DocumentAnalyzer verbatim-evidence rule (its visit-tool extractor separates verbatim `evidence` from `summary`). Its context-budget endgame and the Tongyi-DeepResearch-30B-A3B model itself are ROADMAP candidates.
