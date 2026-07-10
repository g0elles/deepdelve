# DeepDelve

A locally-run, multi-agent deep research assistant built on the **Microsoft Agent Framework** and the **Textual** TUI library, targeting local OpenAI-compatible model servers (defaults to **Ollama**, `http://localhost:11434/v1`).

This is a from-scratch rebuild of an earlier prototype (`../deep-research`), not an incremental patch. That prototype worked end-to-end but was unreliable on anything beyond simple lookups — see "Design rationale" below for the specific bugs found in its retry/grounding logic and what changed here. See [`ROADMAP.md`](ROADMAP.md) for what's done, what's open, and the acceptance criteria for each.

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
10. **The "no-dispatch" failure mode (fixed)**: a comparative eval query showed the Planner writing/rewriting `_todos.md` across every nudge without ever calling `delegate_tasks` — once fabricating narration of delegation that never happened ("After delegating the tasks to a human Searcher, here's what I've found:"). Generic "you must verify" wording didn't stop it. Fixed with an escalating nudge: once `write_todos` has fired ≥2 times with zero `delegate_tasks` calls, the nudge explicitly names and forbids the exact observed pattern instead of only naming the correct action — verified live, the same query that never delegated across 4 attempts now delegates by the 3rd.
11. **Structural salvage for narrated-but-never-written reports (added)**: a second live test of the same comparative query hit a different, recurring pattern — also documented in the *old* project despite multiple rounds of prompt-only fixes there — where the model produces a complete, well-formatted report as chat narration but never calls `write_workspace_file`, even with real delegated results in hand. Rather than tune wording further, added `_salvage_narrated_report()`: when the retry budget is exhausted on a `missing_artifact` problem, the engine auto-persists the model's own last substantial narrated response into the artifact, clearly marked as unverified salvage content that bypassed the grounding check entirely — verified live, the exact query that previously ended with "no report was produced" now produces a real, clearly-disclaimed report instead of nothing.
12. **Fetch-skipping root cause fixed structurally, not by nudging (the big one)**: across 8 live test runs and 5 models, `fetch_url_to_workspace` was reliably never called — the Searcher always stopped at search snippets. Researched how three reference sources solve exactly this before touching code: `llm_wiki`'s search always extracts full content (no snippet-only path exists in its design); `CYC2002tommy`'s SKILL.md has an explicit **"FULL-TEXT READING IS MANDATORY — NO ABSTRACT-ONLY SHORTCUTS"** rule (same principle for papers). Three independent sources converging on "eliminate the snippet-only path, don't just discourage it" was the signal to fix this structurally: `web_search` now auto-fetches the full content of its top result itself (`settings.web_search.auto_fetch_top`, default 1) — `tools/web.py`'s fetch/save logic was refactored so both the tool and the new auto-fetch share one implementation. Verified live: the Rust-version query that previously fabricated a version number from memory now gets real fetched content, and a Searcher on the Colombia/World-Cup query called `fetch_url_to_workspace` 5 times in one run — the first repeated, real fetching observed in the entire test campaign. **Bonus bug found while verifying this**: auto-fetching Rust's own "latest release" URL returned a client-side redirect stub (not a real HTTP redirect, so `httpx`'s redirect-following didn't catch it) with the actual answer sitting unused in the stub's link — fixed by detecting and following exactly this pattern one hop, recording both the original and resolved URL as fetched.
13. **Content-level claim grounding (added)**: the existing grounding check only verified a cited URL was fetched, not that its content actually supports the claim next to it — per `CYC2002tommy`'s SKILL.md claim-grounding phase ("cross-reference the specific claims made in the draft against the raw data collected"), a second, deeper layer now checks this too (`engine/tui.py::_claim_grounding_problem`): for a citation that passed the fetch-presence check, extract salient terms (numbers, versions, proper nouns) from the report's prose and the fetched source, and flag `claim_unsupported` if they share zero overlap — a cheap deterministic check, not another LLM call. **A real bug was found and fixed while testing it**: the first version skipped the check whenever the fetched source had zero extractable terms of its own, meant to protect thin/stub pages from a false flag — but a synthetic test (a fetched page about cooking pasta cited to support a Rust version claim) showed this same logic let a substantial-but-completely-unrelated source through untouched. Fixed by keying the skip on content *length* instead of term count. Verified with 3 synthetic scenarios (unrelated source flagged, genuinely supporting source passes, thin source passes) and 3 live runs with no regressions. Honestly, it hasn't yet fired in real traffic — every live citation problem seen so far was already caught by the cheaper URL-presence check first — so this is a verified-correct defense-in-depth layer, not yet proven against something the first layer wouldn't have caught (see `ROADMAP.md`).

## Resolved: the fetch-skipping limitation

Across 8 live test runs and 5 models, `fetch_url_to_workspace` was reliably never called — the Searcher
always stopped at search snippets, and when the snippet didn't happen to contain the specific fact needed
(confirmed directly in a Rust-version test), the model fell back to confidently fabricating specifics
instead. Prompt tightening alone didn't change this in testing, across every model trialed as Planner.

Fixed structurally (design rationale item 12): `web_search` now auto-fetches the full content of its top
result itself — there is no snippet-only path left for a model to stop at. This was arrived at by checking
how other reference designs solve the same problem rather than iterating on prompt wording further: `llm_wiki`'s
search always extracts full content, and `CYC2002tommy`'s SKILL.md has an explicit, all-caps "FULL-TEXT
READING IS MANDATORY — NO ABSTRACT-ONLY SHORTCUTS" rule for the same underlying problem applied to papers.
Verified live: a Searcher on the Colombia/World-Cup query called `fetch_url_to_workspace` 5 times in a
single run — the first repeated, real fetching observed in the entire test campaign.

**Not fully solved, two smaller follow-ups tracked in `ROADMAP.md`**: blind top-1 auto-fetch occasionally
fetches an irrelevant page when a sub-task's search query is loosely phrased (a real but low-cost
trade-off — observed once, cost one wasted quota unit, didn't block the run), and an unfamiliar
`agent_framework`-level "Maximum consecutive function call errors reached" message appeared once after
several invalid `agent_id` values in a row, not yet deliberately reproduced or understood.

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
> **Four more candidates from an external "16GB VRAM tool-calling" writeup were tested (2026-07-10) — none beat the default, and treating the writeup's claims as verified before testing would have wasted the pulls.**
> Two of the four contradicted our *own prior findings* before even being re-tested: `Hermes-3-Llama-3.1-8B`
> is the exact model already in this table as `hermes3:8b` (0/3 on this schema); `Qwen2.5-Coder-14B-Instruct`
> was already ruled out by the reference project for narrating JSON as text instead of calling tools — retested
> anyway on our schema for completeness, same result:
>
> | Model | Isolated curl test (3 trials) | Live Planner-role trial |
> |---|---|---|
> | `hermes3:8b` (= "Hermes-3-Llama-3.1-8B") | 0-1/3 (re-ran twice, variance between 0/3 and 1/3, still unreliable) | not re-tested live — already disqualified |
> | `qwen2.5-coder:14b-instruct` | **0/3** — narrated JSON as text, confirming the reference project's prior finding on this exact model | not tested live — already disqualified |
> | `llama3-groq-tool-use:8b` | **3/3** | **Failed** — total refusal ("I'm sorry but I do not have enough information to complete this task"), byte-for-byte identical across all 4 attempts, 5.8s total runtime (no real reasoning happened) |
> | `mistral:7b-instruct-v0.3-q5_K_M` | **3/3** | **Failed** — narrated a perfectly-formatted `delegate_tasks(...)` call as a markdown Python code block instead of emitting a real structured tool call, word-for-word identical across all 4 attempts |
>
> Note the exact tag the writeup gave (`mistral:7b-instruct-v0.3`) doesn't exist in Ollama's library —
> tags need an explicit quant suffix (`-q5_K_M` etc.); `ollama pull` fails outright on the bare form.
>
> **All 4 externally-recommended models failed once tested in the actual Planner role, despite half of
> them passing the isolated schema test — reinforcing, not just repeating, the lesson below.**
> `mistral-nemo:12b` remains the only model with a clean track record on both the isolated test and live
> multi-trial Planner-role testing.
>
> **`devstral:24b` is not recommended for the Planner role, based on 3 independent live trials** (not
> just the isolated curl test above, which it passes 3/3). Despite passing that isolated test:
> trial 1 produced *zero* tool calls across all 3 completion-check attempts, repeatedly claiming
> "I don't have the capability to perform web searches" despite `delegate_tasks` being available;
> trial 2 (re-run after the malformed-schema and `children_token` crash fixes landed) worked correctly
> end-to-end and produced a real, if still ungrounded, report; trial 3 delegated correctly and got real
> results back from its WebSearcher sub-agents, but then wrote `final_report.md` containing literal
> `[Placeholder: Insert the result from the WebSearcher task here]` text instead of the actual findings
> — twice, across two nudges — and the run ended with no report ever persisted. 1-of-3 fully functional
> is a worse track record than `mistral-nemo:12b`'s, which failed the same way (missing full source
> verification) but never failed to delegate or synthesize. **Conclusion: passing an isolated tool-call
> test is not sufficient evidence a model will behave reliably in the full multi-agent role — test the
> actual role with multiple independent trials, not a single isolated task.**

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
  Its actual `skills/deep-science-writer/SKILL.md` (read directly, not just the README) has an explicit
  "FULL-TEXT READING IS MANDATORY — NO ABSTRACT-ONLY SHORTCUTS" rule and a content-level claim-grounding
  check ("cross-reference the specific claims made in the draft against the raw data collected") — the
  first is the direct precedent for the `web_search` auto-fetch fix (design rationale item 12); the second
  (verifying a claim's actual content against its source, not just that some URL was fetched) is now
  implemented too, as a cheap deterministic check rather than the LLM-based one CYC2002tommy's pipeline
  uses (design rationale item 13).
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
