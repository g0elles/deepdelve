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

**This ordering is a priority queue, not just a list (Chain of Responsibility) — a check near the
top can permanently starve a check near the bottom of ever getting a turn, for the run's ENTIRE
retry budget, if it keeps re-firing on every attempt.** A 2026-07-24 through 2026-07-31 incident
chain found EIGHT real instances of this one bug class before it got a structural fix instead of a
per-instance patch — see below for the invariant and the mechanism that now enforces it.

### The invariant

**Every check in `COMPLETION_CHECKS`/`GROUNDING_CHECKS` falls into exactly one of three buckets:**

1. **Self-resolving**: its problem name is in `_BUILDER_FIXABLE_PROBLEMS` or
   `_FINDINGS_WRITER_FIXABLE_PROBLEMS`. Every time it wins, the Write→Review→Fix loop dispatches a
   real fresh-context fix, so it converges (or the underlying content genuinely changes) rather than
   looping on identical state — safe to re-fire indefinitely.
2. **Self-clearing**: its own firing condition permanently stops being true once satisfied and
   can't recur on unrelated grounds (e.g. `check_not_delegated` — clears the instant
   `ctx.delegated` flips true, `check_untracked_delegation` — its own explicit "at most once ever"
   gate, stricter than the general cap below).
3. **Everything else**: NOT self-resolving. It only nudges the Planner's own conversation (the
   classic inject path), and its underlying condition CAN legitimately stay true indefinitely (a
   genuinely unfindable source, an unfillable per-task gap). **This bucket must cap its own firing
   via the shared `_capped` helper** (`completion.py`) — without it, the check wins first-match on
   EVERY attempt for as long as its condition holds, permanently starving every check below it.

### The enforcement mechanism (not ad-hoc — one shared implementation, one standing test)

- **`_consecutive_occurrences(run_state, problem, skip_problems=frozenset())`** — the ONE canonical
  definition of "how many times in a row has this problem fired," with an optional skip-list for
  interrupting problems that are themselves a direct symptom of THIS problem's own directive (e.g.
  `untracked_delegation` firing because the model renamed a flagged task instead of retrying it
  under the same name — counting that as a genuine interruption would trap a check in its weakest
  wording forever). Used by `_capped`, `_apply_starvation_yield`, and `run_completion_check`'s own
  `force_whole_rebuild` escalation — all four share one definition now, not four drifting copies.
- **`CONSECUTIVE_SAME_PROBLEM_ESCALATION_THRESHOLD`** (module-level constant, currently 3) — the
  ONE number every cap and escalation references. This used to be a local inside
  `run_completion_check` while `_capped`-equivalent logic used its own separate number; they
  disagreed once already before being unified (a real bug caught mid-session by the test suite,
  not found live).
- **`_capped(ctx, problem, verdict, skip_problems=frozenset())`** — bucket 3's own required call.
  Once `problem` has fired `CONSECUTIVE_SAME_PROBLEM_ESCALATION_THRESHOLD` times in a row, returns
  `None` instead of `verdict`, letting `COMPLETION_CHECKS`/`GROUNDING_CHECKS`' own first-match
  ordering fall through to whatever's next in the list — no rewiring needed elsewhere, the list
  order already does the right thing once the winning check goes quiet.
- **`_yield_to_starved_check(verdict, ctx, starved_check, never_final_blocker=False,
  tier_problems=None)`** — a DIFFERENT shape from `_capped`: protects one specific LOW-PRIORITY
  hygiene check (`check_untracked_delegation`) regardless of WHICH problem is currently winning,
  not tied to one specific problem name. Still hand-called with the specific starved check passed
  directly (never wrapped in `or`).
  **`tier_problems` landmine (2026-08-16, found live)**: the ORIGINAL version of this function's
  starvation window was keyed on `_consecutive_occurrences(ctx.run_state, verdict.problem)` — the
  SAME problem repeating. That misses the case where `COMPLETION_CHECKS` as a whole TIER keeps
  winning but the SPECIFIC problem changes every attempt (`missing_findings -> missing_artifact ->
  uneven_task_investment -> task_verification_flagged`, never repeating) — `GROUNDING_CHECKS` is
  just as starved either way (the hard two-tier gate below only cares whether `COMPLETION_CHECKS`
  returned non-None AT ALL, not which check). Confirmed live: `report_underuses_evidence` never got
  a single turn across an entire run despite `final_report.md` having dropped 3 of 4 requested
  facets. Fixed by adding `_COMPLETION_TIER_PROBLEMS` (the full set of `COMPLETION_CHECKS` problem
  names) + `_consecutive_tier_wins` (membership-based counting, sibling to `_consecutive_
  occurrences`'s equality-based counting) — the `check_report_underuses_evidence` call site now
  passes `tier_problems=_COMPLETION_TIER_PROBLEMS`, so the guard fires on EITHER the same problem
  repeating OR the whole tier winning consecutively. **A new `COMPLETION_CHECKS` entry needs its
  problem name added to `_COMPLETION_TIER_PROBLEMS` too** — see checklist item 6 below.
- **`_STARVATION_YIELD_TARGETS` + `_apply_starvation_yield(verdict, ctx)`** — for a SPECIFIC problem
  that should yield to a SPECIFIC sibling (`report_underuses_findings` → `report_underuses_
  evidence`), a declarative `{problem_name: target_check}` dict, always probed directly. Replaces a
  hand-written `lambda c: A(c) or B(c)` pattern that was live-confirmed dead code — `A` (the check
  already winning) short-circuited before `B` (the actually-starved sibling) ever ran, since `or`
  tries its first operand first and `A`'s condition was almost always still true. A future
  sibling-yield pair is a one-line dict entry now, not a lambda that can get the order backwards.
- **`_salvage_narrated_report`'s call site in `run_completion_check`'s final branch** — used to be
  gated on a hardcoded problem-name tuple, widened three separate times as new terminal problems
  were found unwritten-but-narrated. Now unconditional (the function's own 200-char-minimum gate
  is the real safety check) — a new problem type that can legitimately end a run with `req_artifact`
  unwritten no longer needs anyone to remember to add it to a list.
- **Standing audit test** (`test_structural_checks.py`, right before the final "All structural-check
  assertions passed" line): for every entry in `COMPLETION_CHECKS + GROUNDING_CHECKS`, asserts it's
  either self-resolving (by problem-name tuple membership), on the small explicit self-clearing
  allowlist, or calls `_capped` in its own source. **This is the actual payoff** — two of the eight
  incidents (`check_propagated_ungrounded_content`, `check_report_underuses_evidence`) were found
  by this exact audit before ever causing a live failure, not after. A future check that skips
  `_capped` fails the suite immediately instead of being found five sessions from now at 2am.

Prior art this design is grounded in (not a novel invention for this codebase): OS scheduler
**aging** (a starvation counter that forces a lower-priority item to get a turn once it crosses a
threshold); the **circuit breaker pattern** (Nygard, *Release It!* — stop retrying a failing path
after N attempts, fail over instead of hammering it forever); **Chain of Responsibility** (GoF —
`COMPLETION_CHECKS`/`GROUNDING_CHECKS` already are this pattern, the missing piece was a uniform
"can't handle it after N tries, pass to the next handler" rule); and a 2026-07-14 industry writeup
applying the same retry/circuit-breaker/fallback-chain shape specifically to production LLM agent
systems, confirming this is a known-missing layer, not a codebase-specific quirk.

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

**A problem can also skip the shared tuples entirely and get a bespoke `elif` branch** in
`run_completion_check`'s dispatch chain instead, when a single generic-rebuild dispatch isn't the
right shape for it. Two examples: `thin_coverage` (`_dispatch_deepening_round` — dispatches real
follow-up tasks directly, bypassing the Planner) and `report_underuses_evidence`
(`_dispatch_per_facet_builder_fix` — one Builder Write→Review→Fix cycle *per dropped facet*,
sequential, each scoped to only that facet's real URLs, instead of one Builder turn asked to fix
every neglected facet at once). The latter exists specifically because the naive version —
routing `report_underuses_evidence` through `_BUILDER_FIXABLE_PROBLEMS`' single combined-
instruction dispatch — got a clean **negative** live result (commit `1092add`, after `67e4b00`):
asking Builder to restore multiple neglected facets in one turn reproduced the exact
evidence-crowding pattern the check exists to catch in the first draft, dropping a report's
coverage of the harder facet from ~1/3 to 0%. If you're hunting for `report_underuses_evidence` in
`_BUILDER_FIXABLE_PROBLEMS` and not finding it, this is why — it's deliberately absent from all
four tuples, dispatched by its own `elif` instead.

**Checklist for a new completion-check problem:**
1. Write the check function (`Ctx -> Optional[Verdict]`), following an existing one's shape as a
   template — `check_regulation_unsupported` (narrow, GROUNDING_CHECKS) or
   `check_uneven_task_investment` (structural, COMPLETION_CHECKS) are the clearest examples.
2. Add it to `COMPLETION_CHECKS` or `GROUNDING_CHECKS`, in the right priority position (accuracy
   before breadth, correctness before hygiene — read the list's own ordering comments).
3. Decide: does a bad artifact need quarantining? → `_QUARANTINE_PROBLEMS`.
4. Decide: is this Builder-fixable, FindingsWriter-fixable, both, or neither (Planner-only, like
   `not_delegated`)? → the matching tuple. If the fix needs per-item/per-facet handling a single
   generic dispatch can't express, write a bespoke `elif` branch instead (see `thin_coverage` /
   `report_underuses_evidence` above) — but still make the "why not the shared tuple" reasoning
   explicit in a comment, the same way those two do, so a future reader doesn't read the absence
   as an oversight.
5. **Add a row to `test_structural_checks.py`'s verdict matrix** (`matrix = [...]`, search for
   `_row_name, _delegated, _files, _expected, _phrase`) — this is a project rule (see `CLAUDE.md`),
   not optional. It exists specifically because two identical-looking `elif` bugs (bd307f4, run 13)
   silently merged branches before this file existed. A new bespoke dispatch `elif` (not just a new
   problem name) also needs its own dedicated scenario exercising the dispatch path itself, the way
   `_per_facet_builder_dispatch_scenario` does for `report_underuses_evidence` — the verdict matrix
   alone only proves the check fires with the right problem name, not that the dispatch branch
   handles it correctly.
6. **If it's a new `COMPLETION_CHECKS` entry** (not `GROUNDING_CHECKS`), add its problem name to
   `_COMPLETION_TIER_PROBLEMS` too (2026-08-16) — this is what lets `_yield_to_starved_check`'s
   `tier_problems` param detect "the whole `COMPLETION_CHECKS` tier keeps winning" even when the
   SPECIFIC problem changes every attempt; miss this and a real, permanent `GROUNDING_CHECKS`
   starvation (see this section's own `tier_problems` landmine writeup above) can recur without the
   standing audit test catching it, since that test only checks `_capped` usage, not tier-set
   membership.

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
  until FindingsWriter's first `write_workspace_file` (or `edit_workspace_file` — see below) call,
  because a model given both raw source files AND a compiled evidence string tends to abandon the
  compiled evidence and hand-read files instead, producing a far thinner `findings.md`. Armed only
  for FindingsWriter — Builder's own instructions correctly require reading `findings.md` first, so
  Builder must never be gated by this. **It is armed for EVERY FindingsWriter dispatch**, not just
  the original from-scratch write — including `_dispatch_per_facet_findings_writer_fix`'s
  (`completion.py`; same idea as §1's `report_underuses_evidence`/`_dispatch_per_facet_builder_fix`
  above, one layer earlier — a per-facet ADD-ONLY fix for `findings.md` instead of `final_report.md`)
  corrective passes, whose instructions explicitly say "use `edit_workspace_file` ... do not
  rewrite or touch any other part of the file."
  **Landmine, found live 2026-08-16**: `check_writer_gate` originally only accepted
  `write_workspace_file` as satisfying the gate. A per-facet dispatch trying to read `findings.md`
  first (legitimate — it needs to find an edit anchor for `edit_workspace_file`) got blocked, and
  the block's own hardcoded wording ("call `write_workspace_file` now") actively steered the model
  toward a full-file overwrite that silently destroyed facets a PRIOR per-facet round had already
  correctly added — confirmed live, this exact contradiction fired 20 times in one 45-minute run
  that never converged on a stable `findings.md`. Fixed: `edit_workspace_file` now also satisfies
  the gate. Any FUTURE tool added to FindingsWriter's toolset that can legitimately be its first
  real action (writing/editing the artifact) needs the same treatment in `check_writer_gate`, or
  this exact trap recurs for that tool instead.

**A single `add_finding` call's shared synthesis text creates two non-obvious grounding-check
false positives, both found live 2026-08-17, both from the same root cause**: `_run_single_task`
(`orchestrator.py`) attaches the SAME task-level synthesis text (and the SAME verification-warning
string) to EVERY URL fetched in one turn — one `add_finding` call per URL, see
`_collapse_multi_url_task_findings`'s own docstring above for why this shape exists at all.

- **A `[SYSTEM VERIFICATION WARNING: stub_source:/unverified_urls:/claim_unsupported:...]` marker
  about ONE co-fetched URL used to wholesale-exclude the shared record for ALL of them.** A real
  Mexico City rent synthesis mentioned a stub page AND a genuinely real price (`MX$17,300/month`)
  found on a different page in the SAME text; the resulting warning named only the stub URL, but
  `_is_citable_finding`'s prior wholesale "any VERIFICATION marker → exclude" rule threw the real
  price away too, for all 3 co-fetched URLs including a third never even mentioned in the warning —
  the single largest content-loss mechanism found this session. **Fixed**:
  `_verification_warning_targets_url` (`completion.py`) scopes the exclusion to the finding's OWN
  `source_url` when the marker names specific bad URL(s) (`unverified_urls:`/`stub_source:`/
  `claim_unsupported:` — the only labels that reliably carry `real_grounding_problem`'s own flagged
  URLs), falling back to wholesale exclusion for quote/regulation-identified shapes and the
  Analyzer-tier reconstructed-URL message (which deliberately ALSO names the CORRECT reference URL
  right next to the bad one, and must not have that safe URL swept up by a broader match).
  **A `dedup_key`/URL-scoping fix on a shared-text finding needs the same "which specific co-cited
  URL does this actually apply to" question asked before excluding/including anything.**
- **Side effect of the fix above, found in the very next live run**: once a finding correctly STAYS
  citable despite still carrying a warning about a DIFFERENT co-cited URL, the raw marker TEXT was
  still being rendered verbatim into `_build_findings_source_material`'s block — and a downstream
  consumer (the deterministic fallback below) copied it straight into `findings.md`. A
  findings.md-level grounding check then scanned `findings.md`'s OWN rendered text, found the
  warning's own named "bad" URL sitting there as if it were a real citation, and flagged
  `findings_ungrounded` on content that was otherwise entirely real — reproducing IDENTICALLY on
  every retry (confirmed via 3 byte-identical `findings.md.rejected_attempt_N` snapshots) since the
  underlying research data never changed. **Fixed**: `_build_findings_source_material` now strips
  the marker text (same `_VERIFICATION_WARNING_BLOCK_RE`) from a citable finding's summary before
  rendering it — the marker was only ever meant to inform THIS project's own citability decision,
  never to be copied into a human-facing artifact. **Any text that exists purely to inform an
  internal decision (a marker, a flag, an annotation) must be stripped before it reaches a
  rendered-for-humans artifact, even once the content it's attached to is legitimately citable** —
  "citable" and "safe to render verbatim" are not the same guarantee.
- **A specialist's `FOLLOW-UP DIRECTIONS:` section (suggested next-URLs-to-check, engine-driven
  iterative deepening) is NOT a citation for any claim, but `real_grounding_problem`/
  `extract_cited_urls` couldn't tell the difference** — a URL named only as a suggested lead (never
  fetched, by design) fired a whole-summary `SYSTEM VERIFICATION WARNING`, invalidating genuinely-
  cited real content sitting right next to it in the same summary. **Fixed**: a new
  `_strip_follow_up_directions` helper (`orchestrator.py`, next to `_extract_follow_up_directions`)
  strips that section before the text reaches any grounding check, applied at all 3
  `real_grounding_problem` call sites — `final_text` itself stays untouched, so the deepening
  feature still sees the real bullets afterward. **Any FUTURE section of a specialist's summary
  with a documented, non-citation purpose (a caveat, a confidence note, a suggestion) needs the
  same "strip before grounding-check, extract separately for its real purpose" treatment** — a
  grounding check that scans raw text has no way to know a URL's role in that text is unrelated to
  the claims around it.
- **A writer-role dispatch blocked by `writer_gate_ctx` can just STOP instead of retrying with the
  correct tool** — the SAME "sub-agent ends its own turn immediately after a tool call, with ZERO
  trailing text and no marker at all" mechanism this project already tracks for Searcher/Analyzer
  turns (see `RunState.coverage()`'s own docstring), confirmed 2026-08-17 to also hit a WRITER
  role, where the consequence is worse — nothing gets written at all, not just one empty finding.
  **FIXED 2026-08-18**: the one-shot immediate-retry safety net (`_dispatch_writer_review_fix`)
  wasn't enough — a real live run showed the retry itself ALSO returning nothing usable, on 3 of 3
  completion-check rounds in a row. The single hardcoded retry is now a bounded loop
  (`_WRITER_EMPTY_RETRY_ATTEMPTS = 2`, `src/engine/completion.py`), each attempt reusing the same
  externally-reframed instructions (unchanged from the original fix — no live evidence a 2nd retry
  needs different wording from the 1st), falling through to the deterministic-fallback salvage or
  a raise only once every retry is exhausted. **Not yet live re-tested** — designed against the
  run6 transcript shape and covered by `test_structural_checks.py`, not yet exercised in a fresh
  live run with the new retry count.
- **A `findings_ungrounded` rebuild directive never named the SPECIFIC bad URL, only that
  "something" was ungrounded** — confirmed live 2026-08-18 (a `disable_no_progress_guard` ablation
  run): `findings.md.rejected_attempt_3` and `_4` were byte-identical, 11 minutes apart —
  FindingsWriter kept re-citing the exact same hallucinated URL
  (`https://www.mexicoembassy.org.uk/visas`, confirmed never actually fetched this run, via
  `RunState.data["findings"]`) because the retry directive gave it no signal about WHICH source
  had failed verification, just the same generic "rebuild it, don't fabricate" text plus the same
  source material every time. This is the same class of bug session_status/2026-08-17.md's item 2
  flagged and left uninvestigated ("why FindingsWriter keeps RE-CITING the same already-rejected
  hallucinated URL across rebuild attempts"). **FIXED 2026-08-18**: the `findings_ungrounded`
  write_directive in `_dispatch_writer_review_fix`'s caller (`run_completion_check`,
  `src/engine/completion.py`) now regexes the specific bad URL(s) out of
  `verdict.warning`'s `unverified_entry_sources:...` detail and names them explicitly, with a
  direct "do not attribute any finding to these again" instruction. **Not yet live re-tested** —
  unit-tested (`test_structural_checks.py`, scenario b2 in the `run_completion_check` dispatch
  suite) against a synthetic partially_ungrounded fixture, not exercised in a fresh live run yet.
- **The Searcher/Analyzer instance of "zero trailing text" — the ORIGINAL, longest-tracked case of
  this mechanism (both writer-role incidents above are its siblings) — was only ever DETECTED
  downstream, never actually fixed at the source, until 2026-08-19.** `RunState.coverage()`'s own
  empty-summary exclusion (`_is_null_finding_summary`) correctly stops an unsynthesized finding
  from counting as real coverage, but does nothing to RECOVER it — the real fetched source is just
  silently discarded. Confirmed live via the 2026-08-18 ablation study's own workspace data across
  MULTIPLE independent runs: one task (`digital_nomad_visa_mexico`) had 5/5 real fetched sources
  land with a completely empty finding summary in one run, and similar (60-100%) empty-summary
  rates on several other tasks in the same and other runs — this is not a rare edge case, it is a
  major, silent tax on real research capacity that was previously invisible because the downstream
  exclusion made it look like "no sources found" rather than "sources found, never synthesized."
  This is very likely a bigger lever on overall run quality than any single completion-check guard
  the coordination-layer ablation study measures, since it operates upstream of all of them.
  **FIXED 2026-08-19**: `_run_single_task`'s stream loop (`src/engine/orchestrator.py`) now nudges
  once, mirroring the writer-role retry's shape — when a dispatch called at least one tool
  (`any_tool_call`) but ends its turn with zero trailing text, and nothing else already claimed
  another turn that iteration, one extra turn is granted with `SEARCHER_ANALYZER_SYNTHESIS_NUDGE`
  ("write up your findings NOW... do not call the same tool again first"), gated to research-tier
  roles only (`_NON_RESEARCH_DISPATCH_ROLES` — Builder/FindingsWriter/PeerReviewer have their own
  mechanism). The trigger condition is pulled into `_should_nudge_zero_synthesis`, a pure
  predicate, the same way `_select_budget_nudge` already is — `_run_single_task` itself is a
  nested closure inside `create_local_agent` (ROADMAP's own tracked "god-function" issue) and
  cannot be unit-tested directly, so this is the only piece of the fix that gets a real test.
  **Not yet live re-tested** — no full-stream-loop integration test exists for `_run_single_task`
  at all (a real gap, same root cause as its untestability above); only the extracted predicate is
  covered.

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

### The per-task verification ledger (`task_verification`) and its supersede heuristic — both fuzzy, both fully recomputed every attempt

**Where**: `_update_task_verification` (`completion.py`) writes `run_state.data["task_verification"]`
— one entry per top-level dispatched task, `status` one of `verified` / `flagged` / `superseded` —
fresh, EVERY completion-check attempt, from `run_state.data["findings"]` alone (never incrementally
patched). `check_task_verification_flagged` only ever acts on `status == "flagged"` entries.

Two landmines here, both live-confirmed 2026-08-16, both from the same root shape — a fuzzy
heuristic making a PERMANENT decision about a task without enough context to know it's wrong:

- **`gap_acknowledged` must survive the full recompute.** Once `check_task_verification_flagged`'s
  `quota_exhausted` branch tells the Planner to stop redelegating a flagged task for good, that
  decision has to persist — but `_update_task_verification` REPLACES each flagged entry's whole
  dict every attempt. A field set once and not explicitly carried forward from the prior entry
  (`ledger.get(name, {}).get("gap_acknowledged", False)`, the way `_update_task_verification` now
  does before overwriting) gets silently reset to absent on the very next attempt. Combined with
  `retry_quota_topup` (§1's own docstring on `topup_quota_pool`) periodically un-exhausting the
  quota this branch keys off, an unprotected reset here reopens a directive the model was already
  told was final — see `check_task_verification_flagged`'s own docstring for the full oscillation
  incident. **Any new per-task ledger field meant to be a one-way, sticky decision needs this same
  explicit carry-forward, not just "set it once and assume the dict persists."**
- **The ledger's own depth==1-only blind spot (found 2026-08-17)**: `_update_task_verification`
  only ever grouped `depth == 1` findings before this fix — a task whose OWN findings were all
  empty (the "zero trailing text" mechanism below) read as `flagged: no real citable source` even
  when its nested depth>1 Analyzer children produced full, real, citable content. Confirmed live:
  a task with 5 empty-summary findings of its own had 2 of 3 nested Analyzer dispatches carrying
  complete real content, yet the ledger told the Planner to acknowledge a gap that didn't exist,
  silently dropping the run's single best-researched task. Root cause: a depth>1 finding's own
  `task_name` field is the CHILD dispatch's name (e.g. "Analyze SEF D8 Visa page"), with no stored
  link back to which depth==1 task it was working for. **Fixed** via a new `top_level_task_name`
  contextvar (`orchestrator.py`, set once at the depth 0→1 transition, inherited — never re-set —
  by any deeper nesting under it) threaded through every `add_finding` call and stored as a new
  field on each finding record; `_update_task_verification` now also groups depth>1 findings by
  `top_level_task_name` when computing per-task citability. Deliberately NOT applied to
  `RunState.coverage()` — that method's own docstring already excludes depth>1 for an unrelated,
  still-valid reason (a child REUSING content with no new URL must not make coverage look
  artificially low); this rollup is about the opposite case, a child producing genuinely NEW
  content the parent's own record never captured. **Any future per-task-name consumer of
  `run_state.data["findings"]` needs to ask: does this task's real evidence definition need to
  roll up depth>1 children, or does it have its own good reason (like `coverage()`) not to?** —
  the two functions correctly answer this differently, on purpose, not by oversight.
- **The `superseded` downgrade (`_looks_like_renamed_task`, `orchestrator.py`) is a raw `difflib`
  text-similarity guess, and a HIGH ratio does not mean "same subject."** It exists to catch the
  Planner renaming a flagged task instead of retrying it under the same `task_name` — but two
  INDEPENDENTLY dispatched, always-differently-named tasks that share a template (a multi-entity
  comparison query's own parallel phrasing, e.g. two cities' rent facets differing only in the city
  and neighborhood names) can score 0.89 similarity, comfortably over the 0.6 threshold, while being
  two genuinely different, both-still-needed facets. The downgrade is PERMANENT and silent — a
  `superseded` task is invisible to `check_task_verification_flagged` forever, with nothing else in
  the pipeline ever re-flagging the gap; confirmed live, a whole city's rent facet vanished from
  both `findings.md` and the final report with zero warning anywhere in the run. Fixed via
  `_instruction_entities` (proper-noun extraction) + a Jaccard-overlap override: a high text ratio
  is trusted as a real rename only when the two tasks' extractable named subjects actually overlap.
  **Any FUTURE heuristic that makes a permanent, silent decision from raw text similarity alone
  needs the same kind of "do the ACTUAL subjects match, not just the wording" guard** — text
  similarity and subject identity are correlated, not the same thing, and a templated multi-entity
  query is exactly the shape that pulls them apart.
  **Extended 2026-08-17, TWO more gaps found live in this same function/ledger, both from the same
  "a fuzzy detector's blind spot silently inflates a downstream count" root shape**:
  1. **`difflib.SequenceMatcher`'s char-level ratio badly under-fires on genuine full-sentence
     paraphrase** — the model's actual, common rewrite style when redispatching the same facet under
     a new name. A real live pair pulled from `_run_state.json` ('Find the typical monthly rent for
     a one-bedroom apartment...' vs. its own retry 'Search for recent data on the average monthly
     rent of a one-bedroom apartment...' — unambiguously the same facet, reworded) scored 0.11,
     nowhere near the 0.6 threshold, so the rename was never caught at all. Fixed by adding
     `_content_word_overlap` (Jaccard over lowercase content words) as an OR-combined second
     trigger — the existing entity-mismatch override (above) still runs afterward and still
     correctly rejects a cross-city false positive, unchanged.
  2. **Even when a rename IS caught and marked `superseded`, `RunState.coverage()` never read the
     ledger at all** — it counted every distinct `task_name` at face value, so a facet redispatched
     3 times under 3 different names still inflated `coverage()`'s `total` by 3, not 1. This is what
     actually consumed a live run's retry budget: `check_thin_coverage` kept re-firing "Only
     N/(growing total) tasks covered" as the total climbed with every rename, never converging.
     Fixed: `coverage()` now excludes any task_name whose `task_verification` entry is
     `"superseded"` from its `by_task`/`total` grouping. **Any future consumer of "how many
     distinct tasks/facets exist this run" needs to ask the SAME question — does it already know
     about `task_verification`'s `superseded` status, or will a renamed-and-recognized task still
     silently double-count for it?** `_update_task_verification` always runs immediately before any
     `coverage()`-dependent check in `run_completion_check`'s own per-attempt loop, so the ledger is
     guaranteed fresh for any new consumer that reads it the same way.

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

## 5. A third dispatch surface (`src/api.py`) and the module-globals it can't touch concurrently

**Where**: `src/api.py`, added 2026-08-02 as an optional FastAPI HTTP API + web UI, alongside
`run_cli`/`run_agent` (`src/engine/tui.py`).

**The landmine, found before writing any of it, not after**: `src/engine/orchestrator.py`'s
`_session` (conversational-memory cache, mutated inside `create_local_agent`) and `src/engine/
tui.py`'s `_session_events`/`_current_session_id`/`_current_call_by_source`/
`_current_text_by_source` (mutated by `log_stream_content`/`log_prompt`, used for the persisted
`~/.deepdelve/sessions/session_*.json` log) are module-level **globals**, not
`contextvars.ContextVar`s — confirmed by reading `create_local_agent`'s and `log_stream_content`'s
actual bodies, not assumed. Every OTHER piece of per-run state in this codebase already is a real
contextvar (`tool_quotas_ctx`, `session_dir_ctx`, `run_state_ctx`, `fetched_urls_ctx`, etc.) —
these five are the sole exception, a real gap that only matters once something tries to run two
research turns *concurrently in one process*, which `run_cli` (one-shot per process) and
`run_agent` (one interactive user, one turn at a time) both structurally never do. `api.py` is the
first surface that even could: it's a long-running server that could in principle serve many
callers.

**Design consequence, not a workaround bolted on after**: `api.py` never touches
`_session_events`/`_current_session_id`/etc. at all (a fourth accidental writer would only compound
the problem) and runs research jobs through a single in-process FIFO queue + one worker coroutine
— always single-flight, by construction, not by a lock added defensively. If a future feature
needs real concurrent multi-run execution, the fix is making those five globals contextvars (or an
equivalent per-run registry), not adding a second queue or a mutex around the existing ones.

**A second, narrower instance of the same class of gap**: `api.py`'s `/research/{id}/followup`
originally assumed `orchestrator_module._session` would still hold the right conversation when the
follow-up ran — wrong, because the shared queue can run an unrelated job in between, and that
job's own session reset silently clobbers it. Fixed by persisting `session.to_dict()` to
`<run_dir>/_agent_session.json` per-run (reusing the same `AgentSession.to_dict()`/`from_dict()`
mechanism `--resume <session_id>` already relies on for a different purpose — see §4) instead of
trusting the global to have survived. **Checklist for anything that needs "the same conversation
across two separate calls" outside of `run_cli`/`run_agent`'s own single-process-per-invocation
shape**: don't reach for `orchestrator_module._session` directly: persist and reload explicitly.

**`skip_completion_check` (`run_agent`'s `is_followup` branch, `tui.py`) has an exact precedent
`api.py` had to mirror, not reinvent**: a follow-up in a conversation whose report already exists
is Q&A over existing research, not a new research run — the artifact/grounding contract was
already enforced when the report was first produced, so the full completion-check/artifact-rewrite
pipeline must NOT run again. Missing this (an early version of `api.py`'s own `/followup`) makes a
follow-up question repeatedly reject `findings.md` rewrites and never touch `final_report.md` at
all, confirmed live 2026-08-02. **Checklist for a new follow-up-shaped dispatch anywhere**: `mode
== "followup" and config.get_required_artifact() in get_workspace_files()` is the exact condition
that must gate whether the full completion-check loop runs — copy the condition, don't approximate
it.

## 6. Quick-reference: "I'm adding X, what do I need to check?"

| Adding... | Check these |
|---|---|
| A new completion-check problem | §1's four-tuple checklist + verdict-matrix test row |
| A new `run_state.data` key that should survive `--resume-run` | §3: both resume-carryover tuples (`run_cli`, `_resume_run`) |
| A new sub-agent dispatch role | Does its first message look like FindingsWriter's (one big self-contained blob) or like everyone else's (built up turn-by-turn)? → §2's compaction-exclusion question. Does it need `write_workspace_file` gated behind something else? → `writer_gate_ctx` pattern. |
| A new entry point that dispatches research turns (beyond `run_cli`/`run_agent`) | §5: does it run turns concurrently in one process? The five module-level globals (`_session` and `tui.py`'s session-log state) are NOT contextvar-safe — either guarantee single-flight (the route `api.py` took) or make those globals contextvars first. Does it need follow-up/same-conversation continuation? → persist `AgentSession.to_dict()` per-run, don't trust the global to survive between calls. Does it need "Q&A on an existing report" semantics? → mirror `skip_completion_check`'s exact condition, don't approximate it. |
| A new config key under `settings.*` | `config_template.yaml` (documented default) AND confirm it's read with a safe `.get(..., default)` — this project's convention is "absent in the live `~/.deepdelve/config.yaml` is fine," never require a live-config edit for a new default-on feature |
| Anything that changes behavior based on "how far has this run gotten" | §4: both `run_cli` and the TUI's resume/follow-up paths |
| A new tool result shape or error format | `CLAUDE.md`'s own blast-radius rule: the TUI's `ToolCallWidget` rendering, `log_stream_content`'s persisted event log, `utils/grounding.py`'s citation/error detection |
| A new tool for Builder or FindingsWriter | §2's tool-set checklist: `app.py`'s `SubAgentConfig.tools`, a quota entry (`config_template.yaml` + live config), a mention in that role's `prompts.py` instructions, and — if the tool changes what "delegate" could mean — check every `_BUILDER_FIXABLE_PROBLEMS`/`_FINDINGS_WRITER_FIXABLE_PROBLEMS` check's `inject` text still makes sense for a recipient with this exact tool set |
| A completion check whose `inject` text can tell the reader to delegate/search/fetch | §2: confirm the Builder/FindingsWriter dispatch path (if applicable) doesn't hand a delegation instruction to a role with no `delegate_tasks` tool — see `_BUILDER_NO_DELEGATE_CLARIFICATION` |
| A per-item/per-facet dispatch loop (multiple sequential sub-agent dispatches for one completion-check problem) | §1: a bespoke `elif` branch, not the shared tuples (see `thin_coverage`/`report_underuses_evidence`/`findings_underuses_evidence`). Sequential (`await` in a loop), never `asyncio.gather` — concurrent writes to the same workspace file race. Cap the loop (see `_MAX_FACET_DISPATCHES`) — never unbounded. Scale any quota headroom calls by item count, not the single-dispatch default. Check any dispatch-name regex elsewhere in the codebase (e.g. `finetune/extract_dataset.py`'s `_WRITER_DISPATCH_RE`) before adding a new suffix to disambiguate loop iterations in logs — an anchored regex with no slot for it will silently misclassify the dispatch. **If the check now moves from the shared `_BUILDER_FIXABLE_PROBLEMS`/`_FINDINGS_WRITER_FIXABLE_PROBLEMS` tuple into its own bespoke branch, it stops being "self-resolving" by tuple membership — it now MUST call `_capped()` in its own return, or it can starve every check below it (§1's invariant); the standing audit test catches a missed one, but don't rely on the test to notice for you.** If the writer role is **FindingsWriter** specifically: do NOT pass `deterministic_fallback` to `_dispatch_writer_review_fix` for a per-facet dispatch, even though scoped evidence text is a natural-looking candidate for it — that fallback is a FULL-FILE-OVERWRITE path, only meant for a from-scratch write when the file doesn't exist yet at all; on an existing multi-facet `findings.md` (which per-facet dispatches only ever run against), a single facet's scoped fallback becoming the whole file would destroy every other facet's already-correct entries. Confirmed 2026-08-01 while building `_dispatch_per_facet_findings_writer_fix`. |

This table is not exhaustive by construction — it's the set of landmines this project has actually
stepped on. When you find a new one, add a row here instead of just fixing the instance.

## 7. Serving endpoint: OpenAI-compat vs. Ollama's native API

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

**Landmine, found 2026-07-29** (`RESEARCH.md` §15): the `"ollama"` branch fixes the thinking-leak
above, but per-model Ollama tag configuration matters just as much as the backend choice. Qwen3.6-
architecture models (confirmed on Ornith-1.0-9B) can intermittently drift off their own tool-call XML
template — a known, still-open upstream bug
([ollama/ollama#16383](https://github.com/ollama/ollama/issues/16383)). If the Ollama tag doesn't
explicitly declare `PARSER qwen3.5` / `RENDERER qwen3.5` in its Modelfile, this drift doesn't produce
a clean, catchable error — it silently corrupts the model's tool-call arguments (confirmed live:
`web_search` looping with the entire arg set wrongly nested under one key, deterministically, from
the first call). **Before benchmarking any Qwen3.5/3.6-family GGUF through `api.backend: "ollama"`,
check `ollama show <tag> --modelfile` for an explicit `PARSER`/`RENDERER` declaration matching the
model's actual architecture** — an undeclared tag relying on whatever fallback handles a
GGUF-embedded template is not equivalent and was the actual root cause here, not the model itself.
