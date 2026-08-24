# DeepDelve Roadmap

Status as of 2026-08-20.


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

Genuinely open items only. Closed items that were still narrating their full resolution history
here got moved out, most already live in the wiki's [Completed](https://github.com/g0elles/deepdelve/wiki/Completed)
or [Changelog](https://github.com/g0elles/deepdelve/wiki/Changelog); anything not yet migrated is
tracked in `session_status/CURRENT.md` until the next wiki pass picks it up.

- **`create_local_agent`'s nested-closure god-function — CLOSED 2026-08-24.** `_run_single_task` and
  `delegate_tasks` were deeply nested closures inside `create_local_agent` (1098 lines), capturing
  dozens of enclosing locals by reference, with zero direct test coverage. Resolved 2026-08-23/24:
  four characterization-test layers landed first (pre-dispatch validation, the streaming/retry loop,
  post-loop grounding/finding-storage, and the scope-relevance check — see
  `test_structural_checks.py`'s `_create_local_agent_characterization_scenario`,
  `_run_single_task_streaming_characterization_scenario`, and
  `_run_single_task_post_loop_characterization_scenario`), catching one real previously-unknown
  `UnboundLocalError` on `Message` along the way (root cause: three sibling branches each redundantly
  re-imported `Message` locally, which shadows the module-level import for the whole function — fixed
  by deleting the redundant imports, not adding a fourth). Then the actual decomposition: both
  closures' bodies moved to module-level `_dispatch_single_task`/`_dispatch_tasks_batch`, every
  implicit closure capture turned into an explicit parameter; `create_local_agent` now just builds
  thin same-named forwarding closures (preserving mutual late-binding and every external call site
  unchanged). `create_local_agent`: 1098 → 184 lines. All four characterization layers pass unchanged
  against the decomposed code, plus `test_tools.py` and `ruff check .`. Full narrative belongs in the
  wiki's Completed page on the next migration pass, not repeated here.

- **`completion.py`'s mixed responsibilities, scoped 2026-07-29 — five slices extracted (2026-08-24),
  only `run_completion_check` and the routing tuples deliberately still untouched.** The file's own header describes it as a
  clean list of pure `Ctx -> Optional[Verdict]` check functions, but it also contains findings-
  authoring/evidence-assembly logic, disk-touching quarantine/restore/salvage helpers, async
  sub-agent dispatch orchestration, the task-verification ledger mutator, and the completion-check
  state machine tying all of it together — none individually bug-prone, but "add a new completion
  check" requires understanding all of the above living in one namespace, and `ARCHITECTURE.md` §1
  documents real, delicate cross-cutting invariants here (four routing tuples that must all agree,
  the starvation/capping machinery) that a 2026-07-24 session hit five real bugs in, in one sitting.
  **Slice 1 (quarantine/restore/salvage)**: `_quarantine_artifact`,
  `_content_unchanged_since_last_quarantine`, `_restore_quarantined_draft`, `_salvage_narrated_report`
  + its two banner constants, `_ensure_writer_quota_headroom`, `_ensure_reader_quota_headroom`,
  `_is_transient_ollama_json_error`, `_dispatch_task_retrying_transient_json_error` moved to
  `src/engine/artifact_salvage.py` — zero dependency on `Ctx`/`Verdict` or the check-list/starvation
  machinery, pure disk I/O + quota-dict arithmetic + one retry wrapper.
  **Slice 2 (group A — the 27 `check_*` functions + `Ctx`/`Verdict`)**: moved to a new module,
  `src/engine/completion_checks.py`, per the planning pass's recommended sequencing (least
  interconnected, best first candidate). `Ctx`/`Verdict` moved alongside the checks (every check
  takes/returns one) so `completion.py` imports them FROM `completion_checks.py`, never the reverse
  — avoids a circular import, since `completion.py` still owns `COMPLETION_CHECKS`/`GROUNDING_CHECKS`
  (left in place per the plan) and references every check by name. 4 of the 30 helpers moved too
  (`_findings_facet_coverage`, `_facet_coverage`, `find_duplicate_report_sections`,
  `_redelegate_directive`); `_ablation_disabled` stayed (used outside the check functions too).
  One real circularity found doing this: `_capped`/`_consecutive_occurrences`/`_dedupe_findings`/
  `_is_citable_finding` are called by both group-A checks AND the findings-evidence-assembly/
  starvation machinery that stayed in `completion.py` — a module-level import back from
  `completion_checks.py` would fail (those names don't exist yet when `completion.py`'s own
  top-of-file import of `completion_checks` runs). Resolved with a **local (function-body)
  `from engine.completion import ...` inside the 8 specific check functions/helpers that need
  them** (`check_thin_coverage`, `check_task_verification_flagged`, `check_findings_underuses_
  evidence`, `check_report_underuses_evidence`, `check_propagated_ungrounded_content`,
  `check_stale_findings`, `_findings_facet_coverage`, `_facet_coverage`) — deferred to call time,
  after both modules are fully loaded, so it's safe; every other check function needed no changes.
  `completion.py` re-exports `Ctx`/`Verdict`/all 27 check functions/`find_duplicate_report_sections`/
  `_findings_facet_coverage`/`_facet_coverage` at module level (the same re-export pattern as slice
  1's `_restore_quarantined_draft` via `engine.tui`), so every existing external import path
  (`test_structural_checks.py`'s direct `from engine.completion import check_thin_coverage, Ctx`
  etc., `finetune/*.py`'s similar imports) kept working with ZERO test/finetune-script edits needed.
  **Slice 3 (group B — findings evidence-assembly)**: `_is_citable_finding`,
  `_verification_warning_targets_url`, `_update_task_verification`, `_dedupe_findings`,
  `_collapse_multi_url_task_findings`, `_uncited_task_names`, `_reorder_findings_for_position_bias`,
  `_find_propagated_bad_content`, `_build_findings_source_material` + their 5 dedicated constants
  (`_CUTOFF_ONLY_SUMMARY_RE`, `_WARNING_MARKER_RE`, `_VERIFICATION_WARNING_BLOCK_RE`,
  `_VERIFICATION_FLAGGED_URLS_RE`, `_NEAR_DUP_FINDING_OVERLAP_THRESHOLD`) moved to a new module,
  `src/engine/findings_evidence.py`. No circular import this time — none of these 9 functions take
  `Ctx`/return `Verdict` or call `_capped`/`_consecutive_occurrences`, so the new module imports
  `_is_null_finding_summary` (utils.grounding) and `_looks_like_renamed_task`/
  `_content_word_overlap`/`get_context_budget` (engine.orchestrator) directly at module level, no
  local-import trick needed. `completion.py` re-exports all 9 + `_CUTOFF_ONLY_SUMMARY_RE` — this
  re-export is now load-bearing for TWO consumers, not one: `test_structural_checks.py`'s/
  `finetune/*.py`'s direct imports (as before), AND `completion_checks.py`'s own group-A local
  imports (`_dedupe_findings`/`_is_citable_finding`/etc., added during slice 2) that still say
  `from engine.completion import ...` — confirmed these still resolve since `completion.py` re-
  exports every name they need. Also removed 4 now-dead imports from `completion.py`'s own top
  (`_is_null_finding_summary`, `get_context_budget`, `_looks_like_renamed_task`,
  `_content_word_overlap` — only the moved functions used them), caught by `ruff check .` F401
  immediately after the move.
  **Slice 4 (group C — dispatch orchestration)**: `_dispatch_writer_review_fix`,
  `_dispatch_per_facet_builder_fix`, `_dispatch_per_facet_findings_writer_fix`,
  `_select_deepening_tasks`, `_dispatch_deepening_round` + `_WRITER_EMPTY_RETRY_ATTEMPTS`/
  `_MAX_FACET_DISPATCHES` moved to a new module, `src/engine/completion_dispatch.py`. One real
  circularity: `_dispatch_per_facet_builder_fix` uses `_BUILDER_NO_DELEGATE_CLARIFICATION`, a
  constant defined in `completion.py` right alongside `_BUILDER_FIXABLE_PROBLEMS` (left there
  deliberately — it's also used twice more in `run_completion_check` itself, group E, so moving it
  would just create the reverse problem). Resolved the same way as slice 2's circularity: a local
  (function-body) `from engine.completion import _BUILDER_NO_DELEGATE_CLARIFICATION` inside just
  that one function. `completion.py` re-exports all 7 moved names — load-bearing for
  `test_structural_checks.py`'s direct imports of `_dispatch_writer_review_fix`,
  `_select_deepening_tasks`, and `_WRITER_EMPTY_RETRY_ATTEMPTS`, and for `run_completion_check`
  itself (staying in `completion.py`) calling `_dispatch_per_facet_builder_fix`/
  `_dispatch_per_facet_findings_writer_fix`/`_dispatch_deepening_round`/`_MAX_FACET_DISPATCHES` by
  name. `math`/`os`/`asyncio` became dead imports in `completion.py` after the move (only these 5
  functions used them) — removed via `ruff check . --fix`.
  **Slice 5 (group D — the starvation/capping state machine)**: unlike slices 1-4, this is the
  ARCHITECTURE.md-flagged hazard itself (5 real bugs in one 2026-07-24 sitting), so per the
  planning pass's own recommended sequencing this got a **coverage audit before any move**:
  `_capped` had no direct behavior test (only a static grep enforcing that every non-self-resolving
  check calls it), and `_apply_starvation_yield`/`_collect_other_active_problems`/
  `_with_other_problems_addendum` had zero direct tests at all. Added characterization tests for
  all three (committed separately, `520d08c`, before touching any structure) confirming the exact
  behavior read from source: `_capped`'s threshold/skip_problems logic, `_apply_starvation_yield`'s
  stuck-then-probe-target/no-target/target-returns-same-problem/target-empty branches, and
  `_collect_other_active_problems`/`_with_other_problems_addendum`'s exclusion-of-the-winner/cap-
  at-3/no-op-when-nothing-else-active behavior. Only THEN moved `_consecutive_occurrences`,
  `_consecutive_tier_wins`, `_capped`, `_yield_to_starved_check`, `_STARVATION_YIELD_TARGETS`,
  `_apply_starvation_yield`, `_collect_other_active_problems`, `_with_other_problems_addendum`,
  `_other_grounding_problems`, `_with_other_grounding_addendum` + their 4 dedicated constants
  (`_STARVATION_SKIP_THRESHOLD`, `CONSECUTIVE_SAME_PROBLEM_ESCALATION_THRESHOLD`,
  `_OTHER_ACTIVE_PROBLEMS_CAP`) to a new module, `src/engine/completion_starvation.py`. No new
  circularity beyond the established pattern: the new module imports `Ctx`/`Verdict`/
  `check_report_underuses_evidence` from `completion_checks.py` (one-directional, no cycle);
  `completion.py` re-exports everything moved, load-bearing for `completion_checks.py`'s own
  group-A local imports of `_capped`/`_consecutive_occurrences` and for
  `test_structural_checks.py`'s direct imports. `cheap_grounding_problems` (from `utils.grounding`)
  stayed re-exported from `completion.py` too, even though nothing there calls it anymore, because
  `test_structural_checks.py` imports it from `engine.completion` directly — caught by running the
  suite, not just `ruff check .`, since an unused-import lint pass alone doesn't know an external
  file depends on the re-export.
  All five slices verified by running the existing suite unmodified plus a before/after
  sorted-line-set diff confirming no body line was dropped or duplicated (the only line-level
  diffs each time are import restructuring/header comments, never code content). `completion.py`:
  3854 → 3650 (slice 1) → 2235 (slice 2) → 1618 (slice 3) → 1148 (slice 4) → 873 lines (slice 5).
  New `completion_checks.py`: 1468 lines. New `findings_evidence.py`: 637 lines. New
  `completion_dispatch.py`: 493 lines. New `completion_starvation.py`: 304 lines.
  `test_structural_checks.py`, `test_tools.py`, and `ruff check .` all pass after each slice.
  **Group E (`run_completion_check` + the four routing tuples), 2026-08-24**: coverage-audited
  first per the same discipline as group D — found `run_completion_check` already has 95
  references in `test_structural_checks.py`, including dozens of direct end-to-end
  `_asyncio.run(run_completion_check(...))` invocations across scenarios plus source-inspection
  pins on specific internal details, the opposite situation from `create_local_agent`'s
  near-zero coverage. Given that, the four routing tuples were left in place (they have exactly
  one real consumer — `run_completion_check` itself — plus one audit test reading them via
  `_comp.COMPLETION_CHECKS`/`_comp.GROUNDING_CHECKS`; moving them elsewhere would be pure
  reshuffling with no complexity reduction, unlike groups A-D's helpers which had genuine
  circular-import/logical-grouping reasons to relocate). Instead extracted the 3 genuinely
  separable, no-early-exit/no-`continue` sub-phases of `run_completion_check`'s own
  ~630-line loop body into module-level helpers, all still living in `completion.py` itself
  (not a new file — they're tightly coupled to this one function's loop, so a new module would
  just add indirection without reducing coupling): `_detect_verdict` (build `Ctx`, run the
  two-tier COMPLETION_CHECKS/GROUNDING_CHECKS scan with all starvation/addendum wrappers),
  `_compute_force_whole_rebuild` (the consecutive-same-problem escalation check, including its
  `attempt = max_attempts` mutation, now returned as a tuple), `_notify_final_verdict` (the
  terminal quarantine-restore/salvage/notify branch, side-effect-only). Deliberately did NOT
  touch the dispatch branches (Builder/FindingsWriter/thin_coverage/report_underuses_evidence/
  findings_underuses_evidence) — each ends in a `continue` back to the OUTER while loop, which a
  nested helper cannot replicate without restructuring control flow into a return-a-sentinel
  shape, a real behavior-change risk for a mechanical pass. One legitimate test update needed
  (unlike groups A-D, which needed zero): a source-inspection test asserted
  `'run_state.data.get("fetched_urls")' in inspect.getsource(run_completion_check)` — updated to
  inspect `_detect_verdict`'s source instead, since that's where the logic legitimately now
  lives; the sibling `_BUILDER_NO_DELEGATE_CLARIFICATION` count-2 assertion needed no change
  since that logic stayed in `run_completion_check` itself. Verified via `ruff check .` and the
  full existing suite (no behavior change, only 2 lines of test updated to point at the new
  location). `run_completion_check` itself: ~633 → 432 lines.
  **This closes out the completion.py decomposition plan.** No further splitting of this file
  is planned — `run_completion_check`'s remaining body (the dispatch branches + final loop
  control) is the genuinely irreducible core, and the routing tuples correctly live beside their
  only consumer.

- **`run_cli`/`BasicTuiAgent`/`api.py` full run-lifecycle unification, re-scoped 2026-07-29,
  widened 2026-08-24, still open.** A dedicated audit found the two entry points aren't just
  stylistic duplicates in places that matter: the TUI's approval handling actually executes tools
  client-side and constructs full message pairs, the CLI's doesn't; the TUI has no
  context-budget/wall-clock-deadline concept by design, since a human can just stop it. The
  genuinely safe subset (the resume-merge allowlist, the `required_artifact` lookup, a missing
  `QuotaAbortException` handler, a missing crash-time `run_state.save()`) is already fixed and
  merged. **`src/api.py`'s `_run_research` is a THIRD independent copy of this exact run-lifecycle
  loop, not a unifying layer** — correction to a 2026-08-24 note that first framed this as a new
  finding: `src/api.py`'s own module docstring, and `~/.claude/plans/cosmic-growing-canyon.md`'s
  Phase 4 section (the plan that built `api.py`), already document this as a **deliberate,
  reasoned tradeoff**, not an oversight — that plan explicitly says extracting a shared
  abstraction at API-build time "would mean touching the two already-shipped, already-tested
  entry points as a side effect of an unrelated change" and defers real unification to this exact
  ROADMAP item's "own dedicated session." `api.py` correctly reuses the shared engine pieces
  (`create_local_agent`, `run_completion_check`), but hand-duplicates the surrounding
  stream-consumption loop, the context-budget nudge-then-cutoff mechanism, the wall-clock deadline
  check, and its own `_find_substantial_text`/`_api_notify` pair. Still accurate that any future
  strategy-object design must account for THREE call sites, not two — just not a new discovery.
  **2026-08-24: one real parity bug found and fixed while reading all three loops in full to plan
  the eventual unification** — `api.py`'s stream-consumption loop had NO malformed-tool-call
  retry or `QuotaAbortException` handling at all (confirmed via direct source read: no
  `classify_malformed_retry` call, no `except BaseException` around the stream, anywhere in
  `_run_research`), while `run_cli`/`run_agent` both wrap theirs in exactly this pattern. Any such
  exception previously propagated straight out of `_run_research` uncaught, hit only `_worker`'s
  generic `except Exception` (job marked "failed", no retry, no salvage/quarantine final-verdict
  attempt) — the same "check every surface" gap CLAUDE.md's rule exists for, just found in the
  direction of a robustness fix that landed in two surfaces and never propagated to the third.
  Fixed by mirroring `run_cli`'s exact try/except shape (same shared `classify_malformed_retry`
  helper, same `QuotaAbortException` catch). New `test_api.py` (api.py had ZERO test coverage
  before this — confirmed via codegraph) pins the new behavior with 3 scenarios: malformed-call
  retry, clean `QuotaAbortException` abort, and a genuinely unrecognized exception still
  propagating rather than being silently swallowed. `ruff check .` and the existing suites
  (`test_structural_checks.py`, `test_tools.py`) also still pass.
  Still open, and deliberately NOT attempted in this pass per the plan's own explicit
  "dedicated session" framing (a multi-hour, high-risk undertaking given how deeply stateful and
  subtly different all three loops are — confirmed by reading each in full): unifying the
  stream-consumption loop and the approval-handling block behind explicit strategy objects, a
  real design decision (what varies between CLI/TUI/API), not a mechanical extraction. Recommended
  approach: design the strategy interface first, then extract the loop body to take it as a
  parameter, not "extract the whole function and see what breaks."

- **RAG-augmented small model, raised 2026-07-20, not yet scoped.** The project's own prior "RAG
  failure" turned out to be a benchmark-isolation bug in a deleted exact-string-match cache, not a
  real RAG failure, see the wiki's [Architecture Synthesis](https://github.com/g0elles/deepdelve/wiki/Literature-Review-Architecture-Synthesis)
  page for the full literature review. Real RAG (embeddings/chunking/vector retrieval) is
  architecturally different from what failed before, so the old rejection doesn't automatically
  block it, but any persistent cross-run cache, RAG or not, must be explicitly isolated per model
  during comparative benchmarking or the same contamination bug recurs regardless of the retrieval
  technique underneath it. That's the one non-negotiable constraint from this project's own history.

- **TUI QoE improvements, researched 2026-07-14 — CLOSED 2026-08-24.** A framework capability
  survey (Textual's own source, not assumed from memory) found several likely-already-working
  features needing live confirmation and several unused framework capabilities not yet scoped
  into concrete work. Two smallest, most directly requested items (message copy button,
  right-click paste) shipped earlier, see Completed. Two more closed 2026-08-20: click-drag
  select + copy confirmed already working with zero code needed (`ALLOW_SELECT` is `True` by
  default on both `App` and every widget in this Textual version, confirmed directly via
  `textual.app.App.ALLOW_SELECT`/`Static.ALLOW_SELECT`, not overridden anywhere in `tui.py`); the
  command palette (`ctrl+p`, also on by default) got a real `SlashCommandProvider` wiring
  `SLASH_COMMANDS` into it, live-verified with Textual's own Pilot test harness, see Completed.
  **Widget maximize was ALSO already confirmed working, same discipline, 2026-07-24** —
  `test_structural_checks.py`'s own `_widget_maximize_scenario` (a real Pilot test, not a mock)
  pins `RichLog.allow_maximize` defaulting True (falls back to `can_focus`),
  `Screen.action_maximize()` walking up to the outermost maximizable ancestor (`ToolCallWidget`'s
  `Collapsible`), reachable via the already-wired command palette — this entry just never got
  updated to say so until a 2026-08-24 doc-drift catch.
  **The remaining three items closed 2026-08-24, each confirmed against Textual 8.2.8's actual
  installed source, not docs/memory:**
  - **Theming**: `App.theme`/`available_themes` and the command palette's `ThemeProvider` already
    worked with zero code (same "already works" category as widget-maximize) — the real gap was
    `BasicTuiAgent.CSS` using literal hex values instead of theme variables, so switching themes
    changed nothing visually. Converted every color to Textual's theme CSS variables (`$panel`,
    `$primary`, `$secondary`, `$success`, `$text`, `$text-muted`, `$text-success`, `$panel-*`) —
    live-verified via Pilot that a real rendered background color changes when `app.theme` switches
    (deliberately did NOT assert `$text` itself changes — it stays similar across dark themes by
    design, that's correct behavior, not a gap).
  - **Inline autocomplete**: `PromptInput` now passes `suggester=SuggestFromList(...)` over
    `SLASH_COMMANDS` — Textual's `Input` has native suggester support built in, confirmed via
    source; this complements (doesn't replace) the existing filtered-`OptionList` popup in
    `on_input_changed`. Live-verified: typing `/sto` shows an inline `/stop` ghost completion.
  - **`Tree`/`DataTable` for the run and file pickers**: both were pure DISPLAY lists before this —
    confirmed there was never an `OptionList` selection handler anywhere in this file at all;
    selection only ever worked by typing the exact key text (a slugified run folder name, or a
    filename) and pressing Enter. The run picker (`_show_run_picker`) now uses a real `DataTable`
    with row selection (arrow keys + Enter), and the file picker (`_show_file_picker`) a real
    `Tree` grouping files one directory level deep (confirmed via `get_workspace_files` — a
    `sources/` subdirectory is the standard shape) — both live-verified via Pilot for genuine
    keyboard row/leaf selection, not just that the widget renders. `TabbedContent`/`SelectionList`
    were explicitly assessed and skipped — no multi-pane or multi-select UI exists anywhere in
    this file to justify forcing them in.
  All four items landed with direct Pilot-test coverage in `test_structural_checks.py` (no prior
  test existed for any of them before this pass); `test_tools.py` and `ruff check .` also pass.

- **Fine-tuning, deferred pending a viable base model size, see [Stretch](#stretch).** The
  correctness gate that used to block resuming fine-tuning is confirmed clear as of 2026-08-19, a
  new round is blocked on a different, harder problem now, no viable small base model has yet
  passed a real live benchmark, see the Stretch section below for the full status and the
  standing "one combined retrain, never an isolated LoRA" methodology rule.

### Superseded, kept as a pointer so this doesn't get proposed again

- **The full 11-candidate local-model bake off re run via vLLM, and the `tool_choice: required`
  fix that depended on it.** Both were scoped while vLLM was this project's serving backend
  candidate. That effort was closed 2026-07-26: vLLM plus bitsandbytes on ROCm proved too immature
  on this consumer GPU across 9 disqualified/discarded candidates, several with serving-layer-shaped
  bugs, and Ollama was restored as the permanent backend, see the wiki's
  [Changelog](https://github.com/g0elles/deepdelve/wiki/Changelog-Recent-2) for the full trace. Any
  future vLLM-dependent idea needs a fresh justification to reopen that decision, not a resumption
  of this old plan.

- **Reference repo review candidates not adopted** (STORM's persona-diversity facet discovery, GPT
  Researcher's `SourceCurator` and defensive multi-strategy parsing, host-driven shrinking-budget
  iteration). All real, all considered, all recorded with the specific reason each wasn't adopted in
  the wiki's [Literature Review](https://github.com/g0elles/deepdelve/wiki/Literature-Review-Bakeoff-Findings)
  page. Kept here only as a pointer so they don't get re-proposed as fresh ideas.

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

Fine-tuning is currently the only Stretch item. Full history, from the first ROCm/GRPO feasibility
smoke test through the first trained round's own live disqualification, moved to the wiki:

**→ [Fine-tuning](https://github.com/g0elles/deepdelve/wiki/Fine-tuning)**

**Status as of 2026-08-19**: the correctness gate that used to block resuming fine-tuning is
confirmed clear. A new round is blocked on a different, harder problem now, no small local model
has yet passed a real live benchmark to serve as a viable base to fine-tune in the first place, see
the wiki's [Model Bake off](https://github.com/g0elles/deepdelve/wiki/Model-Bakeoff) page for the
current search.

**Standing methodology rule, engraved after real cost**: every new fine-tuning objective folds into
ONE combined multi-objective GRPO retrain off the same raw base checkpoint, never a separate,
isolated single-purpose LoRA. Separately trained adapters cannot be merged or stacked cleanly, a
model can only deploy with one LoRA active at a time. Before starting any new round: extend the
existing combined reward function with the new dimension, retrain from the same raw base with every
objective's prompts combined, not just the new one, re-evaluate every dimension together on held
out data to confirm no regression, and redeploy as a single new combined artifact replacing the
prior one.

**Still-open, literature-backed candidates for whenever fine-tuning resumes, not yet acted on:**

- Verify the AXPO resampling-reward mechanism against `finetune/reward.py`'s actual
  `writer_role_response_reward` implementation before adapting it. AXPO's own stated limitations
  confirm its trigger mechanism assumes a binary verifiable-outcome reward, which this project's
  structural "did it happen" reward doesn't have; the underlying "concentrate exploration at the
  sparse action boundary" insight likely still transfers, the specific mechanics don't as-is.
- Apply "Iterative Reward Calibration"'s own finding, always measure a new reward term's
  discriminative power before deploying it, to any future expansion of
  `writer_role_response_reward` beyond its current single structural signal. That paper's own live
  result was a naively dense reward costing up to 14 points versus a sparse one, on the same Qwen3
  family this project tests.
- DeepResearch-Slice (arXiv:2601.03261) proposes a small, separately trained boundary-prediction
  head that slices retrieved text down to the relevant span before the reasoning model sees it, a
  real, measured fix for exactly the "distracted by spurious passages" mechanism behind this
  project's own recurring content-vanishes-during-synthesis pattern. Not pursued, needs training a
  real model, same gate as everything else here.
