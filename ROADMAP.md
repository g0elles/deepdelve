# DeepDelve Roadmap

Status as of 2026-07-10.

## Done

- **3-tier domain-specialized architecture**: `Planner -> {WebSearcher, AcademicSearcher, PeerReviewer} -> {DocumentAnalyzer, DataAnalyzer}`. `PeerReviewer` is a Planner-tier delegate (independent findings.md critique), not part of the Searcher→Analyzer chain.
- **Structural reliability fixes**: per-attempt quota top-up, artifact quarantine-before-nudge, structured run-state (`_run_state.json`, now including populated `findings[]`), a real URL-presence + content-level grounding check (`utils/grounding.py`), and history-scanning salvage for a narrated-but-never-written report (fixes the old single-turn-lookback bug that discarded good content when the *final* retry produced empty text — verified against a real saved session log).
- **Upstream verification**: each Searcher specialist's summary is grounding-checked before it reaches the Planner, not just the final report (`settings.grounding_check.verify_specialist_output`).
- **`replan_action` tool**: the Planner's replanning decision is a structured, checkable call (`add_slot`/`verify_conflict`/`finalize_report`) alongside its `think_tool` reasoning, not free text only.
- **Persona-brainstorming step**: before planning non-trivial queries, the Planner briefly reasons from 2-3 relevant expert perspectives to widen slot coverage.
- **HTML boilerplate stripped on the primary fetch path**: previously only the BeautifulSoup fallback stripped nav/footer/script; the primary markitdown path passed raw chrome straight through.
- **DDGS per-call client** instead of a shared singleton (concurrent specialist searches no longer share one client instance).
- **`extract_structured_data` tool**: real tool-level distinction between `DataAnalyzer` and `DocumentAnalyzer`, not just prompt-driven.
- **Wiki index** (`settings.workspace.wiki_index`): deterministic, engine-maintained cross-run `index.md`, independent of session isolation.
- **Heavy search mode** (`settings.search_mode: heavy`): searches deeper and auto-fetches more top results per call, instead of fabricating fake query-variant strings.
- **Human-in-the-loop gate** (`settings.human_in_the_loop`): reuses the existing `ApprovalWidget`/tool-approval infrastructure to gate the Planner's `write_todos`.
- **MCP tool loader** (`settings.mcp_servers`, `tools/mcp_loader.py`): generic loader for `agent_framework`'s native `MCPStdioTool`/`MCPStreamableHTTPTool`, connected per-task via `AsyncExitStack`. Nothing enabled by default; `config_template.yaml` documents two researched, ready-to-uncomment servers (Semantic Scholar MCP, Brave Search MCP) instead of guessed ones.
- **Readable run-folder names**: `<slugified-query>_<timestamp>` instead of a bare unix timestamp.
- **TUI click-to-copy fix**: tries direct system clipboard (`xclip`/`wl-copy`) before falling back to Textual's OSC52 escape sequence, which silently no-ops in terminals that don't support it.
- **Model choice re-tested** against this project's actual nested `delegate_tasks` schema — see README "Model choice". `mistral-nemo:12b` is the default; `devstral:24b`, `hermes3:8b`, `qwen2.5-coder:14b-instruct`, `llama3-groq-tool-use:8b`, and `mistral:7b-instruct-v0.3-q5_K_M` were all tried and rejected for the Planner role.
- **Live test battery**: 10+ live runs across factual lookups, comparative queries, academic paper + related-work queries, current-event queries outside training data, a TUI session, and a real market-research query — see "Findings from live testing" below for what these surfaced.

## Findings from live testing (not yet acted on / informational)

- **Grounding check verifies provenance, not topical relevance.** A live GOA (Grasshopper Optimization Algorithm) research query got a citation from `globaldrivetozero.org` — actually fetched, and sharing surface terms like "GOA"/"Goa" — that's actually about the Indian state of Goa's EV policy, not the algorithm. The URL-presence + term-overlap check passed it because it only checks "was this fetched" and "do terms overlap," not "is this source about the same subject." Acronym collisions are the clearest way to trigger this; unclear how common the failure mode is outside them.
- **JS-gated pages return bot-challenge stubs, not content.** Several fetches (Cloudflare "Just a moment...", a "Human Verification" page, a Prezi slide deck) came back as 16-18 byte stubs since the fetcher doesn't execute JavaScript. No fallback exists today (e.g. a headless-browser fetch path).
- **Delegation instructions that omit the query's core scope (e.g. a country name) produce off-topic sources**, confirmed live: 2 of 6 sub-tasks in a Colombia market-research run didn't mention "Colombia" in their instructions, and both fetched generic/wrong-country sources (a US rural-health methodology page, a global "Indigenous education" Wikipedia article) as a direct result.
- **A citation being present in a report's "Sources" list doesn't mean it was fetched.** In the same market-research run, 5 of 7 named sources were never actually fetched (recalled from the model's training data) — and when independently fact-checked, all 3 of the specific statistics tied to those unfetched sources were measurably wrong, all in the same direction (understated).

## Planned (not started)

- **Address the grounding check's topical-relevance gap** — some form of "is this source actually about the claimed subject," not just "was it fetched and does it share terms." Unclear whether this needs an LLM judge (this local model class has proven unreliable as its own judge elsewhere in this project) or a cheaper heuristic.
- **Delegation-instruction scope enforcement** — either a prompt-level rule requiring every sub-task instruction to restate the query's core scope, or a structural check that flags/rejects a sub-task instruction missing key entities from the parent query.
- **Headless-browser fetch fallback** for JS-gated pages that return bot-challenge stubs to a plain HTTP GET.
- **Thread-safe session-log refactor** — investigated, not implemented: `--web` mode spawns a subprocess per client (`textual_serve.Server`), so there's no actual cross-user state leak to fix. Only worth doing as hygiene, not a bug fix.

## Stretch

- **RL fine-tuning for tool-call reliability** (GRPO/PPO on the actual Planner/Searcher schema) — targets the fetch-skipping/tool-call-reliability root cause directly instead of catching it after the fact. Needs real training infrastructure; not started.
