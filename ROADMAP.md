# DeepDelve Roadmap

Status as of 2026-07-20.


This file is organized into: a standing methodology section (applies to all future model
verdicts), then History (investigation narrative — informational, not a task list), Completed
(shipped fixes/features), Pending (not started), Rejected (evaluated and discarded), and
Stretch (deferred/optional work, currently all fine-tuning).

## Model Evaluation Standard


Written after the user pushed back on two real fairness gaps found by re-reading the bake-off log
critically rather than taking past "discard" verdicts on trust: (1) the heterogeneous-tiering
entry above measured a foreseeable VRAM-thrashing result instead of catching it at design time,
and (2) MiniCPM5-1B's own FINAL VERDICT run (below) swapped the Planner/Builder off `gpt-oss:20b`
onto `mistral-nemo:latest` to free VRAM — meaning that verdict wasn't actually isolating the
specialist model as the one variable under test; some of what got blamed on MiniCPM5-1B (the
uncorrected `"[Authors' names]"` placeholders, specifically) was explicitly attributed to the
swapped-in Builder failing to catch it, not to MiniCPM5-1B itself. Neither gap was hidden — both
are documented in the entries themselves — but neither was caught BEFORE being treated as a
concluded verdict, which is the actual complaint. Going forward, a candidate is not "discarded" or
"adopted" until it clears all of the below:

1. **Confirm the operating mode actually reaches the model before scoring anything.** Don't infer
   a feature (nothink mode, tool-calling format, context length) from a model card or vendor docs
   alone — prove it with a raw API-level request (a direct `curl`/SDK call showing the expected
   field, e.g. `enable_thinking:false` producing zero `<think>` content) BEFORE running any full
   DeepDelve benchmark through it. This is exactly what MiniCPM5-1B's entire think-mode saga
   should have started with, and what caught the Qwen3-family Ollama passthrough bug only after
   several models had already been scored under it.
2. **Isolate the candidate as the only variable.** Every other role (Planner/Builder/
   FindingsWriter/PeerReviewer) stays on the project's known-good baseline (`gpt-oss:20b`) unless
   the candidate itself IS one of those roles. If VRAM genuinely forces a swap elsewhere in the
   pipeline for a test to run at all, that test cannot produce a clean verdict on the candidate —
   it can only be reported as informational, and the entry must say so explicitly. MiniCPM5-1B's
   FINAL VERDICT run above did not meet this bar — the Planner/Builder was swapped to
   `mistral-nemo` for VRAM. In principle that calls for an isolated retest; in this specific case
   the user has explicitly decided NOT to pursue that retest (see the MiniCPM5-1B entry's
   "Retest explicitly NOT queued" note and the "Heterogeneous role tiering" closure note below) —
   pairing `gpt-oss:20b` with any small specialist model is a closed strategy on this hardware
   regardless of which small model fills the slot. The general rule (isolate before verdicting)
   still applies to any FUTURE candidate; it does not retroactively reopen MiniCPM5-1B.
3. **State the serving backend and version alongside every verdict.** "Disqualified" must mean the
   MODEL failed, not that Ollama's serving layer mishandled it — the nested-array stringification
   bug (`ollama/ollama#6155`, affecting `mistral-nemo`/`llama3-groq-tool-use`/`llama3.2:3b`) and
   the think-mode passthrough bug (Qwen3 family) both mean some existing README/ROADMAP
   disqualifications may need a backend-corrected retest before they're trustworthy, not just the
   ones already flagged for the planned vLLM re-run.
4. **More than one run before a verdict, when the result is a discard.** A single run's failure
   can be a real capability ceiling or an unlucky decode/retry cascade — this project's own log has
   both (`qwen3:4b`'s multiple redispatch attempts vs. a genuine hard ceiling). A clean pass can
   still be reported off one run; a discard claim should be corroborated by at least a second run
   before being written up as final, or explicitly marked "single-run, not yet corroborated" if
   time didn't allow a second one.
5. **Keep a verdict changelog instead of silently overwriting.** If a verdict was reached under a
   later-found-flawed methodology (wrong operating mode, confounded pipeline, backend bug), don't
   delete or rewrite the old entry — mark it superseded and link to the corrected retest, so a
   reader can see which methodology produced which conclusion. This is why MiniCPM5-1B's entry
   already has separate "think-mode" and "FINAL VERDICT (nothink)" sub-entries rather than one
   overwritten verdict — keep doing that, and extend it to the confound flagged in point 2.
6. **A candidate that can't fit the project's ~16K-token context floor is discarded outright on
   hardware grounds, not proportionally rescaled to squeeze it in — user decision, 2026-07-21.**
   `config_template.yaml`'s `context_budget_chars: 50000` is explicitly calibrated as "safe margin
   under a 16K-token num_ctx" (see `README.md`'s Context management section and the
   `get_context_budget()` docstring, `src/engine/orchestrator.py`) — this is the project's assumed
   minimum operating context, not a soft target. When MiniCPM3-4B's real per-token KV cost on this
   hardware capped its feasible serving context at 6144 tokens (well under that floor), the
   response was to proportionally scale `context_budget_chars` down to fit — this is now the wrong
   call going forward. Doing so tests the candidate under a context regime the project doesn't
   actually run at, and produces one of two uninformative outcomes: a pass that doesn't generalize
   to any real DeepDelve usage, or a failure that's actually a context-fit problem miscounted as a
   capability problem. **Going forward**: check the candidate's actual max feasible serving context
   on this hardware (via vLLM's own KV-cache-budget error message, same as this evaluation did)
   BEFORE running any benchmark; if it can't clear ~16K tokens, discard immediately with the reason
   recorded as "insufficient context on current hardware," and revisit only if better GPU/VRAM
   becomes available — don't rescale the project's own safety margins to accommodate it.
   **Clarified 2026-07-21, `llama3-groq-tool-use:8b`**: this point targets a HARDWARE-forced squeeze
   (a candidate whose architecture could serve more context but this GPU's VRAM/KV-cache budget
   won't allow it) — it does NOT apply to a model whose own native `max_position_embeddings` is
   simply small by training (`llama3-groq-tool-use:8b`'s is 8192, a real fixed fact about the
   model, not something any amount of better GPU/VRAM would ever change). The user's own distinction:
   a permanent model-level limit is worth actually testing at its real native ceiling — only the
   hardware-driven, potentially-temporary kind gets the outright-discard treatment. Test the
   candidate at its true native context in this case, don't discard on point 6 grounds.


## History

Moved to the wiki, 2026-08-20, to keep this file focused on what is currently open:

**→ [Changelog](https://github.com/g0elles/deepdelve/wiki/Changelog)**


## Completed

Finished items moved to the wiki, 2026-08-20, to keep this file focused on what is still open:

**→ [Completed](https://github.com/g0elles/deepdelve/wiki/Completed)**


## Pending

- ~~Test whether `<Show Your Thinking>` contributes to the narrate-instead-of-call failure~~
  **TESTED 2026-08-19, HYPOTHESIS NOT CONFIRMED -- real negative result, block stays as-is.**
  Built a faithful isolated harness (`eval/ab_show_thinking_harness.py`): real
  `FINDINGS_WRITER_INSTRUCTIONS`, real evidence base (`_build_findings_source_material` called
  directly against `qwen3-4b-combined-v2`'s OWN saved `_run_state.json` from the exact run where it
  was live-disqualified for this failure), real `write_workspace_file` tool schema, 9 reps each
  with the block present vs. stripped via regex. **Result: 9/9 real `tool_calls` in BOTH
  conditions** -- no difference at all. The block does not cause this failure, at least not in an
  isolated single-turn dispatch. This means the live disqualification failure (the same model,
  same evidence, narrating instead of writing during the actual multi-attempt benchmark run) comes
  from something specific to the FULL run's multi-turn/retry dynamics -- accumulated context,
  budget/quota pressure across several completion-check attempts, or conversation-length effects --
  not from this one prompt block in isolation. Genuine negative result, not a re-open: no code
  change made, the block is not implicated. If this failure mode gets root-caused later, look at
  what differs between an isolated first-shot dispatch (which converges cleanly, per this test) and
  attempt N of a real run (which doesn't) -- that's the actual variable, not the prompt content.

- **UNIFIED LIST, 2026-08-17 — every open gap and literature-derived candidate improvement from
  today's session in one place, prioritized.** Consolidated at the user's request after a full
  literature-completeness audit plus a real citation audit of the ablation smoke-test's own
  output. Individual items are detailed in their own dated entries elsewhere in this file /
  `RESEARCH.md` / `session_status/CURRENT.md` — this entry is the index, not a duplicate write-up.

  **A. New correctness bugs found via manual claim audit — both items below CLOSED:**
  1. ~~Citation misattribution: a real, correctly-fetched claim gets attached to the WRONG cited
     URL...~~ **RESOLVED 2026-08-17, see "Completed" above** (`find_unsupported_specific_figures`
     + `check_specific_figure_unsupported`). Live-confirmed
     (2026-08-17 ablation smoke-test re-run, `final_report.md`'s "Mexico Digital Nomad Visa"
     section): 4 specific claims (the "300 days minimum wage/UMA" income formula, the sworn-
     translator requirement, the $53 fee/MiConsulado interview detail, the 180-day sticker
     validity) are all real and accurately worded — but every one of them was traced verbatim to
     `themexicohandbook.com`'s fetched content, while the report cited `esimcard.com` instead
     (whose actual content explicitly says Mexico has NO official digital nomad visa and gives
     DIFFERENT, consulate-varying income figures). Both URLs were genuinely fetched this run, so
     `real_grounding_problem`'s hard gate passed; the specific-number term overlap between the
     claim and its WRONG source's shared generic vocabulary ("income," "monthly," "USD," "months,"
     "2026") plausibly let `claim_grounding_problem`'s cheap term-overlap gate pass too, before NLI
     ever got a chance to judge entailment against the correct evidence window. **This is the SAME
     failure shape `find_unsupported_regulation_ids` was built for** (its own docstring: "the
     URL-presence gate passed... and the zero-overlap content check passed (shared generic terms)...
     so a misattributed law number sailed through both," live-confirmed 2026-07-11) — but that fix
     is scoped narrowly to `_REGULATION_ID_RE`-shaped identifiers (e.g. "Ley 1906 de 2021"), not to
     general numeric/factual claims (dollar amounts, day counts, named-requirement phrases). **The
     generalization WAS built, same day**: `find_unsupported_specific_figures`
     (`src/utils/grounding.py:544`) applies the identical "does THIS claim's specific, checkable
     token appear in ITS OWN cited source's content" check to dollar/fee/day-count figures, not just
     regulation IDs — confirmed by reading the shipped function, not just the changelog claim.
     Directly the mechanism Rasheed et al.'s claim-level-auditability paper (arXiv:2602.13855, §1
     above) formalizes as claim-node → typed-edge → source-node provenance.
     **Residual gap closed 2026-08-19**: the same function now also flags mixed-case named-entity
     TOKENS (`_NAMED_TOKEN_RE` — a lowercase letter followed by an uppercase one mid-word, e.g.
     "MiConsulado") verbatim-absent from their cited source — the exact same incident's
     "MiConsulado" reference was misattributed alongside its numeric figures, and a named
     portal/program name is just as checkable (can't be paraphrased) as a number. Deliberately
     still NOT generalized to arbitrary named-requirement PHRASES ("sworn translator requirement")
     — a phrase can be paraphrased without changing its truth, so a verbatim check there would
     over-fire; that remains a real, permanent design boundary, not a gap to close later.
     `test_structural_checks.py`'s `_specific_figure_scenario` covers the new token case plus a
     negative check (a plain capitalized word like "Mexico" with no internal case switch never
     fires). Full suite passes.
  2. **Portugal visa research shallowness** (same run): the agent fetched Portugal's official visa
     category-index page, correctly found it named "Remote Work / Digital Nomad" as a residency-visa
     category but had no income/process specifics, and reported "No specific visa data was
     retrieved" rather than following the page's own "Necessary Documentation" link to the real
     requirements. Consistent with (not a new bug beyond) the `uneven_task_investment` problem this
     exact run's final completion-check attempt already flagged for the Portugal sub-task (1 fetch
     vs. siblings' 3-5) — the existing check correctly detected thin research, it just didn't get
     resolved before `max_run_minutes`. No new fix needed here beyond what's already tracked; noted
     for completeness since it was found during the same claim audit.

  **B. Live-confirmed but only CONTAINED, not eliminated:**
  3. ~~PeerReviewer's hallucinated-filename churn...~~ **PROMPT-LEVEL NUDGE ADDED 2026-08-17**:
     `PEER_REVIEWER_INSTRUCTIONS`' Workflow step 1 (`src/prompts.py`) now explicitly says not to
     call `read_workspace_file`/`grep_workspace_file` again with a different filename once the
     first read succeeds — write the verdict from what's already been read. This project's own
     standing skepticism of prompt-only nudges on small local models means this should NOT be
     treated as closing the item on its own — `tools/core.py`'s tool-failure-streak guard remains
     the real structural backstop (containment, not elimination, per item above). Not yet live-
     tested whether the prompt change measurably reduces how often the guard even needs to fire.
  4. ~~Duplicate/redundant report content from a task that got through as the FIRST rename
     match...~~ **RESOLVED 2026-08-17, real root cause traced from the session transcript itself,
     structural fix built and live-validated against the actual incident data.** `_dedupe_findings`
     (`src/engine/completion.py`) also now collapses near-identical findings across DIFFERENT
     task_names (reusing `_content_word_overlap`) — a real, defensively-valuable general fix for the
     rename-retry-produces-a-separate-near-duplicate-finding mechanism this item originally
     described, though tracing didn't confirm that mechanism as THIS incident's actual cause (see
     below).
     **The real root cause, found by reading the live session transcript event-by-event**: the very
     last action in the run, `BuilderFix_attempt3_reviewed`'s own `edit_workspace_file` call,
     anchored `old_string` on the bare heading `"### Mexico City"` alone, then wrote a `new_string`
     that retyped the heading's own PRE-EXISTING bullet verbatim before appending a new, more
     specific `"### Mexico City – Central Districts"` subsection next to it — since the original
     bullet was never part of `old_string`, it stayed in place, and the retyped copy landed right in
     front of it. Not a research/dispatch bug at all — a tool-usage mistake in how Builder
     constructed a single edit.
     **Two-layer fix, matching this project's own "prompt nudge alone is unreliable, back it with a
     structural check" standing pattern**: (1) `BUILDER_INSTRUCTIONS`/`FINDINGS_WRITER_INSTRUCTIONS`
     (`src/prompts.py`) now explicitly say to anchor an ADDING edit on the boundary of existing
     content and never retype it into `new_string`; (2) `find_duplicate_report_sections` +
     `check_duplicate_report_sections` (`src/engine/completion.py`, new `settings.
     duplicate_section_check.enabled` config flag) — a structural, self-consistency check comparing
     every h3+ subsection of the SAME report against every other (via `_content_word_overlap`,
     threshold 0.6), registered in `GROUNDING_CHECKS`/`_QUARANTINE_PROBLEMS`/
     `_BUILDER_FIXABLE_PROBLEMS`. **Live-validated directly against the real incident's
     `final_report.md`**: correctly flags exactly `"### Mexico City – Central Districts"` and
     nothing else (no false positives on the genuinely-distinct Lisbon/Portugal sections). Full
     `test_structural_checks.py` coverage: a direct `find_duplicate_report_sections` unit scenario
     and a new verdict-matrix row.

  **C. Literature-derived, actionable, NOT YET implemented, NOT fine-tuning (each already detailed
  in its own RESEARCH.md/README.md entry, listed here just for the consolidated view):**
  5. ~~Append "Wait"...~~ **IMPLEMENTED 2026-08-17**: the Fix-pass dispatch (after PeerReviewer
     flags issues) now leads with "Wait." — Self-Correction Bench's single most actionable,
     training-free finding (89.3% blind-spot reduction). Adapted, not a literal replication: the
     paper's own harness appends it mid-generation as a continuation cue; DeepDelve's dispatch is a
     fresh turn, so it's applied as a deliberation cue at the start of the correction ask instead.
     Covered by `test_structural_checks.py`'s `_clean_check_read_verification_scenario`.
  8. ~~Run the full controlled-ablation study...~~ **BACKLOGGED 2026-08-17 — real, not skipped:
     genuinely needs proper time budget, not a same-session push.** (`eval/ablation_configs/
     *.yaml`, built earlier this session, 4 mechanisms: `force_whole_rebuild`, `no_progress_guard`,
     `rename_reject_escalation`, `tool_failure_streak_guard`) — only single-trial smoke tests have
     been run so far (baseline k=1 twice), never a real k≥3 with/without comparison per any
     mechanism. This is the standard MAST's own causal-intervention methodology and the Illusion-
     of-Multi-Agent-Advantage paper's own audit methodology both hold coordination-layer complexity
     to (RESEARCH.md §18f) — DeepDelve's completion-check mechanisms have accumulated for months
     validated only by "did today's specific symptom stop recurring." **Real cost, computed
     honestly**: a full flat 4 mechanisms × (enabled/disabled) × k≥3 = 24 runs, at the ~46-50
     min/run observed today, is ~19-20 hours of unattended runtime — not something to push through
     in one sitting. **When picked back up, scope it down, don't run the naive full matrix**: (a)
     prioritize `force_whole_rebuild`/`no_progress_guard` first — older, never measured with-vs-
     without at all, unlike `rename_reject_escalation`/`tool_failure_streak_guard` which were JUST
     live-validated against a real incident today; (b) adaptive trial count per this project's own
     Model Evaluation Standard point 4 ("a discard claim needs more than one run, a clean pass can
     stand off one") — one trial per condition first, escalate to the full k≥3 only where a
     with/without difference actually shows up, not a flat count for all 8 conditions upfront;
     (c) run unattended in the background across however many sessions it takes, not blocked on in
     one sitting. **IN PROGRESS 2026-08-18, first two mechanisms both CONFIRMED load-bearing — see
     RESEARCH.md §18f's own results table for the full writeup**: baseline 0.75.
     `disable_force_whole_rebuild` **mean 0.25 across k=3 (0.75, 0.00, 0.00)** — started at k=1
     (0.75, matched baseline), escalated after a k=2 second trial disagreed (0.00), k=3 confirmed
     the majority: 2 of 3 runs hit a completion-check verdict (`task_verification_flagged`/
     `thin_coverage`) repeating 3+ times without the mechanism ever forcing a real change of
     strategy, ending with an incomplete or entirely missing report. `disable_no_progress_guard`
     **mean 0.125 across k=2 (0.00, 0.25)**, both runs timed out at the 47min ceiling — not
     escalated to k=3 (2/2 already failed the same way, a 3rd run offers little new information).
     `disable_no_progress_guard`'s run 1 also surfaced and got a real fix for a separate bug along
     the way (FindingsWriter re-citing the same hallucinated URL across rebuild attempts because
     the retry directive never named which source failed — see "Completed" section for the fix).
     **CLOSED 2026-08-19 — not running `rename_reject_escalation`/`tool_failure_streak_guard`.**
     Both were built in direct response to a real live incident already (see (a) above), so their
     load-bearing-ness already has direct evidence independent of this study; an ablation run would
     only re-confirm that at ~47min/run, and any new run is now confounded (next paragraph) anyway.
     The study's actual goal — MAST/Illusion-of-Multi-Agent-Advantage-style causal validation of
     coordination-layer complexity — is satisfied by the two mechanisms tested: both confirmed
     load-bearing, same underlying failure mode (stuck repetition) recurring at two different
     layers (tool-call level vs. completion-check-verdict level).
     **CONFOUND, 2026-08-19**: `settings.specialist_delegation_cap` was bumped 3 -> 4 (see
     `session_status/CURRENT.md` item 2) after a fork analysis of these same 6 runs found visa/
     regulatory tasks structurally starved by the old cap. Every result recorded above ran at
     `cap: 3` — noted for the historical record, not something a future run needs to reconcile
     since no further ablation runs are planned.
  9. ~~Hierarchical/divide-and-conquer decomposition for the consolidation stage...~~ **CORRECTED
     2026-08-17, was NOT actually open — this list entry was written from a stale premise, no new
     code needed.** The escalation this item described already happened, in an earlier ROADMAP
     entry this list failed to cross-reference before listing it here: the "Multi-facet task
     abandonment" entry below already tried the smaller `edit_workspace_file` directive fix,
     confirmed it live-tested negative, and escalated to real per-facet dispatch
     (`_dispatch_per_facet_builder_fix`/`_dispatch_per_facet_findings_writer_fix`) — which IS the
     hierarchical/divide-and-conquer mechanism this item was asking for, already shipped. The one
     narrower gap that entry left open (a whole-rebuild dispatch reproducing byte-identical
     rejected content) is also already closed, by `_content_unchanged_since_last_quarantine`
     (built and live-validated earlier this SAME session — see that entry's own "RESOLVED" update).
     The three literature papers cited here (MARL, Illusion-of-Multi-Agent-Advantage,
     Divide-and-Conquer noise) remain valid, real evidence for why per-facet dispatch was the right
     call — just evidence for something already built, not a case for building something new.

  **BACKLOG, deferred by explicit user decision 2026-08-17 — "I do not want to fine tune a model if
  the tool is not properly built."** Any fine-tuning-adjacent item goes here by default, below every
  pipeline-correctness item above, until this project has no known open correctness gaps (item A.1
  above is the current blocker for even considering re-opening this backlog):
  6. Verify the AXPO resampling-reward mechanism against `finetune/reward.py`'s actual
     `writer_role_response_reward` implementation before adapting it for a future GRPO round —
     AXPO's own Appendix E Limitations (read in full 2026-08-17) confirms in the paper's own words
     that its trigger mechanism assumes a binary verifiable-outcome reward, which DeepDelve's
     structural (did-it-happen) reward doesn't have; the underlying "concentrate exploration at the
     sparse action boundary" insight likely still transfers, the specific mechanics don't as-is.
  7. Apply Iterative Reward Calibration's "always measure discriminative power before deploying a
     denser reward" checklist to any future expansion of `writer_role_response_reward` beyond its
     current single structural signal — the paper's own live finding was a naive dense reward
     costing up to 14pp versus a sparse one, on the SAME Qwen3 family DeepDelve tests.
  - Also backlogged under this same rule: starting any NEW GRPO training round at all (already
    gated separately, see the "Fine-tuning" section's own standing pause/resume history above).

  **D. Already implemented and live-validated this session, for context (not open items)**: the
  rename-reject-escalation and tool-failure-streak guards (both fired correctly in the re-run
  above); the `_content_unchanged_since_last_quarantine` escalation; the eval reliability harness
  (`--runs`/pass@k/pass^k); the ablation-switch infrastructure itself. See `session_status/
  CURRENT.md` for the full implementation write-up of each.

- **Multi-facet task abandonment under iterative self-correction — scoped 2026-07-31, literature
  review DONE, first fix attempted and LIVE-TESTED: NEGATIVE RESULT, real per-facet dispatch is
  next.** With the completion-check starvation bug class
  fully fixed (see Completed above), a clean, unconfounded gpt-oss run against the standing
  sales-forecasting benchmark converged on a real, honestly-caveated, correctly-grounded report —
  but that report still answered only ~1/3 of the query (no "top 5 heuristic algorithms" list, no
  Colombia cultural-pattern integration), despite `check_report_underuses_findings`/`_evidence`
  correctly flagging the exact missing facets on every single attempt, 6+ Builder rewrites in a row
  plateauing at the same low citation count each time. Same pattern first logged 2026-07-14/18 (see
  MODELS.md's `gpt-oss:20b` entry), now reconfirmed clean of every structural confound found since.

  **Ruled out first, not assumed**: stale/leaked sub-agent context. Traced `_run_single_task`
  (`src/engine/orchestrator.py:803`, the closure every dispatch routes through, including every
  Builder Write→Review→Fix call) — it constructs a genuinely new `dispatch_client.as_agent(...)`
  and calls `.run(current_input, ...)` with only the fresh instructions text on EVERY dispatch, no
  conversation/thread object reused across attempts, `session` (conversational memory) never
  touches this path. Live logs confirm Builder actively calls `read_workspace_file` each dispatch
  (not working from a memorized/stale findings.md). The abandonment is not a memory bug.

  **Literature review** (before attempting a fix, per `feedback_read_docs_before_building`):
  - [*Self-Correction Bench: Uncovering and Addressing the Self-Correction Blind Spot in Large
    Language Models*](https://arxiv.org/abs/2507.02778) (Tsui, COLM 2026 — **corrected 2026-08-17**:
    this file previously attributed the "self-correction blind spot" finding to the Kamoi et al.
    TACL survey below; a literature-completeness audit found the 64.5% figure doesn't appear in
    that paper at all, it's a pure methodology critique with no such measurement — Tsui's paper is
    the actual, sole empirical source, see `RESEARCH.md` §18b) — models are measurably worse at
    correcting errors in their OWN prior output than at correcting the identical error presented
    as external input (~64.5% of self-generated errors survive self-checking across 14 open models
    even though the same errors are caught when presented externally). Directly matches this
    setup: Builder re-examines and "fixes" its own prior draft every retry, not someone else's.
  - [*When Can LLMs Actually Correct Their Own Mistakes? A Critical Survey of Self-Correction of
    LLMs*](https://arxiv.org/html/2406.01297v3) (Kamoi et al., TACL) — read in full 2026-08-17, does
    NOT contain the 64.5% figure (see correction above); its real contribution is a taxonomy of
    self-correction research questions (RQ1: can a model self-correct on inherent capability alone;
    RQ2: with external info, on the best-possible initial response; RQ3: does the final result beat
    other methods) and a Fair/Unfair, Intrinsic/Fair-asymmetric framework classifying HOW a
    self-correction setup gets its feedback. Under this taxonomy, DeepDelve's PeerReviewer-critique
    retry loop is "fair-asymmetric" (external feedback from an independent reviewer) — the pattern
    this paper's own analysis found DOES work — not "intrinsic" self-correction (a model critiquing
    itself with no external signal), which it found largely does not. Worth keeping in mind before
    any redesign of this retry loop: don't accidentally shift it toward the intrinsic shape.
  - [*Cross-Context Review: Improving LLM Output Quality by Separating Production and Review
    Sessions*](https://arxiv.org/pdf/2603.12123) — proposes session-separation between production
    and review as a mitigation. DeepDelve's PeerReviewer already does this partially (fresh-context
    review dispatch), but the FIX step still routes back through Builder regenerating from its own
    prior framing, not a genuinely independent producer.
  - [*When Does Divide and Conquer Work for Long Context LLM? A Noise Decomposition
    Framework*](https://arxiv.org/abs/2506.16411) — names three distinct long-context failure
    modes (cross-chunk dependence / model confusion / **aggregator noise**). The observed pattern
    (individual facts present and correctly grounded in findings.md, but the merge/synthesis step
    drops whole topic clusters) is a clean match for aggregator noise specifically, not the other
    two — useful for scoping WHICH fix family is relevant.
  - Hierarchical/divide-and-conquer decomposition (task → parallel per-topic sub-generation →
    merge) is the literature's standard mitigation for this failure shape in multi-topic
    long-form generation.

  **First attempt: explicit `edit_workspace_file` directive, commit `67e4b00` — LIVE-TESTED,
  NEGATIVE RESULT (2026-07-31→08-01).** Both `check_report_underuses_findings`/`check_report_
  underuses_evidence` directives rewritten to explicitly name `edit_workspace_file` and instruct
  "insert a new section covering ONLY {missing}, do not rewrite or touch any other part of the
  report," on both the first-occurrence and escalated branches — the smallest, lowest-risk,
  literature-grounded fix per this project's own escalation discipline, tried before the bigger
  per-facet-dispatch rebuild. Live re-run confirmed the directive DID change Builder's tool
  choice — it correctly called `edit_workspace_file` in direct response to the check firing
  (`research_output/i_want_documentation_on_heuristic_algoritms_for_de_20260731_234906/`, attempts
  5 and 7) — but the citation ratio stayed frozen at the identical 2/15 both times, and by the
  run's end (attempt 14, retry budget exhausted on an unrelated `nli_unsupported` issue) the final
  report had gone from "answers ~1/3 of the query" to answering **zero** of the ML/heuristics half —
  100% Colombian-festivals content, the deep-learning/sales-forecasting facet not mentioned even
  once, despite the explicit "do not touch any other part of the report" instruction. **Verdict:
  prompt-level tool-routing does not fix this.** The model can be told which tool to use and still
  fail to hold both facets simultaneously — confirms this is genuinely the self-correction blind
  spot / aggregator noise the literature describes, not a tool-choice or wording gap, and prompt-
  only fixes have hit their ceiling per that same literature.

  **Next step, now justified by evidence, not skipped past**: the per-facet Builder dispatch
  architecture. DeepDelve already has the data model it would need — `check_report_underuses_
  evidence`'s per-task URL grouping (`src/engine/completion.py`) already tracks which real
  findings.md sources belong to which facet/task. Dispatch Builder ONCE PER under-represented facet
  (each a genuinely independent, externally-scoped "production" call, per the Cross-Context Review
  framing) instead of one holistic whole-report rewrite hoping a single regeneration fixes
  everything the correction flagged, then a lightweight merge/assembly pass. A real architecture
  change to the writer-dispatch shape (new dispatch shape, a merge step, TUI/CLI parity per this
  project's own mandatory rule, new quota accounting) — needs its own scoped plan before
  implementation, not a small patch.

  **UPDATE 2026-08-17**: `_dispatch_per_facet_builder_fix` and `_dispatch_per_facet_findings_
  writer_fix` (`ARCHITECTURE.md` §1's routing section) now exist and are live — the "next step"
  above shipped since this entry was last written. But the self-correction blind spot this
  literature review names is NOT fully closed by per-facet dispatch alone: a live run
  (`session_status/CURRENT.md`, run6) showed FindingsWriter reproduce the IDENTICAL hallucinated
  citation across 3 consecutive WHOLE-REBUILD dispatches (`missing_findings`/`findings_ungrounded`
  → `_dispatch_writer_review_fix`, not the per-facet ADD-ONLY path) — confirmed via 3
  byte-identical `findings.md.rejected_attempt_N` snapshots on disk, so this wasn't even fresh
  model generation reproducing the error, it was the SAME deterministic-fallback content getting
  rejected on repeat with nothing changing between attempts. Per-facet dispatch fixes the
  "aggregator noise drops a whole facet" shape this entry's literature review targeted; it does
  NOT fix "a rebuild dispatch re-derives (or a fallback re-serves) the exact same wrong
  conclusion from the exact same evidence." ~~Not yet investigated~~ **RESOLVED 2026-08-17, same
  session, chain closed and confirmed by cross-referencing three ROADMAP entries that had drifted
  out of sync with each other**: `_content_unchanged_since_last_quarantine` (`src/engine/
  completion.py`) is exactly the change-detection this question asked for — it directly compares
  the content about to be quarantined against the most recent already-quarantined snapshot for
  the same artifact (byte-for-byte, regardless of whether the completion-check PROBLEM NAME
  changes between attempts, which is what let the original 3-consecutive-same-problem counter miss
  this exact "run6" case). On a match, it forces exactly ONE `force_whole_rebuild` attempt with
  genuinely reframed instructions ("do NOT just patch the specific issue again... reconsidering
  your whole approach"), then caps further looping (`whole_approach_retry_used_for`) rather than
  repeating indefinitely — live-confirmed firing correctly on the ablation smoke-test re-run
  (`session_status/CURRENT.md`). This was built and validated as its OWN fix earlier the same day,
  before this cross-reference was drawn — the "hierarchical decomposition" item in the Pending
  unified list's item C.9 is consequently NOT open work, it was already closed by the combination
  of per-facet dispatch (above) and this mechanism; corrected there rather than building a
  redundant third mechanism.

- **Writer-role zero-trailing-text — found 2026-08-17, FIXED 2026-08-18, LIVE-CONFIRMED
  2026-08-19.** This Pending entry was never updated when the fix shipped. The same "sub-agent
  ends its own turn immediately after a tool call, zero trailing text" mechanism this project
  already tracks for Searcher/Analyzer turns also hit a WRITER role (worse consequence: nothing
  gets written at all, not just one empty finding). `_dispatch_writer_review_fix`'s one-shot retry
  didn't reliably recover it (fired on 3 of 3 rounds in one run without success); replaced with a
  bounded `_WRITER_EMPTY_RETRY_ATTEMPTS = 2` loop (`src/engine/completion.py`). Full detail and the
  live confirmation (a real run's `_retry1`/`_retry2` both firing, then recovering usable content)
  in `ARCHITECTURE.md` §2 and `session_status/CURRENT.md`. Closed — its sibling, the original,
  longest-tracked instance of this mechanism (Searcher/Analyzer dispatches, not writer roles), was
  also found and fixed 2026-08-19, live-confirmed the same day — see `ARCHITECTURE.md` §2 (no
  separate Pending entry, it was found and closed within this same session).

- **`create_local_agent`'s 963-line nested-closure god-function — new, scoped 2026-07-29, NOT
  attempted, needs its own dedicated session.** A whole-repo structural audit
  (`src/engine/orchestrator.py` ~698-1661) found this the single riskiest piece of code in the
  repo to change: `_run_single_task` (~490 lines) and `delegate_tasks` (~280 lines) are defined as
  deeply-nested closures INSIDE `create_local_agent`, capturing dozens of enclosing locals
  (`client`, `specialist_client`, `sem`, `holds_token`, `report_style_instructions`,
  `_sdk_timeout_ceiling_seconds`, etc.) by reference rather than as parameters. `test_structural_
  checks.py` never imports either closure directly — only small pure helper fragments that were
  pulled OUT of this function over time (`_extract_excluded_topics`, `_looks_like_renamed_task`,
  `_ring_fenced_deadline`) are tested; the actual per-task dispatch/quota-ring-fencing/specialist-
  tiering behavior this function implements has ZERO direct test coverage. Per extract-method/
  extract-class refactoring literature checked this session (arXiv:2312.12600, arXiv:2303.14253):
  long functions with implicit shared state directly drive up the number of tests needed for full
  coverage and should be pulled into parameter objects, not left as closures — confirming this
  audit's own assessment, not just restating it. **Recommended approach when picked up**: write
  characterization tests FIRST (pinning current behavior end-to-end, since none exist), THEN
  decompose — attempting decomposition without a safety net on a function this size, alongside
  other unrelated work, is exactly the kind of change that creates a new incident rather than
  closing one.

- **`completion.py`'s mixed responsibilities — new, scoped 2026-07-29, NOT attempted.** The same
  structural audit found this 2591-line file's own header describes it as a clean list of pure
  `Ctx -> Optional[Verdict]` check functions, but it also contains, with no separation: findings-
  authoring/evidence-assembly logic (`_dedupe_findings`, `_collapse_multi_url_task_findings`,
  `_build_findings_source_material`, ~220 lines), disk-touching quarantine/restore/salvage helpers
  that reach into `tools.fs._get_safe_path` (a private name) at four separate call sites, async
  sub-agent dispatch orchestration (`_dispatch_writer_review_fix`, `_dispatch_deepening_round`),
  the task-verification ledger mutator, and the 390-line `run_completion_check` state machine
  tying all of it together. None of this is individually bug-prone (the docstrings show real
  care), but it means "add a new completion check" (the one thing CLAUDE.md documents as routine)
  requires understanding artifact quarantine, writer dispatch, and findings-material assembly
  living in the same module namespace. **Blast-radius warning for whenever this is picked up**:
  `test_structural_checks.py` imports 40+ private (`_`-prefixed) names directly from across
  `orchestrator.py`/`completion.py`/`tui.py`/`tools.fs`/`tools.web`/`tools.core` — any module split
  must update that test file's imports in lockstep, not as an afterthought, or the test suite
  silently stops covering what it used to.

- **Config accessor for the 85 scattered `config.cfg.get("settings", {}).get(...)`-style chains —
  new, scoped 2026-07-29, PARTIALLY addressed.** The structural audit counted 85 call sites across
  `orchestrator.py` (33), `tui.py` (27), `completion.py` (12), and the tools/utils modules, with no
  single accessor and no consistent default handling — confirmed as a REAL bug source, not just
  duplication, by the `required_artifact` case: 3 different literal fallback values (`"final_report
  .md"` in most `tui.py` call sites, `None` in `completion.py`) scattered across the 4 call sites
  for that ONE setting. **Fixed for that one instance** (`config.get_required_artifact()`, added
  2026-07-29 as part of the `run_cli`/`BasicTuiAgent` safe-subset cleanup) — the other 81 call
  sites are unaddressed. Recommend incremental migration (add an accessor the next time any of
  these settings' call sites needs touching for an unrelated reason) rather than a big-bang
  rewrite of every site in one pass — most are one-off reads with no demonstrated inconsistency
  risk like `required_artifact` had.

- **`check_findings_underuses_evidence` evidence-dropping — MONITORING POINT, not a fix target
  yet (2026-07-29).** During the 2026-07-29 findings/report-writing diagnosis session (prompted by
  a direct request to find out whether the writer turn is structurally overwhelming these models,
  not assume it), this check's own docstring surfaced the single strongest piece of evidence found
  for a genuine model synthesis-discipline weakness independent of any infra bug: a clean,
  balanced, non-overloaded 2-task run (`explain_the_health_benefits_of_green_tea_and_separ_
  20260726_113029`, 7 + 5 real sources, `run_state.coverage()` ratio 1.0, no research-volume
  problem) still had FindingsWriter silently drop an ENTIRE covered topic (Roman Empire) from
  `findings.md` — not thin, not truncated, gone outright. Every OTHER 2026-07-28 writer-stage
  failure investigated in that same session (Ornith's URL-case rejection, its wall-clock cutoff,
  qwen3-4b-combined-v2-lora's narration-instead-of-write) traced to a specific, non-model
  mechanism, now fixed — this is the one exception. **Deliberately not fixed via a blind prompt
  rewrite**: per this project's own Model Evaluation Standard reasoning (a discard/fix claim needs
  more than one occurrence, one clean pass can't establish a pattern either), a single incident
  isn't enough to design a targeted fix from without guessing. Concrete reopen trigger: if a
  SECOND clean (non-overloaded, unconfounded) run shows the same whole-topic-dropping shape, it's
  worth a targeted prompt reinforcement (e.g. an explicit per-task-name coverage checklist
  FindingsWriter must satisfy before calling `write_workspace_file`) — not before.

- **Backend-adapter abstraction for serving endpoints — CLOSED same day, see History
  ("2026-07-28: pluggable api.backend").** `api.backend: "ollama"` now reuses
  `agent_framework.ollama.OllamaChatClient` (already-installed sibling package to the default
  `OpenAIChatCompletionClient`) to route around the OpenAI-compat endpoint's thinking-leak
  confirmed in `RESEARCH.md` §14e. Live-verified end to end (see History entry) before being
  considered done, not just unit-tested.

- **FindingsWriter drops most real findings even when it writes the file correctly — CLOSED,
  moved to Completed 2026-07-24** (see Completed's "FindingsWriter evidence-abandonment ROOT
  CAUSE — CLOSED 2026-07-22" entry for the full fix and live-confirmed 5/5 coverage result; this
  Pending entry was simply never updated when the fix shipped the day after the last negative
  re-test). The one still-genuinely-open piece from that investigation: the chunked map-reduce
  dispatch option (LLM×MapReduce-style, arXiv:2410.09342) named as a bigger, deferred alternative
  if the gate/dedupe fix ever proves insufficient again — not built, not currently needed.

- **`run_cli`/`BasicTuiAgent` run-lifecycle duplication in `src/engine/tui.py` — SAFE SUBSET
  CLOSED 2026-07-29, full unification still open, re-scoped after a live transcript-level audit.**
  A dedicated audit (2026-07-29) mapped the duplication precisely rather than re-deriving it from
  outcomes: it found the two entry points aren't just stylistic duplicates in several places — TUI's
  approval-handling actually executes tools client-side and constructs full message pairs, CLI's
  doesn't; TUI has no context-budget/wall-clock-deadline concept by design (a human can `/stop`).
  Full mechanical unification of those parts is genuinely risky, not just tedious. What WAS safe
  and got fixed this pass:
  - The 9-key `RunState` resume-merge allowlist, copy-pasted verbatim in both `_resume_run` and
    `run_cli` with comments in both warning the other must be updated by hand — extracted into
    `utils.run_state.merge_resumed_state`/`_RESUME_CARRYOVER_KEYS`, one source of truth.
  - The `required_artifact` config lookup, copy-pasted 4x with THREE different literal fallback
    values scattered across call sites (a real latent inconsistency) — extracted into
    `config.get_required_artifact()`.
  - TUI's `run_agent` had NO `QuotaAbortException` handling at all — worse than previously
    documented: since it subclasses `BaseException` not `Exception`, it wasn't even being caught by
    `run_agent`'s `except Exception`, so it would have propagated uncaught rather than degrading
    gracefully like CLI does. Fixed by widening to `except BaseException`, with an explicit
    `asyncio.CancelledError` re-raise guard added first so `/stop` (which relies on
    `self.workers.cancel_all()`'s cancellation propagating) doesn't silently break.
  - TUI's `run_agent` had no crash-time `run_state.save()` outside normal loop completion (CLI
    guarantees one on any top-level crash, 2026-07-11 fix) — added a matching outer
    `except Exception` that saves and re-raises, without swallowing the original exception the TUI
    surface still needs to see.
  **Still genuinely open, re-scoped not abandoned**: unifying the stream-consumption loop
  (`iter_agent_stream`'s outer iteration is already shared, but per-update content dispatch is two
  independent implementations) and the approval-handling block behind explicit strategy
  objects/parameters (a `Presenter`/tool-execution-or-not design decision, not a mechanical
  extraction) — this is the part CLAUDE.md's "own dedicated session" guidance still applies to.
  Replacement when picked up: design the strategy-object interface FIRST (what varies between TUI
  and CLI: tool execution on approval, budget/deadline presence, notify() rendering), then extract
  the loop body to take that interface as a parameter — not "extract the whole function and see
  what breaks."

- **Sharper repetition-escalation idea, the NOT-adopted narrower granularity**: `NousResearch/
  hermes-agent` issue #481's proposed SHA-256 tool-call fingerprint loop guard (per-tool-call, one
  level below the completion-check layer) is confirmed CLOSED with "No branches or pull requests"
  — never implemented, just a proposal. The completion-check-level escalation fix it inspired
  shipped in a different, complementary shape (see Completed, "Full-artifact-rebuild
  repetition-escalation"); this finer-grained IN-TURN loop guard (catching e.g. a Searcher calling
  `fetch_url_to_workspace` on the same URL 6 times in one dispatch's own turn — the exact shape of
  the `qwen3:8b`/MiniCPM4-MCP incidents in History) is still a real, distinct, un-built idea if
  ever worth pursuing — would be new DeepDelve-original work, not an adaptation of anyone's code.

- **Re-run the full 11-candidate local-model bake-off via vLLM instead of Ollama — IN PROGRESS,
  most candidates now closed, moved to History as each verdict lands.** Two independent, confirmed
  Ollama-serving-layer bugs (Qwen3 think-mode passthrough, `ollama/ollama#6155` nested-array
  tool-parameter stringification affecting `mistral-nemo`/`llama3-groq-tool-use`/`llama3.2:3b`) mean
  several of README.md's 11 bake-off disqualifications may reflect Ollama's own serving bugs rather
  than genuine model incapability. Full plan in `~/.claude/plans/moonlit-plotting-simon.md`.
  Pre-flight checks (HF repo IDs, `bitsandbytes`-on-ROCm) and every candidate tested so far
  (`mistral-nemo:12b` BLOCKED, `llama3-groq-tool-use:8b` DISQUALIFIED, `qwen3:8b` killed mid-run
  with a real DeepDelve-side fabrication bug found and fixed, MiniCPM5-1B DISQUALIFIED in both
  paired and single-model forms, the Qwen3-family think-mode bug confirmed Ollama-specific via
  vLLM, MiniCPM3-4B INCONCLUSIVE on a real infra hang, MiniCPM4-MCP not yet viable) are all fully
  concluded — see `History`'s "Model bake-off & backend investigation log" for the complete
  evidence trail. **Still genuinely open**:
  - `qwen3:8b` retest — DONE 2026-07-24, DISQUALIFIED, same failure class as before, now
    confirmed clean of every prior excuse. Full detail in the "qwen3:8b vLLM retest" History
    entry below; see Completed/History for the run-by-run trace.
  - `qwen3.6`/`Gemma 4 12B`/`llama3.2:3b` and the rest of the 11-candidate plan — not yet attempted.
  - The Mistral family (`mistral-nemo:12b`, `devstral:24b`, `mistral:7b-instruct-v0.3`) stays
    blocked until a DeepDelve-side fix makes `_get_default_options()`'s `chat_template_kwargs`
    injection conditional — out of scope to hack in mid-benchmark without sign-off (touches every
    model's request path).
  - A clean Qwen3-family re-benchmark via vLLM (now proven to fix the think-mode bug) is a real,
    low-friction option if it's ever worth revisiting — not committed to, since these are all still
    below the literature's own capacity floor regardless.

- **Forced `tool_choice` on vLLM as a structural fix for "narrate instead of write" — new candidate,
  2026-07-19, not yet prototyped.** Found while investigating whether vLLM is a realistic Ollama
  swap (see "Model bake-off" log below for the full investigation, including a real empirical test:
  Ollama silently ignores `strict`/`enum` schema constraints on tool-call arguments — confirmed
  live, `enum: ["Moscow","London"]` did not stop a `deepdelve-gpt-oss` call from returning
  `"Rome"`. vLLM's `tool_choice: "required"` DOES enforce it, 5/5 runs at temperature 1.0 — a real
  grammar-level constraint, not post-hoc parsing. `tool_choice: "auto"` on vLLM is exactly as
  unconstrained as Ollama, so this only helps roles that should NEVER produce a text-only turn.
  That description matches this project's single most-repeated small-model failure exactly:
  Builder/FindingsWriter's "narrate instead of write" bug (Bonsai-8B, `qwen2.5:3b-instruct`,
  `qwen3:8b`, all disqualified partly or wholly for this reason — see their bake-off entries
  below). `tool_choice: required` would structurally prevent that failure class outright for those
  two roles specifically, rather than detecting and salvaging it after the fact
  (`_salvage_narrated_report`). The Planner itself is NOT a candidate for this — it must be free to
  choose between delegating and stopping with plain text, which `required` forbids entirely.
  **Real cost, not glossed over**: needs a working vLLM instance serving Builder/FindingsWriter
  specifically while the Planner stays on Ollama — a mixed-backend architecture, not a config flag.
  Standing up vLLM on this card was genuinely fragile this session (4 crash-fix cycles: missing
  OpenMPI/hwloc/libevent, then a version-mismatched hipBLASLt segfault only resolved once the `.so`
  and its Tensile kernel data came from the same `.deb` — see the vLLM investigation entry below
  for the full resolution chain). A persistent venv (`~/.venvs/vllm`, ~10GB on root) and the
  working env-var recipe (`HIP_VISIBLE_DEVICES=0`, `LD_LIBRARY_PATH`, `HIPBLASLT_TENSILE_LIBPATH`)
  are kept from this session for a future prototype. **User decision 2026-07-19: fine-tuning stays
  the priority (already scoped, proven once); this is a candidate to prototype later, not blocking
  current work** — the cheapest first test would be standing up vLLM for ONE already-disqualified
  small model in the Builder/FindingsWriter role only, with `tool_choice: required`, against the
  exact benchmark query that disqualified it, before investing in a full mixed-backend build.

  **Fresh data point + literature caveat, 2026-07-29**: a findings/report-writing diagnosis session
  (prompted by a direct request to find out whether the writer turn is structurally overwhelming
  these models, not assume it) reconfirmed this exact failure class live — `qwen3-4b-combined-v2
  -lora`'s verbatim narration ("Since I cannot write or edit files directly, I will describe the
  content...") — and it survived every OTHER fix made that same session (URL-case grounding
  false-positive, retry-budget bonus, quota starvation), none of which touch narration avoidance
  at all, confirming this remains the single most direct fix for that specific failure shape.
  BUT: two real caveats surfaced this session, not previously in this entry's own citations:
  (1) arXiv:2606.25605 ("Constraint Tax in Open-Weight LLMs," already cited elsewhere in this
  project) documents forcing required fields via constrained decoding can make a model
  **fabricate a plausible-sounding value instead of narrating uncertainty** when it doesn't
  actually know the answer — `tool_choice: required` could trade "narrates instead of writing"
  for "writes, but confidently fabricates a citation," arguably worse for a project whose
  grounding checks specifically hunt fabricated citations. (2) vLLM's own tool-calling docs/RFC
  #39848 confirm `tool_choice: required` enforces a JSON schema via guided decoding, and
  explicitly warn a model expecting a different native format (e.g. XML) gets forced into JSON
  with possible performance degradation — a concrete risk for THIS project specifically, since
  Ornith and other Qwen3.5-architecture candidates use a native XML-style tool-call template (the
  same family whose `PARSER`/`RENDERER qwen3.5` corruption bug was root-caused and fixed the same
  week). **Any future prototype of this fix must test specifically against the candidate model's
  native tool-call format, not assume JSON-schema forcing is free.**

- **Strategic options for the "no small local model is reliable enough" gap** (decided 2026-07-18,
  after the bake-off reached 10 tried candidates, 9 disqualified — full trial history in the
  "Model bake-off & backend investigation log" section below). The project's own stated local-only
  philosophy is already satisfied — `gpt-oss:20b` at 13GB, comfortably inside a 16-17GB VRAM
  budget, is the one candidate with a full benchmark pass. The real open question is whether a
  LIGHTER default is achievable, given every smaller candidate has failed at agentic coordination
  specifically, not raw single-tool-call capability. **External validation, merged from the SOTA
  literature review (`RESEARCH.md` §1, 2026-07-20)**: this project's own bake-off pattern (every
  2-8B candidate disqualified, `gpt-oss:20b` the only pass) is not an idiosyncratic gap — a
  published capacity-floor study (arXiv:2601.16280, invoice-reconciliation tool-use, admittedly a
  narrower/more controlled domain than DeepDelve's own) found `qwen2.5:14b` as the "minimum viable
  production" threshold for reliable tool invocation, with `qwen2.5:3b`/`7b` failing at 86.1%/42.7%
  rates. Two constraint-tax papers (arXiv:2606.25605 + arXiv:2605.26128) independently found the
  failure is specifically at STRUCTURED SERIALIZATION (schema-valid output, wrong content) — and
  that a 6,000-sample SFT run could not fix it, because it happens downstream of anything
  fine-tuning touches. Together: don't expect a lighter default to fully close this gap via more/
  better fine-tuning data alone — see the new "Non-generative routing classifier" Planned item
  above, which targets the routing sub-problem specifically because it's the piece that generative
  fine-tuning structurally can't guarantee. Four options, in the order agreed to try them,
  1-2 now DONE and tested, 3 still genuinely open:
  1. **Structural fix instead of a new model — DONE.** The immediate narration-salvage fix (see
     "Completed" above) — correct and shipped, but on live re-test didn't rescue its motivating case
     (`qwen2.5:3b-instruct` returns genuinely empty responses, nothing to salvage). Full result in
     the investigation log below.
  2. **Heterogeneous role tiering — DONE, real negative result, and CLOSED as a strategy
     (user decision, 2026-07-21): not worth retrying with any other small-model pairing.**
     Implemented (`settings.specialist_model`, `src/engine/orchestrator.py`) and live A/B tested:
     4.2x SLOWER than plain `gpt-oss:20b` and the report silently dropped the query's main topic.
     The negative result isn't specific to `qwen3:4b` — it follows from `gpt-oss:20b` never being
     unloaded between specialist dispatches (VRAM probe, investigation log below), so pairing it
     with ANY smaller specialist model competes for the same fixed VRAM budget rather than freeing
     any of it. Given that, the user does not want this pairing pursued further with a different
     small model either (explicitly including MiniCPM5-1B, see its entry below) — the mechanism
     only makes sense again if a future candidate can fully REPLACE `gpt-oss:20b` as a standalone
     single model across all roles, not sit alongside it as a lighter specialist tier. Code kept
     (reusable) for that different scenario, not adopted as a default, and not queued for further
     specialist-pairing retests. Full implementation notes, VRAM probe, and A/B result in the
     investigation log below.
  3. **Targeted fine-tuning (SFT + GRPO) of an existing small checkpoint — PREP DONE, training not
     started.** NOT training a foundation model from scratch, which would be disproportionate to a
     coordination/instruction-following gap on top of an already-capable base. Scoped in the
     "Fine-tuning" section's GRPO entry (now DEFERRED, see that section): target `qwen3:4b`, reward function built around its specific
     documented failure (`thin_coverage` non-convergence). **`finetune/reward.py` and
     `finetune/extract_dataset.py` built and validated against real run logs 2026-07-18** (5 real
     examples extracted so far; public-dataset supplementation researched — see the Stretch entry
     for the full recipe). The actual GPU training environment (venv-must-be-on-root-ext4, ~13GB+)
     still waits on the user's own disk reorganization — the next concrete action once that's done.
  4. **Stay on `gpt-oss:20b` as-is** — the fallback baseline that's already true today regardless
     of how far 1-3 get: nothing is actually blocking the project's local-only goal right now.
  5. **RAG-augmented small model — raised by the user 2026-07-20, not yet scoped.** Initially
     framed as "identify what made the user's prior RAG attempt fail" without knowing the specifics
     — **found the actual prior attempt already documented in this same "Rejected"
     section below, and it's IN THIS PROJECT, not a different one**: `src/utils/knowledge_cache.py`
     (deleted commit `929b987`, 2026-07-11). Confirmed via git history
     (`session_status/2026-07-13.md`): **it wasn't real RAG at all** — no embeddings, no chunking,
     no vector retrieval, just an exact-string-match `{normalized_question: answer}` JSON cache
     plus a coarse keyword-heuristic "experience" cache of past successful plans (DelveAgent's
     Dual-Granularity Memory pattern, arXiv:2606.18648). **The actual failure was narrower and more
     specific than a general RAG problem**: during model bake-off benchmarking, a LATER model's
     trial would hit the SAME cached "verified" answer from an EARLIER model's trial on the same
     query and reproduce it near-verbatim — invalidating independent A/B comparison between
     candidate models entirely (you'd think the later model performed well, when it just copied the
     earlier one's cached answer). This is a benchmark-isolation bug, not a retrieval-quality,
     hallucination, or embedding problem — the classic RAG failure taxonomy (see RESEARCH.md §8)
     mostly doesn't apply to what actually broke here.
     - **RESEARCH.md §8, 2026-07-20**: separately researched real RAG literature (3 primary
       sources: a peer-reviewed 33-mode RAG failure taxonomy, an agentic-RAG architecture survey, a
       small-language-model agentic-systems survey) before this git-history discovery landed.
       Headline findings: (1) DeepDelve, already multi-agent, would land in "Agentic RAG" — the
       taxonomy's own finding is this is the LEAST empirically validated RAG category (all 8
       agentic failure modes have zero peer-reviewed evidence); (2) two of those unstudied agentic
       failure modes (Recursive Hallucination Cascades, Unbounded Cost/Latency Spirals) are
       near-exact matches for bugs DeepDelve already found and fixed independently (the
       narrated-report/phantom-document bug, today's MiniCPM quota-exhaustion loops); (3) the SLM
       survey's own ablation data shows grammar/schema-constrained decoding, not RAG or model size,
       is the most load-bearing lever for small-model tool-use reliability — directly reinforcing
       the still-open "Forced `tool_choice` on vLLM" candidate above as a more targeted fix for
       today's actual observed failures than RAG would be.
     - **Combined implication**: real RAG (embeddings/chunking/vector retrieval, unlike the deleted
       cache) is architecturally a DIFFERENT thing than what failed before, so the old rejection
       doesn't automatically block it — but ANY persistent cross-run cache, real-RAG or not, must
       be explicitly disabled or isolated per-model during comparative benchmarking, or the EXACT
       same contamination bug recurs regardless of what retrieval technique sits underneath it.
       That's the one concrete, non-negotiable design constraint from this project's own history.

- **TUI QoE improvements** (researched 2026-07-14, not yet scoped/implemented) — triggered by a
  real usability complaint mid-Phase-6 smoke test ("copying from the console, not only the
  prompt", right-click paste, "a lot of QoE changes"). Investigated the actual installed Textual
  8.2.8 source (not assumed from memory) rather than guessing at framework capabilities:
  - **Likely already works, needs live confirmation, not new code**: click-drag text selection +
    `Ctrl+C` copy — `ALLOW_SELECT = True` is the framework default at `Widget`/`Screen`/`App`
    level, and `Screen.BINDINGS` already binds `ctrl+c` → `action_copy_text`
    (`textual/screen.py`); `BasicTuiAgent` doesn't override any of this.
  - **Unused framework capabilities surfaced, not yet scoped into concrete work**: command palette
    (`ENABLE_COMMAND_PALETTE`, `Ctrl+P`, separate from the hand-built `/`-command `OptionList`
    picker); widget maximize/minimize (`action_maximize`/`action_minimize`, blow up one
    `RichLog`/`AgentMessageWidget` to full-screen); theming system (`register_theme`/
    `available_themes` — currently one fixed CSS theme); `textual.suggester.Suggester`/
    `SuggestFromList` (inline autocomplete-as-you-type, vs. the hand-rolled `_render_cmd_list`
    filtering); `notify()` toasts (used only in copy-error paths today — could surface background
    events, e.g. a sub-agent finishing while scrolled away); unused built-in widgets that map onto
    real needs (`Tree` for `_todos.md`'s plan or the workspace file list; `DataTable` for fetched-
    source metadata; `TabbedContent` to split findings/report/sources instead of one scrolling
    feed; `SelectionList` for multi-file/multi-seed-URL picking).
  - **Explicitly deferred, not scoped into a phase yet** — user chose to record as a backlog item
    rather than implement immediately, given Phase 6 (now shipped, see "Completed") and the model
    bake-off (see the "Model bake-off & backend investigation log" section) were the priority at
    the time. The two smallest, most directly user-requested items (`AgentMessageWidget` copy
    button + right-click paste) have since shipped — see "Completed". Next session should scope a
    concrete subset of the remaining framework-capability survey items, which need real
    prioritization first.



- **VERIMAP Phase 2 — CLOSED as documented no-go, 2026-07-29, real live-run evidence gathered,
  not left open on a "need more data" pretext the data itself now contradicts.** Phase 1
  (`_update_task_verification`/`check_task_verification_flagged`, `src/engine/completion.py`) was
  deliberately left deferred pending real data on whether `task_verification_flagged` recurs often
  enough to justify redispatching ONLY the flagged task directly (bypassing the Planner's own turn,
  similar in spirit to how `_dispatch_writer_review_fix` bypasses the Planner for artifact fixes) —
  a real `run_completion_check` dispatch-loop rework, not an additive ledger. An audit of every
  `research_output/` run where the flag actually fired (K-Pg boundary cluster 2026-07-26,
  deep-learning-vs-2008-crisis cluster 2026-07-27, `qwen3-4b-combined-v2-lora` 2026-07-28) found
  **zero clean, unconfounded cases** where the same task recurred 3+ times under a normal retry
  budget with the Phase-1 nudge genuinely failing to resolve it. Every observed recurrence traced
  to an independent, already-fixed root cause instead: quota exhaustion made the directive
  structurally impossible to follow (fixed by making the check quota-aware) or a stale-task-rename
  loop (fixed by adding the `superseded` ledger status) — and post-fix reruns on the same queries
  show the flag resolving in 0-1 nudges. **Verdict: no-go for now.** Revisit only if a future clean
  run (normal retry budget, no other confound) shows the identical task recurring 3+ times with the
  Phase-1 directive genuinely unable to resolve it — that specific trigger, not elapsed time or
  general "more data," is the reopen condition.

### Candidates from the 2026-07-12 reference-repo review (see README References)

### Candidates from the 2026-08-01 reference-repo survey (`RESEARCH.md` §16 — full detail there)

Applied already, not just noted: `open_deep_research`'s per-comparison-subject task-naming rule
(`src/prompts.py`'s `PLANNER_INSTRUCTIONS`, ~line 195) and the `web_search_backend` provider
abstraction (`src/tools/web.py`, config-driven Tavily/Brave/ddgs selection invisible to the model
— GPT Researcher's `Retriever` interface was the prompt to look at this, though the actual
implementation is a lighter config-branch, not a full class hierarchy — 3 providers doesn't
justify one). Noted here as candidates, deliberately NOT implemented this session (bigger
architectural changes, or judged not to add real value over what DeepDelve already does):

- **STORM's persona-diversity facet discovery** (`stanford-oval/storm`,
  `knowledge_storm/storm_wiki/modules/persona_generator.py`/`knowledge_curation.py`): generate N
  diverse "perspective" personas grounded in related-topic reference structure BEFORE
  decomposition, each driving its own research thread via simulated Q&A. The most structurally
  different, best-grounded idea surveyed for DeepDelve's still-open task-naming/facet-collapse
  problem (item 0, `session_status/CURRENT.md`) — not adopted because it's a genuinely new
  pre-Planner pipeline stage, not a tweak to the existing one. Revisit if the prompt-level fix
  above (already applied) turns out insufficient on live re-test.
- **GPT Researcher's `SourceCurator`** (`gpt_researcher/skills/curator.py`): a dedicated LLM pass
  that ranks/filters sources by credibility before the writer sees them. Considered, not
  recommended: it's pure LLM self-judgment with no structural backing (falls back to the unranked
  list on any parse failure) — DeepDelve's existing grounding pipeline already does something
  stronger downstream (NLI entailment, cross-source contradiction, stub-fetch rejection), so
  adding a weaker upstream judgment call would be redundant, not additive.
- **GPT Researcher's defensive multi-strategy structured-output parsing**
  (`gpt_researcher/skills/deep_research.py:48-116` — `json_repair` → regex-line-fallback cascade):
  a real hardening pattern, but not applicable to DeepDelve's current architecture — native
  tool-calling schemas are framework-validated, not free-text-parsed, so this class of problem
  mostly doesn't arise here. Worth remembering if a future DeepDelve code path does need to parse
  free-text model output by hand.
- **Host-driven shrinking-budget iteration** (`dzhng/deep-research` and GPT Researcher's own
  `deep_research.py` lineage — breadth halves every recursion level, LLM never decides "should I
  search more"): a genuinely different fix family from the hard replan-round cap already shipped
  (`RESEARCH.md` §15, `max_planner_delegate_rounds`) for the SAME problem class (a model that
  over-exercises iteration authority) — cap the model's authority (shipped) vs. never grant it in
  the first place (this). Not adopted: would mean rearchitecting the Planner's `ADAPTIVE PLANNING
  LOOP` away from genuine model-driven adaptation, a real capability trade-off, and the cap already
  shipped is confirmed live-working (RESEARCH.md §15's live-test: 6 rounds → 4, no rejections
  needed). Recorded as a fallback option if the cap alone ever proves insufficient the way the
  fetch-cap-and-cutoff-wording fix alone did.

## Rejected


- Large/small model dispatcher: rejected 2026-07-11 — benchmark showed small models fail sub-agent reasoning (nemo 2/10); revisit only if a small model scores ≥5 on the Colombia rubric solo.
- Knowledge cache (any backend): rejected — poisoned benchmarks/grounding; deleted in commit 929b987; do not reintroduce.
- **Bibliographic-API citation verification** (Semantic Scholar/OpenAlex/Crossref/arXiv, from
  `imbad0202/academic-research-skills`): rejected as a bundled default for the academic output
  mode — a genuinely stronger check than DeepDelve's own fetch-based grounding for *published*
  academic sources, but adds an external API dependency (rate limits, another failure mode to
  handle) for a benefit that only applies to formal papers, not the market-research/general-web
  sources most DeepDelve runs actually cite. Revisit as an opt-in flag specifically for
  `--style academic` if that mode's own fetch-based grounding proves insufficient in practice.
  **Re-researched properly 2026-08-17** (prompted by the day's own citation-misattribution
  incident, to check whether this rejected feature would actually have caught it) — 2 real,
  primary-source-read papers plus 3 GitHub reference implementations, not a re-guess from memory:
  - [*CiteCheck: Retrieval-Grounded Detection of LLM Citation Hallucinations in Scientific
    Text*](https://arxiv.org/abs/2605.27700) (Khajavi, Sadeghi, Adhikari, Tessier — Simon Fraser
    University, 2026, `papers/citecheck_2605.27700.pdf`, read in full) — a real, rigorous
    evaluation (982-citation physics benchmark with controlled corruptions, 88.7 macro-F1 /
    88.9% accuracy, beats GPT-5.4/Claude Sonnet 4.6/Gemini 2.5 Flash zero-shot and few-shot
    baselines including their own web-search variants). Its own three-class taxonomy — EXACT,
    MINOR (a real, recognizable paper with corrupted metadata — wrong author/year/DOI/URL), MAJOR
    (unrelated or fully fabricated) — maps cleanly onto this project's own incident history: the
    2026-07-13 GAVEL fake-DOI incident (below) is a MAJOR hallucination; today's citation-
    misattribution bug is closer to MINOR in spirit, but isn't actually the same failure class
    (see the disqualifying limitation below).
  - **The paper's own explicit limitation is the deciding fact, in its own words (§7,
    Conclusion)**: *"C ITE C HECK verifies citation existence and metadata fidelity, not whether
    the cited source supports the surrounding claim."* This directly disqualifies bibliographic-
    API verification as a fix for today's actual incident — that bug was two REAL, EXISTING,
    correctly-fetched web pages with a claim attached to the wrong one, not a fabricated or
    metadata-corrupted paper. Bibliographic-API checking answers "does this cited THING exist and
    is its metadata right," never "does THIS specific source's content actually say what's
    claimed" — the second question is what `find_unsupported_specific_figures`/`nli_unsupported_
    problem` (this project's own existing checks) already target, and they apply to ANY source
    type, not just DOI/arXiv-indexed academic papers. **Confirms the original rejection reasoning
    was correct, now with primary evidence instead of just an inference.**
  - [*CheckIfExist: Detecting Citation Hallucinations in the Era of AI-Generated
    Content*](https://arxiv.org/abs/2602.15871) (Abbonato, University of Turin, 2026,
    `papers/checkifexist_2602.15871.pdf`, read in full) — a lighter systems paper (open-source
    MIT-licensed tool, no rigorous benchmark numbers unlike CiteCheck), confirms the same
    cascading CrossRef→Semantic Scholar→OpenAlex architecture and that all three APIs are usable
    with NO API key on free tiers (800ms rate-limit interval used to stay compliant).
  - **3 GitHub implementations checked for real technical feasibility** (not guessed): the
    author's own tool ([`zabbonat/References-Validation`](https://github.com/zabbonat/References-Validation),
    28 stars, TypeScript, live web app), [`Vikranth3140/Citation-Hallucination-Detection`](https://github.com/Vikranth3140/Citation-Hallucination-Detection)
    (a real AAAI 2026 student-abstract companion repo — its own README's DOI badge
    `10.1609/aaai.v40i48.42257` verified live against CrossRef's actual API during this research
    pass, resolves to the real paper, correct authors — a fittingly self-referential clean
    citation check), and [`PHY041/claude-skill-citation-checker`](https://github.com/PHY041/claude-skill-citation-checker)
    (28 stars, a single ~720-line Python script, `requests`-only dependency, no API key, tested by
    its own author at 90% known-good/100% known-bad/100% chimeric-detection/0% false-negative —
    an informal author self-test, not a rigorous benchmark like CiteCheck's, but a real, small,
    low-risk reference implementation if this is ever built).
  - **One genuinely new, low-risk scope this research surfaced, not previously considered**:
    DeepDelve already auto-generates `references.bib` for every run
    (`utils/run_state.py::build_bibliography`, wired into both the TUI and `GET /research/{id}
    /references.bib` in the API). A standalone, OPT-IN, post-hoc script that verifies an
    already-completed run's `references.bib` against these free APIs (mirroring the
    `claude-skill-citation-checker` shape exactly) would add ZERO latency/complexity/new failure
    surface to the live agent pipeline — a real, meaningfully smaller-scoped variant of the
    originally-rejected "build it into the live grounding pipeline" idea. Still not started (no
    demonstrated need yet — no live DeepDelve run has ever produced a MAJOR-class fabricated
    academic citation), but the standing "revisit if academic-mode grounding proves insufficient"
    condition now has a concretely scoped, low-risk answer ready if it's ever triggered, instead of
    an open-ended "build bibliographic verification" task.
- **`SkyworkAI/DeepResearchAgent`** (reviewed 2026-07-12): a general self-evolution agent runtime
  (RSPL/SEPL protocol layers, RL-based prompt/solution optimizers, versioned tracing) with example
  agents for trading/ESG/mobile — not a deep-research-specialized project despite the name.
  Rejected: same reasoning as the existing "no DI framework, no plugin system" stance above: its
  tracing/versioning goal is already served by `_run_state.json`, and its optimizer/self-evolution
  loop is out of scope for a project explicitly avoiding RL infrastructure outside the
  "Fine-tuning" section above (itself now deferred indefinitely).
- **Fabricated/misattributed sources caught during the 2026-07-13 3-model research pass** —
  recorded so a future session doesn't re-trust them without re-checking: a "GAVEL: Evidence-
  Contract Debate with Mechanized Scrutiny" paper with a fake ACL-2026-Findings DOI does not exist
  anywhere (checked directly, zero hits). Separately, one of the three responses attached invented
  mechanisms to two *real* papers it likely never actually read: it claimed `arXiv:2603.18000`
  (AgentFactory) describes a disk-quota/`task_uuid` workspace-isolation mechanism — the real paper
  is about reusable sub-agent code, no quota mechanism anywhere in it — and separately claimed a
  real TechRxiv paper (Piskala, *Agent, Sub-Agent, Skill, or Tool?*) describes a "Try-Catch-
  Critique" 1B-parameter tool-error classifier — the real paper is an orchestration-pattern
  taxonomy (tool-centric/hierarchical/decentralized), no such mechanism anywhere in it. That
  response's citations were <25% reliable on direct inspection; its other two ideas (cross-encoder
  reranking, Gemma 4 12B) happened to be individually sound but were not verified by that response
  itself — treat as unsourced until independently re-checked, which is what happened before either
  was added to "Pending" above.
- **`platoyaoxu/pdfdownload`** (reviewed 2026-07-14, user-supplied link, directly relevant given the
  same-day ScienceDirect/Cloudflare Turnstile investigation above): a personal Elsevier/ScienceDirect
  batch PDF downloader — `DrissionPage` opens each DOI in a real visible Chromium tab, a companion
  `AutoClick.py` subprocess does OS-level `pyautogui` screenshot/template-match clicking (real mouse
  input, not CDP-synthetic) against user-supplied PNGs of the Cloudflare checkbox and the download
  button, with a human physically present to solve anything the templates can't handle. Confirms our
  own finding from the same investigation: it's very plausibly beating Turnstile specifically because
  `pyautogui` drives genuinely trusted OS-level input events, not CDP's synthetic `Input.dispatchMouseEvent`
  — a more fundamental distinction than `navigator.webdriver` or Playwright-vs-DrissionPage as
  libraries. **Not adopted, on the same principle already applied to ScienceDirect above**: its entire
  purpose is defeating anti-bot protection to bulk-scrape copyrighted publisher content (the repo's
  own `.gitignore` excludes downloaded PDFs "copyrighted & large," so the author knows what this is) —
  that doesn't belong in DeepDelve's default fetch path even though the "real trusted input" technique
  is a genuinely interesting, confirmed data point. Secondary code-quality notes for the record, not
  actionable for us: no timeout anywhere in either the click-watch loop or the download-wait loop (a
  wrong screen resolution or an inaccessible paper hangs the whole batch indefinitely), and the
  `images/` template folder it depends on isn't shipped in the repo, so it isn't runnable as-is.

## Stretch


**LLM fine-tuning/GRPO work RESUMED 2026-07-28** (was paused indefinitely since the 2026-07-21
ROADMAP reorg — see full result in `History`'s "2026-07-28: Fine-tuning resumed" entry). The
`qwen3-4b-combined-v2-lora` comprehensive 7-dimension round trained successfully and passed its
held-out overfitting check. **The hard gate below still applies going forward** — nothing under
`finetune/artifacts/`/LoRA output dirs gets touched (moved, cleaned up, "freed for space," etc.)
without the user's explicit go-ahead each time, same reason as before (a prior checkpoint was lost
to an assistant suggestion the user considered a mistake). The "one combined base, never an
isolated LoRA" methodology rule below was honored fully this round (all 7 documented+newly-added
reward dimensions in one retrain, not partial). **Still not done**: this round's own live
validation (see the History entry's "NOT YET DONE" note) — do not start a NEXT fine-tuning round
until that's resolved one way or the other.

- **STANDING METHODOLOGY RULE (2026-07-19, engraved after real cost this month): every new
  fine-tuning objective folds into ONE combined multi-objective GRPO retrain off the same raw base
  checkpoint (`Qwen/Qwen3-4B`) — never a separate, isolated single-purpose LoRA.** Separately-
  trained LoRA adapters cannot be merged/stacked cleanly: `thin_coverage` was trained as its own
  LoRA first, then `citation_grounding` as its own separate LoRA, and both had to be superseded by
  a single combined LoRA (`finetune/train_combined_grpo.py`) once it became clear a model can only
  actually deploy with ONE LoRA active — a `thin_coverage`-only model knows nothing about
  citation-grounding and vice versa. The combined approach is CONFIRMED to work, not just assumed:
  both objectives improved together on held-out eval when trained jointly (0.681→0.903 at 130
  steps, 0.375→0.806 at the final 470-step run — see the 2026-07-19 session log for the full
  chronology). Before starting any new round (e.g. `writer_role_response_reward`, already has 54+
  real examples in `finetune/data/writer_role.jsonl`, never yet trained): extend `finetune/reward.py`'s
  existing combined reward function with the new dimension, retrain from the SAME raw base with
  ALL objectives' prompts combined (not just the new one), re-evaluate every dimension together on
  held-out data to confirm no regression, and redeploy as a single new combined artifact replacing
  the prior one — never ship multiple single-purpose model tags alongside the combined one.

- **GRPO training-methodology levers, merged from the SOTA literature review (`RESEARCH.md` §1,
  2026-07-20) — apply before the NEXT combined retrain, not a new isolated round.** Three concrete,
  actionable findings from "Demystifying Reinforcement Learning in Agentic Reasoning"
  (arXiv:2510.11701, read and verified — its own 4B/7B target class is exactly DeepDelve's own):
  1. **Data**: real end-to-end tool-use trajectories give a far stronger SFT initialization than
     stitched synthetic ones — directly validates this project's own existing preference for real
     extracted session data (`thin_coverage.jsonl`, `writer_role.jsonl`) over synthetic prompts, and
     flags the synthetic-prompt-generation fallback used for `thin_coverage` (low real-example count)
     as a real, named limitation worth reconsidering once more real examples accumulate.
  2. **Algorithm**: conservative clipping and strong KL-divergence penalties over-constrain
     exploration during GRPO, especially for smaller models — sustaining higher policy entropy
     improves training efficiency. A concrete, testable hyperparameter change for
     `finetune/train_combined_grpo.py`'s next run, not yet tried.
  3. **Reasoning mode**: a deliberative strategy (more internal reasoning, fewer tool calls)
     outperforms frequent tool calls or verbose self-reasoning — plausibly explains why
     `gpt-oss:20b` (visible `<think>` traces) passed the bake-off while smaller candidates with
     little internal reasoning failed more. **Untested, concrete next step, no new reading
     required**: check whether DeepDelve's own bake-off logs show disqualified small models
     producing shorter/absent `<think>` traces before failed tool calls, using data already on hand
     in `research_output/`/session logs.

  **Separately, a scoping caveat for `writer_role_response_reward` specifically** (already has 54+
  real examples, never yet trained): AXPO (arXiv:2605.28774) looked like a direct match for this
  exact reward shape (tool-call token is the sparse, high-value action under GRPO, same shape as
  `write_workspace_file`) but its "recovery indicator" mechanics were built for a binary
  ground-truth-correctness reward signal, not DeepDelve's structural "was the tool actually called"
  signal — **check this mismatch against `finetune/reward.py`'s actual implementation before
  adapting AXPO's specific resampling mechanism**; the underlying insight (concentrate exploration
  budget at the sparse action boundary) likely still applies even if the exact mechanics don't
  transfer as-is.

- **RL fine-tuning for tool-call reliability** (GRPO/PPO on the actual Planner/Searcher schema) —
  targets the fetch-skipping/tool-call-reliability root cause directly instead of catching it
  after the fact. Not started, but a quick research pass (2026-07-14) found this more feasible on
  this hardware than "needs real training infrastructure" implied:
  - **Hardware is viable now**: the RX 9060 XT got official ROCm 7.0.2+ support this year; PyTorch
    installs via pip with ROCm support and trains "out of the box" per AMD's own docs. Real
    caveat: AMD's own tooling/docs primarily target MI-series datacenter cards, consumer RDNA
    support (including RDNA4) is "real but secondary" — expect rougher edges than a straight
    NVIDIA path.
  - **Unsloth has an AMD-maintained GRPO integration**, with AMD's own official ROCm AI Developer
    Hub tutorial for GRPO training an 8B model. VRAM-wise: Qwen3-1.7B GRPO fits in ~5GB, 7B/8B fits
    comfortably in 16GB — maps directly onto `qwen3:4b`, a model already in this project's own
    bake-off (currently "inconclusive," pending a longer test run) and already sized well within
    the proven budget.
  - **GRPO needs a verifiable reward, not an LLM-judge score** — for tool-call reliability
    specifically, that reward is cheap to build from infrastructure this project already has:
    valid `delegate_tasks` schema, a real registered tool name (not hallucinated — exactly
    `tool_result_error_nudge`'s existing error patterns), `write_workspace_file` actually called
    when required. Research suggests ~1,000 good examples / a few thousand prompts + a
    programmatic verifier is enough — this session's own bake-off/benchmark run logs could
    plausibly seed real (task, correct-tool-call) examples rather than hand-authoring a dataset
    from scratch.
  - **HANDS-ON SMOKE TEST DONE 2026-07-14 — GRPO CONFIRMED WORKING on this exact GPU.** Real
    breakthrough, not just research: built a bare venv (`~/.venvs/rocm-grpo-test`, NOT `/mnt/
    nuevovol` — `python3 -m venv --copies` still fails there, NTFS has zero symlink support even
    with `--copies`, so venvs must live on root/ext4; pip cache and HF model weights CAN and
    should go to the NTFS mount to protect root's limited space, same pattern as `HF_HOME`
    already in `~/.bashrc`). Installed `torch==2.10.0+rocm7.0` (`pip install torch --index-url
    https://download.pytorch.org/whl/rocm7.0` — NOT rocm6.4, which resolves but is the wrong
    generation for this card) + `transformers`/`trl`/`peft`/`accelerate`. `torch.cuda
    .get_device_properties(0).gcnArchName` correctly reports `gfx1200` (RDNA4) — no Docker, no
    NTFS/containerd blocker at all (that constraint was specific to Docker's overlayfs
    snapshotter, not to a native pip/PyTorch install, which is just regular files).
    - **One real blocker hit and fixed**: `GRPOTrainer`'s `accelerate` auto device-mapping tried
      to shard the model across BOTH GPUs on this machine — the discrete RX 9060 XT (`gfx1200`,
      in PyTorch's compiled `get_arch_list()`) AND the Ryzen iGPU (`AMD Radeon Graphics`, some
      RDNA2 arch NOT in that compiled list) — crashing with `torch.AcceleratorError: HIP error:
      invalid device function` inside a trivial RoPE `.float()` cast, the multi-GPU auto-split
      being the actual cause, not the op itself. **Fix**: `export HIP_VISIBLE_DEVICES=0` before
      launching, isolating just the discrete card. Confirmed the iGPU was the culprit by first
      verifying plain `AutoModelForCausalLM.generate()` (no accelerate multi-device logic)
      already worked fine even without this env var.
    - **Real GRPO training ran end-to-end on the GPU**, 2 steps, `Qwen/Qwen2.5-0.5B-Instruct`, a
      toy arithmetic task with a mechanical/verifiable reward (exact-match on the correct
      answer): reward mean went 0.5 -> 1.0 across the 2 steps (real policy-gradient improvement,
      `grad_norm` nonzero on step 1), ~5 seconds total train time. Full working script preserved
      at the bottom of this entry for the next session to reuse directly.
    - **Real disk cost, budget for it next time**: root hit 1.7GB free / 99% used by the end (the
      venv alone was 13GB, entirely on root — there is no way around this given NTFS's symlink
      limitation) — same emergency shape as the earlier vLLM investigation. Cleaned up
      immediately after the test (`rm -rf ~/.venvs/rocm-grpo-test`), back to ~20GB free. The pip
      cache (9.4GB) and HF model cache were left on the NTFS mount (`/mnt/nuevovol/Projects/AI
      shit/LLvm Models/{pip-cache,huggingface}`) specifically so a future re-run reinstalls from
      cache instead of re-downloading the ~5GB torch wheel — but the venv itself will need to be
      rebuilt on root again, and root is the tight resource here (only ~20GB free even at
      baseline), not VRAM or the GPU stack. **Scaling up to a real target model (`qwen3:4b`-class,
      not the 0.5B toy) needs a real plan for root disk budget, not just "install it again."**
    - **Conclusion**: the core technical risk (does GRPO training actually execute on this
      specific RDNA4 card without Docker) is RESOLVED, positively. What's left before a real
      fine-tune is scoping work, not more feasibility-proving: (1) root disk budget for a bigger
      model's venv+cache, (2) building the actual DeepDelve-tool-call verifiable reward function
      (not the toy arithmetic one here), (3) assembling/extracting the training dataset from real
      run logs, (4) deciding the target model (`qwen3:4b` is the natural first pick, already a
      known-decent tool-caller from the bake-off).
    - **Reusable smoke-test script preserved**: `session_status/scripts/grpo_smoke_test.py`
      (gitignored along with the rest of `session_status/`, but persists on disk across
      sessions). Loads `Qwen/Qwen2.5-0.5B-Instruct` via `trl.GRPOTrainer`, 4 toy arithmetic
      prompts x4 repeats, a `correctness_reward` function doing exact-match on the expected
      number, `GRPOConfig(max_steps=2, num_generations=4, per_device_train_batch_size=4,
      max_completion_length=8, bf16=False, fp16=False)`. Swap `MODEL`, the dataset, and the
      reward function for a real DeepDelve tool-call fine-tune. Recreate the venv (`python3 -m
      venv ~/.venvs/rocm-grpo-test` — NOT on `/mnt/nuevovol`), `pip install torch --index-url
      https://download.pytorch.org/whl/rocm7.0 && pip install transformers trl peft accelerate
      numpy`, `export HIP_VISIBLE_DEVICES=0` before running.
  - **Scoped fine-tuning plan (2026-07-18, written, not executed this session — vLLM/actual training
    both explicitly out of scope this session for disk/time reasons).** Target-model choice
    revisited: the "natural first pick" language above is now stale — `qwen3:4b` was conclusively
    DISQUALIFIED this session (see bake-off entry above, 2026-07-18 conclusive re-run), and the
    newly-tried `qwen3:8b` failed the exact same way. **Both failures are the same well-
    characterized, narrow behavior gap**, not a competence problem: real research happens (1-5
    sources fetched, real `delegate_tasks` dispatches, correct schema), but the model doesn't act on
    the completion-check's `thin_coverage` corrective nudge — it just repeats a canned "research
    scope is complete" response (or, for `qwen3:8b`, narrates the report as chat prose) until the
    retry budget exhausts. This is actually a BETTER fine-tuning target than a generic "make tool
    calls more reliable" goal: the failure is narrow, reproducible, and has a clear correct
    behavior to reward (either re-delegate with materially different instructions, or accept the
    gap and let the engine dispatch a writer for an honest partial artifact — never repeat the same
    refusal text twice).
    1. **Target model: `qwen3:4b`** (2.5GB base, smallest real VRAM footprint of any candidate that
       ever passed the tool-call smoke test, comfortable headroom for GRPO training alongside
       `gpt-oss:20b` staying loaded for inference/judging if needed — the smoke test already
       confirmed Qwen3-1.7B-class GRPO fits in ~5GB on this card). `qwen3:8b`'s identical failure
       shape means fixing `qwen3:4b` first is the cheaper experiment; escalate to the 8B base only
       if the 4B's capacity turns out to be the real ceiling, not the convergence behavior.
    2. **Reward function**, buildable entirely from infrastructure this project already has, no new
       schema needed: (a) valid `delegate_tasks`/tool-call schema compliance (reuse
       `tool_result_error_nudge`'s existing error-pattern catalogue as the negative-example
       source), (b) a real, non-hallucinated tool name, (c) **the specific new signal this
       session's failures motivate**: given a `thin_coverage`-shaped prompt in context, reward a
       response that either issues a NEW `delegate_tasks` call with instructions materially
       different from the just-failed task (not a repeat), or correctly stops delegating AND lets
       the engine's own Write-Review-Fix loop take over (no narrated `findings.md`/`final_report.md`
       content in the chat response itself — that's exactly what `qwen3:8b` got wrong). All three
       are programmatically checkable from `RunState`/session-log structure already captured, no
       LLM-judge needed.
    3. **Dataset sourcing**: this project's own `research_output/`/`eval/runs/` folders already
       contain real (prompt, tool-call, outcome) triples across dozens of runs and multiple models,
       including today's two freshly-logged `thin_coverage` failure transcripts as concrete negative
       examples and `gpt-oss:20b`'s successful re-delegation behavior on the same query as a
       positive one. Research cited in this same ROADMAP entry suggests ~1,000 good examples is
       enough — extracting and labeling from existing logs is very likely sufficient without hand-
       authoring new scenarios.
    4. **Disk budget**, learned directly from last session's smoke test: the training venv MUST live
       on the root/ext4 disk (`/mnt/nuevovol`'s NTFS mount has zero symlink support, breaks
       `python3 -m venv` even with `--copies`), costs ~13GB for a 0.5B toy run, so budget more for
       a 4B fine-tune (base weights + optimizer states + venv) and `rm -rf` the venv immediately
       after each run the way the toy smoke test already did — root disk currently has 63G free
       (`df -h /`, confirmed this session), comfortable margin.
    5. **Prep work done 2026-07-18 (data + reward code, per the user's own scoping split — the
       GPU training environment still waits on disk reorganization, everything else doesn't need
       to)**: new `finetune/` directory, real working code, not a plan doc.
       - **`finetune/reward.py`**: all three reward dimensions from item 2 above implemented as
         pure, dependency-free functions (`schema_compliance_reward`,
         `real_tool_name_reward`, `thin_coverage_response_reward`), each calibrated against the
         EXACT real examples this session's bake-off produced (llama3.2:3b's JSON-string `tasks`,
         qwen3:4b's literal canned-refusal text, qwen3:8b's narrated-report text) rather than
         synthetic cases. Self-test suite (`python finetune/reward.py`) passes. One calibration
         note: the re-delegation similarity threshold needed raising from an initial 0.6 to 0.8 —
         a real qwen3:4b reword ("top 5 heuristic algorithms... deep learning sales forecasting"
         → "top 5 metaheuristics for retail sales forecasting with real-world implementations")
         scored 0.607 on `SequenceMatcher`, which is a GOOD genuine re-scope, not a near-duplicate.
       - **`finetune/extract_dataset.py`**: pulls real (context, response) examples from this
         project's own history — `research_output/*/_run_state.json` (WHEN a `thin_coverage`
         problem fired) cross-referenced with `~/.deepdelve/sessions/session_*.json` (WHAT the
         model actually did next). The two aren't joined by any stored ID, so this matches by
         query-text prefix AND `_run_state.json`'s `started_at` proximity to the session's own
         start time — the first version matched by text alone and silently paired a run with the
         WRONG session (this project's own standing benchmark queries get re-run verbatim across
         many sessions/models, confirmed live: an early cut wrongly matched a 2026-07-18 run to a
         2026-07-14 session that happened to share the same first-prompt text). Fixed by requiring
         the closest session start at or before the run's `started_at`.
       - **Real run against the actual corpus**: 66 `research_output/` runs scanned against 84
         persisted session logs, 4 runs successfully matched, **5 real `thin_coverage` examples
         extracted**, reward-scored 2 positive / 3 negative on the current heuristics — small
         (expected: this project is only a few weeks old, and only `thin_coverage`-in-Planner
         cases are covered so far, not schema-compliance or writer-role-omission examples, which
         would need their own extraction logic), but end-to-end-real, not synthetic.
       - **Known rough edges, not fixed this pass** (prep, not the final training-set build): a
         few extracted examples are an intermediate `think_tool`/`write_todos` call rather than
         the model's eventual `delegate_tasks` decision or clean stop — the extractor currently
         grabs the FIRST Agent event after the nudge timestamp, not the first DECISION-shaped one;
         worth walking forward to the next `delegate_tasks`/text-only event instead. One extracted
         example's `response_text` field appears to contain a system notification string rather
         than genuine model output — needs a source-attribution check before trusting it as a
         training example.
    5a. **Dataset expansion, same day, before any training run — real bug found and fixed, then
        real diversity added two ways.** User pushed back that 5 examples "is really not a lot,"
        correctly.
        - **`writer_role_response_reward` added to reward.py** (a 4th dimension) and
          `extract_writer_role_examples` added to `extract_dataset.py`, matched by DISPATCH NAME
          (`SubAgent_{writer_role}Fix_attempt{N}`, exact — not the timestamp heuristic
          `thin_coverage` needs) rather than session-text correlation. Also added
          `extract_tool_name_examples`, mining `tool_error_samples` directly (the hallucinated
          name is already in the error text, no correlation needed for negatives).
        - **Real, live-found bug in the tool-name extractor, caught before trusting the data**:
          scoring `delegate_tasks` as a flat "real tool, so not hallucinated" name was WRONG —
          `Builder`/`FindingsWriter` structurally have no `delegate_tasks` tool at all (confirmed
          via `src/app.py`'s own `SubAgentConfig` definitions), yet `tool_error_samples` across
          multiple runs shows exactly this call being correctly rejected by the real engine. A
          flat check would have scored it as valid, and worse, would have put CONTRADICTORY
          labels on the identical string depending only on which role said it. Fixed by moving
          `ROLE_TOOLS`/`KNOWN_TOOLS` into `reward.py` itself (the same scoring logic that will run
          live during training, not a separate copy) and giving `real_tool_name_reward` an
          optional `role` parameter — role-known dispatches (writer-role fixes, the Planner's own
          "Agent"-sourced turns) are checked against their OWN tool list; role-unknown dispatches
          (generic Searcher/Analyzer labels, which reflect the delegating PARENT's task name, not
          the target agent_id) fall back to the flat union check. Verified zero contradictions
          across the full re-extracted corpus (spot-checked every (tool_name, role) pair).
        - **Full corpus re-scan after the fix**: 70 runs (4 new pilot runs added, see below) → 6
          `thin_coverage`, 54 `writer_role`, 210 `tool_name` real examples — up from 82
          (pre-fix, contradiction-containing) / 5 (original `thin_coverage`-only count).
        - **Live pilot batch (2 new topics — particle physics, pure math — × `qwen3:4b` +
          `gpt-oss:20b`, 4 runs total)**, deliberately run OUTSIDE this project's own two standing
          benchmark queries to test topic generality. Confirms the `thin_coverage` failure is
          topic-general, not sales-forecasting-specific: `qwen3:4b` produced a near-identical
          "No further tool calls needed... Stop here" premature-stop response on the brand-new
          physics topic. Yield was real but low: only 1 of the 2 new topics actually tripped
          `thin_coverage` (the math topic legitimately found real sources for both angles and
          converged cleanly — a valid, not a failed, outcome) — confirms `thin_coverage` occurrence
          is inherently unpredictable per-topic, not just query-design-dependent, making a
          live-run-only scaling strategy expensive (~35-40 min/run) for uncertain yield.
          **Also surfaced a real tradeoff**: `max_completion_check_attempts` was lowered from 8 to
          3 for this batch (to stop burning time on `qwen3:4b` retries that were never going to
          converge) — this caused a genuine false-negative on `gpt-oss:20b`'s math-topic run (17
          real sources fetched, but ran out of retries before Builder finished writing). Restored
          to 8 after the pilot; the tradeoff itself is now a documented, real data point, not a
          guess.
        - **Key realization, changes the whole scaling strategy**: GRPO doesn't need
          (prompt, correct_response) pairs the way SFT does — the model generates its OWN
          completions at training time, scored live by the reward function. The extracted
          RESPONSE data above is only needed to calibrate/validate the reward function offline
          (already done); what actually needs volume for training is PROMPT diversity, and a
          `thin_coverage` prompt is 100% deterministic, produced by
          `engine/completion.py::check_thin_coverage` from a `RunState`'s recorded findings —
          real production code, not something to reimplement. New
          **`finetune/generate_synthetic_prompts.py`** builds varied but realistic `RunState`
          scenarios (39 topics spanning science/medicine/law/economics/history/technology, none
          overlapping this project's own real run history) and calls the REAL
          `check_thin_coverage` function directly, capturing its REAL `Verdict.inject` text —
          zero GPU cost, zero fabricated nudge logic, only the SITUATION (which topics, which
          angles lack sources) is synthetic. Caught and fixed a real modeling bug while building
          it: a 2-task scenario with exactly 1 covered/1 uncovered task (50% ratio) does NOT trip
          the check (`check_thin_coverage` requires a majority missing, `ratio >= threshold`
          returns None, a deliberate design choice — a 50/50 split isn't "thin," it's a tie) — 10
          of the first 20 scenarios were written with this exact shape and silently produced
          nothing; fixed by giving every scenario a real 3rd uncovered task. **Output: 78 prompts
          (39 topics × first-occurrence + escalated-nudge variant) — genuinely real nudge text,
          kept in a clearly separate file
          (`finetune/data/thin_coverage_synthetic_prompts.jsonl`) from the real-response-mined
          dataset so the two are never conflated.**
        - **Total dataset now**: 348 lines across `finetune/data/` (6 real response-validation
          examples + 78 synthetic-scenario/real-code prompts for `thin_coverage`, 54 real
          `writer_role`, 210 real `tool_name`) — up from the original 5. The actual GPU training
          run (torch+ROCm+trl+peft venv, ~13GB+ on root) still waits on the user's own disk
          reorganization, per the session's agreed split between "prepare" and "train."
    6. **Public-dataset supplementation, researched 2026-07-18 — real, downloadable leads found,
       two-stage recipe confirmed sound.** Several genuinely downloadable multi-turn tool-calling
       corpora exist, largest/most current first: **`Agent-Ark/Toucan-1.5M`** (HF, 1.53M real
       trajectories from 495 live MCP servers, 2000+ tools, multi-turn/parallel/sequential — the
       best structural match to this project's own MCP-based tool ecosystem), **`Salesforce/
       APIGen-MT-5k`** (HF, 5K human-verified multi-turn trajectories, ShareGPT format, closest
       shape to `delegate_tasks`'s multi-turn conversation pattern), **`Salesforce/
       xlam-function-calling-60k`** (HF, 60K single-turn, Apache 2.0), **`MadeAgents/
       XLAM-7.5k-Irrelevance`** (HF, 7.5K examples of correctly NOT calling a tool — directly
       useful for `thin_coverage_response_reward`'s "clean stop, no narration" branch), ToolBench/
       ToolLLM (GitHub, 12K instructions/37K real RapidAPI calls, avg 4.1 steps/trace).
       **Re-planning/self-correction specifically (the `thin_coverage` scenario itself) has NO
       standalone public dataset** — Reflexion/WebArena/AgentBench publish trajectory
       code/environments, not a packaged retry-labeled corpus; the closest proxy is Hammer's
       irrelevance-detection subset above. This confirms this project's own 5 real extracted
       examples are more valuable than anything public for THIS specific reward dimension, even
       though they're far too few to carry general tool-calling reliability alone.
       **RLVR-for-agentic-tool-use papers**: nothing paper-and-dataset-bundled matches this
       project's exact reward shape; most public RLVR work is still math/code-verifier-centric.
       One 2026 lead, flagged "worth reading (not downloading)" and left unread for over a month
       — **read in full 2026-08-17** during a literature-completeness audit prompted by the user:
       "Multi-Turn Reinforcement Learning for Tool-Calling Agents with Iterative Reward
       Calibration" (Modecrua, Kaewtawee, Pachtrachai, Kraisingkorn — Amity Research and
       Application Center, arXiv:2604.02869, 9 pages, `papers/iterative_reward_calibration_
       2604.02869.pdf`, unreviewed preprint, no confirmed venue). Trains `Qwen3.5-4B` and
       `Qwen3-30B-A3B` — DeepDelve's own Qwen3 family — on Tau-Bench's realistic multi-turn
       tool-calling tasks (not math/code RLVR). **Directly actionable warning for this project's
       own future reward design**: naively-designed dense per-turn rewards CATASTROPHICALLY
       degraded performance by up to 14pp versus sparse outcome rewards, root-caused (§7.1, all
       three causes quantified) to advantage-direction misalignment under group-normalized RL —
       not a tuning mistake, a structural risk of dense/structural rewards specifically. Their own
       stated takeaway: **"dense rewards require calibration — always measure discriminative power
       before deploying."** This is a real caution for any future combined GRPO round that expands
       `writer_role_response_reward` beyond its current single structural signal (did
       `write_workspace_file` get called) toward anything denser/multi-component — per this
       paper's own finding, adding more reward granularity without checking each new term's
       discriminative power first can make training WORSE, not better. Honestly-scoped limitation:
       evaluated on one narrow domain (Tau-Bench airline, 50 tasks); the paper's own cross-domain
       transfer test (retail) is promising but explicitly not confirmed general.
       **Recommended recipe, confirmed as the same pattern APIGen-MT/xLAM/Hammer's own papers
       used**: (1) SFT/LoRA warm-start on a subsample of Toucan-1.5M + xlam-60k + the Hammer
       irrelevance set, for general schema-compliant, non-hallucinated tool-calling; (2) a small,
       final GRPO pass using this project's own 5 real logs (augmented with synthetic near-
       duplicates covering re-delegation/thin-coverage specifically) for the reward-shaping stage
       that actually targets `qwen3:4b`'s documented failure. Not started — the next concrete
       action, once the GPU training environment exists, is subsampling Toucan-1.5M/xlam-60k down
       to a size that fits this project's disk/time budget rather than downloading either in full.
     - **Training executed for real, 2026-07-18, and evaluated — genuine improvement confirmed.**
       Skipped the public-dataset warm-start (deprioritized as extra scope; the 348-line real+
       synthetic dataset already built this session was tried directly first). Ran
       `finetune/train_thin_coverage_grpo.py` against `Qwen/Qwen3-4B`, LoRA r=16/alpha=32, reward =
       `thin_coverage_response_reward`, 234 steps (~3 epochs over the 78 synthetic prompts), ~73.4
       min, VRAM stable at 15.79GB/17.1GB (GB units, not GiB, per standing instruction). Two real
       reward-function bugs caught and fixed BEFORE training by reading actual base-model
       completions rather than trusting the reward curve: `enable_thinking=False` needed explicit
       in the chat template (Qwen3 is a hybrid-reasoning model), and a "narrates intent to
       re-delegate without ever calling the tool" pattern the reward function originally scored 1.0
       (false positive) — fixed via `_narrates_intent_without_action` in `finetune/reward.py`.
       **Evaluated base vs fine-tuned on 8 held-out prompts** (5 real extracted examples + 3 topics
       never seen in training: octopus cognition, volcanic eruption prediction, Maya script
       decipherment), reading actual completion text, not just reward scores. Base model: 6/8
       (0.750 mean reward), failing exactly the narration-without-action pattern on 2 prompts.
       Fine-tuned model: **8/8 (1.000 mean reward)**, including topic-appropriate, non-degenerate
       tool calls with sensible `agent_id` assignment on all 3 unseen topics — real generalization,
       not a reward-hacking shortcut (verified by reading the actual generated instructions per
       task, not just the pass/fail score).
     - **Merged LoRA → GGUF → Ollama, live end-to-end benchmark run**, 2026-07-18: `merge_and_unload()`
       on CPU → 8GB merged safetensors → `convert_hf_to_gguf.py --outtype q8_0` (llama.cpp cloned
       only for the python conversion script, no C++ build needed, avoided a cmake dependency this
       machine doesn't have) → 4.28GB GGUF → `ollama create deepdelve-qwen3-4b-thin-coverage` using
       the exact Modelfile template/params already proven for `deepdelve-qwen3-4b`. Tool-call smoke
       test passed cleanly. Live full benchmark run launched against the exact sales-forecasting
       query that disqualified `mistral-nemo`/both plain Qwen3 sizes earlier this session.
     - **Full benchmark run concluded and scored — DISQUALIFIED (~1-2/10), but the targeted fix
       genuinely worked.** 1763.4s, 9 completion-check attempts (8 retries + final), ended
       `not_grounded`/retry-budget-exhausted; `final_report.md` was written but correctly flagged
       unverified, not silently accepted. **Confirmed zero `thin_coverage` stalls anywhere in this
       run** — the Planner delegated correctly from the first pass, the exact narration-without-
       action failure that disqualified the base model never recurred, consistent with the 8/8
       held-out eval result above. **But a second, untouched failure mode dominated the outcome**:
       cross-referencing every citation in `final_report.md` against `_run_state.json`'s real
       `fetched_urls` (only 7 URLs actually fetched, 4 with real content) found **0 of 8 cited URLs
       were ever actually fetched** — 100% fabricated citations (`aicompetence.org`, `academia.edu`,
       `ieeexplore.ieee.org`, 2x `researchgate.net`, `trade.gov`, one Springer chapter). Worse: the
       4 real, correctly-grounded findings that WERE in `findings.md` (arXiv 2406.02598, two
       Springer papers, the Revista de Gestão article — all directly on-topic, meta-heuristics
       applied to deep learning) were silently dropped from the final report entirely, replaced by
       the fabricated, topically-weaker PSO/GA/SA section. This is a new instance of the
       already-tracked "real grounded content silently absent from synthesis" pattern (see the
       heterogeneous-tiering A/B result above), not something this training round touched.
     - **Conclusion**: the fine-tune is a genuine, narrow success (the one behavior it targeted is
       fixed, confirmed both offline and live, zero regression), but does not make this model class
       usable overall, because citation fabrication + content-dropping during report synthesis is
       the actual dominant remaining blocker. **Scoped as the next fine-tuning candidate**: a
       citation-grounding reward for the Builder/FindingsWriter role, same recipe as
       `thin_coverage` — the negative signal already exists deterministically
       (`unverified_urls`/`check_stub_source` in `src/engine/completion.py`), so the reward
       function and prompt generation can reuse the same pattern (real check function output as
       ground truth, reward calibrated against real captured hallucinated completions before
       training, real extracted logs from this exact run as a first negative example — the
       fabricated-citation `final_report.md`/`findings.md` pair here is now a real, ready-to-use
       training example, same value as the original 5 `thin_coverage` logs were).
     - **Root cause found and structurally FIXED before training, 2026-07-18** (checked first,
       per this project's own established order: structural fix before fine-tuning). Traced the
       exact origin of the fabricated citations by reading `_run_state.json`'s raw `findings`
       records directly: two task branches ("Compare heuristic algorithms for sales prediction",
       "Verify cultural factors in Colombian sales models") never called
       `fetch_url_to_workspace` at all — pure `web_search`/`brave_web_search` snippet-only
       tasks — yet their own final synthesis text confidently cited specific URLs from the
       search-result list as if verified. The upstream defense for exactly this
       (`engine/orchestrator.py`'s `real_grounding_problem` check on a Searcher-tier specialist's
       own `final_text`, gated on `target_children`) DID fire correctly, but its
       `[SYSTEM VERIFICATION WARNING: ...]` was appended to the END of `final_text`, and
       `run_state.add_finding(..., final_text[:1500], ...)` then truncated to the first 1500
       chars — both flagged findings measured exactly 1500 chars with zero trace of the warning,
       confirming the warning was silently sliced off before ever reaching FindingsWriter. A real
       defense layer that gets truncated away is worse than none — it looks like protection while
       doing nothing. **Fix** (`src/engine/orchestrator.py`, around `_run_single_task`): the two
       grounding warnings (verification + scope-relevance) are now collected into a separate
       `verification_warnings` string instead of mutated into `final_text` in place; the finding
       summary recorded for FindingsWriter reserves the warning's exact length OFF the
       `_FINDING_SUMMARY_BUDGET` (1500) budget and concatenates it back in full afterward, so it
       can never be truncated away regardless of how long the specialist's own body text is. Unit
       -verified the slicing logic directly (a 2000-char body + a real warning string always
       yields the full warning intact in the final summary). `test_structural_checks.py` still
       passes (no completion-check function/`COMPLETION_CHECKS`/`Verdict` touched, only
       `orchestrator.py`). Live-smoke-tested with the real default model (`gpt-oss:20b`,
       `what is the current population of Iceland`): no exceptions/regressions, and no
       hallucinated citation occurred in this run to re-exercise the fixed branch directly (this
       model doesn't hit that failure), but the rest of the grounding pipeline (NLI check,
       uncited-claims check, cross-source-contradiction check) all fired normally throughout,
       confirming the refactor didn't disturb anything else in the same code path. This closes
       the structural half of the finding; the citation-grounding fine-tune above is still worth
       doing for models below `gpt-oss:20b`'s reliability tier, since a small model that already
       fabricates a citation from a search snippet will still need to be taught not to, independent
       of whether the warning about it survives to FindingsWriter.
     - **Structural fix re-tested against the exact same benchmark, 2026-07-18: measurable
       improvement, still disqualified overall.** Re-ran the identical sales-forecasting query
       against `deepdelve-qwen3-4b-thin-coverage` with the fix live. Confirmed directly in
       `_run_state.json` that the fix works exactly as designed: **9 of 16 findings now carry the
       full `[SYSTEM VERIFICATION WARNING...]` text intact** (previously silently truncated away
       every time). Grounding outcome improved concretely: **3 of 9 cited URLs in `final_report.md`
       now trace to real fetches** (arXiv, Springer, insightsoftware.com — up from 0/8 last time),
       but the other 6 are still fabricated, and all 6 trace to tasks with `source_url == task_name`
       (i.e. zero real fetch occurred for that task at all) — the model saw the correctly-delivered
       warning naming those exact URLs as unverified, and cited them anyway. Run still ended
       `not_grounded`, retry budget exhausted (2152.6s). **Conclusion**: the structural fix closed
       the "warning never reaches the model" bug for real (confirmed, not assumed), and genuinely
       improves grounding rate, but doesn't fully solve the problem — a small model, when it has NO
       real alternative source for a task, still sometimes cites a flagged URL rather than
       acknowledging the gap. This is a more precise, now evidence-backed case for the
       citation-grounding GRPO fine-tune above: the target behavior is specifically "when your
       source material contains a verification warning naming your only candidate URL as
       unfetched, do not cite it" — a clean binary reward signal, same shape as `thin_coverage`.

- **New candidate, 2026-07-21, added to the list not the queue** (fine-tuning stays paused per this
  section's own standing gate): DeepResearch-Slice (arXiv:2601.03261, read in full) formalizes
  DeepDelve's own "content vanishes during synthesis" family as `P(Correct) = P(Retrieved) ×
  P(Utilization|Retrieved)` and proposes a trained boundary-prediction head that slices retrieved
  text down to only the relevant span before the reasoning model ever sees it — a real, measured
  fix (+73% relative accuracy, Qwen2.5-7B frozen backbone, no fine-tuning of the reasoning model
  itself, only the small slicing head). Directly relevant to the live-caught
  FindingsWriter-abandons-the-evidence-base bug (found 2026-07-21, same session): their own
  diagnosis names "distracted by spurious passages" as one of three root causes, which matches the
  noisy `[SYSTEM RELEVANCE WARNING]`-flagged entry found sitting in DeepDelve's own assembled
  evidence base. Not pursued now — needs training a real model (the boundary-prediction head),
  same gate as every other fine-tuning candidate here. If fine-tuning ever resumes, this is a
  concrete, evidence-backed target worth scoping alongside the citation-grounding reward above.
