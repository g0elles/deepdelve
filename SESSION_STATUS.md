# Session Status — 2026-07-10

Handoff document for picking this project back up in a fresh session. This is the condensed,
actionable summary — `README.md` ("Design rationale") and `ROADMAP.md` (done/open items with
acceptance criteria) have the full detail behind every claim here.

## Where things stand

DeepDelve is a working, tested, 3-tier domain-specialized deep research agent
(`Planner -> {WebSearcher, AcademicSearcher} -> {DocumentAnalyzer, DataAnalyzer}`), rebuilt from
scratch this session to replace an unreliable prototype. It's had ~10 live end-to-end test rounds
(headless runs against real, often genuinely novel queries — a 2026 World Cup result, a July 2026
Rust release, real academic papers) plus a proper eval suite (`eval/dataset.jsonl` +
`eval/evaluate.py`), and 12+ real bugs have been found and fixed this way, not just via code review.
Repo: `https://github.com/g0elles/deepdelve` (private), all work pushed to `main` as of commit
`cd12bba`.

**The big structural wins this session, in order**: real grounding check (URL-presence, then
content-level) replacing a substring check; per-attempt quota top-up so retries aren't
budget-starved; artifact quarantine before nudging; a structural salvage fallback for
narrated-but-never-written reports; and — the largest one — `web_search` auto-fetching full page
content instead of leaving a snippet-only path a model could stop at, which was the single biggest
lever on actual answer quality.

## In progress right now — mid-investigation, not finished

**Task**: root-cause why the narrated-report salvage fallback did NOT fire on the academic eval
item's second quarantine (see `ROADMAP.md`'s "salvage doesn't fire on a *second* quarantine" item).

**What's confirmed so far**:
- The academic eval run (query: `"Find the paper 'Deep Research in Physical Sciences...'..."`)
  ended with `final_report.md.rejected_attempt_2` in the workspace but no live `final_report.md` —
  meaning a `not_grounded` quarantine happened on attempt 2, and whatever happened on the final
  attempt (3) neither produced a real write NOR triggered salvage.
- Salvage (`_salvage_narrated_report` in `src/engine/tui.py`) only fires when the *final* problem
  type is `missing_artifact` specifically (see the `elif problem == "missing_artifact" and
  _salvage_narrated_report(...)` branch in `run_completion_check`) AND the model's last narrated
  turn text is ≥200 characters. If the final attempt's problem was actually still `not_grounded` (not
  `missing_artifact`), or if the model's last turn was short, salvage would correctly not fire — but
  which of these actually happened hasn't been confirmed yet.
- **The session log for this exact run survived and is ready to inspect**:
  `~/.deepdelve/sessions/session_ffe5dbc7-4730-4093-98e3-8d51d4dbb0ac.json` — confirmed via its first
  event's query text matching the academic query. (Its sibling, `session_9dfea286-...json`, is the
  comparative-query run from the same eval batch, not this one.)

**Next step to resume**: parse that JSON's `ui_events`, walk the `function_call`/`function_result`/
`text` events in order, and specifically find: (a) the model's very last narrated `text` block before
the run ended, and its length, (b) whether a `write_workspace_file` call appears after the attempt-2
quarantine at all, (c) cross-reference against `run_completion_check`'s logic to determine the exact
`problem` classification on the final attempt. This was about to be done via a Python one-liner
against that session file when the session ended — no re-run needed, the data already exists.

## Known open items (see ROADMAP.md for full list + acceptance criteria)

Highest-priority open items right now, in roughly descending order of confidence/impact:
1. The salvage investigation above (in progress).
2. New hallucination pattern found but not yet deeply investigated: on a "find related papers" task,
   the `AcademicSearcher` correctly fetched the real primary paper but cited a **Facebook group post**
   as a "related paper" — a category error, not a factual one. Worth a dedicated small test battery
   (multiple different papers) before assuming this generalizes.
3. Blind top-1 auto-fetch occasionally grabs an irrelevant page for a loosely-phrased sub-task query
   (observed once, low-cost, self-capped by quota).
4. An unfamiliar `agent_framework`-level "Maximum consecutive function call errors reached (3)"
   message appeared once after repeated invalid `agent_id` values — not yet deliberately reproduced or
   understood as a framework-level circuit breaker vs. something that could strand a run.
5. `nashsu/llm_wiki`-inspired: constrain the Planner's replanning action space to an enum instead of
   free text (not started).
6. Stretch: RL fine-tuning (GRPO/PPO) on the actual tool schema, targeting tool-call reliability at
   its root instead of working around it with prompt/structural fixes (not started, real training
   infra needed).

## Environment notes for resuming

- venv: `~/.venvs/deepdelve` (NTFS mount means it can't live inside the project dir — see README
  "NTFS gotcha").
- Default model: `deepdelve-mistral-nemo` via Ollama. `OLLAMA_NUM_PARALLEL=1` must stay set
  system-wide (`/etc/systemd/system/ollama.service.d/override.conf`) or per-request context silently
  shrinks — see README.
- A handful of extra Ollama tags exist from this session's model comparison testing
  (`deepdelve-devstral`, `deepdelve-hermes3`, `deepdelve-groq-tool-use`, `deepdelve-qwen-coder`,
  `deepdelve-mistral-7b`) — harmless to leave, useful if re-testing model choice again.
- Test queries that have proven reliably informative this session (real, checkable, outside/at the
  edge of training data): the 2026 World Cup Colombia-elimination query, the "latest Rust version as
  of July 2026" query, and the two eval suite items. Reusing these gives directly comparable
  before/after signal rather than needing to find new failure cases each time.
