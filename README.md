# DeepDelve

A locally-run, multi-agent deep research assistant built on the **Microsoft Agent Framework** and the **Textual** TUI library, targeting local OpenAI-compatible model servers (defaults to **Ollama**, `http://localhost:11434/v1`).

A from-scratch rebuild of an earlier prototype, not an incremental patch. The prototype worked end-to-end but was unreliable beyond simple lookups. See [`ROADMAP.md`](ROADMAP.md) for what's done, what's open, and the history of bugs found and fixed. See [`ARCHITECTURE.md`](ARCHITECTURE.md) for how the completion-check/writer-dispatch engine underneath this agent topology actually works — the ordered verdict pipeline, the cross-referenced tuples a new problem type needs to touch, and the resume/persisted-state surface. Read it before changing `src/engine/completion.py` or anything resume-related; it exists specifically because several real bugs came from exactly the kind of hidden cross-file coupling it maps out. See [`METHODOLOGY.md`](METHODOLOGY.md) for why this project's verification layer is built the way it is, the design principles that recurred across real incidents, and a sourced assessment (`RESEARCH.md` §9/§9a) of what's actually novel about it versus documented prior art. See [`MODELS.md`](MODELS.md) for the full local-model bake-off (21 candidates, why each one failed, and which verdicts are still open) — "Model & Endpoint" below only summarizes it.

## Architecture

```mermaid
flowchart TD
    Planner["<b>Planner</b><br/>plans + delegates ONLY<br/>no write_workspace_file, cannot write any file itself"]

    subgraph Tier2 ["Tier 2: Search"]
        WebSearcher["<b>WebSearcher</b><br/>general web research"]
        AcademicSearcher["<b>AcademicSearcher</b><br/>papers, citations, related work"]
    end

    subgraph Tier3 ["Tier 3: Analyze (leaf, no web tools)"]
        DocumentAnalyzer["<b>DocumentAnalyzer</b><br/>prose / HTML extraction"]
        DataAnalyzer["<b>DataAnalyzer</b><br/>tables, code, numbers,<br/>only one with extract_structured_data"]
    end

    Planner -- "delegate_tasks<br/>(by research angle)" --> WebSearcher
    Planner -- "delegate_tasks<br/>(by research angle)" --> AcademicSearcher
    WebSearcher -- "delegate_tasks<br/>(by content type)" --> DocumentAnalyzer
    WebSearcher -- "delegate_tasks<br/>(by content type)" --> DataAnalyzer
    AcademicSearcher -- "delegate_tasks<br/>(by content type)" --> DocumentAnalyzer
    AcademicSearcher -- "delegate_tasks<br/>(by content type)" --> DataAnalyzer

    Planner -. "stops delegating,<br/>enough results or quota exhausted" .-> FW

    subgraph WRF ["engine/completion.py Write to Review to Fix loop, runs OUTSIDE the Planner's conversation"]
        direction LR
        FW["<b>FindingsWriter</b><br/>writes findings.md from<br/>RunState's real structured results<br/>write_workspace_file / edit_workspace_file"] --> PR1{{PeerReviewer}}
        PR1 -- issues found --> FW
        PR1 -- clean --> BU["<b>Builder</b><br/>writes final_report.md<br/>from findings.md<br/>write_workspace_file / edit_workspace_file"]
        BU --> PR2{{PeerReviewer}}
        PR2 -- issues found --> BU
    end

    subgraph AUX ["Auxiliary local models — run in-process on CPU, NOT the Ollama-served LLM"]
        direction LR
        NLI["cross-encoder/nli-deberta-v3-small<br/>NLI entailment check<br/>(nli_unsupported_problem)"]
        RERANK["BAAI/bge-reranker-v2-m3<br/>topical-relevance cross-encoder<br/>(topical_mismatch_problem)"]
        EMBED["all-MiniLM-L6-v2<br/>sentence embeddings<br/>(rag_cache lookup + agent_routing_classifier)"]
    end

    WRF -. "grounding checks on<br/>final_report.md content" .-> NLI
    WRF -. "grounding checks on<br/>final_report.md content" .-> RERANK
    Planner -. "delegate_tasks agent_id<br/>prediction (optional)" .-> EMBED
```

- **Planner**: plans in bounded, named slots (`background`/`comparison`/`related_work`/`verification`, never an open-ended task list), dispatches specialists, and runs an adaptive planning loop (observe results, replan if something's missing or contradictory). That is its entire job: it has no `write_workspace_file` tool at all, so it structurally cannot write `findings.md`, `final_report.md`, or anything else. Once it stops delegating (enough real results, or quota exhausted), its turn simply ends.
- **FindingsWriter**: NOT dispatched by the Planner. A Planner-tier delegate dispatched exclusively by the completion-check system, in a fresh context, once the Planner has stopped delegating (or a prior `findings.md` failed its grounding check). Writes `findings.md`, a verbatim consolidation of every dispatched task's real result, from `RunState`'s structured `{source_url, summary}` records (populated automatically by every Searcher/Analyzer dispatch, not from the Planner's own conversation, which it never sees). Has `edit_workspace_file` (targeted old-string/new-string replacement, added 2026-07-28) alongside `write_workspace_file` for a small correction that doesn't warrant regenerating the whole file — see `ARCHITECTURE.md` §2. A fresh `PeerReviewer` dispatch then reviews the result; if flagged, FindingsWriter is re-dispatched once with the critique folded in.
- **Builder**: same pattern, one artifact later. Dispatched once `findings.md` is ready, writes/rewrites `final_report.md` from it (same `write_workspace_file`/`edit_workspace_file` pair), reviewed by a fresh `PeerReviewer` dispatch the same way.
- **WebSearcher / AcademicSearcher**: search and fetch. Specialist summaries are grounding-checked *before* they reach the Planner, not just at final-artifact time.
- **PeerReviewer**: Planner-tier delegate for an independent, fresh-context critique of `findings.md` when the FindingsWriter loop dispatches it, or of `final_report.md` when the Builder loop dispatches it (same role, different target artifact named in its task instructions). Never dispatched by the Planner itself.
- **DocumentAnalyzer / DataAnalyzer**: read/extract from downloaded files. `DataAnalyzer` also has `extract_structured_data` for tables/code/JSON blocks.
- **Auxiliary local models** (`src/utils/grounding.py`, `src/utils/agent_routing.py`, `src/utils/rag_cache.py`): three small models loaded directly in-process via `sentence-transformers`, on CPU, entirely separate from the Ollama-served LLM every agent role above talks to. `cross-encoder/nli-deberta-v3-small` powers the NLI entailment grounding check (layered on top of, never replacing, the existing lexical/term-overlap check — see the HALT-RAG reference below). `BAAI/bge-reranker-v2-m3` powers the topical-relevance check that catches an acronym-collision citation (a source that shares terms with a claim but is about a genuinely different subject). `all-MiniLM-L6-v2` produces the embeddings behind both the cross-run RAG cache's semantic similarity lookup and the optional `agent_routing_classifier` (predicts a `delegate_tasks` call's likely `agent_id` from its instructions text, `settings.agent_routing_classifier`, off by default — see `RESEARCH.md` §6).

Tool access is withheld from each parent so it's structurally forced to delegate rather than short-circuit the chain; see each role's Delegation Routing block in `src/prompts.py`. FindingsWriter and Builder exist for the same reason, one level down: giving the Planner the job of writing *either* artifact meant a retry on it grew the Planner's own conversation, the context-poisoning risk this design exists to avoid (see "Context management" below). That was true for `final_report.md`/Builder from the start; it was only fixed for `findings.md`/FindingsWriter on 2026-07-14, after a live benchmark run hit 4 consecutive `findings_ungrounded` retries and exhausted its budget with nothing ever written.

## Context management

The Planner's own conversation only ever grows across a run (no compaction/pruning exists in the underlying agent-framework session). Every completion-check retry historically meant appending another nudge message and re-showing the model its own prior rejected drafts, which risks the model's attention degrading well before any hard token limit is hit ("context poisoning"). The **FindingsWriter/Builder + Write to Review to Fix loop** (`src/engine/completion.py`) is the structural fix: for artifact-authoring problems on EITHER artifact (missing findings, ungrounded citation, unsupported claim, etc.), the completion-check system dispatches a fresh-context writer role directly, never touching the Planner's `current_input`, instead of nudging the Planner to fix it itself. Only one genuinely strategic failure that needs new research (`not_delegated`) still escalates to the Planner's own conversation, since only the Planner can decide what to delegate next. `settings.context_budget_chars` (below) remains a second, independent guard against a single sub-agent stream itself growing too large.

This closed a real gap late: `findings.md` only got this treatment on 2026-07-14, well after `final_report.md`/Builder. Until then, the Planner wrote `findings.md` itself, so a `findings_ungrounded` retry grew the Planner's own conversation the exact way this whole mechanism exists to prevent. Confirmed live the same day it was fixed: a benchmark run hit 4 consecutive `findings_ungrounded` retries and exhausted its budget with nothing ever written.

## Key structural fixes over the prototype

The full history (with live-test evidence for each) is in `ROADMAP.md`. The headline ones:

- **Real grounding check**: cross-references every cited URL against URLs actually fetched this run (`utils/grounding.py`), not a substring check, with a path-segment boundary so a fetched `.../article` can't ground a decorated fabrication like `.../article-fake-2024`. A second, content-level layer flags a citation whose source shares zero checkable facts with the claim next to it. A third layer catches a claim attributed to something that isn't a URL at all — a bare `(DANE, 2020)` parenthetical, a `Source: <prose>` line, or (added 2026-07-29, live case) a `【Bracketed Label】`-style full-width-bracket marker with the real link living only in a separate, unordered "Sources" list — unverifiable in exactly the same way a fabricated URL is, but invisible to a check that only looks for `https?://`. A fourth catches a regulation identifier ("Ley 1906 de 2021") cited to a genuinely-fetched source whose content never mentions that number. A fifth refuses citations to **stub fetches**: a URL that answered HTTP 200 with a paywall/not-found shell is recorded as `stub` at fetch time and can neither pass the URL gate nor support any claim (closes the invented-URL-plus-soft-404 hole found live in run 14). A sixth (`uncited_claims`) catches claims structurally decoupled from citations: a table of figures plus a detached "Source URLs" list passes every line-scoped check vacuously, so ≥3 figure-bearing lines in a section with no URL fail the check even when every listed URL is real. A seventh, **NLI entailment check** (`settings.grounding_check.nli_verify`, `cross-encoder/nli-deberta-v3-small`, CPU-only), catches a citation whose claim shares checkable terms with its source (so the term-overlap check alone passes it) but is actually CONTRADICTED by what the source says, e.g. a paper title quoted with one word swapped, running only on lines that already passed term-overlap, on the source's own best-matching paragraph window. An eighth, **atomic-claim segmentation** (`utils/grounding.py::decompose_claim_segments`), splits a line into per-citation segments before any of the above run, closing a gap where two distinct claims sharing one line (each with its own citation) could pass on a shared generic term even though one claim's own citation didn't actually back it. A ninth, **cross-source contradiction detection** (FEVER-style, `find_cross_source_contradictions`), flags a report silently picking one side of a real disagreement between two of its OWN fetched sources without saying so; distinct from claim_unsupported, since the cited source really does support the claim, it's just not the only fetched source with an opinion. A tenth, a second cross-encoder checkpoint (`BAAI/bge-reranker-v2-m3`, `topical_relevance_problem`), asks whether the cited source is actually about the SAME SUBJECT as the claim, not just lexically non-contradictory. It catches an acronym-collision citation (confirmed live: a Grasshopper Optimization Algorithm claim citing a source that was actually about the Indian state of Goa's EV policy) that passes every upstream layer since "GOA"/"Goa" genuinely overlaps and doesn't contradict. Runs both on the final report and on each specialist's summary before it reaches the Planner. `findings.md` (Pass 1) is gated too: it must exist before `final_report.md` is accepted, and a wholesale-fabricated one (zero real citations) is quarantined. The verdict logic lives in `src/engine/completion.py` as an ordered check list, pinned by `test_structural_checks.py`'s verdict matrix. Because it's an ordered "first verdict wins" list, a persistently-recurring higher-priority problem can silently shadow a real, different, lower-priority one for a run's entire retry budget — confirmed live 2026-07-29 (`check_stub_source` shadowing `check_uncited_claims` for 3 attempts, never disclosed even in the final "unresolved" message). `utils/grounding.py::cheap_grounding_problems` now re-surfaces every OTHER currently-true problem alongside the winning one (both in the model-facing nudge and the user-facing terminal message), without changing which single problem is recorded/escalated on.
- **Write to Review to Fix loop for BOTH `findings.md` and `final_report.md`** (`src/engine/completion.py`): the completion-check system, not the Planner, owns getting each artifact written and correct; see "Context management" above. A dedicated `FindingsWriter` sub-agent writes `findings.md` straight from `RunState`'s structured per-task results (not the Planner's conversation, which it never sees); a dedicated `Builder` sub-agent writes `final_report.md` from `findings.md`. `PeerReviewer` independently checks each result in a fresh context, and the writer gets one corrective re-dispatch if flagged, all before the Planner ever sees anything, since (as of 2026-07-14) the Planner has no `write_workspace_file` tool at all and cannot write either artifact itself. A writer dispatch that returns a genuinely EMPTY response (zero tool calls, zero narrated text — confirmed live even on `gpt-oss`, the project's own baseline, under sustained retry pressure) gets one immediate retry with a fresh dispatch before the cycle gives up; if a Write dispatch still produces nothing after that retry, the loop raises immediately rather than dispatching `PeerReviewer` against an artifact that doesn't exist — closes a real compounding bug where `PeerReviewer`, asked to review a nonexistent file, degraded into guessing wrong filenames and burned its entire `read_workspace_file` quota on nothing.
- **Coverage accounting** (`utils/run_state.py::RunState.coverage()`, `engine/completion.py::check_thin_coverage`): distinct from every grounding check above (those verify content that already exists is properly cited), this instead asks whether the Planner's own top-level delegated research plan actually paid off. Flags a report that's perfectly grounded yet thin because a majority of the Planner's own delegated angles came back with no real source and got silently dropped. Built entirely from already-reliable, engine-populated data (delegation depth + per-task fetch attribution) rather than a new Planner-authored schema, deliberately avoiding the small-local-model structured-output-compliance problem the rest of this project has repeatedly hit. A single-task query that succeeded is never affected, regardless of "breadth." Two further layers close the same gap one and two stages downstream: `check_findings_underuses_evidence` catches a covered task's real evidence vanishing entirely during `findings.md` consolidation (a task with genuine sources produces zero entries), and `check_report_underuses_evidence` (added 2026-07-29) catches `final_report.md` citing zero sources for a task even though `findings.md` has real, surviving ones for it — a gap the flat citation-ratio check (`check_report_underuses_findings`) can miss outright when the surviving task simply had more raw sources than the dropped one (live case: a report cleared a 50% citation-ratio threshold while dropping an entire query facet).
- **xQuAD-style search-result diversity reranking** (`tools/web.py::_diversity_rerank`): DDGS's own ranking commonly surfaces several near-duplicate results for the same angle at the top, so this greedily reorders results by marginal NEW aspect-term coverage instead of raw rank (DDGS's own #1 always stays first). Pure reranking, no LLM call, no new dependency, improving both the auto-fetch selection and the returned snippet ordering.
- **Independent per-dispatch wall-clock deadline** (`settings.sub_agent_timeout_minutes`): every sub-agent dispatch (Searcher/Analyzer/Builder/FindingsWriter/PeerReviewer) races its own stream against a fresh deadline, deliberately independent of the run-wide `max_run_minutes` guard (a shared/anchored deadline loses the cancellation race to the outer guard, confirmed live). Closes a real gap where a single runaway generation (confirmed live: 19,908+ tokens, continuously and validly decoding, no stall) had nothing watching it and fell back to the raw SDK client's blunt ~600s default, which discards the whole in-progress response instead of degrading gracefully with whatever text had already been generated.
- **Fetch-time metadata extraction** (`tools/web.py::_extract_html_metadata`): title/author/published-date are pulled from the same BeautifulSoup parse already done for boilerplate-stripping and written as `Title:`/`Authors:`/`Published:` header lines alongside `Source-URL:`, eliminating the need for a separate sub-agent dispatch just to extract a paper's byline.
- **Fetched pages decoded by their real charset** (strict UTF-8 to HTTP header to meta tag to cp1252 fallback, stale charset meta tags scrubbed before markdown conversion). Mojibake had silently gutted every accent-bearing Spanish term match in the grounding checks on the benchmark's flagship language.
- **Fetched files carry provenance**: everything a run fetches lands in the run folder's `sources/` subdirectory with `Source-URL: <true url>` as line 1, so a cited claim can be traced to the exact bytes it came from; the run root holds only `final_report.md`, `findings.md`, `_todos.md`, and `_run_state.json`.
- **`web_search` auto-fetches its top result's full content**: there's no snippet-only path left for a model to stop at (`settings.web_search.auto_fetch_top`), which was the single biggest lever on real answer quality.
- **Per-attempt quota top-up, artifact quarantine before nudging, and history-scanning salvage** for a narrated-but-never-written report. All structural fixes, not prompt tuning, for failure modes that prompt tuning alone didn't resolve in testing.
- **Detailed tool-call validation errors** (`client.function_invocation_configuration["include_detailed_errors"]`): a rejected tool call shows the real Pydantic reason (e.g. "query: Input should be a valid string, got list") instead of a bare "Argument parsing failed." This was the single most common error signature in real session logs (41 occurrences in one day) and was previously undiagnosable, for the model as well as for debugging.
- **`RunState`** (`utils/run_state.py`) persists fetched URLs, findings, and completion-check attempts per run as `_run_state.json`, independent of the model's own narration.
- **Headless/headed-browser fetch fallback** (`src/utils/browser_fetch.py`, optional `playwright`+`pyvirtualdisplay` extra, see Setup): a fetch that comes back looking like a bot-wall stub (Akamai blocks, browser-version-sniffing blocks, headless-specific fingerprint blocks) gets one retry, real (non-headless) Chromium first if a display or virtual Xvfb display is available, headless otherwise, reusing the same boilerplate-strip/markdown pipeline as the plain fetch. Recovers real sources a scripted client alone can't reach (confirmed live against Springer, and against MDPI with the headed path specifically). Deliberate non-goal: a genuine Cloudflare Turnstile challenge (confirmed live against ScienceDirect) resists both headless and headed Chromium. It's automation/CDP-fingerprint detection, not a timing problem, and DeepDelve doesn't attempt to spoof past it; that source correctly falls through to the stub flag instead.

## Setup

### 1. Environment

> **NTFS gotcha:** if this project directory is on an NTFS mount (`df -T .` shows `ntfs3`), `python3 -m venv venv` inside the project folder fails since NTFS doesn't support the symlinks venv needs. Create it elsewhere instead:
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

**Optional: headless/headed-browser fetch fallback.** Some publishers (confirmed live 2026-07-14:
Springer, ScienceDirect, MDPI) bot-wall a plain HTTP fetch (a UA-sniffing block, a JS challenge,
or a headless-specific fingerprint block), so a real, citable paper can come back looking like a
fake/stub source. Installing Playwright lets `fetch_url_to_workspace` retry once via Chromium
before giving up (`src/utils/browser_fetch.py`, `settings.fetch.headless_fallback`, default on,
no-op if not installed). It tries a real (non-headless) browser first, confirmed live to recover
sites headless alone couldn't (MDPI), falling back to headless if no display is available:
```bash
pip install -e ".[browser]"
playwright install chromium
```
On a display-less Linux server, also install system `Xvfb` (`sudo apt install xvfb` or your
distro's equivalent) so the headed browser has a virtual display to run against.
`pyvirtualdisplay` (bundled in the `browser` extra) manages it automatically. Without `Xvfb`, the
fallback still works, just headless-only (recovers Springer, not MDPI's stricter block).

### 2. Model & Endpoint

DeepDelve talks to any **OpenAI-compatible chat-completions endpoint**. It isn't Ollama-specific, that's just the default. Three ways to point it elsewhere, in order of precedence (later overrides earlier):

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
   export OPENAI_API_BACKEND="openai_hosted"   # only for a real hosted frontier API — see
                                                # api.backend below; omit for local/self-hosted
   python src/app.py
   ```
3. **A separate config file entirely** via `--config`/`-c`:
   ```bash
   python src/app.py --config /path/to/other-config.yaml
   ```

**`api.backend`** (default `"openai"`, added 2026-07-28): set to `"ollama"` to talk to Ollama's own
**native** `/api/chat` endpoint instead of its OpenAI-compat `/v1/chat/completions` one
(`agent_framework.ollama.OllamaChatClient`, same plugin family as the default OpenAI-compat
client). Confirmed live (`RESEARCH.md` §14e): the OpenAI-compat endpoint leaks a short reasoning
field back into tool-calling turns even with `settings.enable_thinking: false`, while the native
endpoint suppresses it cleanly in the identical scenario — a real, model-independent gap in
Ollama's OpenAI-compat shim, not something a config value can fix on that path. `openai_base_url`
can keep its existing `/v1` suffix when switching — it's stripped automatically. Only meaningful
when actually running against Ollama; irrelevant for other OpenAI-compatible providers (vLLM,
LM Studio, real OpenAI, etc.), which stay on the default `"openai"` backend.

**`api.backend: "openai_hosted"`** (added 2026-08-04): for a real **hosted frontier API**
(DeepSeek, and whatever gets tested next) as opposed to local/self-hosted OpenAI-compat serving.
The default `"openai"` backend's thinking-mode control (`chat_template_kwargs` +
`reasoning_effort: "none"`) is a vLLM/local-serving convention — confirmed live against DeepSeek
that a real hosted API just silently ignores both fields, leaving thinking mode stuck at that
provider's own default (DeepSeek: ON, effort `"high"`). `"openai_hosted"` skips that local-serving
dance entirely and looks up each provider's own documented thinking-mode toggle instead
(`orchestrator.py`'s `_HOSTED_PROVIDER_THINKING_EXTRA_BODY`, keyed by a substring of
`openai_base_url` — add an entry there, not a new backend value, for the next hosted provider).

This works for any local server that speaks the OpenAI chat-completions API (Ollama, LM Studio, vLLM, llama.cpp's server, text-generation-webui) or any hosted provider that does (OpenAI itself, OpenRouter, Together, Groq, etc.). Just set the base URL, model name, and API key accordingly. The one hard requirement, regardless of provider, is real structured tool-calling support (see below): this agent is 100% tool-call driven, and a model/endpoint that only narrates JSON as text will not work.

The rest of this section documents the **Ollama default** and its specific gotchas. Skip it if you're pointing at a different provider.

Default model: `deepdelve-gpt-oss` (a `gpt-oss:20b` derived tag, see below). Two things that will silently break tool-calling if skipped:

> **Tool-call support:** this agent is 100% tool-call driven. If a model never emits a structured `tool_calls` response, every agent just narrates instead of acting. Models from the official Ollama library ship with a maintainer-verified tool-call parser; `hf.co/...` GGUF imports often don't. Verify with:
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
> Also set `OLLAMA_NUM_PARALLEL=1` in `/etc/systemd/system/ollama.service.d/override.conf` and restart. Ollama otherwise divides `num_ctx` across parallel request slots (often 4), silently giving each real request a quarter of the context you configured.

**Model choice**: `deepdelve-gpt-oss` (`gpt-oss:20b`) is the only local candidate with a full
benchmark pass across two live benchmarks (13-run Colombia B2B rubric, `eval/
colombia_b2b_benchmark.md`; sales-forecasting/heuristic-algorithms rubric, `eval/
sales_forecasting_benchmark.md`) out of **21 candidates tried**. Passing an isolated tool-call
schema test is NOT sufficient evidence a model behaves reliably in the real multi-agent role — every
candidate was run through the actual Planner/Searcher/Writer roles, not just a smoke test. Full
per-candidate detail (why each one failed, backend/serving-layer bugs found along the way, and which
verdicts are INCONCLUSIVE vs. a settled DISQUALIFIED) moved to **[`MODELS.md`](MODELS.md)** — the
table outgrew what plain Markdown renders legibly once notes ran to paragraph length. Live-run
detail and ongoing trials are in `ROADMAP.md`'s "Local-model bake-off" entry.

The `†` marks in `MODELS.md` denote every Qwen3-family candidate benchmarked under a since-confirmed
Ollama serving-layer bug (uncontrolled chain-of-thought reasoning polluting `.content` despite
`enable_thinking: false`) — a lower-cost re-test path now exists (`api.backend: "ollama"`,
`ARCHITECTURE.md` §6) but hasn't been run against those rows yet. See `MODELS.md`'s "Cross-cutting
notes" for the full trace. **The meta-result holds across every run and model regardless: no
fabricated report has ever gotten past the grounding gates unlabeled** — the defense layer is the
validated product; model quality only determines how often it has to fire.

### 3. Run

```bash
python src/app.py                                        # TUI
python src/app.py --prompt "..." --auto-approve          # headless
python src/app.py --prompt "..." --depth deep            # quota/search/retry presets: quick|standard|deep
python src/app.py --prompt "..." --style academic        # literature-review paper shape + (Author, Year) citations
python src/app.py --prompt "..." --seed-url https://...  # pre-fetch known-good sources (repeatable)
python src/app.py --prompt "..." --seed-doc ./notes.pdf  # load a local file (PDF/DOCX/XLSX/PPTX/txt/md) into the run (repeatable)
python src/app.py --resume-run <run_folder>              # reattach an interrupted run, fresh budget
python src/app.py --list-runs                            # workspace runs + report status
```

Headless runs are honest about failure: a pre-run search-health probe aborts in seconds with `ENVIRONMENT UNHEALTHY` (exit 1) instead of burning a doomed 20-minute run; a crashed run exits 1 and still saves forensics; every run ends with a finish-line summary (`Report: <path>` or `NOT WRITTEN`, sources fetched, search failures). `_run_state.json` is written from run start and updated on every fetch/search event, so even a killed run leaves a scoreable record. `settings.max_run_minutes` (default 45) cuts a runaway run at the turn boundary; labeling and salvage still run, so it ends with an explicit outcome.

In the TUI, the first message of a conversation gets a one-shot intake check (`clarify_before_research`): the model either replies CLEAR and proceeds or asks up to 3 scoping questions first (fail-open, and headless runs never ask). Follow-up messages in the same conversation reuse the run's workspace and fetched-URL state; once a report exists, follow-ups skip the completion check (Q&A mode). `/seed-doc` is the TUI slash-command equivalent of `--seed-doc`.

**Every run's output folder** gets, alongside `final_report.md`: `references.bib` (BibTeX, one entry per URL the report ACTUALLY cites — not every source fetched, see `utils/run_state.py::build_bibliography`), always generated once the report cites at least one real source, and optionally `final_report.pdf` if `settings.pdf_engine` is set (off by default, needs the external `pandoc` binary — see below).

### 4. Optional: HTTP API + web UI

`src/api.py` (FastAPI) is an alternative surface to the TUI/CLI — a local HTTP server exposing the same research engine, plus a minimal web UI (`src/static/index.html`) for submitting queries, browsing past runs (with resume), asking follow-ups, and editing settings from a browser. Not installed by default:

```bash
pip install -e ".[api]"
deepdelve-api                          # binds 127.0.0.1:8420 by default
deepdelve-api --host 0.0.0.0 --port 8420 --i-understand-the-risk   # LAN/phone access
```

Then open `http://127.0.0.1:8420/` on the same machine, or `http://<your-LAN-IP>:8420/` from another device once bound non-loopback (`hostname -I` or `ip route get 1.1.1.1` prints your LAN IP on Linux). Three tabs:
- **Research**: submit a query (depth/style/local-file attachments), watch it run as a per-agent timeline — one collapsible entry per sub-agent dispatch with a live running/done status icon, not a flat interleaved log — with a Cancel button while it's active. Once done, view the rendered report (a source-cards grid, not just raw markdown) and ask follow-ups in the same conversation.
- **Runs**: every past run, with an accurate status badge (running/done/failed/cancelled/no report) sourced from the live job state, not just "does a file exist on disk" — a run still in progress never shows a stale Resume button, and View Report never points at a file that's still being rewritten. Reattach to anything currently running, or Resume anything that ended without a report.
- **Settings**: your full live config as an editable form (not a raw JSON blob), secrets masked on read.

Endpoints, for scripting against it directly: `POST /research` (start, multipart for file uploads), `GET /research/{id}/status|stream|report|bib|pdf`, `POST /research/{id}/resume|followup|cancel`, `GET /runs`, `GET /active` (discover whatever's currently running), `GET`/`POST /settings`.

**Concurrency**: one research run at a time, process-wide (an in-memory FIFO queue) — `orchestrator.py`'s conversational-memory session and `tui.py`'s session-log state are module-level globals, not per-request-safe, so the API deliberately never runs two jobs concurrently rather than risking cross-run state corruption. Submitting while one is in flight queues it, it doesn't reject or run alongside.

**Security**: no auth by default, matching the TUI/CLI's local-single-user posture — binding a non-loopback host requires the explicit `--i-understand-the-risk` flag. If you do expose it (e.g. for phone access on your LAN), set `settings.api_password` first: every request except the static page itself then requires a matching `X-API-Password` header (the web UI prompts for it automatically and remembers it). The settings editor can read and write your full config, API keys included — treat that endpoint accordingly on a shared network.

## Config highlights (`config_template.yaml`)

- `settings.quotas` / `settings.retry_quota_topup`: global, cumulative-across-all-agents tool-call budgets, with extra headroom on a completion-check retry.
- `settings.grounding_check`: `content_level_check`, `non_url_citation_check`, `regulation_id_check`, `stub_detection`, `citation_format_check`, `check_findings`, `verify_specialist_output`, `verify_scope_relevance`, `live_http_verify`/`live_http_timeout`, `nli_verify` (entailment check, on by default, first run pays a one-time CPU model download/load cost), `topical_relevance_check`/`topical_relevance_threshold` (same-subject check, second cross-encoder checkpoint, same one-time cost).
- `settings.coverage_check`: `enabled`/`threshold`/`min_tasks`, flags a report whose Planner-delegated research plan came back mostly empty, even if what got written is fully grounded.
- `settings.sub_agent_timeout_minutes` (default 10): independent per-dispatch wall-clock ceiling for every sub-agent call, separate from `settings.max_run_minutes`. **Not back-filled automatically into an existing `~/.deepdelve/config.yaml`.** If you're upgrading from an older config rather than starting fresh, add this key by hand or a sub-agent dispatch has no deadline of its own.
- `settings.max_run_minutes`: wall-clock budget for headless runs; on expiry the completion check jumps to its final verdict instead of hard-killing.
- `settings.max_completion_check_attempts` (default 3): how many retry cycles a run gets on an unresolved completion-check problem before giving up; raise it if you have wall-clock/quota budget to spare and would rather the agent keep revising.
- `settings.context_budget_chars` (default 50000): per-agent-stream character budget guarding against Ollama's silent top-of-context truncation on overflow; see "Context management" above.
- `settings.search_mode: heavy`: search deeper and auto-fetch more top results per call.
- `settings.search_backend`: `auto` rotates/falls back across ddgs's 10+ engines. A pinned single engine is a single point of failure (live-confirmed: DDG throttling made whole runs look like model fabrication).
- `settings.concurrency.max_concurrent_tasks` (default 3): how many sub-agent dispatches can run in parallel within one `delegate_tasks` batch.
- `settings.report_style` (`standard`/`academic`/`answer`, also settable per-run via `--style`): report shape, orthogonal to `--depth`, which only changes tool budgets. See the file's inline comments for what each shape produces.
- `settings.human_in_the_loop`: require approval on the Planner's `write_todos` before research proceeds.
- `settings.permissions`: per-tool approval gate (`<tool_name>: require_approval`). Defaults to gating `remove_workspace_file`, since deleting a file is the one destructive workspace action.
- `settings.enable_conversational_memory` / `settings.enable_session_persistence`: whether follow-up messages in the same TUI conversation reuse prior context, and whether a session survives a restart (`~/.deepdelve/sessions/session_<id>.json`).
- `settings.mcp_servers`: wire in external MCP tools (e.g. Semantic Scholar, Brave Search), scoped per sub-agent. See the file's inline comments for ready-to-uncomment examples.
- `settings.pdf_engine`: off (`null`) by default. Set to `"weasyprint"` (`pip install -e ".[pdf]"`, no system TeX needed) or `"xelatex"`/`"pdflatex"` (real LaTeX typesetting, needs a full TeX install) to also produce `final_report.pdf` — needs the `pandoc` system binary either way (`apt install pandoc` or equivalent); skipped with a one-line notice, not a hard failure, if pandoc isn't on `PATH`.
- `settings.api_password`: unset by default (no auth). Only relevant if you run `src/api.py`'s optional HTTP API/web UI on a non-loopback host — see "Optional: HTTP API + web UI" above.
- `settings.specialist_model` (+ `settings.specialist_base_url` for a specialist on a different endpoint, e.g. a translation proxy or a second local server): optional second model, used only for the leaf specialist roles (WebSearcher/AcademicSearcher/DocumentAnalyzer/DataAnalyzer) while `api.openai_model` stays reserved for Planner/Builder/FindingsWriter/PeerReviewer. Unset by default. A live A/B (`gpt-oss:20b` + `qwen3:4b`) found this pairing 4.2x slower and lower-quality than a single model on this hardware. Two MiniCPM candidates were also tried and discarded here: `MiniCPM4-MCP` (needed a custom translation proxy for its non-OpenAI tool-call format, infrastructure kept but the model itself not viable) and `MiniCPM5-1B` (tested in both Ollama's unintentional think-mode — Ollama doesn't evaluate the model's real chat template, a documented gap shared by MLX/LM Studio — and genuine nothink mode via a real vLLM server; the properly-configured nothink run produced a citation-traced hallucination worse than the think-mode run, discarded). Full trial history for all of these in `ROADMAP.md`'s bake-off log; kept as a config option for a smaller/faster specialist model or different hardware where two models can coexist in VRAM without reload thrashing.

## Eval Harness

`eval/` is a headless-run + score harness. `dataset.jsonl` ships with 4 items: a simple factual lookup, a comparative query, a paper-plus-related-work academic query, and the Colombia B2B niche-research query used for the 13-run benchmark above (weighted multi-criteria rubric, see `eval/colombia_b2b_benchmark.md`).

```bash
python eval/evaluate.py --runs 3
python eval/results_viewer.py
```

## References

**Why local models are hard for this task, briefly**: DeepDelve's own model bake-off (10 candidates,
9 disqualified, `gpt-oss:20b` the only full pass) is not an idiosyncratic gap. A published capacity-
floor study found `qwen2.5:14b` as the "minimum viable production" threshold for reliable tool
invocation, with sub-8B models failing at 40-85%+ rates on a narrower, more controlled task than
DeepDelve's own open-ended research (Huang, Malwe, Wang, arXiv:2601.16280). Independently, two
studies found small/mid open-weight models specifically fail at structured tool-call serialization
(schema-valid output, wrong content) in a way a 6,000-sample fine-tuning run could not fix, because
it happens downstream of anything fine-tuning touches (Li, Zhang, Lv, arXiv:2606.25605; Ray,
arXiv:2605.26128). MAST's 14-mode failure taxonomy (Cemri et al., arXiv:2503.13657, NeurIPS 2025)
maps closely onto DeepDelve's own documented bug catalog (see ROADMAP.md's "narrate instead of
write," over-research, and exclusion-enforcement entries) — evidence these are known, published
agent-failure patterns, not DeepDelve-specific quirks. Full literature review, corrections, and
still-open leads in `RESEARCH.md`.

- Huang, Malwe, Wang. *When Agents Fail to Act: A Diagnostic Framework for Tool Invocation Reliability in Multi-Agent LLM Systems*. [arXiv:2601.16280](https://arxiv.org/abs/2601.16280). Capacity-floor evidence for the local-model-selection constraint above (14B as the tested "minimum viable production" threshold for tool invocation), motivating why `gpt-oss:20b` rather than a smaller default.
- Li, Zhang, Lv. *Constraint Tax in Open-Weight LLMs: An Empirical Study of Tool Calling Suppression Under Structured Output Constraints*. [arXiv:2606.25605](https://arxiv.org/abs/2606.25605). Confirms DeepDelve's own code never combines a JSON-schema `response_format` with tool availability in the same call (the exact condition that causes 0% tool-invocation-rate on every open-weight model tested, including `GPT-OSS-20B`); its proven Two-Pass Execution mitigation independently matches DeepDelve's own Planner→Builder/FindingsWriter split.
- Ray. *The Constraint Tax: Measuring Validity-Correctness Tradeoffs in Structured Outputs for Small Language Models*. [arXiv:2605.26128](https://arxiv.org/abs/2605.26128). Originating paper for the constraint-tax concept above; its "reason free, constrain late" finding independently confirms the same design choice as the Two-Pass Execution paper.
- Cemri, Pan, Yang, et al. *Why Do Multi-Agent LLM Systems Fail?* (MAST). [arXiv:2503.13657](https://arxiv.org/abs/2503.13657), NeurIPS 2025 Track on Datasets and Benchmarks. 14-mode failure taxonomy mapping closely onto DeepDelve's own documented bug catalog (see ROADMAP.md).
- Cemri, Cojocaru, Pan, et al. *Fantastic Adaptive Taxonomies and How to Use Them* (ATLAS/AdaMAST). ICML 2026 Workshop on Failure Modes in Agentic AI. [`multi-agent-systems-failure-taxonomy/ATLAS`](https://github.com/multi-agent-systems-failure-taxonomy/ATLAS). Induces a domain-specific failure taxonomy directly from a target system's own execution traces rather than MAST's fixed vocabulary; a ROADMAP candidate for DeepDelve's own `_run_state.json` history.
- Liu, Lin, Hewitt, et al. *Lost in the Middle: How Language Models Use Long Contexts*. [arXiv:2307.03172](https://arxiv.org/abs/2307.03172), TACL 2024. A candidate mechanism (distinct from context-size truncation) for DeepDelve's own recurring "real content silently vanishes during final synthesis" pattern.
- Li, Zhang, Zhang, Chaturvedi. *Coverage-based Fairness in Multi-document Summarization*. [arXiv:2412.08795](https://arxiv.org/abs/2412.08795), NAACL 2025. Formalizes per-cluster/per-document representation checks ("Equal Coverage"/"Coverage Parity") as the standard way to catch a summarizer that silently favors one source-group over another; validates the per-task binary-coverage design behind `check_report_underuses_evidence` (Builder-stage report-writing check, `src/engine/completion.py`) as a legitimate, cheap proxy for their more expensive NLI-based metric — closes a live-confirmed gap where a report can clear a flat citation-ratio threshold while dropping an entire query facet, because the surviving facet simply had more raw sources.
- Huang, Ma, Tahir, Zhang, Xiao, Xiao. *Regression Accumulation in Multi-Turn LLM Programming Conversations*. [arXiv:2607.01855](https://arxiv.org/abs/2607.01855). Finds 55.7% of multi-turn LLM correction regressions are a later fix breaking an earlier, already-satisfied requirement through incompatibility, not forgetting; their validated "Verification Gate" mitigation (full re-verification every turn, every failing constraint made visible) directly informed the fix for a real check-priority-shadowing bug (`utils/grounding.py::cheap_grounding_problems`, `src/engine/completion.py::_with_other_grounding_addendum`) where `real_grounding_problem`'s own first-hit priority chain could silently shadow a second, simultaneously-true grounding problem for an entire run's retry budget.
- Kamoi, Zhang, Zhang, Han, Zhang. *When Can LLMs Actually Correct Their Own Mistakes? A Critical Survey of Self-Correction of LLMs*. [arXiv:2406.01297](https://arxiv.org/abs/2406.01297), TACL. The "self-correction blind spot" (models are measurably worse at fixing errors in their OWN prior output than the identical error framed as external input — ~64.5% of self-generated errors survive self-checking across 14 open models) — the mechanism behind a live-confirmed, still-open failure mode: `check_report_underuses_findings`/`check_report_underuses_evidence` (`src/engine/completion.py`) correctly name the exact missing sources on every retry, but Builder's own full-document regeneration plateaus at the same low coverage across 6+ consecutive attempts.
- Tsui. *Self-Correction Bench: Uncovering and Addressing the Self-Correction Blind Spot in Large Language Models*. [arXiv:2507.02778](https://arxiv.org/abs/2507.02778), COLM 2026. Read in full (not just the WebSearch summary a first pass this session mistakenly relied on) — the likely ORIGINAL empirical source of the 64.5% figure above (the Kamoi et al. paper is a survey, which typically compiles rather than measures). Isolates the blind spot as a genuine activation failure (models correct the identical error when it's presented externally, ruling out "doesn't know better") and finds it is NOT solved by closed-source frontier models either (Claude 3.5 Haiku 52.5%, Claude Sonnet 4 41.4% blind spot) — relevant confirmation that a frontier model failing to self-correct its own prior output is a known, measured phenomenon, not an anomaly. Its most actionable finding for this project: appending the single word "Wait" after a model's own erroneous output, with no fine-tuning at all, reduces the blind spot by 89.3% and nearly matches that same model's full reasoning-mode accuracy — a candidate addition to `_dispatch_writer_review_fix`'s retry-instructions prepend (`src/engine/completion.py`, commit `4dc19bc`) not yet implemented.
- Song. *Cross-Context Review: Improving LLM Output Quality by Separating Production and Review Sessions*. [arXiv:2603.12123](https://arxiv.org/abs/2603.12123). Session-separation between production and review as a mitigation for the self-correction blind spot above; motivated routing the two checks' fix directive through `edit_workspace_file` (a narrowly-scoped addition) explicitly rather than an implicit full `write_workspace_file` regeneration from the model's own prior draft.
- Xu, Zhu, Wang, Wang, Athiwaratkun, Wang, Zou, Zhang. *When Does Divide and Conquer Work for Long Context LLM? A Noise Decomposition Framework*. [arXiv:2506.16411](https://arxiv.org/abs/2506.16411). Names three distinct long-context failure modes (cross-chunk dependence, model confusion, and **aggregator noise** — individual facts correct but a merge/synthesis step drops whole clusters); the observed multi-facet-abandonment pattern above is a clean match for aggregator noise specifically, scoping which fix family (hierarchical/divide-and-conquer decomposition) is the natural next escalation if the `edit_workspace_file` directive fix above doesn't move the needle (see ROADMAP.md Pending).
- Khanal, Tao, Zhou. *Beyond pass@1: A Reliability Science Framework for Long-Horizon LLM Agents*. [arXiv:2603.29231](https://arxiv.org/pdf/2603.29231), Northern Kentucky University, 2026-04-01. Formalizes the pass@1-vs-reliability gap this project's own fixes were validated against with n=1 live-run anecdotes until 2026-08-17: pass@k (succeeds at least once across k trials) vs. pass^k (succeeds EVERY time) are different questions, and τ-bench's own measurement (GPT-4o: 61% pass@1 but only 25% pass@8 on retail agent tasks) shows how far apart they can be. One of the paper's three benchmark domains is literally "Agentic Web Research" (multi-step search/fetch/synthesis) — directly on-domain, not just analogous — and its own methodology uses k=3 repeats, the same floor `eval/evaluate.py`'s new `compute_reliability_summary` (pass@k/pass^k reporting over the existing `--runs` flag) is built around.
- Mustahsan, Lim, Anand, Jain, McCann. *Stochasticity in Agentic Evaluations: Quantifying Inconsistency with Intraclass Correlation*. [arXiv:2512.06710](https://arxiv.org/pdf/2512.06710), AAAI 2026. Read in full including all appendices. Proposes ICC (variance decomposed into task-difficulty vs. agent-inconsistency components) as a more rigorous reliability metric than a bare pass@k/pass^k rate. Its most load-bearing finding for this project: on the benchmark task shape closest to DeepDelve's own (GAIA Level 3, "hard open-ended reasoning"), even GPT-5 only reaches ICC=0.629 ("moderate," not "good" reliability) and GPT-4o's ICC=0.304 means 70% of observed variance is trial-to-trial randomness, not task difficulty — "single-run results are essentially unreliable." The paper's own convergence analysis found stable ICC estimates need n≈32 trials for this task shape (vs. n≈8-16 for simpler structured tasks), and its own closest analog to a DeepDelve-shaped agent (o4-mini deep research) only managed n=8 trials for cost reasons, with its own Limitations section stating this isn't enough for generalizable conclusions. Directly informs `eval/evaluate.py`'s new `--runs`/reliability-summary feature: k=3 is a practical floor chosen for this project's own per-run cost (20-70 minutes on local hardware), not a claim that k=3 achieves statistically converged reliability for a task this hard.
- Jwalapuram, Lin, Li, Jiao, Wang, Ming, Ke, Qin, Carenini, Joty. *The Illusion of Multi-Agent Advantage*. [arXiv:2606.13003](https://arxiv.org/abs/2606.13003), Salesforce Research/HKUST/UBC/NTU, 2026. Read in full. Its critique — automated multi-agent frameworks that dynamically decide their OWN coordination structure per query (DyLAN, ADAS, AFlow, etc.) mostly collapse into simple single-agent self-consistency at 10x the cost — does NOT indict DeepDelve's architecture, which uses a fixed, hand-designed pipeline, not per-query dynamic routing. The paper's own contrast case, "Expert-MAS" (deterministic, explicit role decomposition, Python-orchestrated control flow — structurally the closest match to DeepDelve's own shape), is the one architecture in the paper that wins decisively (GPT-5: 57.0% → 96.5% over single-agent). Its real contribution for this project is the audit methodology, not a verdict against decomposition: complexity is "architectural bloat" specifically when added without verified causal contribution (their own causal ablations, e.g. reducing decision-making from consensus to single-agent authority raising success +9.4%) — a standard this project's own completion-check mechanisms (`src/engine/completion.py`) have never been held to, since each was validated only by "did today's specific symptom stop recurring," never by a controlled with/without ablation.
- Su, Wu. *When Do Multi-Agent Systems Outperform? Analysing the Learning Efficiency of Agentic Systems*. [arXiv:2602.08272](https://arxiv.org/abs/2602.08272), University of Hong Kong, 2026-02-10. Read in full (main body, Sections 1-5; theorem proofs in the appendices not read line-by-line). A PAC-learning theoretical analysis of multi-agent RL training, empirically validated on real GSM8K data, not an inference-time deployment study — but its central mechanism generalizes: decomposing into genuinely INDEPENDENT subtasks scales cost down (complexity dominated by the single hardest subtask), while DEPENDENT subtasks introduce error propagation with a quadratic worst-case penalty in agent count, confirmed on GSM8K ("SARL consistently outperforms MARL due to error propagation" in the dependent-subtask setting). Maps precisely onto where DeepDelve's own bugs concentrate: the Planner's per-facet research dispatch is the genuinely-independent case this paper's theory favors, but FindingsWriter/Builder's consolidation stage is the dependent, error-propagating junction — and every bug in today's 7-fix arc (evidence-crowding, marker leaks, the self-correction loop) occurred there, not during independent per-facet research.
- Jiang, Yang, Cui, et al. *Deep Research in Physical Sciences: A Multi-Agent Framework and Comprehensive Benchmark* (DelveAgent / PhySciBench). [arXiv:2606.18648](https://arxiv.org/abs/2606.18648). Primary architecture source (Adaptive Planning Loop, Dual-Granularity Memory, Hierarchical Reflection).
- Huang, Chen, Zhang, et al. *Deep Research Agents: A Systematic Examination and Roadmap*. [arXiv:2506.18096](https://arxiv.org/abs/2506.18096)
- Xu, Peng. *A Comprehensive Survey of Deep Research: Systems, Methodologies, and Applications*. [arXiv:2506.12594](https://arxiv.org/abs/2506.12594)
- Xi, Lin, Xiao, et al. *A Survey of LLM-based Deep Search Agents*. [arXiv:2508.05668](https://arxiv.org/abs/2508.05668)
- *Plan-and-Execute agentic architectures* survey work, e.g. [arXiv:2509.08646](https://arxiv.org/abs/2509.08646). The established pattern the Builder + Build to Review to Fix loop maps onto: decouple planning (decompose, can use a cheaper/more strategic pass) from execution (carries out + retries mechanically), re-planning only on genuine failure rather than every step. Directly informed by the observation that DeepDelve's Planner conversation grows unboundedly across a run with no compaction (a documented "context poisoning" risk) and that the existing `delegate_tasks` mechanism already gives every dispatched sub-agent a genuinely fresh context. The fix is routing report-writing retries through that mechanism instead of the Planner's own conversation.
- Min, Krishna, Lyu, et al. *FActScore: Fine-grained Atomic Evaluation of Factual Precision in Long Form Text Generation*. [arXiv:2305.14251](https://arxiv.org/abs/2305.14251). Decompose-then-verify pattern (break a generation into atomic checkable facts, score each independently) that the grounding layer's line-scoped checks already followed in spirit; cited as prior art motivating the NLI entailment check (`nli_unsupported_problem`) below.
- Anonymous. *HALT-RAG: A Task-Adaptable Framework for Hallucination Detection with Calibrated NLI Ensembles and Abstention*. [arXiv:2509.07475](https://arxiv.org/abs/2509.07475). Source of the "layer NLI entailment on top of lexical/term-overlap checks, don't replace them" design choice for `nli_unsupported_problem`: HALT-RAG's own finding is that combining NLI with lexical signals outperforms either alone, which is why the entailment check only runs on claim lines that already passed the existing term-overlap check rather than gating independently.
- Rasheed, et al. *Claim-Evidence Provenance in Grounded Generation*. arXiv:2602.13855. Claim-evidence provenance framing that, together with FActScore above, motivated `decompose_claim_segments`'s atomic-claim decomposition: splitting a line into per-citation segments so each claim is checked only against its OWN bound citation, closing a gap where two distinct claims sharing one line could pass on a shared generic term.
- Thorne, Vlachos, Christodoulopoulos, Mittal. *FEVER: a large-scale dataset for Fact Extraction and VERification*. NAACL 2018, [fever.ai](https://fever.ai). FEVER-style claim/evidence framing behind `find_cross_source_contradictions`: cross-references a report's claims against ALL of its own fetched sources (not just the one cited), catching a report that silently picks one side of a real disagreement between two of its own sources without flagging the conflict.
- Santos, Peng, Macdonald, Ounis. *Explicit Search Result Diversification through Sub-Queries*. ECIR 2010. Source of the greedy marginal-aspect-coverage reranking pattern behind `tools/web.py::_diversity_rerank`: after the engine's own top result, each subsequent pick is whichever remaining result adds the most new aspect terms, rather than raw engine rank.
- Anthropic. *How we built our multi-agent research system*. [anthropic.com/engineering/multi-agent-research-system](https://www.anthropic.com/engineering/multi-agent-research-system). Independent confirmation that a multi-agent research architecture (lead agent delegating to parallel subagents with their own fresh context) beat a single agent by 90.2% on Anthropic's own internal research eval, validating DeepDelve's existing Planner-Searchers-Analyzers shape and directly informing the Builder + Build to Review to Fix loop's decision to route report-writing retries through a fresh-context sub-agent dispatch rather than the Planner's own growing conversation.
- [`kyuz0/deep-research-agent`](https://github.com/kyuz0/deep-research-agent): base architecture this was forked from.
- [`CYC2002tommy/Deep-Research-Agent`](https://github.com/CYC2002tommy/Deep-Research-Agent): source of the "full-text reading is mandatory" and content-level claim-grounding ideas.
- [`nashsu/llm_wiki`](https://github.com/nashsu/llm_wiki): source of the `findings.md` to `final_report.md` two-pass pattern and the structured run-state idea.
- [`dzhng/deep-research`](https://github.com/dzhng/deep-research): source of the schema-forced FOLLOW-UP DIRECTIONS idea (Searchers must return next-round research leads for the Planner) and the information-density rule for findings (entities, exact metrics, dates). Its structural iterative-deepening loop (learnings-conditioned query generation with geometric narrowing) is a ROADMAP candidate.
- [`Alibaba-NLP/DeepResearch`](https://github.com/Alibaba-NLP/DeepResearch) (Tongyi DeepResearch): source of the heavy search mode (test-time scaling, credited in `tools/web.py`) and the DocumentAnalyzer verbatim-evidence rule (its visit-tool extractor separates verbatim `evidence` from `summary`). Its context-budget endgame and the Tongyi-DeepResearch-30B-A3B model itself are ROADMAP candidates.
- [`imbad0202/academic-research-skills`](https://github.com/imbad0202/academic-research-skills): reviewed for its literature-review paper structure and Anti-Leakage Protocol ("Knowledge Isolation Directive": prefer session materials over parametric memory, flag `[MATERIAL GAP]` instead of fabricating). Both are ROADMAP candidates for the academic output-mode work. Its bibliographic-API citation verification (Semantic Scholar/OpenAlex/Crossref/arXiv) was reviewed but not adopted, see ROADMAP "Rejected".
- [`SkyworkAI/DeepResearchAgent`](https://github.com/SkyworkAI/DeepResearchAgent): reviewed (self-evolution agent runtime: RSPL/SEPL protocol layers, RL-based prompt/solution optimizers, versioned tracing). Not adopted, see ROADMAP "Rejected".
- [`froggeric/Qwen-Fixed-Chat-Templates`](https://huggingface.co/froggeric/Qwen-Fixed-Chat-Templates): community-patched Qwen 3.5/3.6-family chat template fixing an "empty `<think>` block poisons the model into tool-looping" defect in the stock template, plus KV-cache-invalidating whitespace churn across turns. Applied directly (2026-07-28) to an `Ornith-1.0-9B` GGUF's `tokenizer.chat_template` metadata via `gguf_new_metadata.py --chat-template-file` (no tensor rewrite) during a live bake-off — measurably reduced task-splitting/looping regressions. See `RESEARCH.md` §13/§14 for the full before/after evidence.
- [`deepreinforce-ai/Ornith-1`](https://github.com/deepreinforce-ai/Ornith-1) GitHub issues [#4](https://github.com/deepreinforce-ai/Ornith-1/issues/4) and [#16](https://github.com/deepreinforce-ai/Ornith-1/issues/16): the model author's own issue tracker, read directly as primary corroboration that the tool-looping pattern hit during this project's own `Ornith-1.0-9B` bake-off reproduces independently across unrelated harnesses (VSCode/Continue, Claude Code) — confirms it as a real, model-family-wide trait rather than a DeepDelve-specific bug, and names the same root cause (`froggeric`'s template fix, HF discussion #42 on the 35B GGUF) this project independently arrived at the same day.
- [`microsoft/agent-framework`](https://github.com/microsoft/agent-framework)'s own `packages/devui` reference app (`frontend/src/components/features/workflow/execution-timeline.tsx`): source of `src/static/index.html`'s per-agent-run timeline (one collapsible entry per sub-agent dispatch with a running/done status icon, replacing an earlier flat interleaved log) — checked directly against the actual framework DeepDelve is built on, not improvised, after a live UX complaint about the flat log reading as noise once more than one or two sub-agents had run.
- [`assafelovic/gpt-researcher`](https://github.com/assafelovic/gpt-researcher)'s Next.js frontend: two ideas checked directly and adapted, not copied verbatim — `components/research/ResearchContent.tsx`'s `disabled={loading}` pattern (every input locks during a run, not just the submit button) confirmed a live UX bug in DeepDelve's own web UI; `components/ResearchBlocks/elements/SourceCard.tsx` is the source of the report viewer's source-cards grid, with its external Google-favicon-service call deliberately dropped (a per-citation outbound request conflicts with this being an otherwise fully local-first tool) in favor of a plain domain-initial avatar.
