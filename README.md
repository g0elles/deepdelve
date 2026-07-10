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
7. **Two more bugs found via a live end-to-end smoke test, not just code review (fixed)**: running an actual headless query (`"Who created the Python programming language?"`) surfaced that the Planner, when nudged about a missing artifact, would re-call `delegate_tasks` on the exact same task again instead of writing the report it already had content for — repeating the same call three times in a row, in direct violation of its own Anti-Looping rule, until the retry budget ran out with nothing written. Fixed by making the nudge explicitly name and forbid the wrong action ("do NOT call delegate_tasks again") rather than only naming the right one. Separately, when the retry budget ran out *while a real problem still existed* (e.g. an ungrounded citation), the engine silently accepted the output with zero indication to the user — fixed by emitting an explicit final disclosure (`run_completion_check`'s `elif problem:` branch) instead of failing silently.
8. **A grounding-check design flaw found via a live test with `devstral:24b` (fixed)**: the original `live_http_verify` fallback treated "not fetched this run, but the URL resolves live" as a soft pass — meaning a model could cite a real, well-known URL (e.g. Wikipedia's general article on a topic) it never actually visited, and the check would wave it through, since any plausible real-domain guess resolves live. This defeated the entire point of the check for exactly its most important case (a live test showed it passing three consecutive fabricated citations — `gauthmath.com`, `omnicalculator.com`, etc.). Fixed: a URL not in the actually-fetched set is now *always* a grounding problem; `live_http_verify` only enriches the diagnostic message with dead-link info, it never bypasses the check. (Cache-hit reuse — a legitimate case where cited URLs weren't fetched *this* run — is still handled correctly, by pre-registering the cached answer's own URLs as verified.)
9. **Two more bugs found testing a genuinely novel query** (`"who eliminated Colombia from the 2026 World Cup"` — a real, current event outside any model's training data): first, a malformed `delegate_tasks` call (`{"due": 1, "task": "..."}` instead of the real schema) was silently accepted with `task_name` defaulting to `"Unknown_Task"` and empty instructions — the sub-agent then searched for the literal string `"Unknown_Task"` and got random unrelated results, which is what actually explains most of the ungrounded-report failures seen in earlier tests, not a fetch-skipping habit per se. Fixed: `delegate_tasks` now validates every task's shape before dispatching anything and rejects the whole malformed batch with a clear, actionable error instead of silently degrading — confirmed working: the model saw the rejection and retried with the correct shape. Second, once the model retried with a fictional `agent_id` ("AI-3") that didn't match a real specialist, the clean "sub-agent does not exist" error path crashed instead with `UnboundLocalError: children_token` — a latent bug inherited from the reference project, never previously observed because a bad `agent_id` apparently never came up in its own testing. Fixed by initializing the token before the early-return path.

## Known limitation (found via live testing, not yet solved)

Even after tightening the WebSearcher/AcademicSearcher prompts to explicitly forbid returning a summary without first calling `fetch_url_to_workspace`, and after fixing the malformed-`delegate_tasks`-silently-degrading bug above (which explained *some*, but not all, of the ungrounded reports seen in earlier tests), a live test with a genuinely novel query — `"who eliminated Colombia from the 2026 FIFA World Cup"`, a real event outside any model's training data — still showed the pattern: `mistral-nemo:12b` correctly extracted the right headline fact (Switzerland) from live `web_search` snippets returned by a real, current DuckDuckGo query (confirming the web-search pipeline itself genuinely reaches current information beyond training cutoff), but never actually called `fetch_url_to_workspace` to fetch and verify a full source, so a detailed follow-up claim (Colombia's squad roster) came from the model's own stale training knowledge and was fabricated. The grounding check catches this correctly every time it's been observed (source: `_run_state.json`'s `fetched_urls` staying empty despite cited sources) — it quarantines the artifact, forces a correction attempt, and if the retry budget runs out before it's resolved, now clearly discloses the report as unverified rather than presenting it as trustworthy. But it does not *prevent* the behavior, only catch and disclose it. This looks like a genuine instruction-following limit of this model class rather than a prompt-wording problem — further prompt iteration didn't change it in testing, and it persisted across `mistral-nemo:12b` and (in a separate test) `devstral:24b`, which additionally failed outright to use its tools at all in the Planner role in one trial despite passing an isolated tool-call reliability test — see "Model choice" below. The two real next steps, in order of effort: try `devstral:24b` more extensively now that the malformed-schema and crash bugs are fixed (both could have been masking or distorting earlier devstral results); or the RL fine-tuning direction noted in the original plan doc, which targets this exact class of problem at its root instead of working around it.

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

> **Model choice — do not assume the old ranking holds, and it doesn't.** The domain-specialized
> prompts here have a different tool-call shape than the prototype's flat 3-tool schema — specifically,
> `delegate_tasks(tasks: [{task_name, instructions, agent_id: enum}])` is a nested array of objects with
> a constrained `agent_id` field, not a flat argument list. Re-tested head-to-head (3 trials each,
> `AcademicSearcher`-style delegation task) on 2026-07-10:
>
> | Model | Real, correctly-shaped `tool_calls`? |
> |---|---|
> | `mistral-nemo:12b` | **3/3** — unchanged from the old ranking |
> | `devstral:24b` | **3/3** — unchanged from the old ranking |
> | `hermes3:8b` | **0/3** — regressed hard. Was the *most* reliable tool caller on the old project's flat schema (even parallel calls); on this schema it either skipped tool calls entirely (narrated as text) or emitted a malformed, double-nested arguments object. Disqualified for this role on this schema, despite the old README's recommendation. |
>
> `mistral-nemo:12b` remains the default (`deepdelve-mistral-nemo` in `config_template.yaml`) — same
> pick as before, but now verified against the actual schema this project uses, not inherited from the
> old project's test.
>
> **Caveat on `devstral:24b`, found via live Planner-role testing (not just the isolated curl test
> above)**: despite passing the isolated 3/3 tool-call test, one live end-to-end trial as the actual
> Planner (full system prompt, not a single isolated task) produced *zero* tool calls across all 3
> completion-check attempts — it repeatedly claimed "I don't have the capability to perform web
> searches or access specific tools" despite `delegate_tasks` being present in its tool list. A second,
> independent trial on a different query worked correctly. This inconsistency was tested *before* the
> malformed-`delegate_tasks`-schema and `children_token` crash fixes above landed, so it should be
> re-tested now that those are fixed rather than treated as settled — but until re-confirmed, don't
> assume the isolated curl test is sufficient evidence a model will behave reliably in the full
> multi-agent role; test the actual role, not just raw tool-call capability.

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

## References

What was actually used, and how — distinguishing paper-backed claims from our own adaptations (see
"Design rationale" for the specific mapping):

**Primary architecture source**
- Jiang, Yang, Cui, et al. *Deep Research in Physical Sciences: A Multi-Agent Framework and
  Comprehensive Benchmark* (DelveAgent / PhySciBench). [arXiv:2606.18648](https://arxiv.org/abs/2606.18648).
  Source of the Adaptive Planning Loop, Dual-Granularity Memory, and Hierarchical Reflection concepts,
  and the real evidence that a Planner-dispatches-to-named-specialists structure is a validated
  pattern (verified by extracting the paper's actual PDF text, not an automated summary — see below).

**Cross-validation surveys** (pulled specifically to check DelveAgent's ideas weren't just one paper's
idiosyncratic design, since it's a single domain-specific study):
- Huang, Chen, Zhang, et al. *Deep Research Agents: A Systematic Examination and Roadmap*. [arXiv:2506.18096](https://arxiv.org/abs/2506.18096).
- Xu, Peng. *A Comprehensive Survey of Deep Research: Systems, Methodologies, and Applications* (reviews 80+ implementations). [arXiv:2506.12594](https://arxiv.org/abs/2506.12594).
- Xi, Lin, Xiao, et al. *A Survey of LLM-based Deep Search Agents: Paradigm, Optimization, Evaluation, and Challenges*. [arXiv:2508.05668](https://arxiv.org/abs/2508.05668).

**Reference implementations**
- [`kyuz0/deep-research-agent`](https://github.com/kyuz0/deep-research-agent) — the base architecture
  (Orchestrator/Searcher/Analyzer chain, Microsoft Agent Framework + Textual) that the prior prototype
  (`../deep-research`) was built from. Confirmed via its commit history to be a small, lightly-tested
  demo scaffold (3 commits, no completion-check or memory system) rather than a proven reference —
  which is why this rebuild treats its tier structure as a starting point to fix, not a solved problem.
- [`CYC2002tommy/Deep-Research-Agent`](https://github.com/CYC2002tommy/Deep-Research-Agent) — not a
  local-model agent (a Claude-Code skill requiring Scopus/NotebookLM MCP servers), but its live-HTTP
  source-verification idea ("Zero-Hallucination" phase) is echoed in `settings.grounding_check.live_http_verify`.
- [`nashsu/llm_wiki`](https://github.com/nashsu/llm_wiki) — a mature desktop app (Rust/Tauri, not
  portable code here), source of the two-step "analyze, then generate" pattern behind the Planner's
  `findings.md` → `final_report.md` split, and the source-traceability idea behind the structured
  run-state (`utils/run_state.py`).

**Considered and set aside**
- Three arXiv IDs originally supplied (2607.08027, 2607.07984, 2607.08740) turned out on inspection to
  be about LLM structured pruning, agentic neural architecture search, and a "workflow-as-knowledge"
  persistence framework, respectively — not deep research agents. The first was left out entirely; the
  other two's *transferable ideas* (bounded/"slotted" planning from the NAS paper; persisting run state
  as inspectable, resumable objects from the workflow-as-knowledge paper) were still adopted where they
  fit, and are called out explicitly wherever they show up in the code (`prompts.py`'s bounded-slot
  planning rule, `utils/run_state.py`).
