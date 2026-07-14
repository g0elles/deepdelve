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
(general web research)     (papers, citations,        (fresh-context critique —
  |                          related work)              findings.md OR, in report
  |                                    |                 mode, final_report.md)
  +-- delegate_tasks, routed by content type -----+
  |                                               |
  v                                               v
DocumentAnalyzer                          DataAnalyzer
(prose/HTML extraction)              (tables, code, numbers, citations —
                                       verbatim pulls; also the only tool
                                       with extract_structured_data)

engine/completion.py's Build->Review->Fix loop (NOT the Planner):
  missing/failed final_report.md --> Builder writes it --> PeerReviewer
  reviews it (report mode) --> Builder fixes if flagged --> re-checked
```

- **Planner**: plans in bounded, named slots (`background`/`comparison`/`related_work`/`verification` — never an open-ended task list), dispatches specialists, runs an adaptive planning loop (observe results, replan if something's missing or contradictory), and writes `findings.md` (Pass 1: verbatim extraction), optionally delegating to `PeerReviewer` to critique it before considering its own turn done. The Planner never writes or delegates `final_report.md` itself — see Builder below.
- **Builder**: NOT dispatched by the Planner — a Planner-tier delegate dispatched exclusively by the completion-check system (`src/engine/completion.py`'s Build→Review→Fix loop), in a fresh context, once `findings.md` is ready. Writes/rewrites `final_report.md` from `findings.md`, then a fresh `PeerReviewer` dispatch reviews the result (`REVIEW: CLEAN` / `REVIEW: ISSUES FOUND:`); if flagged, Builder is re-dispatched once with the critique folded in. All of this happens outside the Planner's own conversation — retries never grow the Planner's context, which was the actual point (see "Context management" below).
- **WebSearcher / AcademicSearcher**: search and fetch. Specialist summaries are grounding-checked *before* they reach the Planner, not just at final-report time.
- **PeerReviewer**: Planner-tier delegate for an independent, fresh-context critique — of `findings.md` when the Planner dispatches it (Pass 2), or of `final_report.md` when the Build→Review→Fix loop dispatches it (same role, different target artifact named in its task instructions).
- **DocumentAnalyzer / DataAnalyzer**: read/extract from downloaded files. `DataAnalyzer` also has `extract_structured_data` for tables/code/JSON blocks.

Tool access is withheld from each parent so it's structurally forced to delegate rather than short-circuit the chain — see each role's Delegation Routing block in `src/prompts.py`.

## Context management

The Planner's own conversation only ever grows across a run (no compaction/pruning exists in the
underlying agent-framework session) — every completion-check retry historically meant appending
another nudge message and re-showing the model its own prior rejected drafts, which risks the
model's attention degrading well before any hard token limit is hit ("context poisoning"). The
**Builder + Build→Review→Fix loop** (`src/engine/completion.py`) is the structural fix: for
artifact-authoring problems (missing report, ungrounded citation, unsupported claim, etc.), the
completion-check system dispatches a fresh-context Builder directly — never touching the Planner's
`current_input` — instead of nudging the Planner to fix it itself. Only genuinely strategic
failures that need new research (`missing_findings`, `findings_ungrounded`, `not_delegated`) still
escalate to the Planner's own conversation, since only the Planner can decide what to delegate next.
`settings.context_budget_chars` (below) remains a second, independent guard against a single
sub-agent stream itself growing too large.

## Key structural fixes over the prototype

The full history (with live-test evidence for each) is in `ROADMAP.md`. The headline ones:

- **Real grounding check**: cross-references every cited URL against URLs actually fetched this run (`utils/grounding.py`), not a substring check — with a path-segment boundary, so a fetched `.../article` can't ground a decorated fabrication like `.../article-fake-2024`. A second, content-level layer flags a citation whose source shares zero checkable facts with the claim next to it. A third layer catches a claim attributed to something that isn't a URL at all (a bare `(DANE, 2020)` parenthetical, a `Source: <prose>` line) — unverifiable in exactly the same way a fabricated URL is, but invisible to a check that only looks for `https?://`. A fourth catches a regulation identifier ("Ley 1906 de 2021") cited to a genuinely-fetched source whose content never mentions that number. A fifth refuses citations to **stub fetches** — a URL that answered HTTP 200 with a paywall/not-found shell is recorded as `stub` at fetch time and can neither pass the URL gate nor support any claim (closes the invented-URL-plus-soft-404 hole found live in run 14). A sixth (`uncited_claims`) catches claims structurally decoupled from citations — a table of figures plus a detached "Source URLs" list passes every line-scoped check vacuously, so ≥3 figure-bearing lines in a section with no URL fail the check even when every listed URL is real. A seventh, **NLI entailment check** (`settings.grounding_check.nli_verify`, `cross-encoder/nli-deberta-v3-small`, CPU-only) catches a citation whose claim shares checkable terms with its source (so the term-overlap check alone passes it) but is actually CONTRADICTED by what the source says — e.g. a paper title quoted with one word swapped — running only on lines that already passed term-overlap, on the source's own best-matching paragraph window. Runs both on the final report and on each specialist's summary before it reaches the Planner. `findings.md` (Pass 1) is gated too: it must exist before `final_report.md` is accepted, and a wholesale-fabricated one (zero real citations) is quarantined. The verdict logic lives in `src/engine/completion.py` as an ordered check list, pinned by `test_structural_checks.py`'s verdict matrix.
- **Build→Review→Fix loop for `final_report.md`** (`src/engine/completion.py`): the completion-check system, not the Planner, owns getting the report written and correct — see "Context management" above. A dedicated `Builder` sub-agent writes/rewrites it from `findings.md`, `PeerReviewer` independently checks the result in a fresh context, and Builder gets one corrective re-dispatch if flagged, all before the Planner ever sees anything.
- **Fetch-time metadata extraction** (`tools/web.py::_extract_html_metadata`): title/author/published-date are pulled from the same BeautifulSoup parse already done for boilerplate-stripping and written as `Title:`/`Authors:`/`Published:` header lines alongside `Source-URL:` — eliminates the need for a separate sub-agent dispatch just to extract a paper's byline.
- **Fetched pages decoded by their real charset** (strict UTF-8 → HTTP header → meta tag → cp1252 fallback, stale charset meta tags scrubbed before markdown conversion) — mojibake had silently gutted every accent-bearing Spanish term match in the grounding checks on the benchmark's flagship language.
- **Fetched files carry provenance**: everything a run fetches lands in the run folder's `sources/` subdirectory with `Source-URL: <true url>` as line 1, so a cited claim can be traced to the exact bytes it came from; the run root holds only `final_report.md`, `findings.md`, `_todos.md`, and `_run_state.json`.
- **`web_search` auto-fetches its top result's full content** — there's no snippet-only path left for a model to stop at (`settings.web_search.auto_fetch_top`), which was the single biggest lever on real answer quality.
- **Per-attempt quota top-up, artifact quarantine before nudging, and history-scanning salvage** for a narrated-but-never-written report — all structural fixes, not prompt tuning, for failure modes that prompt tuning alone didn't resolve in testing.
- **Detailed tool-call validation errors** (`client.function_invocation_configuration["include_detailed_errors"]`): a rejected tool call shows the real Pydantic reason (e.g. "query: Input should be a valid string, got list") instead of a bare "Argument parsing failed." — this was the single most common error signature in real session logs (41 occurrences in one day) and was previously undiagnosable, for the model as well as for debugging.
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

1. **Edit `~/.deepdelve/config.yaml`** (created on first run from `src/tools/config_template.yaml`):
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
python src/app.py --prompt "..." --style academic        # literature-review paper shape + (Author, Year) citations
python src/app.py --prompt "..." --seed-url https://...  # pre-fetch known-good sources (repeatable)
python src/app.py --resume-run <run_folder>              # reattach an interrupted run, fresh budget
python src/app.py --list-runs                            # workspace runs + report status
```

Headless runs are honest about failure: a pre-run search-health probe aborts in seconds with `ENVIRONMENT UNHEALTHY` (exit 1) instead of burning a doomed 20-minute run; a crashed run exits 1 and still saves forensics; every run ends with a finish-line summary (`Report: <path>` or `NOT WRITTEN`, sources fetched, search failures). `_run_state.json` is written from run start and updated on every fetch/search event, so even a killed run leaves a scoreable record. `settings.max_run_minutes` (default 45) cuts a runaway run at the turn boundary — labeling and salvage still run, so it ends with an explicit outcome.

In the TUI, the first message of a conversation gets a one-shot intake check (`clarify_before_research`): the model either replies CLEAR and proceeds or asks up to 3 scoping questions first — fail-open, and headless runs never ask. Follow-up messages in the same conversation reuse the run's workspace and fetched-URL state; once a report exists, follow-ups skip the completion check (Q&A mode).

## Config highlights (`config_template.yaml`)

- `settings.quotas` / `settings.retry_quota_topup` — global, cumulative-across-all-agents tool-call budgets, with extra headroom on a completion-check retry.
- `settings.grounding_check` — `content_level_check`, `non_url_citation_check`, `regulation_id_check`, `check_findings`, `verify_specialist_output`, `verify_scope_relevance`, `live_http_verify`, `nli_verify` (entailment check, on by default — first run pays a one-time CPU model download/load cost).
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
- *Plan-and-Execute agentic architectures* survey work, e.g. [arXiv:2509.08646](https://arxiv.org/abs/2509.08646) — the established pattern the Builder + Build→Review→Fix loop maps onto: decouple planning (decompose, can use a cheaper/more strategic pass) from execution (carries out + retries mechanically), re-planning only on genuine failure rather than every step. Directly informed by the observation that DeepDelve's Planner conversation grows unboundedly across a run with no compaction (a documented "context poisoning" risk) and that the existing `delegate_tasks` mechanism already gives every dispatched sub-agent a genuinely fresh context — the fix is routing report-writing retries through that mechanism instead of the Planner's own conversation.
- Min, Krishna, Lyu, et al. *FActScore: Fine-grained Atomic Evaluation of Factual Precision in Long Form Text Generation*. [arXiv:2305.14251](https://arxiv.org/abs/2305.14251) — decompose-then-verify pattern (break a generation into atomic checkable facts, score each independently) that the grounding layer's line-scoped checks already followed in spirit; cited as prior art motivating the NLI entailment check (`nli_unsupported_problem`) below.
- Anonymous. *HALT-RAG: A Task-Adaptable Framework for Hallucination Detection with Calibrated NLI Ensembles and Abstention*. [arXiv:2509.07475](https://arxiv.org/abs/2509.07475) — source of the "layer NLI entailment on top of lexical/term-overlap checks, don't replace them" design choice for `nli_unsupported_problem`: HALT-RAG's own finding is that combining NLI with lexical signals outperforms either alone, which is why the entailment check only runs on claim lines that already passed the existing term-overlap check rather than gating independently.
- Anthropic. *How we built our multi-agent research system*. [anthropic.com/engineering/multi-agent-research-system](https://www.anthropic.com/engineering/multi-agent-research-system) — independent confirmation that a multi-agent research architecture (lead agent delegating to parallel subagents with their own fresh context) beat a single agent by 90.2% on Anthropic's own internal research eval, validating DeepDelve's existing Planner→Searchers→Analyzers shape and directly informing the Builder + Build→Review→Fix loop's decision to route report-writing retries through a fresh-context sub-agent dispatch rather than the Planner's own growing conversation.
- [`kyuz0/deep-research-agent`](https://github.com/kyuz0/deep-research-agent) — base architecture this was forked from.
- [`CYC2002tommy/Deep-Research-Agent`](https://github.com/CYC2002tommy/Deep-Research-Agent) — source of the "full-text reading is mandatory" and content-level claim-grounding ideas.
- [`nashsu/llm_wiki`](https://github.com/nashsu/llm_wiki) — source of the `findings.md` → `final_report.md` two-pass pattern and the structured run-state idea.
- [`dzhng/deep-research`](https://github.com/dzhng/deep-research) — source of the schema-forced FOLLOW-UP DIRECTIONS idea (Searchers must return next-round research leads for the Planner) and the information-density rule for findings (entities, exact metrics, dates). Its structural iterative-deepening loop (learnings-conditioned query generation with geometric narrowing) is a ROADMAP candidate.
- [`Alibaba-NLP/DeepResearch`](https://github.com/Alibaba-NLP/DeepResearch) (Tongyi DeepResearch) — source of the heavy search mode (test-time scaling, credited in `tools/web.py`) and the DocumentAnalyzer verbatim-evidence rule (its visit-tool extractor separates verbatim `evidence` from `summary`). Its context-budget endgame and the Tongyi-DeepResearch-30B-A3B model itself are ROADMAP candidates.
- [`imbad0202/academic-research-skills`](https://github.com/imbad0202/academic-research-skills) — reviewed for its literature-review paper structure and Anti-Leakage Protocol ("Knowledge Isolation Directive": prefer session materials over parametric memory, flag `[MATERIAL GAP]` instead of fabricating). Both are ROADMAP candidates for the academic output-mode work. Its bibliographic-API citation verification (Semantic Scholar/OpenAlex/Crossref/arXiv) was reviewed but not adopted — see ROADMAP "Evaluated and rejected".
- [`SkyworkAI/DeepResearchAgent`](https://github.com/SkyworkAI/DeepResearchAgent) — reviewed (self-evolution agent runtime: RSPL/SEPL protocol layers, RL-based prompt/solution optimizers, versioned tracing). Not adopted — see ROADMAP "Evaluated and rejected".
