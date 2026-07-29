# Architecture: the completion-check / writer-dispatch system

This document exists because a 2026-07-24 session hit five real bugs in a row, all in the same
subsystem, several caused by the exact same shape of mistake: a piece of per-run state or a
routing decision lives in ONE place conceptually but is actually encoded across several
independent lists/tuples/allowlists that have to be kept in sync by hand, with nothing that
verifies they are. README.md's "Architecture" section describes the AGENT topology (who delegates
to whom); this document describes the ENGINE machinery underneath that decides when a run is done,
who fixes what, and what state survives a resume — the part that actually caused the bugs.

Read this before touching `src/engine/completion.py`, `src/engine/tui.py`'s `run_cli`/`_resume_run`,
or anything in `src/utils/run_state.py`. If you add a new completion-check problem, a new
`run_state.data` key, or a new sub-agent dispatch role, the checklists in each section below tell
you exactly which other places need to change too — read them, don't rediscover them live.

## 1. The completion-check verdict pipeline

**Where**: `src/engine/completion.py`, driven by `run_completion_check()`.

Every attempt, the engine picks AT MOST ONE `Verdict` (problem name + user-facing warning +
model-facing inject text) by scanning two ordered lists, first match wins:

```
COMPLETION_CHECKS   — structural/process checks (delegation happened? findings.md written and
                       fresh? artifact exists? research breadth adequate? plan hygiene?)
        ↓ (only if COMPLETION_CHECKS found nothing)
GROUNDING_CHECKS     — is the artifact's CONTENT actually grounded in real fetched sources?
```

**This ordering is a priority queue, not just a list.** A check near the top can permanently starve
a check near the bottom of ever getting a turn, for the run's ENTIRE retry budget, if it keeps
re-firing on every attempt. This bit twice in one session:
- `check_report_underuses_findings` (in `GROUNDING_CHECKS`, near the bottom, correctness-adjacent
  but ranked below citation-accuracy checks) never fired on 3 separate live runs across two
  sessions — always preempted by something earlier.
- `check_untracked_delegation` (last in `COMPLETION_CHECKS`, explicitly a low-priority hygiene
  nudge per its own docstring) never fired on a live run where `stale_findings`/
  `uneven_task_investment` kept recurring every attempt.

**Fix, 2026-07-24**: `_yield_to_starved_check(verdict, ctx, starved_check)` +
`_consecutive_occurrences(run_state, problem)` (`completion.py`, right before
`run_completion_check`). If the currently-winning problem has fired 2 attempts in a row with no
progress, the deliberately-low-priority check gets one direct extra probe. Only safe to do this for
checks confirmed to be **pure reads** — `check_no_urls` mutates `run_state.data["no_urls_count"]`
as a side effect of being called, so it is NOT a candidate for this pattern without first removing
that mutation. Before adding a new check to this starvation-guard mechanism, confirm it doesn't
write to `run_state.data`.

### Verdict → downstream routing: four tuples that must all agree

A new problem name isn't "done" when its check function returns a `Verdict`. It also needs an entry
in as many of these as apply — **all four live in `completion.py`, none of them derive from the
check list itself, so a missed one fails silently** (the check fires, gets the wrong treatment, and
nothing errors):

| Tuple/list | What it controls | Miss it and... |
|---|---|---|
| `COMPLETION_CHECKS` / `GROUNDING_CHECKS` | whether the check runs at all, and its priority | the check never fires |
| `_QUARANTINE_PROBLEMS` | whether the bad artifact gets renamed aside (`.rejected_attempt_N`) before retry, vs. left in place for the model to "overwrite" | a small model re-conditions on its own wrong prior draft instead of truly restarting |
| `_BUILDER_FIXABLE_PROBLEMS` | whether a fresh-context **Builder** dispatch fixes `final_report.md`, vs. falling back to injecting a nudge into the Planner's own (ever-growing) conversation | the fix works but grows the Planner's context the exact way this whole dispatch system exists to avoid |
| `_FINDINGS_WRITER_FIXABLE_PROBLEMS` | same, for **FindingsWriter**/`findings.md` | same risk, one artifact earlier |

`findings_ungrounded` is the one problem name that additionally gets special-cased inline in
`run_completion_check` (quarantines `findings.md` specifically, not `req_artifact`) — see the
`if problem == "findings_ungrounded": ... elif problem in _QUARANTINE_PROBLEMS:` branch. Any other
problem that needs to quarantine something OTHER than `req_artifact` needs its own special case the
same way; the tuple alone assumes `req_artifact`.

**Checklist for a new completion-check problem:**
1. Write the check function (`Ctx -> Optional[Verdict]`), following an existing one's shape as a
   template — `check_regulation_unsupported` (narrow, GROUNDING_CHECKS) or
   `check_uneven_task_investment` (structural, COMPLETION_CHECKS) are the clearest examples.
2. Add it to `COMPLETION_CHECKS` or `GROUNDING_CHECKS`, in the right priority position (accuracy
   before breadth, correctness before hygiene — read the list's own ordering comments).
3. Decide: does a bad artifact need quarantining? → `_QUARANTINE_PROBLEMS`.
4. Decide: is this Builder-fixable, FindingsWriter-fixable, both, or neither (Planner-only, like
   `not_delegated`)? → the matching tuple, or neither.
5. **Add a row to `test_structural_checks.py`'s verdict matrix** (`matrix = [...]`, search for
   `_row_name, _delegated, _files, _expected, _phrase`) — this is a project rule (see `CLAUDE.md`),
   not optional. It exists specifically because two identical-looking `elif` bugs (bd307f4, run 13)
   silently merged branches before this file existed.

## 2. The writer-dispatch system (FindingsWriter / Builder)

**Where**: `_dispatch_writer_review_fix` in `completion.py`, dispatched from
`run_completion_check`'s `_FINDINGS_WRITER_FIXABLE_PROBLEMS`/`_BUILDER_FIXABLE_PROBLEMS` branches.

Both writer roles run the same Write → Review → Fix loop, in a **fresh context with zero shared
history with the Planner**: dispatch the writer, dispatch a fresh `PeerReviewer` to check the
result, and if flagged, one corrective writer re-dispatch with the review folded in. This exists so
a retry never grows the Planner's own conversation (see README's "Context management").

### FindingsWriter's evidence base is structurally unlike every other dispatch

Every other sub-agent role builds up context turn by turn (search, read, think, repeat).
FindingsWriter's very FIRST message is the **entire evidence base in one shot** —
`_build_findings_source_material(run_state)` (`completion.py`), a single potentially-huge string
assembled from `run_state.data["findings"]`. This shape broke two independent things this session
because code written for the "normal" multi-turn dispatch shape didn't consider it:

- **Session compaction** (`_compaction_strategy_for_role`, `src/engine/orchestrator.py`): the
  generic `agent_framework` compaction strategy evicts whole message groups oldest-first when over
  budget. On a first-turn dispatch there is only ONE non-system group — the giant evidence
  message — so once it crosses the truncation threshold, compaction has nothing else to sacrifice
  and deletes the entire evidence base in one shot. **FindingsWriter is excluded from compaction
  entirely** (`agent_id == "FindingsWriter"` → `compaction_strategy=None`); every other role keeps
  it. Before adding a new dispatch role whose first message is also large and self-contained (not
  built up turn-by-turn), check whether it needs the same exclusion.
- **`writer_gate_ctx`** (`src/tools/core.py`): blocks `read_workspace_file`/`grep_workspace_file`
  until FindingsWriter's first `write_workspace_file` call, because a model given both raw source
  files AND a compiled evidence string tends to abandon the compiled evidence and hand-read files
  instead, producing a far thinner `findings.md`. Armed only for FindingsWriter — Builder's own
  instructions correctly require reading `findings.md` first, so Builder must never be gated by
  this.

`_build_findings_source_material` enforces its own **shared character budget**
(`settings.context_budget_chars`) across `findings_block` + `fetched_block` + the omitted/uncited
notes + fixed boilerplate — all reserved from ONE total before any section is capped, not
independently, after a real regression where each section was budgeted separately and the true sum
still overflowed. Any change to this function's return-value shape needs to re-verify the true
total still fits, not just that each individual piece does.

### Builder/FindingsWriter's tool set is narrower than the Planner's — `verdict.inject` text must respect that

Both writer roles have exactly `read_workspace_file`, `grep_workspace_file`, `write_workspace_file`,
`edit_workspace_file`, `think_tool` — **no `delegate_tasks`, no `web_search`, no
`fetch_url_to_workspace`**. Several `_BUILDER_FIXABLE_PROBLEMS` checks' `verdict.inject` text (the
shared `_redelegate_directive` helper, plus `check_no_urls`/`check_regulation_unsupported`/
`check_stub_source`/others) is worded for the Planner — "delegate a Searcher", "your ONLY next tool
call must be delegate_tasks" — because that text also gets injected into the Planner's own
conversation on the classic (non-writer-dispatch) path. Embedding it verbatim into a Builder
dispatch (`run_completion_check`'s `_BUILDER_FIXABLE_PROBLEMS` branch) hands Builder an instruction
it is structurally incapable of following. Live-confirmed 2026-07-28: a Builder correction cycle
got stuck narrating "I will delegate a Searcher..." across multiple retries instead of ever
rewriting the file, because that is literally what its (wrong-audience) instructions told it to do.
**Fixed** via a single shared `_BUILDER_NO_DELEGATE_CLARIFICATION` string appended after
`verdict.inject` in both Builder-dispatch branches (classic and `force_whole_rebuild`) — telling
Builder plainly that a delegation instruction doesn't apply to it and to drop/rewrite the claim
instead. `FindingsWriter`'s own dispatch branch never had this problem — it was already rewritten
to use per-problem, role-appropriate directives instead of raw `verdict.inject` (see its own
docstring: "Deliberately NOT verdict.inject... would be actively confusing to FindingsWriter").

**Checklist for a new `_BUILDER_FIXABLE_PROBLEMS` (or `_FINDINGS_WRITER_FIXABLE_PROBLEMS`) entry**:
if the check's `inject` text can ever tell the reader to delegate/search/fetch (directly, or via
`_redelegate_directive`), confirm the Builder-dispatch path still makes sense for it — either the
shared clarification covers it, or the problem doesn't belong in that tuple at all.

`edit_workspace_file(filename, old_string, new_string, replace_all=False)` (added 2026-07-28,
`src/tools/fs.py`) exists specifically so a correction cycle doesn't have to be a full-document
regeneration — a genuine capacity difference for smaller models, confirmed live: a "drop 3 flagged
citations, keep everything else" correction repeatedly produced nothing usable via
`write_workspace_file`-only regeneration (whack-a-mole: each full rewrite fixed the previously
flagged citations while introducing different new ones). Has its own quota
(`edit_workspace_file: 10`, both `config_template.yaml` and any live config) — a new tool added to
either writer role's tool list needs the same three things: the tool list in `app.py`, a quota
entry (or it runs unmetered), and a mention in that role's own prompt instructions in
`prompts.py` telling it when to prefer the new tool over the old one.

### The staleness marker: `findings_written_citable_count`

`check_stale_findings` (`completion.py`) compares this `run_state.data` key — the number of real,
distinct, citable findings that existed at the moment `findings.md` was LAST written — against the
current count, and fires when more exist now. It is stamped every time
`_dispatch_writer_review_fix(..., "FindingsWriter", ...)` succeeds. **This is exactly the kind of
key that needs to be added everywhere `run_state.data` gets partially copied** — see §3.

## 3. `RunState.data`: the persisted-state surface, and the carryover-allowlist trap

**Where**: `src/utils/run_state.py`'s `RunState.__init__` (the full key inventory) and
`_run_state.json` (what actually gets written to disk).

`run_state.data` is a flat dict, persisted to `_run_state.json` on essentially every mutation. Most
consumers read the live in-memory object and never think about it again. The trap is **anything
that copies a SUBSET of this dict across a process boundary** — currently, exactly one such copy
exists, but it exists in TWO places that must independently stay in sync:

- `src/engine/tui.py`'s `run_cli` (headless `--resume-run`)
- `src/engine/tui.py`'s `BasicTuiAgent._resume_run` (interactive `/resume-run`)

Both contain a hardcoded tuple of keys copied from the interrupted run's `prior_state` into the new
`RunState`:

```python
for key in ("query", "findings", "fetched_urls", "completion_check_attempts",
            "search_health", "started_at", "plan", "findings_written_citable_count"):
```

**Any new `run_state.data` key that needs to survive a resume must be added to BOTH copies of this
tuple.** This is exactly the bug found and fixed 2026-07-24: `findings_written_citable_count` was
added to `run_state.data` (the staleness marker above) but initially left off both tuples, so every
resume silently reset it, permanently disabling `check_stale_findings` for any resumed run. There
is a unit test pinning both copies now
(`test_structural_checks.py`: the TUI-side scenario asserts the marker survives resume; a
source-inspection assertion separately pins `run_cli`'s own copy, since `run_cli` itself isn't
easily unit-testable in isolation) — but a THIRD key added later still needs a THIRD manual edit in
both places; the test only catches drift in keys it already knows about.

**Checklist for a new `run_state.data` key**: does an interrupted run's value need to survive
`--resume-run`? If yes, add it to both tuples above, and extend the pinning tests.

## 4. The resume system (`--resume-run`)

**Where**: `run_cli`'s resume branch and `BasicTuiAgent._resume_run`/`build_resume_input`, all in
`src/engine/tui.py`.

Deliberate design choices, each with a real live-found failure mode when they combine unexpectedly:

- **`attempt` resets to 0 and the quota pool is rebuilt fresh** on resume (a resumed run gets a
  full new completion-check retry budget and a full new `delegate_tasks`/`web_search`/
  `fetch_url_to_workspace` allowance, NOT the interrupted run's remaining budget). This was a
  deliberate, self-documented tradeoff (a `ponytail:` comment named the exact risk in advance:
  "quotas exist to stop model loops, not to meter cross-run budgets ... revisit if that ever
  proves too generous"). Confirmed live 2026-07-24 that it did: a resumed Planner combined the
  fresh budget with `build_resume_input`'s vague "delegate for the gaps" wording and re-delegated
  the same research angle 40+ times with nothing stopping it.
- **Fix, same session**: `_scale_resume_quota_pool` (`tui.py`, next to `apply_depth_preset`) halves
  the three research-volume quotas on resume (with a floor so a `quick`-depth resume doesn't scale
  to near-zero) — deliberately NOT full cross-process usage-carryover accounting (would need
  persisting per-tool `used` counts, real persistence-layer work this problem doesn't need).
  **Wired into BOTH surfaces** for the same reason as §3's tuple: `run_cli`'s resume branch calls
  it directly; the TUI's `run_agent` builds "a fresh pool per turn, including follow-ups"
  independently and needed its own call, gated on `self._resuming_run` (set by `_resume_run` just
  before dispatch).
- **`build_resume_input`** now tells the resumed Planner explicitly which stage the interrupted run
  reached (found `findings.md`? found `final_report.md` too?) so it doesn't treat "resumed" as
  "start over with a full budget and no context for what already happened."
- **`check_not_delegated` was scoped to the CURRENT PROCESS's own quota usage** (`ctx.delegated`
  checked only `quotas.get("delegate_tasks", {}).get("used", 0) > 0`), which is always 0 at the
  start of a fresh resumed process even though the interrupted run already delegated real
  research. A resumed Planner that correctly wants to stop immediately got forced to delegate at
  least once regardless. **Fixed 2026-07-28** (live-confirmed: a resumed Ornith-1.0-9B run got
  caught in this exact contradiction and spiraled into a `think_tool` reflection loop until it hit
  quota and was force-aborted): `Ctx.delegated`'s construction in `run_completion_check` now also
  treats a non-empty `run_state.data["fetched_urls"]` as proof delegation happened, in ANY session
  — `fetched_urls` is already carried over via this section's own resume-carryover allowlist and
  only a real specialist dispatch ever populates it, so it's ground truth regardless of which
  process actually delegated. Single fix at the one construction site propagates to every reader
  of `ctx.delegated` (`check_not_delegated` and `check_missing_artifact`'s redelegation-forbidding
  message alike) — no other call site needed touching.

**Checklist for anything new that touches "how much work has this run already done"**: does it need
to behave differently on a fresh run vs. a resumed one? Check both `run_cli`'s resume branch and
`BasicTuiAgent._resume_run`/`run_agent` — per this project's own TUI/CLI-parity rule (`CLAUDE.md`),
a fix in one without the other is an incomplete fix, not a smaller one.

## 5. Quick-reference: "I'm adding X, what do I need to check?"

| Adding... | Check these |
|---|---|
| A new completion-check problem | §1's four-tuple checklist + verdict-matrix test row |
| A new `run_state.data` key that should survive `--resume-run` | §3: both resume-carryover tuples (`run_cli`, `_resume_run`) |
| A new sub-agent dispatch role | Does its first message look like FindingsWriter's (one big self-contained blob) or like everyone else's (built up turn-by-turn)? → §2's compaction-exclusion question. Does it need `write_workspace_file` gated behind something else? → `writer_gate_ctx` pattern. |
| A new config key under `settings.*` | `config_template.yaml` (documented default) AND confirm it's read with a safe `.get(..., default)` — this project's convention is "absent in the live `~/.deepdelve/config.yaml` is fine," never require a live-config edit for a new default-on feature |
| Anything that changes behavior based on "how far has this run gotten" | §4: both `run_cli` and the TUI's resume/follow-up paths |
| A new tool result shape or error format | `CLAUDE.md`'s own blast-radius rule: the TUI's `ToolCallWidget` rendering, `log_stream_content`'s persisted event log, `utils/grounding.py`'s citation/error detection |
| A new tool for Builder or FindingsWriter | §2's tool-set checklist: `app.py`'s `SubAgentConfig.tools`, a quota entry (`config_template.yaml` + live config), a mention in that role's `prompts.py` instructions, and — if the tool changes what "delegate" could mean — check every `_BUILDER_FIXABLE_PROBLEMS`/`_FINDINGS_WRITER_FIXABLE_PROBLEMS` check's `inject` text still makes sense for a recipient with this exact tool set |
| A completion check whose `inject` text can tell the reader to delegate/search/fetch | §2: confirm the Builder/FindingsWriter dispatch path (if applicable) doesn't hand a delegation instruction to a role with no `delegate_tasks` tool — see `_BUILDER_NO_DELEGATE_CLARIFICATION` |

This table is not exhaustive by construction — it's the set of landmines this project has actually
stepped on. When you find a new one, add a row here instead of just fixing the instance.

## 6. Serving endpoint: OpenAI-compat vs. Ollama's native API

**Where**: `src/engine/orchestrator.py`'s `_get_default_options()`, `config.cfg["api"]["openai_base_url"]`.

DeepDelve talks to its serving backend exclusively through an OpenAI-compatible client
(`api.openai_base_url`, currently always Ollama's `/v1/chat/completions`). **Confirmed live
2026-07-28** (four direct API tests against the same model/template, holding everything else
constant — see `RESEARCH.md` §14e for the full table): Ollama's native `/api/chat` endpoint
correctly suppresses thinking even with tools present and `think:false`, but the OpenAI-compat
`/v1/chat/completions` endpoint leaks a short reasoning field back in for the exact same request —
tools present is the trigger. This means **every model this project has ever tested through Ollama
has been subject to this specific endpoint-level leak** whenever it makes a tool call with thinking
nominally disabled, distinct from (and narrower than) cases where the underlying model genuinely
cannot suppress thinking at all regardless of endpoint (the Qwen3-4B finding, `RESEARCH.md`
§13/§13a).

**Fixed 2026-07-28**: `api.backend` (`"openai"` default, `"ollama"` new) in both `_build_client` and
`_get_default_options`. The `"ollama"` branch doesn't hand-roll a custom adapter — it reuses
`agent_framework.ollama.OllamaChatClient`, already installed in this project's own dependency tree
as part of the same plugin family `OpenAIChatCompletionClient` comes from (`agent_framework_ollama`,
alongside `agent_framework_anthropic`/`agent_framework_bedrock`/etc., all pre-installed but unused
until now). It's built on the official `ollama` package's `AsyncClient` against `/api/chat`
directly, with a genuine `think: bool` field on its `OllamaChatOptions` — no `chat_template_kwargs`/
`extra_body`/`reasoning_effort` dance needed on this path at all, which is exactly why the leak
described above doesn't happen here. `create_local_agent`'s existing
`client.function_invocation_configuration[...] = True` line (right after `_build_client()` returns)
needed no change — it already operates on the generic client interface both branches satisfy.

Extending to another backend later (Anthropic, Bedrock, a different OpenAI-compatible server with
its own quirks) follows the identical one-branch pattern in both functions — the corresponding
`agent_framework_*` plugin packages are already installed, just unused. Deliberately NOT built
speculatively here — add a branch when a real need shows up, not before.
