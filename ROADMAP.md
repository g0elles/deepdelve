# DeepDelve Roadmap

Status as of 2026-07-20.

## Done

- **Non-generative routing classifier for `delegate_tasks`'s `agent_id` — IMPLEMENTED and
  live-verified 2026-07-20.** Merged from the SOTA literature review (`RESEARCH.md` §6): small/mid
  models fail disproportionately at STRUCTURED SERIALIZATION (schema-valid output, wrong content),
  not semantic understanding, in a way a 6,000-sample SFT run can't fix (constraint-tax papers,
  arXiv:2606.25605 + arXiv:2605.26128) — confirmed live in this project's own data (~4.9% of real
  `delegate_tasks` calls used a hallucinated but well-formed `agent_id`). Pulled routing out of free
  generation into a frozen `all-MiniLM-L6-v2` embedding + `LogisticRegression(class_weight=
  "balanced")`, decided policy **reject-and-nudge** (not silent override, not advisory-only).
  - **New: `finetune/extract_agent_routing_dataset.py`.** Real bug caught and fixed while building
    it: an initial version filtered session-log events to `source == "Agent"` (the Planner's own
    turn) only, silently missing 100% of `DocumentAnalyzer`/`DataAnalyzer` examples — those only
    ever appear as targets of a NESTED `delegate_tasks` call made by `WebSearcher`/
    `AcademicSearcher`'s own dispatch (`source == "SubAgent_<task_name>"`, per `src/app.py`'s
    `sub_agents=[document_analyzer, data_analyzer]` on both searcher roles), not the Planner
    directly. Fixed by dropping the source filter entirely. Real extraction, run against
    101 session logs: **1,096 valid pairs + 57 hallucinated (4.9%)** — matches the literature
    review's earlier ad hoc count (1,153 total, ~4.9%) almost exactly, now from a reproducible
    script instead of a one-off pass. After near-duplicate removal: 814 valid pairs, class
    distribution `DocumentAnalyzer=331, WebSearcher=330, DataAnalyzer=99, AcademicSearcher=54`,
    stratified 651 train / 163 held-out.
  - **New: `finetune/train_agent_routing_classifier.py`.** Real held-out per-class results:
    `DocumentAnalyzer` 0.89 precision / 0.88 recall, `WebSearcher` 0.89/0.85, `DataAnalyzer`
    0.68/0.65, `AcademicSearcher` 0.44/0.64 (weakest — smallest class, 54 real examples, matches
    the literature review's own "imbalanced but workable" caveat). Overall accuracy 0.82. **Real
    regression-check finding**: every `"searcher"` (lowercase) hallucination correctly routes to
    `WebSearcher` with real confidence (0.44–0.83); `IndustrySearcher`/`BookSearcher`/
    `BusinessNewsSearcher` correctly route to `AcademicSearcher` (0.62–0.73); all `PeerReviewer`
    hallucinations get LOW confidence (0.30–0.46, all below the chosen 0.6 threshold) — the
    classifier correctly abstains on a genuinely out-of-scope role rather than forcing a guess,
    validating the `min_confidence` design.
  - **New: `src/utils/agent_routing.py`** — lazy singleton, fails open (mirrors
    `grounding.py::_get_nli_model`). Real design gap found and fixed before shipping: the
    classifier's 4 known classes (`KNOWN_AGENT_IDS`) span TWO different delegation levels —
    Planner→`{WebSearcher,AcademicSearcher,PeerReviewer,Builder,FindingsWriter}` (only 2 of 5 are
    classifier classes) and Searcher→`{DocumentAnalyzer,DataAnalyzer}` (a disjoint pair) — so
    `predict_agent_id` takes a `candidate_classes` param, restricting the prediction to the
    INTERSECTION of the caller's own real roster and the classifier's known classes, never a
    blind global argmax that could suggest a role the caller doesn't even have available.
  - **`src/engine/orchestrator.py`**: new pure function `_agent_routing_rejection_reason`
    (decision logic only, directly testable without the async tool closure) + a new check inside
    `delegate_tasks`'s per-task validation loop, before the existing `if errors:` gate. Rejects
    (adds to the same error-accumulation path every other `delegate_tasks` validation already
    uses) when the declared `agent_id` isn't real for this caller, or the classifier disagrees
    above `min_confidence` — silently no-ops on the common case (declared role agrees, or
    classifier abstains).
  - **Config**: new `settings.agent_routing_classifier` block in `config_template.yaml` AND the
    live `~/.deepdelve/config.yaml` (per this project's own standing rule), default `enabled:
    false` — new, not yet exercised against live mixed Planner/Searcher traffic end-to-end, a
    conservative rollout matching this project's own standing caution about untested features.
  - **New dependency**: `scikit-learn>=1.7.0,<2.0.0` in `pyproject.toml` (already installed
    transitively via the dev venv, no fresh install needed).
  - **Tests**: new `_agent_routing_rejection_scenario` in `test_structural_checks.py` (5 cases:
    no-prediction no-op, unknown-role rejection, strong-disagreement rejection, agreement no-op,
    low-confidence-abstention no-op) + `src/utils/agent_routing.py`'s own `__main__` self-test
    (fail-open behavior, empty/out-of-scope candidate-class handling). Full suite + `ruff check`
    clean across all changed/new files.
  - **Live sanity check**: 5 real instruction strings run through the loaded artifact directly.
    4/5 correct (Rust version → WebSearcher 0.74; peer-reviewed GOA papers → AcademicSearcher
    0.83; numeric population table → DataAnalyzer 0.78; a PeerReviewer critique task correctly
    got low-confidence 0.12, would abstain/reject on the unknown-role path). **One real, honestly-
    reported misclassification**: a DocumentAnalyzer-shaped "read a file and extract findings"
    instruction predicted `DataAnalyzer` (0.68 confidence) — consistent with the measured 0.68
    precision for that class, not a bug, a genuine limitation of a 0.82-accuracy classifier on
    ambiguous wording between two semantically close roles.
  - **LIVE END-TO-END TEST RUN, 2026-07-20 (same day) — FOUND A REAL REGRESSION, REVERTED TO
    `enabled: false`.** Flipped the live config on, ran a real headless query exercising both
    delegation levels ("What is the current stable version of the Rust programming language, and
    what does peer-reviewed academic research say about the soundness of Rust's borrow checker?").
    The nested WebSearcher→DocumentAnalyzer level worked correctly, no false rejections. **The
    Planner-level AcademicSearcher dispatch was wrongly rejected 8 CONSECUTIVE times** — every
    single retry of "find peer-reviewed papers on borrow-checker soundness" was rejected with
    "looks like a WebSearcher task" at 0.67-0.75 confidence (above the 0.6 threshold), across
    ~2 real minutes / 8 wasted Planner turns, until the Planner gave up on the angle entirely.
    **Concrete, measured harm**: the final report contains ZERO content on the academic angle —
    half the user's actual question was never researched, a real content-coverage failure directly
    caused by this feature, not a pre-existing bug. **Root cause**: `AcademicSearcher` is the
    classifier's weakest, smallest class (54 real examples, 0.44 held-out precision, by far the
    worst of the 4) — a flat `min_confidence=0.6` threshold treats a confidence number as equally
    trustworthy regardless of which class produced it, but this run showed the model can be
    CONFIDENTLY wrong specifically for the class it's worst at. **Structural policy flaw, not just
    a data problem**: "reject-and-nudge" conflated two different risk profiles under one policy —
    rejecting a declared role that isn't real for this caller at all (safe, unambiguous, no valid
    alternative exists) vs. rejecting a declared role that IS valid because the classifier
    disagrees (risky, since the classifier can be confidently wrong, as just demonstrated). The
    live test showed the second case caused active harm this session. **Config reverted**:
    `~/.deepdelve/config.yaml`'s `agent_routing_classifier.enabled` set back to `false` immediately
    after this finding.
  - **DATA-QUALITY FIX, 2026-07-20 (same day, direction (a) above) — a real, root-cause bug found in
    the training data itself, not just a volume problem.** Auditing the 54 `AcademicSearcher`
    examples by eye surfaced 6 that were obviously market-sizing/business-research tasks
    ("Evaluate the X market in Colombia... market size, current state, gaps, competition"), not
    literature searches — none used any academic-search language at all. Tracing further: all 13
    came from ONE session, a near-identical task TEMPLATE repeated with a different sector noun
    each time, sitting alongside a differently-phrased sibling task in the SAME session correctly
    labeled `WebSearcher`. This is one real historical Planner routing mistake (or drift across a
    repeated batch) baked into the training data as if it were correct ground truth — a genuine
    root cause, not just "not enough data."
    - **Two general-purpose automated detectors tried and rejected, worth keeping the reasoning
      for**: (1) SequenceMatcher literal-text similarity never caught this at all (different sector
      nouns keep character overlap below the dedup threshold despite an identical template). (2)
      Embedding (all-MiniLM-L6-v2) cosine similarity, even restricted to same-routing-level pairs,
      couldn't cleanly separate "same underlying ask, inconsistently routed" from "different ask,
      same topic domain, correctly routed differently" — this project's own real historical
      benchmark queries cluster so heavily on one topic (heuristic algorithms / sales forecasting /
      Colombia) that a threshold loose enough to catch the real conflict also flagged hundreds of
      genuinely correct, differently-labeled pairs. **Landed on a direct, documented exclusion of
      the one manually-verified session** instead of forcing an unreliable general heuristic —
      honest engineering given the alternative was trading a small, well-evidenced problem for a
      much larger, noisier one.
    - **Real result after retraining on the cleaned data**: `AcademicSearcher` class shrank 54→50
      (the wrong examples excluded, not replaced), but held-out RECALL rose 0.64→0.80 at the same
      0.44 precision — fewer false negatives, directly the failure direction that caused the live
      regression (the classifier was calling real academic tasks `WebSearcher`). **Directly
      re-tested against the exact 3 real instruction variants that failed live** — all 3 now
      correctly predict `AcademicSearcher` (0.63-0.66 confidence, was `WebSearcher` before).
      **Regression-checked the other direction too**: 4 genuine `WebSearcher`-shaped market/factual
      tasks (including ones structurally identical to the now-excluded bad examples) still
      correctly predict `WebSearcher`, several at HIGHER confidence than before (0.72-0.89) — the
      fix sharpened the boundary in both directions, not just patched the one failing case.
      `AcademicSearcher` precision (0.44) is still the weakest of the 4 classes — volume/diversity
      (candidate direction (a), still the smallest class by far) remains the deeper fix, not yet
      done; this closes the specific, confirmed data-contamination bug, not the whole gap.
    - **New `finetune/extract_agent_routing_dataset.py` output**: `agent_routing_conflicting.jsonl`
      (13 excluded examples, kept for inspection, not silently discarded).
    - **SECOND LIVE RE-TEST, 2026-07-20, same day, same exact query that failed before**
      ("What is the current stable version of the Rust programming language, and what does
      peer-reviewed academic research say about the soundness of Rust's borrow checker?") —
      `~/.deepdelve/config.yaml`'s `agent_routing_classifier.enabled` re-flipped to `true`, run
      end-to-end via `python src/app.py --auto-approve`. **Confirmed fixed**: zero classifier
      rejections anywhere in the run (checked both the raw log and the session's `ui_events`).
      The `AcademicSearcher` task (`borrow_checker_soundness`) was accepted on the first try, found
      the real paper (arXiv:2404.02680, "Sound Borrow-Checking for Rust via Symbolic Semantics"),
      and `findings.md` correctly contains both the Rust-version findings AND the academic section
      — directly reproduces the first test's setup and confirms the data-quality fix holds in the
      real pipeline, not just isolated classifier calls.
      - **A second, unrelated bug surfaced in the same run, worth recording separately**:
        `final_report.md` dropped the entire academic section despite `findings.md` having it
        correctly. Root cause is NOT the routing classifier — this run hit 3 completion-check
        remediation cycles (missing two-pass discipline on `findings.md`, missing
        `final_report.md`, an unsupported-claim flag on the Rust-docs source), each dispatching a
        corrective sub-agent (`FindingsWriterFix`, `BuilderFix` x3, `ReviewFix` x3) that itself
        calls `read_workspace_file`/`write_workspace_file`. By the third `BuilderFix` pass, the
        `read_workspace_file` quota (limit 30, `config_template.yaml`) was exhausted, and the final
        report says so outright: "Due to workspace tool quota limits, I was unable to re-read the
        source file during this session... only claims that can be directly traced to specific
        lines in findings.md are included" — then cites only 2 of ~7 real sources. **Not yet
        fixed; new finding, not scoped into this session's work.** Candidate fixes: raise
        `read_workspace_file`'s quota specifically for `BuilderFix`/`ReviewFix` remediation
        sub-agents, or give the completion-check remediation loop its own separate quota pool so
        legitimate first-pass work doesn't starve retries (and vice versa).
- **Sub-agent dispatches had NO wall-clock deadline at all — a real, live-confirmed gap, fixed and
  live-verified.** `engine/tui.py`'s `run_cli` already races each stream update against
  `settings.max_run_minutes` via `asyncio.wait_for(stream_iter.__anext__(), timeout=remaining)`
  instead of a plain `async for` (2026-07-12 fix, because a plain async-for only checks a deadline
  once it actually RECEIVES an update — invisible to a stream that goes silent for a long time).
  That fix was never propagated to `engine/orchestrator.py::_run_single_task`, the code path EVERY
  Searcher/Analyzer/Builder/FindingsWriter/PeerReviewer dispatch goes through — it used a bare
  `async for update in stream:` with zero deadline, relying entirely on the raw openai-SDK HTTP
  client's blunt ~600s default connection timeout, which then discards the whole in-progress
  response and raises a generic error instead of degrading gracefully.
  - **Root-caused live (2026-07-14)**, during Phase 4 smoke testing, after the user pushed back on
    accepting repeated live-run timeouts as "just model slowness" without further investigation —
    correctly, since the real cause turned out to be structural: cross-referenced
    `journalctl -u ollama` against the exact failure window and found two requests that returned
    HTTP 500 after hanging exactly `10m0s` and `6m24s`. The `10m0s` one was NOT stuck — `ollama`'s
    own `print_timing` log showed it continuously, validly decoding tokens the entire time (steady
    ~33 tok/s, no gaps) up to 19,908+ tokens when the connection was force-closed —
    `600s × ~33 tok/s ≈ 19,800 tokens`, matching almost exactly. A single sub-agent turn ran away
    into a very long generation with no early-cutoff mechanism watching it at all.
  - **First fix attempt was itself incomplete, caught by live verification, not just the unit
    suite**: an initial version gave `_run_single_task` the SAME `max_run_minutes` deadline as
    `run_cli`'s own top-level guard, anchored to the same run-start clock. Live-tested with a tight
    `max_run_minutes: 2` — a cutoff DID fire, but checking the persisted
    `~/.deepdelve/sessions/session_<id>.json` UI-event log showed the new inner marker text never
    actually appeared; the OUTER guard's cancellation had propagated down through `asyncio.wait_for`
    and pre-empted the inner one before its own deadline check ever got a chance to run on its own
    terms — meaning in realistic configs (max_run_minutes=45) the new code was effectively dead,
    providing no protection against ONE runaway call among MANY quick ones early in a long run.
  - **Real fix**: new, INDEPENDENT `settings.sub_agent_timeout_minutes` (default 10), a fresh
    per-DISPATCH deadline computed at the start of each `_run_single_task` call (not shared with
    the run-wide clock or any other dispatch) — closes the actual gap (one runaway call) instead of
    just duplicating the whole-run ceiling. Also bumped `_build_client`'s `AsyncOpenAI` `timeout=`
    to run comfortably past both `max_run_minutes` and `sub_agent_timeout_minutes` — otherwise the
    SDK's own blunt ~600s default keeps winning the race against either of our graceful cutoffs
    whenever a configured budget exceeds 10 minutes (both template defaults do), making them dead
    code in any realistic config, only ever exercised by an artificially short override.
  - **Live-verified the corrected fix directly**: ran with `max_run_minutes: 45` (generous, so the
    outer guard has no reason to fire) and `sub_agent_timeout_minutes: 1` (tight) — a sub-agent
    dispatch was cut short within a minute, and the Planner's own next turn literally said "The
    task timed out, but the capital of Italy is a well-known fact (Rome)... I stop delegation
    immediately," proving the graceful marker text reached the Planner and it correctly adapted
    instead of hanging, retrying blindly, or crashing.
  - Verified the core `asyncio.wait_for`-racing mechanism in isolation too (a stream that hangs
    999s against a 1s deadline is cut off at ~1.00s, not 999s). Full suite + `ruff check .` pass.
  - `_run_budget_deadline` shared across `run_cli`/`run_agent` (both call `create_local_agent`
    once per run) — no separate TUI-specific change needed to close this gap on both surfaces.
  - **This also retroactively explains several "timeout" observations during today's earlier Phase
    2-4 live smoke tests** that had been attributed to model slowness/complexity — those diagnoses
    weren't necessarily wrong (Gemma4's genuine slowness and a separate `qwen3:4b` tool-repetition
    pattern were both independently confirmed too, see "Findings from live testing" below), but this
    structural gap was the common thread making ANY of those failure modes catastrophic (total loss
    of the turn, no graceful degrade, occasionally a doomed retry into the same pattern) instead of
    just slow.

- **3-tier domain-specialized architecture**: `Planner -> {WebSearcher, AcademicSearcher, PeerReviewer} -> {DocumentAnalyzer, DataAnalyzer}`. `PeerReviewer` is a Planner-tier delegate (independent critique, findings.md or, in report mode, final_report.md), not part of the Searcher→Analyzer chain. *(2026-07-13: a `Builder` Planner-tier delegate was added — see the "Builder sub-agent + Build→Review→Fix loop" entry below. 2026-07-14: a `FindingsWriter` Planner-tier delegate was added the same way, one artifact earlier — see "Planner now only plans and delegates" below. Five Planner-tier delegates total now: WebSearcher, AcademicSearcher, PeerReviewer, Builder, FindingsWriter.)*
- **Planner now only plans and delegates — it cannot write ANY file.** *(2026-07-14, user-driven design question: "the planner should only plan and delegate... giving the planner the job of writing the findings will poison context.")* Previously the Planner wrote `findings.md` itself (the only artifact-writing job it still had after Builder was split out for `final_report.md`) — a real, inconsistent gap: a `findings_ungrounded`/`missing_findings` retry grew the PLANNER'S OWN conversation exactly the way Builder was invented to prevent for `final_report.md`. Confirmed live the same day, independent of this fix: a benchmark run hit 4 consecutive `findings_ungrounded` retries and exhausted its budget with nothing ever written. Fix: new `FindingsWriter` Planner-tier delegate (`src/prompts.py::FINDINGS_WRITER_INSTRUCTIONS`, `src/app.py::findings_writer_agent`), dispatched exclusively by `engine/completion.py`'s generalized Write→Review→Fix loop (renamed from Build→Review→Fix — `_dispatch_build_review_fix` → `_dispatch_writer_review_fix`, now shared by both Builder and FindingsWriter; `_ensure_builder_write_quota_headroom` → `_ensure_writer_quota_headroom` for the same reason) when `missing_findings`/`findings_ungrounded` fires. FindingsWriter never sees the Planner's conversation — its dispatch instructions are built entirely from `RunState`'s structured `data["findings"]` (`{source_url, summary}` per dispatched task, populated automatically by every Searcher/Analyzer call — see `_build_findings_source_material`), plus `read_workspace_file`/`grep_workspace_file` access to go deeper into a raw fetched source if a summary isn't detailed enough. The Planner's `write_workspace_file` tool was removed entirely (`src/app.py`) — it is now structurally incapable of writing any file, the same way it's already structurally incapable of researching. `PLANNER_INSTRUCTIONS` rewritten accordingly (job ends at delegation; `PeerReviewer`/`Builder`/`FindingsWriter` all removed from its Delegation Routing, since none are ever Planner-dispatched anymore). Fallback verdict text for both problems rewritten to never instruct a `write_workspace_file` call the Planner can't make. New test coverage: `_findings_writer_dispatch_scenario` in `test_structural_checks.py`, mirroring `_builder_dispatch_scenario` (CLEAN review, ISSUES FOUND review with corrective re-dispatch, malformed-sentinel conservative handling, missing-registration fallback that doesn't reference the removed tool).
  - **Live-verified end-to-end, two real runs, same day**: architecture confirmed working correctly — `FindingsWriter`/`Builder` both dispatch via their own independent Write→Review→Fix loops, `PeerReviewer` catches real issues in both (confirmed: flagged a genuine `findings.md` problem, triggering a real corrective FindingsWriter re-dispatch; separately flagged a `claim_unsupported` citation in `final_report.md`), and `current_input` stays provably unchanged across dispatches (no context growth) exactly as designed. Traced one flagged citation to its root cause to confirm layer independence, not just redundancy: `findings.md` correctly recorded that `python.org`'s landing page has NO biographical content about Python's creator (a truthful negative finding); Builder then cited `python.org` in support of a creator claim anyway on its own initiative — a downstream synthesis error introduced AFTER FindingsWriter's own review, caught by a completely separate check, not something that leaked through from bad `findings.md` content.
  - **New gap surfaced by this same live testing, not a flaw in the dispatch mechanism itself**: on a run where Builder repeatedly re-committed the identical `claim_unsupported` mistake across separate corrective attempts (never actually fixing it, just re-flagged next cycle), the Planner — resuming each time with `current_input` unchanged and no signal that a Write→Review→Fix cycle just ran — kept deciding to delegate MORE research rather than recognizing the problem was a downstream citation/authoring error, not a research gap. Because `delegate_tasks`'s own quota is independent of `max_completion_check_attempts`, this let a trivially simple factual query ("who created Python") run 25 minutes and fetch 35 URLs before finally exhausting its completion-check budget. The system's OWN safety net still worked correctly at the end — `_restore_quarantined_draft` produced a real, honestly-labeled, mostly-correct report instead of a silent failure or a hang — so this is an efficiency/looping gap, not a correctness or reliability regression.
  - **Fixed the same day**: `run_completion_check` (`src/engine/completion.py`) now wraps its retry loop in `while True:`, and the two successful-dispatch paths (`Builder`, `FindingsWriter`) `continue` straight into the next completion-check iteration instead of `return`ing control to the Planner. A persistently-failing chain (e.g. the `claim_unsupported` loop above) now burns its retries entirely inside one `run_completion_check` call — landing on the same quarantine-restore/salvage outcome, but without the wasted Planner-driven "more research" turns in between. Bounded by the same `attempt < max_attempts` ceiling as before, no new infinite-loop risk (traced: `current_input` is never mutated in place, so the "unchanged on success" invariant that makes the whole Write→Review→Fix mechanism safe holds automatically across any number of internal `continue` cycles). `test_structural_checks.py`'s `_builder_dispatch_scenario`/`_findings_writer_dispatch_scenario` reworked with stateful mocks that genuinely write to the fake workspace (a canned string list can no longer stand in for a dispatch — the chained re-check would just exhaust it and fall through), including a new primary FindingsWriter→Builder chained case and a narrow variant pinning the classic-path fallback when a needed writer role isn't registered.
- **`context_budget_chars` blind spot for the classic inject-into-Planner path, fixed.** `run_stream_chars` (`src/engine/tui.py`'s `run_cli`) only ever counted chars from the Planner's own streamed generation — a completion-check nudge appended to `current_input` outside that stream loop (now only `not_delegated` on the normal path with both writer pairs registered; still any of `missing_findings`/`findings_ungrounded`/`missing_artifact` too if a writer role isn't registered) was invisible to the budget guard, so it could in principle grow the Planner's context unboundedly on repeat with zero accounting. Fixed by measuring the char length of whatever `run_completion_check` actually appended to `current_input` (diffed by list length before/after the call) and adding it to `run_stream_chars` right after the call. `context_budget_chars` remains deliberately headless-only ("TUI Planner exempt") — no TUI-side change needed, matching that existing, documented design choice.
- **Structural reliability fixes**: per-attempt quota top-up, artifact quarantine-before-nudge, structured run-state (`_run_state.json`, now including populated `findings[]`), a real URL-presence + content-level grounding check (`utils/grounding.py`), and history-scanning salvage for a narrated-but-never-written report (fixes the old single-turn-lookback bug that discarded good content when the *final* retry produced empty text — verified against a real saved session log).
- **Upstream verification**: each Searcher specialist's summary is grounding-checked before it reaches the Planner, not just the final report (`settings.grounding_check.verify_specialist_output`).
- **`replan_action` tool**: the Planner's replanning decision is a structured, checkable call (`add_slot`/`verify_conflict`/`finalize_report`) alongside its `think_tool` reasoning, not free text only. *(Deleted in the 2026-07-11 ponytail audit, 929b987 — unused in practice.)*
- **Persona-brainstorming step**: before planning non-trivial queries, the Planner briefly reasons from 2-3 relevant expert perspectives to widen slot coverage.
- **HTML boilerplate stripped on the primary fetch path**: previously only the BeautifulSoup fallback stripped nav/footer/script; the primary markitdown path passed raw chrome straight through.
- **DDGS per-call client** instead of a shared singleton (concurrent specialist searches no longer share one client instance).
- **`extract_structured_data` tool**: real tool-level distinction between `DataAnalyzer` and `DocumentAnalyzer`, not just prompt-driven.
- **Wiki index** (`settings.workspace.wiki_index`): deterministic, engine-maintained cross-run `index.md`, independent of session isolation. *(Deleted in the 2026-07-11 ponytail audit, 929b987 — cross-run state poisons benchmarks, same reasoning as the rejected knowledge cache.)*
- **Heavy search mode** (`settings.search_mode: heavy`): searches deeper and auto-fetches more top results per call, instead of fabricating fake query-variant strings.
- **Human-in-the-loop gate** (`settings.human_in_the_loop`): reuses the existing `ApprovalWidget`/tool-approval infrastructure to gate the Planner's `write_todos`.
- **MCP tool loader** (`settings.mcp_servers`, `tools/mcp_loader.py`): generic loader for `agent_framework`'s native `MCPStdioTool`/`MCPStreamableHTTPTool`, connected per-task via `AsyncExitStack`. Nothing enabled by default; `config_template.yaml` documents two researched, ready-to-uncomment servers (Semantic Scholar MCP, Brave Search MCP) instead of guessed ones.
- **Readable run-folder names**: `<slugified-query>_<timestamp>` instead of a bare unix timestamp.
- **TUI click-to-copy fix**: tries direct system clipboard (`xclip`/`wl-copy`) before falling back to Textual's OSC52 escape sequence, which silently no-ops in terminals that don't support it.
- **TUI paste fixes**: Textual's base `Input._on_paste` silently keeps only the first line of a paste and drops the rest — the query box now flattens a multi-line paste into one line instead. Also debounces a same/prefix paste redelivered within 0.5s, found live: a large pasted prompt showed up with a truncated repeat of its own opening appended, consistent with the terminal re-sending an interrupted paste.
- **Model choice re-tested** against this project's actual nested `delegate_tasks` schema — see README "Model choice". `mistral-nemo:12b` is the default; `devstral:24b`, `hermes3:8b`, `qwen2.5-coder:14b-instruct`, `llama3-groq-tool-use:8b`, and `mistral:7b-instruct-v0.3-q5_K_M` were all tried and rejected for the Planner role. **Re-confirmed on a real demanding query** (2026-07-10): `devstral:24b`, despite being ~2x the parameter count, made zero real `delegate_tasks` tool calls across a full 8-attempt run — it narrated perfectly-formatted JSON in a markdown code block instead of emitting a real structured call, every attempt. Bigger is not better on this schema; the failure is a structured-output habit, not a reasoning/capacity limit. *(Superseded 2026-07-11: the 13-run Colombia B2B benchmark made `deepdelve-gpt-oss` the default — best 7/10, only model in the "usable with verification" band; nemo passes the schema test but ceilings at 2/10 on the full rubric.)*
- **Per-task fetch-tracking race condition, fixed**: the "URLs fetched by this task" delta used to be computed via a before/after length check on the single run-wide shared fetched-URL list, which races under concurrent `delegate_tasks` dispatch — confirmed live (3-task run produced 9 cross-attributed findings instead of 3). Fixed with a proper per-task-scoped contextvar; verified at 10-task concurrency with zero duplication afterward.
- **Delegation-scope relevance check** (`settings.grounding_check.verify_scope_relevance`): flags a specialist's summary when nothing it actually fetched mentions the entity (e.g. a country) its own delegation instructions required. Depended on the race-condition fix above to attribute fetches to the right task at all.
- **`no_urls` gets its own distinct completion-check message** with escalating language (hands back the exact fetched-URL list on repeat failures) instead of reusing wrong-citation wording that didn't fit a report with zero citations. `max_completion_check_attempts` is now configurable instead of hardcoded to 3.
- **Explicit re-delegation directive**: when a grounding-check failure repeats with no new fetches since the last completion-check attempt, the nudge now forces `delegate_tasks` again instead of implicitly assuming enough real findings already exist. Confirmed live to detect the exact failure it targets (a 9-attempt run that never delegated a second time, fetched_url_count stuck at 2 the whole way).
- **`delegate_tasks` rejects unresolved placeholder tasks and same-batch cross-task dependencies** before dispatching — e.g. "sector 1" / "sector X" instead of a real name, or "for each identified sector" bundled in the same batch as the discovery task it depends on. Both patterns were confirmed live (garbage `web_search` queries like `"market size of sector 1 in colombia"`) and both checks verified against real observed strings with no false positives on legitimate task names from working runs.
- **Live test battery**: 15+ live runs across factual lookups, comparative queries, academic paper + related-work queries, current-event queries outside training data, a TUI session, and multiple real market-research queries at varying scope (5, 6, 10, 12, 14 sectors) — see "Findings from live testing" below for what these surfaced.
- **Full strict code audit, 2 real bugs found and fixed**: (1) `eval/evaluate.py`'s `find_latest_session` still filtered on a `run_*` prefix left over from before the "Readable run-folder names" rename above — no current run folder has ever matched `run_*`, so every eval harness run since that rename silently scored raw stdout instead of the actual `final_report.md` artifact. Fixed to take whatever directory exists in the run's isolated workspace, verified against the real slugified-timestamp naming. (2) `remove_workspace_file`'s own docstring claims it "mandates human oversight," but `config_template.yaml` shipped with no `settings.permissions` entry gating it, so that claim was unenforced by default (moot today since no agent is actually wired to this tool yet, but misleading if one ever is). Fixed by adding `settings.permissions.remove_workspace_file: require_approval` to the default config. Also declared `pydantic` as an explicit direct dependency in `pyproject.toml` — it's imported directly in `engine/sdk.py` but was only ever installed transitively via `agent-framework`.
- **`delegate_tasks`'s placeholder-detector false-positive, found and fixed via a real bad-output diagnosis.** Traced a live "neglected markets in Colombia" run (`research_output/do_a_market_research_of_neglected_markets_in_colom_20260710_223455`) where every single cited source turned out to be fabricated. Root cause: the Planner's real, well-formed 12-task batch (each with a genuine specific topic in `instructions`, e.g. `"Assess the needs of logistics and supply chain management in Colombia..."`) was rejected *wholesale* by the placeholder detector because `task_name` used an ordinary numbered label (`"Analyze market 1: ..."`) — the detector checked `task_name` and `instructions` together, so a harmless numbered label falsely tripped the same check meant for a task with no real name anywhere. Facing a full-batch rejection, the model gave up delegating those 12 sectors entirely and fabricated all of `findings.md` from memory instead (including a repeated fake source domain used identically across every unrelated sector) — nothing catches this because the grounding check only runs on `final_report.md`, never on `findings.md`. Fixed by checking only `instructions` for the placeholder pattern, since that's the field that actually becomes a Searcher's query, not `task_name`; verified against both the real rejected 12-task batch (now passes) and the original documented true-positive case (still correctly rejected).
  - **Not yet fixed, same diagnosis**: `findings.md` (Pass 1's "verbatim extraction") is never grounding-checked at all — only `final_report.md` is. A Planner that abandons real delegation partway through a run (as happened here) can fabricate `findings.md` wholesale and nothing structural catches it before Pass 2 treats it as ground truth. Also reproduced live in the same run: hard exclusion rules still don't hold (SESSION_STATUS.md's tracked #2 item) — 4 explicitly-excluded sectors (Agritech, HealthTech, EdTech, VR/AR-Education) were researched and included anyway.
- **Non-URL pseudo-citations now caught** (`settings.grounding_check.non_url_citation_check`, `utils/grounding.py::find_non_url_citations`) — closes the #1-ranked open item above. Re-tested the placeholder-detector fix on a smaller 4-6 sector scope of the same query and confirmed the fix worked (real Colombia-specific sources were actually delegated and fetched this time, no wholesale batch rejection) — but the run still ended in unverified salvage because the model attributed a claim to a bare `(DANE, 2020)`-style parenthetical instead of a real hyperlink, which `extract_cited_urls` never even saw since it only recognizes `https?://`. Added a line-scoped check for a `Source:`-labeled or `(Org, Year)`-shaped attribution with no URL on the same line; runs as a hard gate alongside the existing URL-presence check, on both the final report and each specialist's summary (shared `real_grounding_problem`, so `orchestrator.py`'s upstream check picks it up for free). Verified against the real fabricated line from that run (caught), a well-formed `- **[Title](URL)**`-only report (not flagged), and the exact mixed case SESSION_STATUS.md documented — real URL citations elsewhere in the report plus one bare `Source: Expert opinion from...` line (caught, without disturbing the real citations). Also excludes the engine's own injected `[SYSTEM ... WARNING: ...]` nudge text from the check, found necessary because a salvaged report can carry one of those across a turn boundary and its own use of the word "source" would otherwise self-flag.

- **Headless/headed-browser fetch fallback** (`settings.fetch.headless_fallback`, optional `playwright`+`pyvirtualdisplay` extra — `pip install deepdelve[browser] && playwright install chromium`) for pages that bot-wall a plain `httpx` GET. Motivated by a live-reported bug: real, citable papers on Springer, ScienceDirect, and MDPI were all getting flagged as fake/stub sources. Root-cause investigation (2026-07-14) found three distinct bot-wall signatures, reproduced directly against the reported URLs: Springer served a stripped shell to the plain fetch (200 OK, title only, no body), ScienceDirect returned a Cloudflare Turnstile challenge (initially masked by an additional UA-sniffing "browser is outdated" block, HTTP 400, on the pre-fix stale UA), and MDPI served an Akamai Bot Manager block. `_stub_reason` (`src/tools/web.py`) was correctly flagging all three — the actual gap was one level upstream, no way to get past the wall at all. Fix (`src/tools/web.py::_fetch_raw`, `src/utils/browser_fetch.py`): when the plain fetch looks like a stub, retry once via a real browser before giving up, reusing the exact same boilerplate-strip/markdown pipeline on the browser-rendered HTML. Also bumped the plain-fetch UA string off a stale 2021 Chrome build. Soft dependency, fails open with zero latency cost if Playwright isn't installed — mirrors `utils/parsers.py`'s markitdown soft-import pattern.
  - **Headed beats headless, confirmed live**: MDPI's block turned out to be a headless-specific fingerprint check — it fires at the network/edge level before any JS/DOM loads, so JS-side stealth tweaks (webdriver-flag override, custom UA/plugins/locale, `--disable-blink-features=AutomationControlled`) made zero difference under `headless=True`, but a genuinely headed (non-headless) Chromium sailed straight through, both against a real X session and a freshly-started virtual one (Xvfb via `pyvirtualdisplay`, `DISPLAY` unset beforehand to rule out riding the real desktop's session). Shipped behavior: try headed first (real display, or auto-started Xvfb on Linux) and fall back to headless only when no display is available at all — recovers MDPI in addition to Springer.
  - **Found and fixed one more real bug along the way**: `_strip_boilerplate_html`'s boilerplate-class regex (`cookie|consent|advert|sidebar|...`) is a substring match, so it was deleting Springer's actual 221K-char article-body container because its CSS class (`eds-l-with-sidebar`, a layout hint) happened to contain "sidebar" — silently leaving only the cookie-consent banner behind, which then *passed* the stub check on its own prose mass. Pre-existing bug, invisible until headless/headed fetch started returning real content to trigger it. Fixed with a size guard (elements over 3000 chars are left alone — real chrome is never that large).
  - **ScienceDirect confirmed NOT fixable this way — root cause pinned down precisely, not just "still blocked."** It's gated by Cloudflare Turnstile, not Akamai. Live-tested exhaustively (2026-07-14): the challenge iframe never resolves even after 60s of patient polling with a real headed browser, clicking any checkbox that appears (none ever did — the widget cycles in and out of the DOM every ~6-12s, retrying itself indefinitely). Isolated the cause: Playwright's Chromium exposes `navigator.webdriver: true` by default (confirmed: `page.evaluate("() => navigator.webdriver")` → `True`); spoofing it to `undefined` via `add_init_script` changed the JS-visible value but the challenge *still* never resolved after another 45s of patient polling — so the detection is deeper than any single JS flag, almost certainly Cloudflare fingerprinting the CDP (Chrome DevTools Protocol) connection Playwright itself requires to drive the browser, a much harder thing to hide than `navigator.webdriver` (the reason dedicated "undetected browser" tooling exists as its own arms race, with a poor track record against Cloudflare specifically). **Directly confirmed the same exact URL, same machine/IP, loads cleanly in the user's real (non-automated) Firefox** — ruling out IP-reputation/rate-limiting as the cause; it's specifically automation-fingerprint detection. Deliberately **not pursued further**: defeating this would mean building and maintaining real anti-detection/stealth-patching tooling aimed specifically at circumventing a publisher's bot controls, which doesn't belong in DeepDelve's shipped default behavior even though the underlying paper is legitimately citable — same reasoning as declining to add CAPTCHA-solving. Left as a permanent, honestly-flagged residual gap, not a bug to keep chasing.
  - **Checked whether "just fetch the abstract instead of the full PDF" routes around this — it
    doesn't, for ScienceDirect specifically.** The abstract/landing page (`/science/article/pii/...`)
    IS the same URL already being tested; Turnstile gates that whole page, not specifically a PDF
    download action. Also checked Crossref's metadata API for two real DOIs from the pdfdownload
    review above — no abstracts returned; Elsevier generally doesn't submit them to Crossref. The
    legitimate alternative is Elsevier's own developer API (`dev.elsevier.com`, registered API key,
    often free for text-mining/research use) — noted as a real future option, not started (no key
    exists yet; the tool would need one as a required config value). Worth stating plainly what this
    finding does NOT change: DeepDelve's existing fetch behavior already does the right thing for
    every other publisher — `fetch_url_to_workspace` never tries to get past a paywall to reach a
    full PDF specifically, it just fetches whatever's at the URL, so a typical journal with an open
    abstract page and a separately-paywalled PDF link already naturally grounds on the abstract with
    no special-casing needed. ScienceDirect is the unlucky case where the wall sits in front of the
    abstract too, not a sign anything needs to change generally.
  - TUI/CLI parity: `/toggle_headless_fetch` slash command + status-bar/banner indicator on both surfaces (session-only, not persisted, same as `report_style`).
- **The three remaining structural fabrication gaps, closed in one pass (2026-07-11, first Windows-side session).** (1) `findings.md` wholesale-fabrication gate: `run_completion_check` now flags a Pass-1 file with zero cited URLs or where not one cited URL matches a real fetch (`utils/grounding.py::fully_ungrounded`, `settings.grounding_check.check_findings`), quarantines it and forces re-delegation — deliberately laxer than the strict per-URL final-report check, since Pass-1 notes legitimately mention unfetched snippet URLs. (2) Structural exclusion enforcement: `delegate_tasks` extracts explicit exclusions from the original query (`_extract_excluded_topics` — only unambiguous cues like "excluding"/"except", NOT "avoid", which appears inside legitimate topics) and *skips* matching tasks individually rather than rejecting the batch, because the placeholder-detector incident showed wholesale rejection makes the model abandon delegation and fabricate. (3) Unresolved-referent rejection: a live delegated task "Summarize its headline feature." searched the web with no idea what "its" meant and returned Microsoft Research patent statistics as Python's headline feature — short instructions leaning on a bare pronoun with no proper-noun/digit/quote anchor are now rejected with guidance to restate the subject (`_lacks_concrete_subject`, kept deliberately conservative given the placeholder false-positive history). All three covered by `test_structural_checks.py`; verified live that none false-positive on a clean run.
- **Windows migration (dual-boot, same NTFS drive).** Ollama model store shared via `OLLAMA_MODELS=D:\Projects\AI shit\Models`; cp1252 UnicodeEncodeError in headless mode fixed with a UTF-8 reconfigure guard in `app.py`; `markitdown[all]` is unresolvable on Windows/py3.14 (silently downgrades to a 0.0.2 stub) so `pyproject.toml` now pins the doc extras actually used. Full pipeline re-verified live on Windows (ROCm on an RX 9060 XT 16GB).
- **Production batch (2026-07-11, commits `2ef3f46`..`ee63b0d`), all validated live during the 13-run benchmark day:** `findings.md` existence gate (`missing_findings` — runs 10/11's exact failure, Pass 1 now structurally required before `final_report.md` is accepted); `--resume-run` (reattaches an interrupted run: same workspace, fetched URLs restored into the grounding check, engine-built resume briefing); TUI intake clarifier (`clarify_before_research`, fail-open); `settings.max_run_minutes` run budget; `--depth quick|standard|deep` presets; repeatable `--seed-url`; finish-line summary + `--list-runs`; TUI follow-up continuity (Q&A mode on an existing report); **`regulation_id_check`** (a law number cited to a genuinely-fetched source that never mentions that number — run 12's "Ley 1906 de 2021" failure class, caught live on first deployment in run 13); **quarantined-draft restore at final verdict** (runs 11/13 ended with a real draft in `.rejected_attempt_N` while salvage delivered meta-narration — the draft now wins, loudly labeled).
- **Completion-check refactor (2026-07-12):** the ~250-line if/elif verdict chain in `tui.py` — which shipped the swallowed-elif bug twice (bd307f4, run 13) — is now a data-driven check list in `src/engine/completion.py` (`check_<problem>(ctx) -> Verdict|None`, first verdict wins, no elif headers to swallow), pinned by a 10-row verdict matrix in `test_structural_checks.py` (mutation-verified) and a CLAUDE.md suite-before-commit rule.
- **`_get_safe_path` Windows workspace escape, fixed (2026-07-12):** `os.path.join(base, "C:\evil")` discards the base entirely, so drive-qualified/drive-relative filenames escaped the workspace (Planner has `write_workspace_file`). Drive-lettered names now rejected outright + abspath containment check on disk workspaces. External review #2's one HIGH finding.
- **Documentation update pass (2026-07-12, `a4d8380`):** config template default flipped to `deepdelve-gpt-oss`, README model-verdict table + new CLI flags + headless failure semantics + `sources/` provenance; ROADMAP synced.
- **Context-budget endgame guard (`98ef24a`):** ROADMAP candidate from Tongyi's `react_agent.py`
  — local models run at `num_ctx ~16384` with no context accounting, so on overflow Ollama
  silently truncates from the TOP (eating the system prompt mid-run, indistinguishable from model
  collapse). `settings.context_budget_chars` (template default 50000) counts text + tool args +
  results per agent stream; on overshoot the turn is cut and the agent gets one forced wrap-up
  turn (sub-agents return findings immediately; at the time this shipped the headless Planner
  wrote `final_report.md` directly on overshoot too — since 2026-07-13 that's Builder's job, and
  the Planner's own wrap-up now only affects `findings.md`), a second overshoot forces the
  completion check's final verdict. TUI Planner exempt. Verified live with a 3000-char budget:
  honest "budget exhausted" report, no silent truncation.
- **Grounding-layer hardening batch (2026-07-12 evening, `7f0782f`..`5c24607`), every fix validated live in run 15:** stub-fetch detection (soft-404/paywall shells recorded as `stub` in `fetched_urls`, refused by all grounding checks, own `stub_source` verdict — closes run 14's invented-URL hole; 10/21 run-15 fetches flagged, zero false positives); Source-URL header self-grounding fix (the injected line-1 header's URL slug no longer counts as source content); charset fix (HTML decoded by real encoding — strict UTF-8 → header → meta → cp1252; stale meta tags scrubbed so markitdown can't re-mojibake; Spanish accents verified intact live); citation-format enforcement (`uncited_claims`: ≥3 figure-bearing lines with no citation in an h1-h3 section without URLs — run 14's table + detached "Source URLs" shape; section-scoped after run 15 caught the per-niche `#### Sources` false positive); URL prefix-boundary fix (fetched `.../article` no longer grounds fabricated `.../article-fake-2024`); `grounding_check.enabled` master switch actually honored; platform-independent drive-letter guard (splitdrive silently stopped rejecting `C:\evil` after the Linux migration).
- **Repo governance + CI (2026-07-12)**, triggered by an external audit's one genuinely real
  finding (the repo is public with no LICENSE): `LICENSE` (MIT), `.github/workflows/ci.yml`
  (install + `ruff check` + `test_structural_checks.py` on push/PR to main, verified green in a
  clean throwaway venv before ever touching GitHub), a pragmatic `[tool.ruff]` config
  (pyflakes-only — `E`/`I` generated 189 line-length/import-sort hits that were pure style noise
  against this codebase's established dense-comment/lazy-import conventions; narrowed to `F`,
  which found 21 real issues: dead imports, an unused variable left over from this session's own
  `_fetch_raw` rewrite, one f-string-without-placeholders). Floor+ceiling dependency pins
  (`agent-framework`, `httpx`, `textual`, `beautifulsoup4`, `PyYAML`, `ddgs`, `markitdown`,
  `pydantic` — E12, previously only `markitdown` was pinned) + `requirements.lock` (192-package
  `pip freeze` snapshot from a clean install). The rest of that audit's "critical" findings
  (no iterative loop, no token budgeting, TUI blocks on LLM calls, needs a DI rewrite, roadmap
  "contradictions") were checked directly against the code and found false or already solved —
  see the session's plan file for the full point-by-point rebuttal; not reproduced here since
  none of it required a code change.
- **Academic / literature-review output mode (2026-07-12), triggered by a real gap**: a live
  sales-forecasting query got a properly-structured literature-review paper from DeepSeek
  (`eval/reference/sales_forecasting_deepseek.md`, `(Author, Year)` citations + numbered
  References) while `deepdelve-mistral-nemo` collapsed on the same query through DeepDelve
  (`eval/sales_forecasting_benchmark.md` — 9 completion-check attempts, no accepted artifact).
  `settings.report_style` / `--style standard|academic` (orthogonal to `--depth`, which only
  changes tool budgets): academic style rewrites `PLANNER_INSTRUCTIONS`' Report Structure step to
  a literature-review shape (Abstract, Introduction, thematic sections, Cross-Cutting Synthesis,
  Quantitative Benchmarking Summary, Challenges & Future Directions, Conclusion, References) —
  modeled on `imbad0202/academic-research-skills`' `literature_review_template.md` (see README
  References) — and swaps the citation-format instructions to `(Author, Year)` in-text + a
  numbered References list, instead of the default inline `- **[Title](URL)**`. Also carries that
  repo's **Anti-Leakage Protocol** ("Knowledge Isolation Directive": prefer `findings.md` over
  parametric memory, write "Not covered by this run's research" instead of inventing a section).
  `utils/grounding.py` gained `parse_academic_references` (maps `(surname, year)` keys to the
  URL on that References entry — an entry with no real URL stays unresolvable, same failure mode
  as a fabricated inline citation) and every line-scoped check
  (`find_non_url_citations`/`find_uncited_claim_lines`/`claim_grounding_problem`/
  `find_unsupported_regulation_ids`) now resolves academic citations through it alongside the
  existing inline-URL format — same grounding guarantees, second citation dialect. A real bug was
  caught building the test coverage: a line with TWO `(Author, Year)` citations only had its FIRST
  one checked (regex `.search()` vs `.finditer()`), so a real citation earlier on a line could mask
  a fabricated one later on the same line — fixed, pinned by a dedicated test row. A fresh audit
  pass then caught a HIGHER-severity bug in the same feature before any live run: the citation
  detector required every token before the comma to start with an ASCII capital, so it silently
  failed to even DETECT "et al."/"&"/"and"/accented-surname citations at all — exactly the forms
  the feature's own prompt tells the model to use — breaking grounding in both directions
  (a fabricated multi-author citation went undetected; a well-formed one got wrongly quarantined).
  Fixed, plus a related mis-keying bug (a reference entry's own title could shadow its real
  author/year) and 5 more regression rows. 13 total assertions in `test_structural_checks.py`.
  **Live-validated 2026-07-12** (`deepdelve-gpt-oss --style academic` against
  `eval/sales_forecasting_benchmark.md`, 21.5 min,
  `research_output/i_want_documentation_on_heuristic_algoritms_for_de_20260712_144216`): the
  literature-review shape was produced correctly end to end (Abstract, Introduction, thematic
  sections with tables, Cross-Cutting Synthesis, Challenges & Future Directions, Conclusion,
  numbered References), org-style `(Wikipedia, 2026)`/`(Papaya Global, 2026)` citations all
  resolved with zero false positives from the citation-format work. The run's one real failure —
  quarantined at `not_grounded` (`unverified_urls:https://en.wikipedia.org/wiki/Heuristic`, cited
  without its `_(computer_science)` disambiguator, vs. the actually-fetched
  `.../wiki/Heuristic_(computer_science)`) — is the pre-existing hard URL-presence gate correctly
  catching a genuine citation-accuracy slip, not a defect in academic mode. The subsequent
  8-attempt `missing_artifact` stall (model never rewrote after quarantine) reproduces the
  already-documented gpt-oss endgame-collapse weakness (runs 11/13); the quarantined-draft-restore
  fix delivered the real, mostly-correct draft with a loud warning banner instead of losing it to
  salvage narration, exactly as designed.

- **Checkmark-on-error TUI bug fixed (2026-07-12, `ad07a5f`)**: `ToolCallWidget.set_result` always
  rendered a green checkmark regardless of the result text — a real run showed a
  `read_workspace_file` call marked complete despite returning an error. New
  `_looks_like_tool_error()` (matches "Error:"/"CRITICAL TOOL EXECUTION ERROR"/"forcefully
  aborted") drives both the TUI glyph and a new `RunState.record_tool_error` counter/sample log.
- **Fuzzy-filename fallback for `read_workspace_file`/`grep_workspace_file` (2026-07-12)**: traced
  root cause of a run that gathered substantial research (33 fetches, 38 findings) but never
  produced a report — 16% of workspace-read calls used a garbled/truncated filename reconstructed
  from memory by a sub-agent one hop removed from the original fetch (e.g.
  `sources/nixtaverse_nixta?`), each failure burning a turn and a quota unit, cascading into
  `QuotaAbortException` aborts. `resolve_fuzzy_filename()` in `src/tools/fs.py`
  (`difflib.SequenceMatcher`, conservative single-best-match threshold) now auto-resolves these
  instead of erroring.
- **Structured `_run_state.json` logging (2026-07-12)**: full completion-check verdict detail
  (not just the problem label) now persisted per attempt; `RunState.record_tool_error`
  (count + samples); `RunState.next_subagent_label` disambiguates repeat dispatches of the same
  task name (`SubAgent_x` → `SubAgent_x#2`, with a collision-avoidance guard against a task
  literally named to collide with the auto-generated suffix) so post-hoc elapsed-time analysis on
  sub-agents is trustworthy without hand-parsing the raw session log. Live-validated end-to-end in
  the answer-mode smoke test below.
- **`/resume-run` added to the TUI (2026-07-12)**: was CLI-only for a full prior session
  unnoticed — the exact scenario it exists for (a quarantined run with real work already on disk)
  happened and had no TUI path. New no-argument slash command with a picker, reusing the existing
  headless `load_resume_state`/`build_resume_input` logic. Prompted two new CLAUDE.md rules:
  mandatory TUI/CLI feature parity checks, and tracing a change's blast radius across sibling
  surfaces before calling it done.
- **Answer mode (2026-07-12)**, from the `dzhng/deep-research` candidate below: third
  `report_style` option (`standard`/`academic`/`answer`) — a short 1-3 sentence direct answer, no
  section headings, inline `(Source: [Title](URL))` citation instead of a References list.
  **Live-validated** on `deepdelve-gpt-oss`: first attempt hit a real `claim_unsupported`
  quarantine (model's citation format deviated from spec, no square brackets around the title);
  the completion-check cycle correctly caught it and nudged a rewrite; attempt 2 passed with a
  clean short answer — confirms `extract_cited_urls` tolerates the format deviation and that the
  quarantine/nudge cycle generalizes to a third report style, not just the original two.

- **TUI `ProcessingWidget` timer leak fixed (2026-07-12, `e24ecd8`)**: caught live — a run's final
  turn (model's response after tool quotas were exhausted, with nothing left to say) streamed zero
  content, so `ProcessingWidget.stop()` — gated on the turn's first content token — never fired.
  Its `set_interval` animation kept climbing the elapsed-seconds counter indefinitely, well past
  the point the run had already reached its quarantine-restore final verdict, making a genuinely
  finished run look stuck. Same UI-implies-false-run-state bug class as the checkmark-on-error fix
  earlier this session. Fixed with unconditional cleanup once the stream is guaranteed exhausted,
  not just the reactive first-token path. Checked `run_cli` (no equivalent — plain stdout writes,
  no stateful timer widget there).

- **NIM cross-model benchmark (2026-07-12/13)**: the standing heuristics-algorithms/sales-forecasting
  benchmark query run against `deepseek-ai/deepseek-v4-pro`, `nvidia/llama-3.3-nemotron-super-49b-v1.5`,
  and `openai/gpt-oss-20b`, all via NVIDIA NIM. deepseek-v4-pro crashed on an uncaught 429 mid-run
  (real progress lost, not a quality issue); nemotron-super-49b made zero real `delegate_tasks`
  calls and fabricated 100% placeholder `example.com` citations in its wrap-up; gpt-oss-20b was the
  only one to reach a clean pass, but the report was thin and its one real citation had a wrong
  paper title (the exact failure class Track 1 below now catches). None beat local `gpt-oss:20b` —
  confirms a single general-purpose LLM handling research+synthesis+verification end-to-end has a
  real ceiling here, not just a local-model weakness, directly motivating the two tracks below.
- **Two tracks of "specialized non-LLM component instead of another LLM call" (2026-07-12/13)**,
  informed by FactScore's decompose-then-verify pattern and HALT-RAG's combine-lexical-and-NLI
  finding (don't replace term-overlap with NLI, layer it on top), plus independent confirmation
  that Anthropic's own multi-agent research system beats a single agent by 90.2% specifically on
  deep research — validating DeepDelve's existing Planner→Searchers→Analyzers shape, not just the
  new work here:
  - **Track 1, NLI-based grounding verification** (`dc977a6`): `nli_unsupported_problem` in
    `utils/grounding.py` — a small `cross-encoder/nli-deberta-v3-small` entailment classifier
    (86M params, CPU-only, lazy singleton, fails open on any load error) runs only on claim lines
    that already passed the cheap term-overlap check, scored against the source's own
    best-matching paragraph window, flagging only on contradiction (never neutral). Catches a
    citation with the right source and shared terms but a wrong specific detail — e.g. a paper
    title quoted with one word swapped ("Dual Causal Network" vs. the source's real "Dual
    Correlation Network," the NIM benchmark's exact failure above). `settings.grounding_check.nli_verify`
    (default `true`, fail-open). First ML/NLP dependency this project has taken on; caught and fixed
    a real footprint issue (`pip install sentence-transformers` pulls the full CUDA torch build,
    ~6GB, even though nothing here touches a GPU — switched to the ~200MB CPU-only wheel).
  - **Track 2, fetch-time metadata extraction** (`05b175b`): `_extract_html_metadata` in
    `tools/web.py` pulls title/author/published-date from the same BeautifulSoup parse
    `_strip_boilerplate_html` already builds, written as `Title:`/`Authors:`/`Published:` header
    lines alongside `Source-URL:`. Eliminates the "Extract title/authors/abstract from [paper]"
    sub-agent dispatch pattern that fired 13 times identically in one day's logs — the single most
    repeated mechanical delegation observed.
  - **Live-verified end-to-end** on local gpt-oss (not just mocked tests): confirmed real
    `Title:`/`Authors:` headers on fetched sources and confirmed the old mechanical metadata
    sub-agent pattern never fired once in the verification run.
- **Uncaught crash on malformed-tool-call retry exhaustion, fixed + TUI parity added** (`f5dd1af`):
  a huge `write_workspace_file` argument got truncated mid-JSON by the model; the existing 2-retry
  recovery correctly retried twice, but the 3rd consecutive occurrence hit a bare `raise` that
  killed the whole run with an uncaught 500 — at attempt 8/8, after 18 real sources already fetched
  and 5 report attempts already written to disk. `run_cli` now degrades to the same final-verdict
  path used for `max_run_minutes`/`context_budget` exhaustion instead of crashing; `run_agent` (TUI)
  gained the identical retry-then-degrade logic it previously had none of at all for this failure
  class (CLAUDE.md TUI/CLI parity rule).
- **Tool-call validation-error visibility gap found and fixed** (`5eb8fbc`): a full-day log
  cross-reference found `"Error: Argument parsing failed."` was the single most common error
  signature of the day (41 occurrences) — and every one had its actual cause silently stripped,
  because `agent-framework`'s `include_detailed_errors` config was never enabled. Enabled on the
  shared client (helps the model self-correct on retry too, not just diagnostics). Two of the 41's
  concrete root causes fixed the same commit: `grep_workspace_file` was missing `pattern` in 13
  occurrences (the model was using it to check file existence, not search — docstring now says so
  explicitly) and `fetch_url_to_workspace` was missing `filename` in 5 (made optional with an
  auto-derived default, since a missing REQUIRED field is rejected by schema validation before the
  function body ever runs and can't be caught defensively inside it). ~15 more `web_search`
  multi-item-query failures investigated but inconclusive offline — will be diagnosable live now
  that detailed errors are on.
- **Second live-confirmed completion-check stall, `missing_findings`, fixed** (`66fae56`): a
  verification run produced literally zero content (no tool call, no text) in response to this
  nudge for 6 consecutive attempts, then genuinely self-corrected with real content on the 7th — a
  different failure shape from `missing_artifact`'s (which never self-corrected without help).
  Wording escalates after the first occurrence and, from the second on, hands the model its own
  actual fetched URLs verbatim as proof real material exists — deliberately WITHOUT
  `missing_artifact`'s aggressive early-cutoff, since that would have killed this exact run's real
  recovery at attempt 3, before its genuine success at attempt 7. Superseded for report-authoring
  problems by the Builder Build→Review→Fix loop below, but `missing_findings` itself stays
  Planner-escalated (see that entry) since it means Pass 1 was skipped, not that the report is bad.
- **Builder sub-agent + Build→Review→Fix loop (2026-07-13)** — the direct fix for the context-growth
  risk above. User's diagnosis: the Planner's own conversation only ever grows across a run (no
  compaction exists in the underlying `agent-framework` session; every completion-check retry
  historically meant appending another nudge and re-showing the model its own rejected drafts) —
  "context poisoning," a documented failure mode where an agent's own accumulated context degrades
  its attention well before any hard token limit. Maps onto the established "Plan-and-Execute"
  agentic pattern (see README References) and reuses the *existing* `delegate_tasks`/
  `_run_single_task` mechanism, which already gives every dispatched sub-agent a genuinely fresh,
  isolated context — the fix is routing report-writing retries through that mechanism instead of
  the Planner's own conversation, not inventing a new one.
  - New **Builder** role (`src/prompts.py`, `src/app.py`) — writes/rewrites `final_report.md` from
    `findings.md`. The Planner no longer writes or delegates the report at all; its own instructions
    end at Pass 1 (`findings.md`, optionally reviewed by `PeerReviewer`).
  - `src/engine/completion.py` classifies completion-check problems into **Builder-fixable**
    (`missing_artifact`, `not_grounded`, `claim_unsupported`, `non_url_citation`,
    `regulation_unsupported`, `stub_source`, `nli_unsupported`, `uncited_claims` — all fixable by
    rewriting the report from the SAME `findings.md`, no new research needed) vs.
    **Planner-escalated** (`missing_findings`, `findings_ungrounded`, `not_delegated` — genuinely
    need more/different research, which only the Planner can decide to delegate).
  - For Builder-fixable problems, `run_completion_check` dispatches a **Build → Review → Fix**
    sequence directly — Builder rewrites the artifact, a fresh `PeerReviewer` dispatch reviews the
    result (generalized to review either `findings.md` or `final_report.md`, with a required
    `REVIEW: CLEAN` / `REVIEW: ISSUES FOUND:` opening line so the caller can branch without another
    LLM call — a malformed/missing sentinel is treated conservatively as ISSUES FOUND), and Builder
    gets exactly one corrective re-dispatch if flagged. None of this touches the Planner's own
    `current_input` — `run_completion_check` returns it byte-for-byte unchanged on this path, which
    is the actual regression `test_structural_checks.py`'s new scenario pins. Reuses the existing
    attempt budget/escalation threshold and quarantine/salvage machinery unchanged (all already
    filesystem-only, no conversation-state coupling).
  - **Live-validated end-to-end, two runs** (`deepdelve-gpt-oss`, 2026-07-13):
    - **Simple factual query** ("current stable Python version + headline feature"): hit
      `missing_artifact` on attempt 1, dispatched Builder (wrote the report), dispatched
      PeerReviewer (`REVIEW: CLEAN`, no corrective pass needed), completed cleanly in 691s with the
      Planner's own conversation untouched by any of it (`_run_state.json` shows exactly one
      Builder-fixable cycle, `None` on the next check). Confirms the clean-pass path works
      end-to-end exactly as designed.
    - **The standing heuristics-algorithms sales-forecasting benchmark** (the same 3-way-AND query
      already documented above as having no source satisfying all three criteria — genuinely hard,
      not a fluke): the loop DID fire correctly 3 times on real `not_grounded` problems (a
      fabricated arXiv URL), each time dispatching Builder then PeerReviewer without touching the
      Planner's conversation. **New finding, not previously possible to observe**: on attempts 4-6,
      Builder itself hit the SAME "narrate instead of write" failure the Planner used to be prone
      to — because Builder shares the run's single `write_workspace_file` quota pool with the
      Planner and every prior Builder dispatch, and by attempt 5 that pool was exhausted; Builder's
      own text even says so explicitly ("I'm unable to create new files because the
      `write_workspace_file` quota has been exhausted"). The pre-existing quarantine-restore
      fallback caught this correctly at the final verdict — restored the best surviving draft
      (from the attempt-3 quarantine) with its loud unresolved-check banner, an honest labeled
      recovery rather than a silent failure or a lost draft. Total run time (1448.7s) was longer
      than this exact query's earlier pre-Builder baseline (1174.4s, ended in unlabeled "retry
      budget exhausted" instead) — extra wall-clock from the added Build/Review dispatch turns, on
      a query the architecture was never going to make suddenly satisfiable. **Net assessment**:
      the Build→Review→Fix mechanism itself works as designed (dispatch routing, sentinel parsing,
      current_input staying untouched, all confirmed); it does not (and isn't meant to) rescue a
      query where the source material genuinely doesn't exist, and it surfaced a new, real
      quota-sharing constraint under heavy retry load — see "Carried forward" below.
  - Deferred, documented as a known residual gap rather than blocking this change:
    `context_budget_chars`/`stream_content_chars()` still doesn't count text injected outside a
    stream's own generation loop, so the 3 Planner-escalated problems can still in principle grow
    the Planner's `current_input` unboundedly on repeat — lower priority since those problems are
    rarer/more terminal (a stuck Planner, not an oscillating-on-polish loop).

- **Phase 1 of the approved 6-phase plan: claim-level grounding upgrade (atomic-claim
  decomposition + per-claim evidence binding).** (Found 2026-07-13, informed by FActScore's
  decompose-then-verify pattern (arXiv:2305.14251, already cited in README for the NLI check) and
  Rasheed et al.'s claim-evidence provenance framing (arXiv:2602.13855).) The prior
  `claim_grounding_problem` compared a WHOLE LINE's terms against the UNION of every source cited
  anywhere on that line — a real gap when a line carries two distinct claims each with its own
  citation (e.g. "Sector A grew 12% [gov](url1), while Sector B declined 3% [news](url2)"): a
  shared generic term between claim A and claim B's source could mark BOTH claims "supported" even
  though claim B's own citation didn't actually back it. New `utils/grounding.py::decompose_claim_segments`
  splits a line into atomic segments at each citation boundary (mechanical regex-token splitting,
  no NLP, no new dependency — matches the "the decomposition step only splits propositions, it
  doesn't decide what's true" design goal); `claim_grounding_problem` now checks each segment only
  against its OWN bound citation's source, closing the citation-sharing/drift gap. A line with
  zero or one citation decomposes to itself unchanged, so this is a strict refinement — every
  previously-passing single-citation-per-line test is unaffected (verified: full suite passes with
  zero pre-existing assertion changes needed). New tests: `decompose_claim_segments` unit
  assertions (single-citation invariance, 2-citation split, trailing-uncited-text handling) plus a
  live-shaped same-line scenario in `test_structural_checks.py` (a genuinely-supported cacao claim
  and a fabricated software claim sharing one line, each with its own distinct citation — correctly
  flags only the fabricated one, by its own citation, not the supported one's). **Residual note
  — CLOSED 2026-07-14, commit `fa2e562`**: `nli_unsupported_problem`/`topical_relevance_problem`
  (both driven by the shared `_grounded_claim_pairs`) had the same latent whole-line term-overlap
  gap this pass fixed for `claim_grounding_problem` — now ported to
  `utils/grounding.py::_grounded_claim_pairs`, iterating `decompose_claim_segments(line)` the same
  way. New test: `_grounded_claim_pairs_scenario` (pure function, no NLI model load needed) pins a
  same-line two-claim case yielding two correctly segment-scoped pairs.
- **`check_excluded_topic` — report-write-time enforcement of query exclusions, closing the gap in the "Hard exclusion rules" finding below.** `delegate_tasks` already skipped DISPATCHING a task whose own topic matched an explicit query exclusion (`_extract_excluded_topics`), but did nothing to stop that topic showing up as its own section in the final report anyway, recalled from a sibling task's tangential findings. New `engine/completion.py::check_excluded_topic` (a `GROUNDING_CHECKS` entry, `_BUILDER_FIXABLE_PROBLEMS` member) reuses the exact same `_extract_excluded_topics` parser, now applied to `final_report.md`'s own h1-h3 heading sections (`utils/grounding.py::split_into_heading_sections`, extracted from the existing `find_uncited_claim_lines` section-scoping logic so both share one implementation) — deliberately heading-scoped rather than whole-document substring matching, so a topic mentioned once in passing prose doesn't false-positive the way a bare match would. New verdict-matrix row in `test_structural_checks.py` (query exclusion + a report with its own "## Sector Agritech" section).
- **Phase 2 of the approved 6-phase plan: cross-source contradiction detection (FEVER-style, Thorne
  et al., NAACL 2018, `fever.ai`).** Depends on Phase 1's claim segmentation
  (`decompose_claim_segments`). New `utils/grounding.py::find_cross_source_contradictions`: builds
  a (subject_phrase, figure) index of every OTHER fetched source's own claims
  (`_extract_figure_claims`, each subject paired with its NEAREST same-line figure by character
  distance, not a full cross-product — avoids cross-contaminating unrelated subjects sharing a
  line), then for each report claim segment, checks whether a DIFFERENT fetched source (one not
  cited on that segment) reports a same-kind (`_figure_kind` — never a year against a percentage)
  but numerically different figure for the same subject, unmentioned anywhere else in the report.
  Distinct from `claim_unsupported`: the cited source really does support the claim — this instead
  catches the report silently picking a side of a real disagreement between two fetched sources
  without saying so. New `engine/completion.py::check_cross_source_contradiction`
  (`GROUNDING_CHECKS` entry, `_BUILDER_FIXABLE_PROBLEMS` member — Builder is told to surface both
  figures rather than pick one). New verdict-matrix row (two fetched sources reporting 12% vs 18%
  for "Sector Fintech", report cites only the 12% one) plus isolated pure-function sanity checks
  during development that caught and fixed a real bug before it shipped: an early version paired
  every subject with every number on a line regardless of kind, so a line naming both a year and
  an unrelated percentage spuriously "contradicted" any other source's differing percentage for a
  totally unrelated reason — fixed by the same-kind guard and nearest-figure pairing.
  - **Second real bug, found live 2026-07-14 during Phase 6's TUI smoke test, fixed and
    live-verified.** A citation attribution (an organization name appearing ONLY inside a
    `- Source: [Title - Statistics Iceland](url)` line, and dozens of times across a long fetched
    Wikipedia article as bare source attribution / image captions / reference-list entries, never
    as the subject of an actual claim) got treated by `_extract_figure_claims` as a genuine claim
    subject, paired with an unrelated nearby year by the nearest-figure heuristic — firing
    `cross_source_contradiction` on the exact same phantom issue after every single Builder
    rewrite, a structurally unfixable, non-converging retry loop (Builder can't satisfy a check
    based on a false premise). Caught only because the user pushed back on accepting the loop at
    face value ("you're not analyzing the run properly") rather than assuming it was Phase 6's
    stream-handling. Fixed with new `utils/grounding.py::_is_citation_only_line` — a line is
    bibliographic, not a claim, if fewer than 8 letters of real text remain after stripping
    markdown links and a leading bullet/number/"Source:" marker; `_extract_figure_claims` now
    skips citation-only lines entirely, on both the report's own prose and each fetched source's
    raw content. Verified two ways: (1) pure reproduction against the real saved report +
    Wikipedia source from the killed run, confirming `find_cross_source_contradictions` went from
    a real hit to `[]`; (2) a fresh live re-run of the identical query converged in 1 Builder cycle
    and 307.2s (vs. 5+ cycles and never converging before). New regression test
    `_cross_source_citation_line_scenario` in `test_structural_checks.py`, confirmed not to weaken
    the existing genuine-contradiction verdict-matrix row.
- **Phase 3 of the approved 6-phase plan: xQuAD-style search-result diversity reranking** (Santos,
  Peng, Macdonald, Ounis, *Explicit Search Result Diversification through Sub-Queries*, ECIR 2010).
  DDGS already ranks by its own relevance signal, but several near-duplicate results for the same
  angle commonly dominate the top of that ranking — addresses the already-documented "scaling down
  scope did not improve grounding rate" finding (a 5-source run still only surfaced ~5 genuinely
  distinct sources, thin discovery even at small scope). New `tools/web.py::_diversity_rerank`:
  greedily reorders `web_search`'s results by MARGINAL new aspect-term coverage instead of raw
  rank — DDGS's own #1 always stays first (preserving its relevance judgment for the single best
  result), then each subsequent pick is whichever remaining result adds the most new aspect terms
  (`_result_aspect_terms`, a deliberately looser local term extractor than
  `utils.grounding.extract_salient_terms` — a short snippet needs single-word distinguishing terms,
  not just 2+-word capitalized phrases, same reasoning `orchestrator.py`'s `_extract_scope_entities`
  already documents for not reusing `extract_salient_terms` either). Pure reranking, no LLM call,
  no new dependency. Single integration point (`web_search`, right after search-health recording,
  before the auto-fetch slice) improves both consumers downstream — the auto-fetch selection and
  the returned snippet ordering — without touching either consumer directly. New tests in
  `test_structural_checks.py`: a near-duplicate-heavy case (3 near-identical fintech results + 1
  genuinely distinct agritech result — the distinct one gets promoted to position 2), empty/single-
  result edge cases, an already-diverse case (order preserved), and direct `_result_aspect_terms`
  stopword/length-filter assertions.
- **Phase 4 of the approved 6-phase plan: topical-relevance cross-encoder reranker.** Third-stage
  grounding check, layered after `claim_grounding_problem` (term-overlap) and
  `nli_unsupported_problem` (entailment) — reuses the exact same evidence set as the NLI check
  (extracted into a new shared `_grounded_claim_pairs` helper, factored out of both functions) but
  asks a different question: is the cited source actually about the SAME SUBJECT as the claim, not
  just lexically overlapping and non-contradictory? Fixes the GOA-algorithm-vs-Goa-state acronym
  collision above — 'GOA'/'Goa' term-overlap passes and an EV-policy sentence about Goa doesn't
  *contradict* an algorithm claim (it's just unrelated), so neither upstream layer can catch it.
  New `utils/grounding.py::_get_topical_relevance_model`/`topical_relevance_problem`: a second
  `sentence-transformers` `CrossEncoder` checkpoint, `BAAI/bge-reranker-v2-m3` — **not a new pip
  dependency**, `sentence-transformers` is already installed for the NLI check, this just loads a
  second checkpoint through the same library. Constructed with an explicit `Sigmoid` activation so
  `.predict()` returns a 0-1 relevance probability directly. New `engine/completion.py::check_topical_mismatch`
  (`GROUNDING_CHECKS`/`_QUARANTINE_PROBLEMS`/`_BUILDER_FIXABLE_PROBLEMS` member, mirrors
  `check_nli_unsupported`'s string-prefix-matching pattern exactly). New config keys
  `settings.grounding_check.topical_relevance_check`/`topical_relevance_threshold` (default 0.1),
  documented in `config_template.yaml`. **Verified against the REAL checkpoint, not just the
  mocked test** (`test_structural_checks.py`'s `_topical_relevance_scenario` mocks the model the
  same way `_nli_verify_scenario` does, to keep the suite fast/offline): loaded the real model
  standalone and scored the exact GOA/Goa pair — the irrelevant (Goa-state) pair scored **0.023**,
  the relevant (GOA-algorithm) pair scored **0.997**, a huge margin either side of the 0.1
  threshold, confirming the Sigmoid-activation design assumption was correct before it ever
  reached a live run. **Real bug caught and fixed during this same pass**: the new check's config
  gate wasn't included in the test suite's existing `nli_verify: False` guards (4 call sites),
  so the first full-suite run after wiring it in silently loaded the REAL, unmocked
  bge-reranker-v2-m3 model — the exact anti-pattern that guard was built to prevent, now closed at
  all 4 sites plus a 5th (`_nli_verify_scenario` itself, which needed `topical_relevance_check:
  false` added since it deliberately leaves `nli_verify` on).

- **Phase 5 of the approved 6-phase plan: coverage accounting / ResearchMap.** Distinct from every
  other completion check: those all verify content that ALREADY EXISTS is properly grounded/cited;
  this instead asks whether the Planner's own top-level delegated research plan actually paid off
  — a report can be perfectly grounded yet still be thin because most of the Planner's own
  delegated angles came back with nothing usable and got silently dropped rather than surfaced or
  retried. Deliberately built entirely from already-reliable, model-independent structural data
  instead of a new Planner-authored schema (investigated first and explicitly ruled out: `_todos.md`
  is free text with only a prompted, zero-code-validated convention — exactly the kind of
  compliance-dependent signal this project's own established philosophy avoids, given repeated
  live failures of small local models following new structured-output requirements). New
  `utils/run_state.py::RunState.coverage()` reuses two ALREADY-existing, engine-populated
  primitives — `delegation_depth_ctx` (depth==1 = a task the Planner itself dispatched via
  `delegate_tasks`; depth>1 = a nested Analyzer-tier sub-call, excluded from coverage since it's
  expected to reuse already-fetched content with no new URL of its own) and per-task fetch
  attribution (`task_fetched_urls_ctx`, from the 2026-07-12 race-condition fix) — to compute
  `{total, covered, ratio, uncovered_task_names}` over distinct top-level task names.
  `RunState.add_finding` gained optional `task_name`/`depth` params (both default `None`, fully
  backward compatible) so `orchestrator.py::_run_single_task`'s existing two call sites can tag
  each finding with what produced it. New `engine/completion.py::check_thin_coverage`
  (`COMPLETION_CHECKS` entry, right after `check_not_delegated` — same category, "did research
  happen adequately," not grounding). Not Builder/FindingsWriter-fixable (fixing thin coverage
  needs NEW delegation, which only the Planner can decide, same as `not_delegated`) — falls through
  to the classic inject-into-Planner path by design. Conservative by construction: fires only when
  a MAJORITY of top-level tasks came back with no real source (`threshold`, default 0.5) AND there
  are enough of them for that ratio to mean anything (`min_tasks`, default 2) — a single-task query
  (the common case for a simple factual lookup) that succeeded is never affected regardless of
  "breadth." New config: `settings.coverage_check.{enabled,threshold,min_tasks}`. New tests: a
  pure `RunState.coverage()` unit-test block (empty run, single covered task, nested-Analyzer
  exclusion, 1-of-3 thin case) plus a `check_thin_coverage` wiring scenario (fires with the correct
  injected task names + ratio text, stays silent on a successful single-task query, stays silent
  exactly AT the threshold — confirms "below," not "at or below"). TUI/CLI parity confirmed by
  construction: both `run_cli` and `run_agent` call the same shared `run_completion_check`, and
  `_run_single_task` is shared engine code, so no surface-specific wiring was needed. Full suite +
  `ruff check .` pass. Committed `2a70d01`.
  - **Live verification (same day) found 2 more real bugs, both fixed and live-confirmed** — the
    standing-rule smoke test for this phase took 4 attempts; the first 3 timed out for reasons NOT
    in Phase 5's own code, and root-causing each timeout (per the "don't hand-wave as model
    slowness" standing rule — `journalctl -u ollama`, `~/.deepdelve/sessions/session_<id>.json`,
    `ollama ps`) surfaced two separate, previously-invisible bugs:
    1. **`settings.sub_agent_timeout_minutes` (the Phase-4-era sub-agent deadline fix, `d72772c`/
       `9962a22`) was never actually live** — it exists in `config_template.yaml` but nothing
       back-fills an existing user's real `~/.deepdelve/config.yaml`, so it was silently `0`
       (disabled) the whole time; every earlier "live-verified" confirmation of that fix was only
       true because the key had been temporarily test-added to the config and reverted afterward
       along with unrelated per-test overrides. Not a code bug — fixed by adding the key directly
       to the live config. New standing memory: new `settings.*` keys must be grepped in the LIVE
       config, not just the template, before a dependent fix counts as verified.
    2. **`_dispatch_writer_review_fix`'s corrective Fix pass had no evidence base of its own**
       (`src/engine/completion.py`). Its second dispatch (fixing PeerReviewer-flagged issues) is a
       fresh sub-agent with zero memory of the first Write dispatch; `fix_instructions` said to use
       "the real source material you were given" but never actually included it. Harmless for
       Builder (its source, `findings.md`, is a real re-readable file) but fatal for FindingsWriter,
       whose source material (`_build_findings_source_material`) only ever existed as a string in
       the first dispatch's prompt. Confirmed live: a `FindingsWriterFix_..._reviewed` dispatch
       burned its entire turn hunting `read_workspace_file` for guessed, nonexistent filenames
       (`task_results.json`, `research_results.json`, `instructions.md`) instead of writing a fix.
       Fixed by re-appending the original `write_instructions` to `fix_instructions`, keeping the
       function writer-role-agnostic. Live-confirmed fixed on the very next run.
    3. **`check_thin_coverage` itself false-positived on the project's own internal
       Write→Review→Fix dispatches** (`src/engine/orchestrator.py`). `Builder`/`FindingsWriter`/
       `PeerReviewer` are dispatched directly from the Planner's own top-level context (via
       `run_completion_check`, not `delegate_tasks`), so they land at `delegation_depth_ctx==1`
       exactly like a genuine top-level research task — structurally indistinguishable by depth
       alone. Confirmed live: coverage counted `'FindingsWriterFix_attempt1'`,
       `'ReviewFix_attempt1'`, `'FindingsWriterFix_attempt1_reviewed'` as 3 of 5 "delegated
       research tasks" that produced no source. Fixed with a new
       `_NON_RESEARCH_DISPATCH_ROLES = frozenset({"Builder", "FindingsWriter", "PeerReviewer"})`
       constant; `add_finding` now skips recording entirely for those roles. Pinned by a regression
       test asserting the exact role set. Live-confirmed fixed: the final clean run's
       `_run_state.json` showed only real task names in `findings`, coverage 2/2 (ratio 1.0), no
       `thin_coverage` entry.
    - **Final clean end-to-end run**
      (`compare_the_population_of_canada_and_australia_20260714_170629`, 1018.5s): `findings.md`
      and `final_report.md` both written, PeerReviewer passed clean on the `final_report.md`
      re-check, real grounded citations (ABS + Wikipedia). Full suite + `ruff check .` pass
      throughout. Committed `3dd349a`.
- **Phase 6 / B4: unify `run_cli`/`run_agent`'s stream-iteration + retry logic — DONE.** The two
  genuinely duplicated pieces between headless (`run_cli`) and TUI (`run_agent`) extracted into
  shared helpers in `engine/orchestrator.py`: `iter_agent_stream(stream, deadline)` (async
  generator racing each update against an optional wall-clock deadline via `asyncio.wait_for`,
  replacing `run_cli`'s inline manual `stream_iter`/`while True` loop; `deadline=None`, the TUI's
  case with no wall-clock limit by design, is behavior-identical to a plain `async for` per
  `asyncio.wait_for`'s own documented semantics, so `run_agent` gets the same iteration mechanics
  for free with zero behavior change) and `classify_malformed_retry(...)` (pure decision logic for
  the malformed-tool-call retry pattern, previously copy-pasted between both call sites and once
  found missing from `run_agent` entirely — callers keep their own stdout/widget notification,
  only the retry decision itself is shared). CI green, both CLI and TUI live-verified this session
  (same smoke test that caught the `_is_citation_only_line` cross-source-contradiction bug above).
  Committed `2e4758f`. This was the last open phase of the 2026-07-14 6-phase plan — all 6 phases
  now done.
- **`claim_grounding_problem`/`_grounded_claim_pairs` false-positive on citation-only sub-bullets —
  FIXED 2026-07-14, commit `061c10a`.** Root-caused a live Eiffel Tower smoke-test failure that
  burned its entire 8-attempt retry budget on `claim_unsupported`: both flagged claims ("2 years,
  2 months and 5 days", "assembly began July 1, 1887, completed twenty-two months later") were
  verbatim in the fetched source — a genuine false positive, not model fabrication. This project's
  own Builder output shape puts a claim on one line and its citation on a SEPARATE
  `- Source: [Title](url).` sub-bullet; the bare sub-bullet was being processed as its own claim
  segment, and `extract_salient_terms` pulled "Official Eiffel Tower" out of the citation's own
  editorialized anchor text as if it were a checkable fact — then failed it because that exact
  phrase (the writer's own paraphrase) doesn't appear verbatim in the source.
  `_is_citation_only_line` already existed for exactly this line shape (built for
  `_extract_figure_claims`/cross-source-contradiction, 2026-07-14 earlier this same day) but was
  never applied here. Now guarded in both functions. New test:
  `_citation_only_subbullet_scenario`, reproducing the real failing report/source directly rather
  than a synthetic case. **Live end-to-end re-verification**: the exact same query re-run
  end-to-end produced zero `claim_unsupported` occurrences (vs. 6 consecutive + retry-budget-
  exhausted before), converging cleanly by attempt 4 in 490.0s vs. the prior run's 1017.9s wasted
  grinding on the false positive.
- **`_dispatch_writer_review_fix` immediate narration salvage — IMPLEMENTED 2026-07-18, live
  verification in progress.** Targets the "writer role Finishes its turn without ever calling
  `write_workspace_file`" failure class (Bonsai-8B, `qwen2.5:3b-instruct`) at its root: the
  project already had `_salvage_narrated_report` for a model that narrates a complete report as
  chat text instead of calling the tool, but it only ran as a LAST-RESORT at final-verdict time,
  and only for `missing_artifact` — `missing_findings` (the FindingsWriter case, the one that
  actually burned `qwen2.5:3b-instruct`'s full 8-attempt budget) had no equivalent path at all.
  Now checked immediately after every Write dispatch inside `_dispatch_writer_review_fix` itself
  (shared by both Builder and FindingsWriter): if `req_artifact` is still missing but the dispatch
  returned ≥200 chars of real text, that text is persisted as the artifact right away, clearly
  flagged `AUTO-RECOVERED DRAFT`, and flows into the same PeerReviewer/Fix cycle and grounding
  checks a genuine write would — instead of looping blind on a file that will never appear on its
  own. New test coverage: `_immediate_narration_salvage_scenario` (salvage fires, correctly
  flagged, converges in 2 dispatches) and a negative-case scenario confirming a real write is
  never clobbered by salvage logic. Does not touch `COMPLETION_CHECKS`/`GROUNDING_CHECKS`/any
  `Verdict` — `test_structural_checks.py` run and passing regardless per project rule.
  **Live re-test against the exact case that motivated it (`qwen2.5:3b-instruct`, same
  sales-forecasting benchmark) surfaced a more precise root cause than assumed**: this model's
  `FindingsWriterFix` dispatches return a **genuinely empty response** (confirmed via the
  persisted session log: zero events, no tool call, no text at all — not a narrated report), the
  exact same symptom already documented for Bonsai-8B, not the "narrates full content instead of
  calling the tool" pattern the fix targets. Salvage correctly declines to act (nothing above the
  200-char floor to recover) rather than fabricating content from nothing. **Conclusion: the fix
  is verified correct and safe, but doesn't rescue THIS specific model's failure shape** — it
  would still help a model that genuinely narrates substantial content instead of calling the
  tool (the originally-documented pattern, seen in the reference project this was forked from
  too). Whether `qwen2.5:3b-instruct` is rescuable at all needs a different angle (e.g. option 4
  below, keep it out of the writer role entirely) if pursued further.
  - **Full re-run completed 2026-07-18, confirms the diagnosis: `Report: NOT WRITTEN`, still
    `missing_findings`, 8/8 attempts, 3524.3s (58.7 min — vs. 254.6s on the original run) with the
    live default `sub_agent_timeout_minutes: 10` in effect. `findings.md` still never existed on
    disk at any point across all 9 dispatches (0 through the final `_reviewed` pass) — confirmed
    via the run folder listing (`_run_state.json`/`sources/` only) and the persisted session log
    (zero `FindingsWriterFix*`-sourced events across the ENTIRE run, meaning every single
    dispatch, not just attempt 1, returned nothing usable).** The 14x wall-clock increase traces
    to one specific event, confirmed live via `journalctl -u ollama`'s own `print_timing` output
    (not assumed): the final corrective pass (`FindingsWriterFix_attempt8_reviewed`) decoded
    **45,000+ tokens continuously at ~80 tok/s**, blew past its own 16K context window once
    (forcing a `context shift, n_discard = 8189`), and was still running when checked — a second,
    independent confirmation of the "runaway generation with no natural stop point" failure class
    this project already fixed the missing GUARD for (README: "Independent per-dispatch wall-clock
    deadline," originally found via a Gemma4 19,908-token case) — `sub_agent_timeout_minutes`
    correctly cut it off rather than hanging forever, but whatever text existed at cutoff still
    wasn't real content (0 events recorded), so nothing was salvageable even from that dispatch.
    **Final verdict: `qwen2.5:3b-instruct` genuinely has no recoverable content to give in the
    FindingsWriter role, empty responses and runaway non-answers alike — this is a harder failure
    than "narrates instead of writing," and no structural salvage can rescue a dispatch that
    produces nothing at all.** Confirms option 1 (structural fix) is exhausted for this specific
    candidate; option 2 (keep it out of the writer role, use it only for Searcher/Analyzer-tier
    work where it has shown real capability — 3 sources fetched cleanly, 0 search failures, both
    runs) is the next thing worth trying if this model is revisited.
- **Shared quota-pool starvation — FIXED 2026-07-18** (`src/tools/core.py::check_quota`). Ring-
  fences a task's remaining quota once it's shown real fetch activity this dispatch
  (`task_fetched_urls_ctx` non-empty, already per-task/race-free): the first time a tool call would
  exceed the shared cumulative limit for a task that's already fetched something real, grants one
  small one-time top-up (+2) instead of hard-blocking, bounded by a `_rescued` flag on the pool
  entry so it can only fire once per tool per run — not an unbounded loophole. Directly targets the
  documented failure below (a dispatch that fetched 2 real sources, then hit a bare "Quota reached"
  wall before ever synthesizing them) — addresses option (b) from that finding's own candidate list
  (ring-fencing a task's remaining quota once it's shown real progress). Does NOT fully cover every
  angle that finding raised: a REDISPATCH's own `task_fetched_urls_ctx` starts empty again, so a
  task that gets cut off before fetching anything on a later retry still isn't rescued — options
  (a)/(c) from that finding remain open if that gap resurfaces. Verified with 3 direct unit
  scenarios (rescue fires once, normal enforcement resumes after, no rescue without real fetch
  activity) plus a live headless smoke test with no regressions. Doesn't touch
  `COMPLETION_CHECKS`/`GROUNDING_CHECKS`/any `Verdict`, so the verdict-matrix test requirement
  doesn't formally apply, though `test_structural_checks.py` was still run and passes.
- **Brave Search MCP `country` parameter rejecting real countries (e.g. Colombia) — FIXED
  2026-07-18** (`src/tools/mcp_loader.py::_wrap_brave_search_tool`/`_BRAVE_SEARCH_COUNTRY_ENUM`).
  `@brave/brave-search-mcp-server`'s `country` param is a fixed 37-code zod enum that does not
  include `CO` (confirmed by reading the installed package's own schema source) — broke every
  Colombia-targeted search outright (`MCP error -32602`). Wraps `MCPTool.call_tool` (confirmed via
  `agent_framework`'s own source that every model-invoked call to any function this MCP server
  advertises funnels through this one method) to strip an out-of-enum `country` value before it
  reaches the subprocess, falling back to an unscoped search instead of a hard rejection. Scoped
  only to specs whose server name contains "brave", so it can't affect any other MCP server.
  Unit-verified directly against a fake `call_tool` before the live smoke test confirmed no
  regressions.

## Findings from live testing (not yet acted on / informational)

- **SOTA literature review, durable conclusions merged 2026-07-20** (full detail, primary-source
  citations, and still-open leads in `RESEARCH.md`, which stays the standalone working document).
  - **MAST's 14-mode failure taxonomy (arXiv:2503.13657, NeurIPS 2025) maps closely onto this
    project's own bug catalog**, confirming DeepDelve's failures are named, published patterns
    rather than idiosyncratic bugs: FM 2.6 "Reasoning-Action Mismatch" = the "narrate instead of
    write" bug; FM 1.5 "Unaware of Termination Conditions" = the over-research/STOP-EARLY problem;
    FM 3.2/3.3 "No/Incorrect Verification" = the entire reason the grounding-check layer exists;
    FM 1.1 "Disobey Task Specification" = the exclusion-enforcement bug class. A follow-on
    production-telemetry replication (639K steps/23.6K runs, one closed-alpha platform) found
    verification gaps dominate real deployment failures while coordination failures nearly vanish
    (1.14% of runs) — closer to DeepDelve's own lived experience than MAST's benchmark-derived
    aggregate, though caveated as one platform, not peer-reviewed. A large-scale coding-agent study
    (arXiv:2605.29442, 16,118 validated episodes) independently found the same two DeepDelve
    patterns (inaccurate self-reporting ≈ "narrate instead of write"; constraint violation ≈
    exclusion-enforcement) in a totally different agent domain — real, cross-domain corroboration,
    not a DeepDelve-specific quirk.
  - **Three independent sources now converge on "verification/architecture amplifies a capable
    model, it doesn't rescue an incapable one"**: the capacity-floor paper (arXiv:2601.16280, 14B
    "minimum viable" for tool invocation), PIVOT (arXiv:2605.11225, "repair quality remains bounded
    by the underlying model reasoning capacity"), and ATLAS/AdaMAST (its own 8pp residual gap on
    OlympiadBench, attributed to an "architectural-vs-parametric distinction"). Relevant to every
    future decision about fixing a small-model gap with more structure vs. a bigger/better model.
  - **A third, distinct candidate mechanism for the recurring "real fetched content silently
    vanishes during final synthesis" pattern** (already independently observed 3 times in this
    project — quota-starvation drop, heterogeneous-tiering drop, citation-truncation drop, each
    fixed individually; see the scattered incidents at lines ~481, ~689, ~966, ~1440, ~1858 above).
    "Lost in the Middle" (arXiv:2307.03172, TACL 2024, foundational/highly-credible) shows models
    use context well at the start/end and poorly in the middle — a candidate SECOND cause distinct
    from truncation, not yet checked against DeepDelve's own findings-ordering. PIVOT
    (arXiv:2605.11225) adds a candidate THIRD: 100% of its tested models' thinking tokens fire on
    the FIRST turn (task decomposition), 99.2% of final-synthesis steps get ZERO thinking tokens,
    REGARDLESS of how large the thinking budget is raised — models don't naturally allocate
    reasoning to synthesis/verification, only to planning. None of these three are confirmed as
    DeepDelve's own root cause; each is a real, externally-sourced, testable hypothesis for the
    still-open "common structural cause" investigation already flagged in this file.
  - **Comparative survey against 5 other real deep-research-agent projects** (Tongyi DeepResearch,
    dzhng/deep-research, CYC2002tommy/Deep-Research-Agent, SkyworkAI/DeepResearchAgent, nashsu/
    llm_wiki — all already credited in README's References) answered a deliberate test question from
    the user honestly: DeepDelve's 10-layer grounding pipeline is more elaborate than any of the 5
    for the SPECIFIC problem of post-hoc citation verification on a small/local model — but this is
    explicitly NOT "most sophisticated deep research agent, period." Tongyi DeepResearch solves
    reliability via a much larger purpose-trained model, a different and likely more effective lever
    DeepDelve's own local-only constraint doesn't have access to; and "sophisticated mechanism" is
    not the same claim as "proven real-world catch rate" — most of DeepDelve's own grounding checks
    still lack real-captured-fabrication test coverage (see "Test coverage debt" note in session
    history). See `RESEARCH.md` §7 for the full, appropriately-bounded writeup.
  - **A non-generative routing-classifier design for `delegate_tasks`** is now a scoped "Planned"
    item (see above) rather than a research note, prerequisite data already confirmed sufficient.
- **Full grounding/completion-check compliance audit (2026-07-18), all 12 README-claimed guarantees
  re-verified against the actual code, not just the docs.** Checked each of: URL grounding with
  path-boundary matching, content-level zero-fact-overlap, non-URL citation detection, regulation-
  identifier check, stub-fetch detection, `uncited_claims`, NLI entailment
  (`nli-deberta-v3-small`), atomic-claim segmentation (`decompose_claim_segments`), FEVER-style
  cross-source contradiction, topical relevance (`bge-reranker-v2-m3`), coverage accounting
  (`RunState.coverage()`), and the `test_structural_checks.py` verdict-matrix pin. **All 12 found
  genuinely implemented and reachable from the real completion-check flow** (`GROUNDING_CHECKS`/
  `COMPLETION_CHECKS` in `src/engine/completion.py:563-582`) — no dead code, no orphaned function,
  no early-return that silently skips a check, no always-false gating condition. Every check fails
  open (returns `None`) on model-load failure rather than crashing a run, confirmed as deliberate
  documented behavior rather than an oversight. `check_not_grounded`'s ordering as the last, generic
  catch-all in `GROUNDING_CHECKS` is deliberate so the more specific verdicts fire first. Net: the
  README's grounding-guarantees section does not overclaim relative to the code as of this date.
- **Grounding check verifies provenance, not topical relevance.** A live GOA (Grasshopper Optimization Algorithm) research query got a citation from `globaldrivetozero.org` — actually fetched, and sharing surface terms like "GOA"/"Goa" — that's actually about the Indian state of Goa's EV policy, not the algorithm. The URL-presence + term-overlap check passed it because it only checks "was this fetched" and "do terms overlap," not "is this source about the same subject." Acronym collisions are the clearest way to trigger this; unclear how common the failure mode is outside them. **Fixed 2026-07-14 — see "Done" (Phase 4, `topical_relevance_problem`).**
- **JS-gated pages return bot-challenge stubs, not content.** Several fetches (Cloudflare "Just a moment...", a "Human Verification" page, a Prezi slide deck) came back as 16-18 byte stubs since the fetcher doesn't execute JavaScript. *(Fixed for most cases — see "Done": headless/headed-browser fetch fallback, 2026-07-14. Recovers Springer (headless-sufficient) and MDPI (needed headed). NOT a universal fix: a genuine Cloudflare Turnstile challenge (ScienceDirect) resists both headless AND headed Chromium regardless of patience or `navigator.webdriver` spoofing — confirmed to be automation/CDP-fingerprint detection, not a solvable timing issue, and deliberately not pursued further; see the ScienceDirect sub-bullet above for the full investigation. Still correctly falls through to the stub flag rather than silently failing.)*
- **A citation being present in a report's "Sources" list doesn't mean it was fetched.** Across several market-research runs, more than half of named sources were routinely never actually fetched (recalled from the model's training data) — and when independently fact-checked, specific statistics tied to unfetched sources were measurably wrong, usually understated.
- **Hard exclusion rules ("do not research sector X") repeatedly fail to hold**, confirmed across at least 2 independent runs with different prompt wordings: an explicitly-excluded "Agricultural"/"agribusiness" sector got researched and included in the final report anyway — once purely from memory, once with the model actually delegating and fetching a real source for the excluded sector. Simply naming the exclusion in the prompt isn't enough; `delegate_tasks`'s existing dispatch-time skip (`_extract_excluded_topics`) only stopped NEW research on the topic, not the topic showing up in the final report anyway via a sibling task's tangential findings. **Fixed 2026-07-14** — see "Done" below (`check_excluded_topic`).
- **Non-URL "citations" evade the grounding check entirely.** A live report sourced several claims to `"Expert opinion from a cold storage facility manager in Colombia"` — not URL-shaped, so `extract_cited_urls` never sees it, even though it's exactly as ungrounded as a fabricated URL. The grounding check's whole model is "cross-reference cited URLs against fetched URLs" — a citation with no URL at all currently gets a free pass. **Fixed — see "Done" above (`non_url_citation_check`).**
- **Scaling down scope (12 sectors → 5) improved surface polish, not actual grounding rate.** A 5-sector re-run produced far more plausible-looking, consistently-formatted citations than a 12-sector run, but cross-referencing against `_run_state.json`'s real `fetched_urls` showed most of them were still fabricated — only 5 URLs were ever fetched all run, while the final report cited well over twice that many distinct domains. Fewer sectors did not proportionally reduce the fabrication rate.
- **Shared cumulative `web_search` quota pool can starve a specific task of the ability to
  synthesize what it already fetched (2026-07-14).** Live sales-forecasting benchmark run
  (`research_output/i_want_documentation_on_heuristic_algoritms_for_de_20260714_225720/`): the
  final report was well-grounded on its technical content but silently dropped the Colombia
  cultural-context section (holidays/paydays) ENTIRELY, despite the query explicitly requiring it
  and the research genuinely happening — NOT the same bug as the FindingsWriter dedup fix shipped
  earlier this session (that fix worked correctly here; the empty-summary entry reached
  FindingsWriter's material intact, there was just nothing usable in it).
  `SubAgent_Colombian cultural events affecting sales` was dispatched 4 separate times across the
  run's retries. Dispatch #1 genuinely fetched 2 real sources
  (`timeanddate.com/holidays/colombia/2024`, an ADP payroll-calendar article) but its
  `RunState.add_finding` entries have EMPTY summaries — it fetched but never got to actually
  analyze/synthesize before being cut off. Dispatch #4 (the last one) has a real summary, but it's
  just an apology: *"I've reached the maximum number of web-search calls allowed for this session
  (15). No sources were successfully fetched..."* Root cause: `web_search`'s quota
  (`build_quota_pool`) is ONE shared, cumulative pool across every sub-agent in the run — other
  tasks (particularly "Top 5 common heuristic algorithms," which shows heavy repeated web activity
  in this run's findings) burned through the pool first, so by the time the Colombia task got
  redispatched on retries #3/#4, the shared quota was already exhausted, and it could never finish
  analyzing the sources it originally fetched. **Partially fixed 2026-07-18 — see "Done" above
  (`check_quota`'s ring-fence)**, which addresses angle (b) below (a dispatch that already fetched
  something real no longer gets hard-blocked mid-synthesis). Angles (a)/(c) remain open — candidate
  angles: (a) a per-task reserved minimum quota allotment, (b) [addressed] protecting/ring-fencing a
  task's remaining quota once it's shown real fetch activity (distinguishing "genuinely
  progressing but interrupted" from "never started"), (c) some kind of fairness/round-robin
  ordering across redispatched tasks instead of first-come-first-served on a shared pool. Distinct
  from `retry_quota_topup` (which already tops up the pool between completion-check ROUNDS) —
  this is about fairness WITHIN a round, across concurrently/sequentially dispatched sibling
  tasks sharing the same pool.

- **gpt-oss hallucinates entire tool names, not just filenames (2026-07-12).** Distinct from the
  fuzzy-filename problem fixed this session (a real tool called with a garbled argument) — this is
  the model inventing a function that was never in its schema at all: `grep_search?` and `justify`
  both fired as literal function-call names in one live run (heuristic-algorithms sales-forecasting
  query), 3 occurrences total. Each one only cost a turn (clean error, `malformed_tool_call_nudge`
  path, sub-agent recovered without stalling) but three in a single run is a real pattern worth its
  own investigation, not noise to fold into the filename fix.
  - **Investigated 2026-07-14 — no code fix, re-tested live, existing infra already covers it.**
    Re-ran the EXACT same benchmark query live (`research_output/i_want_documentation_on_heuristic_
    algoritms_for_de_20260714_225720/`, 939.3s, clean pass, converged by attempt 3): **zero**
    hallucinated-tool-name errors this time, out of 11 total tool errors recorded (all legitimate —
    a real missing-field validation error, a real missing file, expected quota-exhaustion
    messages). Doesn't prove the underlying tendency is gone (one run against one prior run is weak
    evidence either way — could be genuine improvement from the many structural fixes shipped since
    2026-07-12, or just run-to-run variance), but two things make further code investment
    unjustified without stronger recurrence evidence: (1) the tool schema the model sees is the
    real, structural OpenAI-style function-calling schema (name/description/params passed via the
    API's own `tools` parameter), not prose — occasional hallucination despite having the correct
    schema in context is a generation-sampling failure, not a missing-information one, so "tighter
    prompt framing" was never likely to help; (2) `tool_result_error_nudge`
    (`src/engine/orchestrator.py:268`, shipped 2026-07-14, AFTER this finding was first recorded)
    already generically pattern-matches the exact `Requested function "..." not found` error text
    and gives a corrective nudge — so even if this recurs, it's no longer a silently-wasted turn,
    it costs at most one extra turn with real guidance, same fix that already closed the "zero
    recovery path" gap for this exact error class. Revisit only if this resurfaces with real
    frequency data across multiple runs, not as a standalone investment.
- **gpt-oss endgame-collapse reproduced again, fresh data point (2026-07-12), now also observed
  INSIDE Builder (2026-07-13).** Same live run above: 9 completion-check attempts, cascading
  `web_search`/`grep_workspace_file`/`fetch_url_to_workspace` quota exhaustion across multiple
  re-delegation rounds (including a genuine `QuotaAbortException` nested-agent abort), before
  finally falling back to the quarantine-restore path at attempt 9/9 — the query (peer-reviewed
  sourcing for heuristic algorithms + deep learning + multi-franchise sales forecasting, a 3-way
  AND) never had a real source satisfying all three criteria. Already tracked as a known gap (runs
  11/13) — not a new finding on its own, but confirms it's not resolved and reproduces on a
  genuinely hard query, not just a fluke. **Re-tested 2026-07-13 against the same exact query after
  the Builder architecture shipped**: the collapse shape moved, it didn't disappear — Build→Review→Fix
  correctly fired 3 times on real `not_grounded` problems, but on attempts 4-6 Builder itself ran out
  of the shared `write_workspace_file` quota and fell back to narrating the report as chat text
  instead of writing it (Builder's own output: "I'm unable to create new files because the
  `write_workspace_file` quota has been exhausted") — the identical failure shape the Planner used
  to exhibit, now happening one level down. The quarantine-restore fallback still worked exactly as
  designed both times: final artifact carries a loud warning banner (or is fully restored from the
  best surviving quarantined draft) instead of a fabricated clean-looking report or a lost one. See
  "Planned" below for the quota-sharing angle this surfaced.
- **Line-scoped claim grounding (2026-07-12):** `claim_grounding_problem` compared WHOLE-report terms against each source, so generic shared terms masked per-claim fabrication (run 12's flagship figure was absent from its cited source but passed via other lines' overlap). Now each line with a fetched citation is checked against its own source(s) — the regulation-check pattern generalized; conservative as before (≥1 checkable term + zero overlap only, URL slugs stripped).
- **Structural eval scorer (2026-07-12):** new `eval_type: structural` in `eval/evaluate.py` — rubric tier 1 scored deterministically from `_run_state.json` + workspace files (cited⊆fetched, findings.md grounded, no salvage/quarantine banner, no unresolved final problem), which no other scorer read at all.
- **Four concrete findings from a fresh live run of the standing sales-forecasting benchmark
  (2026-07-13, later the same day the Builder loop shipped)** — user killed the run after it
  stalled; each finding traced to an exact file/line, not guessed:
  - **`_strip_trailing_punct` (`src/utils/grounding.py:59-66`) didn't strip a trailing `*`.**
    *(Fixed 2026-07-14.)* Builder's own citation format `**[Title](URL)**` puts `**`
    immediately after the link's closing `)` with no space; the existing unbalanced-`)`-stripping
    loop only fired when the string *ends* with `)`, so a URL ending in `)**` was never cleaned up.
    Confirmed live: two of this run's four completion-check attempts were `not_grounded` verdicts
    citing the literal string `...546e2a498c2f)**` as "unverified" — a genuinely-fetched,
    correctly-cited source false-flagged as hallucinated purely by this string-handling gap,
    burning half the run's retry budget on a checker bug, not a model failure. Fix: added `*` to
    the initial `rstrip()` char set, stripped BEFORE the balanced-paren check so a bold-wrapped
    URL's real trailing `)` is exposed to it correctly (verified against a bold URL that also has
    its own internal balanced parens, e.g. a Wikipedia disambiguator page — both layers now
    resolve in the right order). Two new assertions in `test_structural_checks.py`.
  - **Sub-agent "tool not found"/"argument parsing failed" errors had zero recovery path.**
    *(Fixed 2026-07-14.)* Confirmed via code trace: these come back from `agent_framework`'s SDK
    as in-band tool-result text, never as exceptions, so they never reached `_run_single_task`'s
    `except` block and never triggered the existing `malformed_tool_call_nudge` (which only covers
    transport-level "error parsing tool call" failures). Confirmed live: a `SubAgent_BuilderFix`
    retry hallucinated a call to `delegate_tasks` (Builder's real tool list never includes it — the
    model invented the call, not a config leak); a separate sub-agent called a malformed
    `grep_workspace?`; `PeerReviewer` tried reading a nonexistent `workspace.txt`. Each burned a
    turn with no corrective nudge of any kind, unlike the Planner's own conversation. Fix: new
    `engine/orchestrator.py::tool_result_error_nudge`, a sibling of `malformed_tool_call_nudge`
    scoped to the exact SDK error strings pulled from `agent_framework/_tools.py` source (not
    guessed) — `Error: Requested function "{name}" not found.` (hallucinated tool name),
    `Error: Argument parsing failed.` (rejected arguments), and `tools/fs.py`'s
    `Error: '{filename}' not found.` (missing file). Wired into `_run_single_task`'s stream loop:
    the pending nudge is overwritten on every `function_result` seen, so a LATER successful call
    after an earlier error (the model already self-correcting within the SDK's own internal turn)
    clears it — only an error still standing at the end of the stream gets nudged, capped at 2
    retries like `malformed_retries`. Deliberately narrow (three specific, evidence-backed error
    shapes, not every possible tool failure) so a legitimate business-logic error (a real search
    that genuinely failed, a quota genuinely exhausted) doesn't get blindly retried when that
    wouldn't help — verified against both the three matching cases and two non-matching ones (a
    real fetch-success string, `web_search`'s own timeout error) with no false positives. New
    assertions in `test_structural_checks.py`. **Deliberately NOT extended to the Planner's own
    loop** (`run_agent`/`run_cli` in `engine/tui.py`) despite this project's usual TUI/CLI parity
    rule — this is a reasoned scope decision, not an oversight: the Planner already has independent
    recovery via its multi-attempt completion-check loop (several full outer retries across an
    entire run, each with fresh nudges and quota top-ups), unlike a sub-agent's single one-shot
    dispatch with no outer safety net at all — the asymmetry this fix closes is specific to
    sub-agents, not a gap in the Planner too. **Relationship to the researched LangGraph
    `RetryPolicy` pattern** (see the earlier-recorded research-pass note): that pattern's
    retryable-vs-fatal split maps onto DIFFERENT layers of this codebase rather than one function —
    the genuinely *retryable* class (timeout, rate-limit, transient parse garble) is exactly what
    `web_search`'s own daemon-timeout fix and the SDK's built-in 429/5xx backoff already handle;
    `tool_result_error_nudge` covers what that pattern calls *fatal* (hallucinated tool name,
    rejected arguments) — except here "fatal" doesn't mean "give up," it means "immediately
    actionable by telling the model exactly what's wrong," which is what the nudge does.
  - **`web_search`/`probe_search_health` (`src/tools/web.py`) had no outer wall-clock timeout.**
    *(Fixed 2026-07-14.)* `DDGS()` is built with no explicit timeout at either call site, relying
    on the `ddgs` library's own internal 5s-per-engine default — not a real ceiling, since `ddgs`
    runs engines in a `ThreadPoolExecutor` and its context-manager exit calls `shutdown(wait=True)`,
    which blocks until every thread finishes regardless of the nominal per-engine timeout. Confirmed
    live: the process ended up blocked with one established TCP connection open 9+ minutes to a
    yandex.ru-resolving IP (not an intentional backend anywhere in this codebase — almost certainly
    a redirect inside `ddgs`), local model unloaded, GPU idle. Generalizes the already-tracked
    "no liveness/stall detection" gap (previously scoped to hosted/NIM runs only) to local
    `web_search` too. Fix: `tools/web.py::_run_with_daemon_timeout` — a real `threading.Thread(daemon=True)`
    with `.join(timeout)`, not a bare `asyncio.wait_for(asyncio.to_thread(...))`. That distinction
    mattered in practice: a plain `wait_for` DOES unblock the awaiting coroutine on time, but its
    underlying executor thread is not a daemon thread, so if the search call never actually returns
    (confirmed against two real GitHub issues, `HKUDS/nanobot#2804` and `microsoft/amplifier#219`,
    describing `ddgs`'s `primp` Rust HTTP client blocking below anything asyncio can interrupt), the
    orphaned thread then blocks the WHOLE PROCESS from exiting cleanly at the end of a run — verified
    directly with a `time.sleep(999)`-hung call: bare `wait_for`/`to_thread` times out the caller
    fine but the process itself never exits; the daemon-thread version times out the caller AND lets
    the process exit cleanly. `settings.web_search.timeout_seconds` (default 20), shared by both
    `web_search`'s two attempts and the pre-run `probe_search_health` check
    (`src/engine/tui.py`, `run_cli`). Process-based isolation (spawn+kill a subprocess) was
    considered and rejected — it would require calling `ddgs` from a picklable module-level worker,
    breaking the existing in-process `ddgs.DDGS` monkeypatch test in `test_structural_checks.py`
    since a subprocess re-imports fresh, unpatched modules; the daemon-thread approach closes the
    same gap (including the exit-hang) without that cost.
  - **Sub-agent status widgets had no staleness indication.** *(Fixed 2026-07-14.)*
    (`src/engine/tui.py`, `handle_agent_update`). Unlike `ProcessingWidget`/`ToolCallWidget`'s
    animated timers, the per-sub-agent `Static` widget showed `"▶ {agent_name} executing..."` with
    no timer and no upper bound — if the underlying dispatch never resolved (exactly what the stall
    above causes), it stayed frozen on "executing" forever with zero visual signal anything was
    wrong. Same bug *class* as the already-fixed `ProcessingWidget` elapsed-counter issue, but that
    fix never got applied here — this is what "stuck agent" looked like from the user's side that
    night. Fix: new `SubAgentStatusWidget` class (mirrors `ProcessingWidget`'s animated-dots +
    live elapsed-seconds pattern exactly), swapped in at the one mount site in
    `handle_agent_update`; `mark_finished(elapsed)` replaces the old one-shot `.update(...)` call
    on completion. Also wired into `/stop`'s existing widget-cleanup block (alongside
    `ToolCallWidget`/`ProcessingWidget`/`ThinkingWidget`) so a manually-stopped run marks these
    stopped too instead of leaving them frozen mid-animation — a related gap the bare `Static`
    couldn't have supported anyway (no `mark_stopped` method existed to call).
  - Full prioritized fix plan (strip-punct fix → search timeout → sub-agent error nudge → widget
    staleness indicator) was written to a local plan file during triage. All four items fixed
    2026-07-14 — see "Done" above/below.
  - **Builder's `write_workspace_file` quota was shared with the Planner and every prior Builder
    dispatch, with no guaranteed headroom of its own.** *(Fixed 2026-07-14.)* On a long, many-retry
    run, the shared pool could be exhausted by the time a later corrective Builder dispatch needed
    it, degrading Builder to narrating the report as chat text instead of writing it — the same
    "narrate instead of write" failure the Planner used to be prone to, now one level down.
    `retry_quota_topup` already topped up the pool on every completion-check retry, so this wasn't
    starved by DEFAULT config, but a config with a low `write_workspace_file` limit/topup would
    starve Builder specifically. Fix: new `engine/completion.py::_ensure_builder_write_quota_headroom`,
    called right before every `_dispatch_build_review_fix` dispatch (after the existing per-attempt
    `topup_quota_pool`) — tops up ONLY `write_workspace_file`, and only by the exact headroom this
    one cycle could need (2 units: Builder's initial rewrite + one possible corrective Fix pass),
    not a blanket amount that would also quietly inflate the Planner's own budget. Chose this over
    the other option on the table (a separate Builder-reserved quota pool) because a reserved pool
    would work against `build_quota_pool`'s deliberate one-shared-cumulative-pool-per-role design,
    not just extend it. New unit tests in `test_structural_checks.py` (near-exhausted pool topped
    up to exactly 2 headroom, a pool with plenty already left untouched — no silent inflation —
    and a pool missing the key entirely, no `KeyError`).

## Planned (not started)

- **Re-run the full 11-candidate local-model bake-off via vLLM instead of Ollama — planned
  2026-07-21, not started.** Two independent, confirmed Ollama-serving-layer bugs (the think-mode
  passthrough failure documented in this file's Qwen3-family entry above, and the pre-existing
  `ollama/ollama#6155` nested-array tool-parameter stringification bug affecting `mistral-nemo`,
  `llama3-groq-tool-use`, and `llama3.2:3b`) mean several of README.md's 11 bake-off disqualifications
  may reflect Ollama's own serving bugs rather than genuine model incapability. Full plan (per-candidate
  VRAM/quantization feasibility, tool-parser mapping, execution order, and the real blockers found
  during research — this vLLM install has **no GGUF support at all**, `bitsandbytes` isn't installed,
  Bonsai-8B's quant type is unrecognized by vLLM, the GRPO fine-tune's merge checkpoint is gone from
  disk, `qwen3.6`/`Gemma 4 12B`'s HF availability is unconfirmed) written to
  `~/.claude/plans/moonlit-plotting-simon.md`. Scoped explicitly as a multi-session effort, not a
  single sitting.
  - **Both pre-flight checks DONE, 2026-07-21 — cleared, execution can proceed.**
    - **HF repo IDs confirmed to exist**: `google/gemma-4-12B-it` (official Google org, not the
      community `SetneufPT` GGUF reupload originally used) and `Qwen/Qwen3.6-35B-A3B` — plus a
      bonus find, a pre-quantized `Qwen/Qwen3.6-35B-A3B-FP8` checkpoint exists too, which helps the
      MoE-fits-at-all question the plan flagged as unconfirmed.
    - **`bitsandbytes` spiked on ROCm, real functional pass, not just import success.** Installed
      cleanly (`pip install bitsandbytes` — wasn't present before). Checked bitsandbytes' own
      support matrix first (not assumed): this GPU's `gfx1200` target (confirmed via `rocminfo`,
      RX 9060 XT) IS on their officially-supported RDNA list. Ran a real discriminating test on
      `mistralai/Mistral-7B-Instruct-v0.3` (`--quantization bitsandbytes --load-format
      bitsandbytes`): failed to fit in a deliberately tight 0.3 `gpu_memory_utilization` budget
      (~5.1GB) with "no available memory for cache blocks," then succeeded cleanly at 0.45 (~7.7GB
      total, weights+KV). Since the real bf16 checkpoint is 13.5GB, fitting inside 7.7GB total is
      only possible if the weights are genuinely quantized to roughly 4-5GB, not silently loaded at
      full precision — confirmed real, working 4-bit quantization on this hardware, not a silent
      no-op. Correct generation output ("Paris") and a real structured `tool_calls` response (via
      `mistral_tool_parser.py`) both verified. **The 8-candidate quantization bucket in the plan
      above is now trustworthy to execute.**
    - **New operational lesson, found mid-spike, applies to EVERY vLLM launch/kill in this
      project from now on**: killing an already-running (not self-crashed) `vllm serve` process
      with `pkill -9`/`kill -9` reliably orphans its `VLLM::EngineCore` child (confirmed via
      `ps -ef --forest`: EngineCore is a real child of the APIServer process, spawned via Python
      `multiprocessing` with no death-signal hookup to its parent) — SIGKILL can't be trapped, so
      vLLM's own shutdown code never runs to tear down the child, and it keeps holding VRAM
      indefinitely. This explains every "stale EngineCore still holding Xgb" gotcha hit repeatedly
      this session (MiniCPM5-1B twice, MiniCPM3-4B, this spike's first kill attempt). **Fix: use
      plain SIGTERM first** (`pkill -f "vllm serve ..."`, no `-9`) and give it a few seconds — this
      lets vLLM's own cleanup path run, confirmed via a clean SIGTERM kill on this exact spike's
      running server leaving zero orphan afterward. Only escalate to `-9` on whatever's left if
      `rocm-smi --showpids` still shows something after a graceful SIGTERM attempt.
  - **Next session/execution starting point**: proceed straight to the plan's per-candidate
    procedure, in its documented priority order (`mistral-nemo:12b` and `llama3-groq-tool-use:8b`
    first — highest information value, directly implicated in the confirmed `#6155` Ollama bug).

  - **`mistral-nemo:12b` re-test, 2026-07-21 — BLOCKED, a real infrastructure incompatibility, not
    a capability verdict.** `mistralai/Mistral-Nemo-Instruct-2407` (not gated, native
    `MistralForCausalLM` support, confirmed real bf16 checkpoint) loaded cleanly via
    `~/.venvs/vllm` with `bitsandbytes` 4-bit quantization (same proven path as the pre-flight
    spike) and the `mistral` tool-call parser. **Isolated tool-call smoke test with DeepDelve's
    real nested `delegate_tasks` schema PASSED cleanly**: a genuine structured array
    (`tasks: [{...}]`), not the stringified-JSON shape from Ollama's `#6155` bug — direct
    confirmation the bug is absent on this backend, exactly as expected.
    **But the full DeepDelve run failed immediately on its very first request, 100% reproducibly,
    with `400: "chat_template is not supported for Mistral tokenizers."`** Root cause traced to
    vLLM's own source (`vllm/tokenizers/mistral.py::validate_request_params`): vLLM's
    Mistral-native tokenizer class unconditionally REJECTS any request containing
    `chat_template_kwargs` — and DeepDelve's `_get_default_options()` (`src/engine/
    orchestrator.py`) unconditionally SENDS `chat_template_kwargs: {"enable_thinking": ...}` on
    every single dispatch, regardless of model family. This is a hard, structural mismatch between
    DeepDelve's client and any genuine Mistral-family repo served via vLLM's native tokenizer mode
    — not something a benchmark run can work around.
    - **Both alternate tokenizer modes tried and both failed for a different reason each**:
      `--tokenizer-mode auto` still auto-detects Mistral's native tokenizer class from the repo's
      shipped `tekken.json`/`params.json` (same rejection, unchanged). `--tokenizer-mode hf` fails
      at engine startup entirely with `AttributeError: CachedMistralCommonBackend has no attribute
      is_fast` — this repo's shipped tokenizer files aren't compatible enough with vLLM's
      HF-tokenizer-mode wrapper either. No third option exists in this vLLM version.
    - **Verdict: BLOCKED, not disqualified and not re-testable as-is.** MiniCPM3-4B's "genuinely
      open infrastructure question" framing applies here too — this never reached testing
      `mistral-nemo`'s actual research/delegation behavior at all, so the original Ollama-served
      2/10 score stands unconfirmed/unrefuted by this attempt. **Real fix, if this is worth pursuing
      later, is a DeepDelve-side change**: make `_get_default_options()`'s `chat_template_kwargs`
      injection conditional (e.g., skip it for models that don't need/support the `enable_thinking`
      toggle at all, or catch/strip on this specific 400 and retry once) — out of scope to hack
      into production code mid-benchmark without the user's sign-off, since it touches every model's
      request path, not just this one candidate's. **Confirmed to affect every other genuine
      Mistral-family repo in this project's candidate list, not just a possibility**: checked
      `mistralai/Devstral-Small-2507` directly — same `MistralForCausalLM` architecture, same
      shipped `tekken.json` (Mistral's native tokenizer format), so `devstral:24b`'s re-test would
      hit the identical 400 block. `mistral:7b-instruct-v0.3` (already spiked earlier this session
      for the bitsandbytes pre-flight check, same `mistralai` org/format) would too. **All three
      Mistral-family candidates in the vLLM re-test plan are blocked by this same issue** — none
      re-testable until DeepDelve's client-side fix above lands. Cleanup: config reverted to
      `deepdelve-gpt-oss:latest`/`rag_cache: enabled: true`, vLLM server shut down cleanly (SIGTERM,
      confirmed zero orphan both times it was killed during this attempt).

  - **`llama3-groq-tool-use:8b` re-test, 2026-07-21 — DISQUALIFIED on real, docs-grounded evidence,
    NOT a serving-stack artifact.** `Groq/Llama-3-Groq-8B-Tool-Use` (not gated, native
    `LlamaForCausalLM`, real bf16 checkpoint). Its own native `max_position_embeddings` is only
    8192 — below the project's ~16K floor, but this is a permanent model-level training fact, not a
    hardware-forced squeeze, so the "discard outright below 16K" standard point 6 does NOT apply
    here (clarified in that point above) — tested at its real native 8192 ceiling instead.
    - **First smoke test (plain OpenAI-style `tools=` + `tool_choice: "auto"`) failed outright**:
      the model narrated a plain-text answer, never attempting a tool call at all. Root cause
      checked directly, not assumed: this repo's own `tokenizer_config.json` chat template has ZERO
      tool-rendering logic (`'tools' in chat_template` is False) — a bare vanilla Llama-3 template.
      vLLM's `tools=` parameter never got rendered into the prompt in any form this model could act
      on, so this first result wasn't a real capability test yet.
    - **Read the model's own HF README** (credits NousResearch for this exact tag convention) and
      manually built its documented raw system-prompt format (`<tools>...</tools>` +
      `<tool_call>...</tool_call>` instructions embedded directly in the system message, bypassing
      the broken auto-render path). Result: 3/3 samples (including the model card's own recommended
      `temperature=0.5, top_p=0.65`) produced genuinely well-formed, correctly-structured JSON with
      a real nested `tasks` array (`#6155`-class bug confirmed absent) — but the model consistently
      omitted the required `<tool_call>`/`</tool_call>` XML wrapper tags every single time.
    - **Caught mid-investigation, per the user's explicit correction**: tried priming the assistant
      turn with a literal `<tool_call>` opening tag as a fix — an UNSOURCED generic technique, not
      verified against this model's own documentation first. User stopped this and asked directly
      whether the model's docs had actually been consulted; they hadn't. Went back to primary
      sources instead: checked Groq's own cookbook (documents their HOSTED API, a different serving
      stack, not applicable to local vLLM hosting), then found and read NousResearch's own
      `Hermes-Function-Calling` reference repo (the exact upstream implementation this model's tag
      convention is credited to) and its real parsing code —
      `utils.py::validate_and_extract_tool_calls` requires the literal `<tool_call>` XML element via
      `root.findall(".//tool_call")` and returns zero tool calls without it. **Confirmed vLLM's own
      bundled `hermes_tool_parser.py` requires the identical `<tool_call>` token** (same
      `tool_call_start_token` check before extraction) — so this isn't a vLLM-specific integration
      gap either; both the credited reference implementation and vLLM's own parser agree the tags
      are mandatory.
    - **Verdict, now grounded in real evidence rather than assumption**: the model's underlying
      JSON-generation quality is genuinely good (correct structure, real BFCL-consistent
      capability, no `#6155`-class bug) — but it does not reliably emit the `<tool_call>` wrapper
      tags any correctly-built Hermes-style parser requires to extract a real structured tool call,
      confirmed against 2 independent authoritative sources (the credited upstream reference parser
      and vLLM's own bundled parser), not just this session's own serving setup. This is a genuine,
      dual-confirmed disqualification, not the Ollama `#6155` artifact this candidate was
      originally suspected of — the original schema-stage rejection stands, now on firmer evidence
      than before. Cleanup: server shut down cleanly (SIGTERM, zero orphan), no config change
      needed (never got far enough to wire DeepDelve's config at all — disqualified at the isolated
      smoke-test stage, per the plan's own step 3 evidentiary bar, no full benchmark run spent).

- **`qwen3:8b` vLLM re-test, 2026-07-21 — KILLED mid-run, real DeepDelve-side fabrication bug
  found and fixed, no verdict on the model yet.** Loaded via `~/.venvs/vllm`, nothink mode
  confirmed clean via direct curl before running (README's qwen3-family think-mode bug is Ollama's
  own serving-layer defect, not the model's — already confirmed absent on vLLM the same session).
  Run was genuinely progressing (3rd delegation round, 18 fetched URLs, 19 findings, clearly
  better-behaved than any MiniCPM candidate) when a user-requested cross-check against the real
  `sources/` folder caught a real integrity problem: only 15 files on disk vs. 18 claimed
  `fetched_urls` and 19 findings, and **5 of 19 findings had a fabricated `source_url`** — a leaked
  task/instruction name string instead of a real URL. Run killed before reaching FindingsWriter;
  no verdict reached on `qwen3:8b` itself.
  - **Root-caused, 2026-07-21, confirmed model-agnostic**: `_run_single_task`'s `add_finding`
    fallback (`src/engine/orchestrator.py`) used the bare `task_name` as `source_url` whenever a
    dispatched task (any Analyzer-tier call, by design) fetched no URL of its own, with no marker
    distinguishing it from a real citation. `_build_findings_source_material`
    (`src/engine/completion.py`) then rendered every finding identically as `### Source:
    {source_url}` regardless of whether that value was a real URL or the placeholder — FindingsWriter
    (any model, on any backend) had no structural signal to tell them apart. This is the same
    mechanism regardless of which model is serving FindingsWriter, so it was not `qwen3:8b`-specific
    and would have equally exposed every other vLLM re-test candidate still to come.
  - **Fixed, commit `0852cc4`**: (1) `orchestrator.py` now recovers the real reference URL a
    Searcher handed its Analyzer (already extracted for the reconstructed-URL check, now computed
    unconditionally rather than gated behind `grounding_check.enabled`) before ever falling back to
    `task_name`; (2) `_build_findings_source_material` never renders a non-`http(s)` `source_url` as
    a `### Source: ...` entry anymore — such findings are named in a separate, explicitly
    non-citable list instead, with instructions not to invent a source for them. Matters more given
    this project tiers some writer roles onto smaller specialist models
    (`settings.specialist_model`), which are less likely to infer the ambiguity on their own.
    `test_structural_checks.py` extended (`_findings_uncited_fallback_scenario`) and existing
    filename-scenario assertion corrected to match the new behavior; both pass.
  - **No past verdict in this file was corrupted by this bug**: MiniCPM5-1B's disqualification was
    zero `delegate_tasks` calls (never reached findings.md), `llama3-groq-tool-use:8b`'s was a
    missing `<tool_call>` wrapper (never reached research), `mistral-nemo:12b`'s was a first-request
    400 (never reached research) — none of the currently-closed vLLM re-test verdicts relied on
    findings.md content, so none need re-opening.
  - **Next step**: retest `qwen3:8b` fresh now that the bug is fixed — this candidate is the most
    informative next run precisely because it's the one that surfaced the bug.

- **MiniCPM5-1B evaluated as both a paired specialist AND a full single-model replacement,
  2026-07-20/21 — DISQUALIFIED in both forms, fully closed, see the single-model entry near the
  end of this bullet for the final, clean, decisive result.**
  User asked to check other MiniCPM4-family options after the MiniCPM4-MCP evaluation below;
  research (RESEARCH.md's earlier MiniCPM5-1B leaderboard entry) already flagged this as a
  sub-1.5B model, far below this project's own established capacity floor — but user's explicit
  framing was "one thing is documentation, another is test, let's try," so tested live rather
  than ruled out on priors alone.
  - **Genuinely simpler integration than MiniCPM4-MCP, confirmed by reading docs first this time**
    (see the correction above about not doing that for MiniCPM4-MCP): MiniCPM5-1B emits XML-style
    `<function name="...">...<param name="...">value</param></function>` tool calls (its own
    `chat_template.jinja`, read directly), a format close enough to the Hermes/Qwen convention
    that **Ollama's built-in tool-call parser handles it natively** — confirmed live, direct
    `/api/chat` calls with a `tools=` param returned correct OpenAI-shaped `tool_calls` with zero
    custom proxy code. Plain `LlamaForCausalLM` architecture (config.json), no custom kernels.
    OpenBMB has an official Ollama deployment cookbook (`docs/deployment/ollama.md`) confirming
    the same integration path and recommended sampling (`temperature=0.7, top_p=0.95` no-think
    mode; `0.9/0.95` think mode) — used exactly as documented, not reverse-engineered.
  - **Pulled via `ollama pull hf.co/openbmb/MiniCPM5-1B-GGUF:Q8_0`** (1.1GB), local tag
    `minicpm5-1b` with `num_ctx` set to 131072 (the model's actual native max per its own
    `config.json`'s `max_position_embeddings`, not the cookbook's conservative 8192 example
    value — same standard applied to MiniCPM4-MCP's 32768 setting earlier).
  - **Isolated 5-case smoke test: 5/5 passed**, correct function selection, correct abstention on
    a non-tool question (honestly declined an arithmetic question rather than fabricating an
    answer — a real, observed instance of a friend's claim that small models given permission to
    say "I don't know" avoid confident hallucination). One real gap already visible in this
    isolated test, though: one `delegate_tasks` call dropped the actual task instructions,
    keeping only `task_name` — an argument-completeness weakness, not a format failure.
  - **Specialist-role system prompts audited before testing further** (per the same
    read-first correction): `WebSearcherInstructions`/`AcademicSearcherInstructions`
    (`src/prompts.py`) already explicitly ban finishing a task from "search snippets or your own
    prior knowledge" — exactly the strategy a friend of the user's independently recommended for
    small models. No prompt changes were needed; this was already the existing design.
  - **Live end-to-end test, same query used throughout this evaluation**: the single most
    favorable MiniCPM result of the day. Correctly found not just the arXiv preprint
    (2404.02680) but also the actual peer-reviewed PUBLISHED version (ACM DOI 10.1145/3674640)
    of the same paper, and correctly flagged that a third source (ETH Zürich) self-labels
    "peer-reviewed" without evidence of external review — a more careful preprint-vs-published-
    vs-self-claimed distinction than any earlier gpt-oss or MiniCPM4-MCP run made. Rust version
    (1.97.1, plus beta/nightly) correct. Lowest tool-error count of any MiniCPM variant tested
    (11, vs. 27-53 for MiniCPM4-MCP's runs), closest yet to the clean gpt-oss baseline (0-8).
  - **Real problem, still present**: the `related_work` sub-agent was forcibly aborted TWICE
    ("Agent trapped in loop. Quota exceeded multiple times for fetch_url_to_workspace") before
    finally succeeding on retry attempt #4. Same underlying category as MiniCPM4-MCP's issues
    (not reliably knowing when to stop), different specific shape. The system's own retry/
    recovery machinery absorbed this and still produced a good outcome, but first-attempt
    reliability isn't clean.
  - **CORRECTION, 2026-07-21 — every result above was very likely produced in unintended THINK
    mode, not the nothink mode intended for this role.** User asked for an in-depth read of the
    full official `openbmb/minicpm` docs/skills tree before treating anything as a settled
    "discard" — all 23 currently-relevant English docs read directly (main README, all 8
    deployment cookbooks, all 5 fine-tuning cookbooks, both `minicpm5-deploy`/`minicpm5-deploy-
    ollama` Agent Skills). Confirmed empirically first: every live `/api/chat` response from the
    `minicpm5-1b` Ollama tag includes a populated `"thinking"` field with real verbose
    chain-of-thought, even with a custom Modelfile injecting an empty `<think>\n\n</think>\n\n`
    prefix meant to force nothink mode per the model's own `chat_template.jinja` logic — the
    injection did not suppress it. **This is not a mistake unique to this setup — it's a
    documented, vendor-acknowledged gap in Ollama's OWN official cookbook and shared by other
    edge/consumer backends**: `docs/deployment/ollama.md`'s own example Modelfile only sets
    `temperature`/`top_p` and comments them "tuned for no-think mode," but never actually injects
    a `<think>` prefix into the `TEMPLATE` block — because "Ollama does not auto-evaluate the
    GGUF-embedded Jinja chat template; it falls back to the Modelfile's Go `TEMPLATE` block."
    Independently confirmed by two OTHER backends' own docs: `docs/deployment/mlx.md` states
    plainly "the released chat template auto-injects `<think>\n` when no system message disables
    it, so you get think-mode behaviour by default"; `docs/deployment/lmstudio.md` states LM
    Studio's `chat_template_kwargs.enable_thinking` flag is not consistently honored either. Only
    **vLLM and SGLang** correctly implement real `enable_thinking` (both evaluate the actual HF
    template). Practical tool-calling path found for each: SGLang's MiniCPM5 XML parser only
    exists on an unreleased `main` branch (merged 2026-05-22, no pip release yet); vLLM is more
    practical right now — the repo itself ships the parser file
    (`tool_parsers/minicpm5xml_tool_parser.py`, same as the pending upstream PR) loadable into a
    normal `pip install vllm>=0.21` via `--tool-parser-plugin`, no from-source build needed.
    **Implication**: every positive result recorded above (best report quality of any MiniCPM
    candidate, correct preprint-vs-published distinction) was very likely produced in the heavier,
    more deliberate think mode, not the fast/latency-bound mode this role actually calls for — so
    neither the positive results nor the one real weakness (the forced-abort looping) can be
    trusted as representative of the model's intended operating mode. No discard-or-keep verdict
    is actually settled; this reopens the question rather than closing it either way. Next step:
    stand up vLLM with the bridged tool-parser plugin and re-run the same live query in genuine
    nothink mode before drawing any conclusion. Real hardware caveat checked (not assumed): the
    user's GPU (RX 9060 XT) is AMD RDNA4, not NVIDIA — vLLM defaults to CUDA-only, but ROCm 7.2
    (March 2026) added official RDNA4 vLLM support with "out-of-the-box parity" alongside Ollama/
    llama.cpp, so this should work, just via the ROCm-specific install path (Docker image or ROCm
    wheel) and less battle-tested than the CUDA default every vLLM doc assumes.
  - **Status: not yet a final call either way**, now for a second, more fundamental reason than
    "needs more runs" — the model hasn't even been tested in its correct operating mode yet. Best
    MiniCPM candidate tested by a real margin under think mode; live config left pointed at it
    (`specialist_model: minicpm5-1b`) rather than reverted, pending a proper nothink-mode re-test
    via vLLM before any final call.
  - **FINAL VERDICT, 2026-07-20 — genuine nothink-mode retest via vLLM completed; DISCARD for this
    role.** Fixed the pre-existing, broken `~/.venvs/vllm` install (missing `libopenmpi3t64`, then
    ROCm userspace libs stale relative to the current kernel — fixed via
    `sudo amdgpu-install --usecase=rocm,hip --no-dkms`, no kernel module/DKMS involved, fully
    reversible; a system-level DKMS attempt tried first failed on a genuine kernel-symbol conflict
    against `7.0.0-28-generic` and was cleaned up before this correct approach was found). Launched
    `vllm serve openbmb/MiniCPM5-1B --tool-call-parser minicpm5 --enforce-eager
    --gpu-memory-utilization 0.20 --max-model-len 16384` (memory-utilization and `--enforce-eager`
    both driven down from the plan's defaults after gpt-oss's usual 14.6GB Planner footprint left no
    VRAM headroom on the 17.1GB card — swapped Planner to `deepdelve-mistral-nemo:latest`, 7.1GB,
    for this test only; **`api.openai_model` in `~/.deepdelve/config.yaml` is still set to this
    temporary value and must be reverted to `deepdelve-gpt-oss:latest` once this entry is read**).
    Confirmed via direct `curl` with `chat_template_kwargs: {"enable_thinking": false}` that
    real nothink mode now works (no `<think>` leakage) — DeepDelve needed zero new code for this,
    since `orchestrator.py::_get_default_options()` already threads `enable_thinking` through
    `extra_body`/`chat_template_kwargs` at both dispatch sites (line ~678 specialist, ~1274 main).
    Ran the same live query used throughout this whole evaluation. Result, traced through the raw
    session log rather than assumed:
    - **A genuine content hallucination reached the final report.** The model's own first-pass
      reasoning (session log event 17, well before any remediation pass) already commits to
      "Blog Rust 1.85.0 - URL: https://blog.rust-lang.org/2025/02/20/Rust-1.85.0/" — a real but
      *stale* blog post surfaced by a web-search snippet — as "the latest stable version." It later
      correctly fetches the actually-current `releases.rs` page (which plainly states
      `Stable: 1.97.1`, confirmed by directly grepping the saved source file), but never revises
      its earlier claim — instead the final findings/report cite `<https://releases.rs/>` as the
      source for the wrong "1.85.0, released on February 20, 2025" value. This is not a
      misread-ambiguous-source case like the earlier MiniCPM4-MCP filename-hash mistake; the
      correct number was sitting in a source the model itself fetched and cited, and it reported
      the wrong one anyway. Traced with certainty to MiniCPM5-1B's own Searcher/Analyzer
      reasoning, not to the temporarily-swapped mistral-nemo Planner's remediation passes
      (`FindingsWriterFix_attempt2`/`BuilderFix_attempt3` copied this text forward verbatim from
      the same flawed `findings.md`, they did not introduce it).
    - **`findings.md` itself never passed the grounding check on its own terms**: it shipped as an
      "AUTO-RECOVERED DRAFT" (the model narrated the findings as chat text instead of calling
      `write_workspace_file`, across the full retry budget) — the salvage path saved the run from
      an outright `missing_findings` failure, but the underlying content was never actually
      verified before being carried into `final_report.md`.
    - **Six identical malformed tool calls**: `fetch_url_to_workspace` called with
      `{"url": "sources/paper_143022.md"}` — a workspace-relative path to a file it had already
      saved, not a real URL — repeated six times with no self-correction, on top of one
      argument-parsing failure elsewhere. Confusing "fetch a URL" with "read a file I already
      wrote" is a new, distinct failure shape from anything seen in the earlier think-mode run.
    - Two literal `"[Authors' names]"` placeholder strings (HAL preprint, ACM paper) also reached
      the final report uncorrected — a completeness/fabrication-adjacent defect the downstream
      Builder (mistral-nemo, not gpt-oss, for this run) failed to catch, unlike an earlier same-day
      run where gpt-oss's Builder did catch and fix an analogous mistake.
    - Required 4 completion-check attempts (`not_delegated`, `missing_findings`, `missing_artifact`,
      then clean) before the run closed at all.
    **Conclusion**: genuine nothink mode is now confirmed reachable and correctly wired end-to-end
    (infrastructure verdict: works, zero new code needed), but this properly-configured test is, on
    content reliability, *worse* than the earlier (unintentional think-mode) run — not better. A
    single model-generated hallucination that directly contradicts its own cited source, shipped
    past an already-degraded (auto-recovered, unverified) grounding path, past a Builder that didn't
    catch it, into the user-facing report, is disqualifying for an unsupervised specialist role
    regardless of mode. Sub-1.5B parameter budget was flagged as a priors-based concern from the
    very start of this evaluation (RESEARCH.md); this live result confirms rather than contradicts
    that prior. **Discarding MiniCPM5-1B (both modes now tested) for the specialist role.** Cleanup
    still open: revert `api.openai_model` to `deepdelve-gpt-oss:latest`, decide whether to keep or
    stop the standing `~/.venvs/vllm` server, remove `specialist_model`/`specialist_base_url` from
    live config (or point them at a different, larger candidate later).
    - **RE-FLAGGED 2026-07-21, per the new "Model Evaluation Standard" section above (point 2,
      isolation): this verdict does not actually isolate MiniCPM5-1B as the only variable** — the
      Planner/Builder was swapped off `gpt-oss:20b` onto `mistral-nemo:latest` to free VRAM for
      this run (see line ~1186 above), and the uncorrected `"[Authors' names]"` placeholders were
      explicitly attributed to that swapped-in Builder failing to catch them, not to MiniCPM5-1B's
      own output. The traced-to-source Rust-version hallucination and the six malformed
      `fetch_url_to_workspace` calls ARE cleanly attributable to MiniCPM5-1B itself (confirmed via
      the raw session log, not the Builder), so the discard isn't baseless — but it was reached
      under a confounded pipeline, not a clean one, and should not be read as a fully settled,
      isolated verdict on the model's own capability.
      **Retest explicitly NOT queued — user decision, 2026-07-21**: a clean isolated retest
      (`gpt-oss:20b` kept in the Planner/Builder seat) was initially proposed as the outstanding
      item, but the user rejected pursuing that combination further at all — pairing `gpt-oss:20b`
      as coordinator with any small model as a specialist is a strategy the user doesn't want tried
      again regardless of which small model sits in the specialist slot (see the "Heterogeneous
      role tiering" closure note above). MiniCPM5-1B's status is therefore left as: discard
      reached under a confounded test, not fairly re-litigated, and not going to be re-tested in
      that same paired form.
      **Single-model bake-off run — COMPLETED 2026-07-21, clean and decisive: DISQUALIFIED, no
      caveats this time.** `MiniCPM5-1B` set as `api.openai_model` across ALL roles (Planner/
      Builder/FindingsWriter/PeerReviewer, not just Searcher/Analyzer) — the same architecture
      every other bake-off candidate in this section was measured under, and the one evaluation
      MiniCPM5-1B had never actually had. Ran via `~/.venvs/vllm` with the model's real full context
      (`--max-model-len 131072`, not the earlier tests' 16384 — the actual `max_position_embeddings`
      from the model's own `config.json`; needed `--gpu-memory-utilization 0.9` once nothing else
      was competing for VRAM, since a stale `VLLM::EngineCore` process from an earlier launch
      attempt was still holding 8.1GB and had to be killed first). Confirmed via direct `curl`
      before running anything through DeepDelve: nothink mode clean (`reasoning: null`, zero
      `<think>` leakage) — same infrastructure verdict as before, this part was never in question.
      Ran the exact standing sales-forecasting benchmark prompt (`eval/sales_forecasting_
      benchmark.md`) used throughout this whole bake-off.
      **Result, traced through the raw session log**: the model called `list_workspace_files` once,
      then `think_tool` with near-identical reflection text ~20 times in a row, burning its entire
      `think_tool` quota (30) without ever once calling `delegate_tasks` — no Searcher was ever
      spawned, `fetched_urls` stayed empty, `findings.md` was never written. It then asserted
      "I'll compile the findings and final report now based on the delegated tasks" — a flatly false
      claim, since nothing had been delegated and no findings existed — repeated verbatim several
      times in the trailing text output. The engine's own `not_delegated` completion check caught
      this correctly (`_run_state.json`: `"No delegate_tasks call was ever made — this looks like an
      answer from memory, not real research."`) and the run terminated with `Report: NOT WRITTEN`
      once the overall retry budget was exhausted — no artifact, no fabrication reaching the user,
      the failure mode this project's completion checks exist to catch, working as designed.
      **This clears every point of the Model Evaluation Standard above with no exceptions**:
      operating mode confirmed via raw API call before scoring (point 1); MiniCPM5-1B was the only
      variable in the entire pipeline, nothing paired or swapped (point 2, the exact gap the two
      earlier verdicts had); backend/version stated (vLLM 0.25.1, ROCm, ~/.venvs/vllm) (point 3).
      A second corroborating run was not initially executed given how early and total the failure
      was (dead by turn ~20 of a 30-call quota, zero real work of any kind produced).
      **Point 4 corroborated with a real second run, same day**: after this session separately
      found and fixed a real process-hygiene bug (killing an already-running `vllm serve` with
      `-9` orphans its `VLLM::EngineCore` child, since SIGKILL can't be trapped — see the
      "Heterogeneous role tiering"/vLLM re-test entry above), the user asked whether that finding
      could have contaminated THIS verdict's VRAM/context state. Traced the actual timeline: the
      one stale-process contamination hit during this evaluation happened BEFORE the scored run
      (an 8.1GB orphan from a failed 16384-ctx attempt, found and killed before the successful
      131072-ctx relaunch that the benchmark actually ran against) — the scored run itself used a
      clean, correctly-provisioned, freshly-confirmed server throughout, so the original verdict was
      never actually contaminated. Re-ran anyway as a precaution, from a freshly-clean GPU state
      (`rocm-smi --showpids` confirmed zero KFD processes before relaunch), same full 131072
      context, same nothink-mode curl confirmation. **Result: reproduced the identical core
      failure** — 63 events this time (`list_workspace_files` x11, `think_tool` x10, spread across
      3 completion-check attempts instead of 1), but again ZERO `delegate_tasks` calls across the
      entire run, `Report: NOT WRITTEN`. Point 4 (discard needs >1 run) is now genuinely satisfied,
      not just argued around.
      **Final verdict, now doubly corroborated**: MiniCPM5-1B is disqualified as a DeepDelve model
      candidate in BOTH forms tested — paired specialist (confounded, not re-litigated per the
      user's own decision) and full single-model replacement (clean, decisive, reproduced on an
      independent run). No further MiniCPM5-1B testing is planned; nothing about this model's
      evaluation remains open.
  - **Cleanup done, 2026-07-21**: `api.openai_model` reverted to `deepdelve-gpt-oss:latest`,
    `settings.specialist_model`/`settings.specialist_base_url` removed from `~/.deepdelve/
    config.yaml` (confirmed `_build_client`'s `.get(...)` fallback in `orchestrator.py` handles
    their absence, single-model config resumes cleanly), test `vllm serve` process killed.
    `~/.venvs/vllm` itself kept on disk — a verified-working general ROCm+vLLM install for this
    exact GPU/kernel, reusable for a future, larger specialist candidate without redoing the ROCm
    fix.
  - **Cleanup done again, 2026-07-21, after the single-model run above**: `api.openai_model`
    reverted to `deepdelve-gpt-oss:latest`/`http://localhost:11434/v1` (confirmed via config diff),
    the config backup at `~/.deepdelve/config.yaml.bak_pre_minicpm_singlemodel_20260721` can be
    deleted once this entry is read, the vLLM server process (port 8000) killed and confirmed via
    `rocm-smi` back to near-zero VRAM use.

- **Qwen3-family think-mode control confirmed broken on Ollama too, 2026-07-21 — every Qwen3
  benchmark row in README.md's model table was very likely reasoning-polluted.** Surfaced while
  answering the user's direct question ("could the models we benchmarked have a nothink mode too?")
  after the MiniCPM5-1B finding above. Tested live against Ollama 0.31.2, both mechanisms DeepDelve
  could plausibly rely on:
  - `chat_template_kwargs.enable_thinking: false` via the OpenAI-compat endpoint (the mechanism
    `orchestrator.py::_get_default_options()` actually sends): confirmed via direct `curl` against
    `deepdelve-qwen3-4b` that this has **zero effect** — the model still burns its full token budget
    on unrequested reasoning (a populated `reasoning` field, `content` left empty on a 200-token cap).
  - Ollama's own native `/api/chat` `"think": false` field (the mechanism Ollama itself recommends
    for hybrid-reasoning models, and which DeepDelve does NOT currently send at all): confirmed via
    direct `curl` against the plain, unmodified `qwen3:4b` base tag that this is **actively worse
    than doing nothing**. With `think: false`, the model still reasons at length but the raw,
    unstructured chain-of-thought is dumped straight into `message.content` with no `<think>` tag
    and no separate `thinking` field at all. With `think: true`, the exact same request correctly
    separates reasoning into its own field and `content` holds only the clean final answer ("4").
    The "off" setting is the one that pollutes the model's real working output; "on" is the one
    that's clean.
  - **Why this doesn't apply to `gpt-oss:20b` (the current default)**: tested the same two
    mechanisms against `deepdelve-gpt-oss` — also ineffective at fully suppressing reasoning (gpt-oss's
    harmony format always produces an analysis channel by design, this isn't a bug), but critically,
    Ollama keeps that reasoning cleanly separated into its own `reasoning`/`thinking` field in BOTH
    cases, never mixed into `content`. Confirmed via `agent_framework`'s own client source
    (`choice.message.content` read directly at the point a `Content.from_text(...)` is built;
    `reasoning_details` handled as a distinct `text_reasoning` content type, never merged into the
    text DeepDelve's agents treat as the model's actual output) that DeepDelve only ever consumes
    `.content` — so gpt-oss's inability to fully disable thinking is benign here, while Qwen3's
    content-pollution bug is not.
  - **Implication**: `qwen3.6` (35b-a3b), `qwen3:4b`, `qwen3:8b`, and the `qwen3:4b` GRPO fine-tune's
    live Ollama benchmark run (its TRAINING pipeline correctly used `enable_thinking=False` via HF's
    own `apply_chat_template`, unaffected — see the training entry below — this is specifically
    about the live benchmark's inference path) were almost certainly running with large amounts of
    uncontrolled reasoning text bleeding directly into every tool-call argument and piece of written
    output across their entire benchmarked runs, this whole time. This is a real, previously-unknown
    contributing factor to their disqualifying failure modes (thin_coverage stalls, narrated-instead-
    of-written reports, canned non-responses on the corrective nudge) — plausibly consistent with
    "a small model getting confused/derailed by its own unmanaged internal monologue," layered on
    top of (not a replacement for) the capacity-floor literature evidence already cited in README.md.
  - **Not yet re-tested and not re-scored**: no Qwen3 candidate has been re-run with genuine nothink
    mode (would need the same vLLM/SGLang fix class used for MiniCPM5-1B — `~/.venvs/vllm` is
    already available for this). Existing scores are left standing as the best evidence so far, not
    silently trusted as clean; README.md's model table now flags every affected row with a `†` and
    an explanation rather than treating the old numbers as unaffected. Whether re-testing is worth
    the time (these are all still sub-14B, below the literature's own capacity floor regardless) is
    an open call, not yet made.
  - **Confirmed via vLLM, 2026-07-21: the bug is Ollama-specific, not a Qwen3 model limitation.**
    Unloaded `gpt-oss` from Ollama first (`ollama stop`, freed ~14.3GB, matching the earlier lesson
    about not squeezing vLLM into leftover VRAM), launched `vllm serve Qwen/Qwen3-4B --tool-call-
    parser hermes --enforce-eager --gpu-memory-utilization 0.85 --max-model-len 16384` (first attempt
    at `0.55` under-budgeted the KV cache and failed cleanly with a clear `ValueError`, not a crash —
    raised to `0.85`, succeeded). Direct `curl` against the real vLLM server (genuine jinja
    chat-template evaluation, same class of fix as MiniCPM5-1B):
    - `chat_template_kwargs.enable_thinking: false` → clean `"4."`, `reasoning: null`, 3 completion
      tokens, zero `<think>` content anywhere.
    - Same request with `enable_thinking: true` → full `<think>...reasoning...</think>` block
      inline in `content` (Qwen3's own convention keeps it in `content`, unlike gpt-oss's separate
      channel — confirmed as the model's real, correct behavior, not a bug).
    - A real `tools=` request with `enable_thinking: false` → clean OpenAI-shaped `tool_calls`
      (`web_search({"query": "population of Tokyo"})`), no reasoning leakage, no stray text.
    **Conclusion**: Qwen3-4B's nothink mode is real and works correctly end-to-end once served by
    something that actually evaluates its chat template — Ollama's failure to do so (confirmed
    earlier in this same entry) is entirely Ollama's own gap, not evidence against the model. This
    makes a genuine, clean re-benchmark of the Qwen3 family (via vLLM, same infra now proven twice)
    a real, low-friction option if it's ever worth revisiting — test server stopped after
    verification, nothing left running.

- **MiniCPM3-4B scoped and attempted as a single-model candidate, 2026-07-21 — INCONCLUSIVE, a real
  infrastructure hang, not a capability verdict.** After MiniCPM5-1B's disqualification, checked
  other real MiniCPM-family candidates. `MiniCPM4-8B`/`MiniCPM4.1-8B` ruled out immediately — their
  own model cards document no function-calling support at all (only `MiniCPM4-MCP`, already
  discarded, was OpenBMB's dedicated tool-use variant of that generation). `MiniCPM3-4B` looked
  genuinely promising: documented BFCL v2 71.6 (beats several 7-9B models), Apache-2.0, native vLLM
  model support (`MiniCPM3ForCausalLM`). Initially concluded (wrongly, corrected by the user — see
  `feedback_read_docs_before_building.md`) that no vLLM tool-call-parser existed for its custom
  `<|tool_call_start|>`/Python-function-call format, having only checked the locally installed vLLM
  package's bundled parsers. OpenBMB's own `github.com/OpenBMB/MiniCPM` repo
  (`demo/minicpm3/function_call/`) ships a ready `minicpm_tool_parser.py` + matching jinja chat
  template for exactly this — needed two small compatibility fixes for this vLLM version (0.25.1):
  import paths moved (`vllm.entrypoints.openai.protocol` → `.chat_completion.protocol` +
  `.engine.protocol`; `vllm.entrypoints.openai.tool_parsers` → `vllm.tool_parsers`), and the base
  `ToolParser.__init__` now takes a second `tools` param the reference script's subclass didn't
  accept. Confirmed working after patching: real structured `tool_calls` out of a direct `curl`
  test, no narrated JSON.
  - **Real hardware ceiling found, applied correctly THIS time before benchmarking**: MiniCPM3-4B's
    62-layer, non-MLA-optimized-in-this-config KV cache cost forced a real serving ceiling of ~6144
    tokens on this GPU (vLLM's own KV-cache-budget error gave this number directly), well under the
    project's ~16K-token floor (`context_budget_chars: 50000`'s documented "safe margin under a
    16K-token num_ctx"). **First response was to proportionally scale `context_budget_chars` down
    to 8000 and run the benchmark anyway — the user corrected this as the wrong general policy
    going forward** (new Model Evaluation Standard point 6, above): a candidate that can't clear
    ~16K tokens should be discarded outright on hardware grounds, not accommodated by rescaling the
    project's own safety margins. This specific run was allowed to finish since it was already
    informative either way, but is not the template for future candidates.
  - **Result: a real hang, not a clean pass or fail.** The DeepDelve run itself showed zero visible
    progress for ~16+ minutes past the startup banner. Diagnosis: vLLM's own periodic engine-stats
    logger (normally prints every ~10s) went completely silent after the first exchange, the
    APIServer process (not EngineCore) was pinned at ~94% CPU while GPU utilization sat at only 7%,
    and even the lightest possible request (`GET /v1/models`) timed out entirely. This pattern
    points at OpenBMB's own reference `extract_tool_calls_streaming` — it re-scans the ENTIRE
    accumulated generation text with a nested-parentheses regex
    (`r"(\w+)\(((?:[^()]*|\([^()]*\))*)\)"`) on every single streamed token, a known catastrophic-
    backtracking risk class, not something DeepDelve's own code touches. Killed the hung run and
    server rather than let it burn GPU time indefinitely; confirmed no leftover `VLLM::EngineCore`
    process afterward (this evaluation's third time hitting that exact leftover-process gotcha —
    always `rocm-smi --showpids` after any `pkill`/kill of a `vllm serve` parent, the EngineCore
    child does not reliably die with it).
  - **Verdict**: NOT a capability disqualification like MiniCPM5-1B's — this never reached the
    point of testing MiniCPM3-4B's actual research/delegation behavior at all, so per the Model
    Evaluation Standard's point 1 (confirm the operating mode works before scoring), this doesn't
    count as a settled discard. It's an open infrastructure question: OpenBMB's own reference
    tool-parser has an apparent streaming-performance bug (or this vLLM version's streaming
    invocation pattern doesn't suit it) that would need a real fix (e.g., incremental parsing
    instead of re-scanning full text per token) before a fair benchmark could run. Not pursued
    further this session — flagged as genuinely unresolved, not "MiniCPM3-4B discarded."

- **MiniCPM4-MCP evaluated as a specialist-role candidate, 2026-07-20 — real infrastructure built
  and kept, model itself not yet viable.** User surfaced `github.com/openbmb/minicpm`; downloaded
  `MiniCPM4-MCP` (the tool-use SFT checkpoint, not the base chat model — see RESEARCH.md's §6.2
  entry on why the base checkpoint doesn't inherit the MCP fine-tune's tool-calling numbers),
  Q5_K_M GGUF via `ollama pull hf.co/mradermacher/MiniCPM4-MCP-GGUF:Q5_K_M` (5.8GB, comfortable
  on 16GB VRAM), local tag `minicpm4-mcp` with `num_ctx` set to the model's real native max
  (32768, confirmed via the GGUF's own `minicpm.context_length` metadata and the upstream
  `config.json`'s `max_position_embeddings` — going further to the maker's documented
  128K-validated LongRoPE factors would require re-converting the GGUF from patched source
  weights, not just an Ollama parameter, deferred for later).
  - **Format mismatch found and solved**: MiniCPM4-MCP's own embedded chat template doesn't emit
    OpenAI-style JSON `tool_calls` — it emits a `<|thought_start|>...<|tool_call_start|>
    func(arg=val)<|tool_call_end|>` Python-code-block format. Ollama's generic `/v1/chat/
    completions` tool-calling support assumes OpenAI JSON and fails outright against this model
    ("peg-native format" 500 error, confirmed live). **Built `finetune/minicpm_tool_proxy.py`**: a
    FastAPI translation proxy (checked GitHub for prior art first — `philipluo/MY-LITE-LLM` does
    the same class of thing generically for `minicpm-v`; this one is tailored to MiniCPM4-MCP's
    actual documented format instead of generic JSON-prompting) that builds the model's own
    "# Functions" prompt block from OpenAI `tools=` schema, renders full multi-turn history
    (including prior tool_calls/tool-result messages) into the model's native turn format, and
    parses its Python-code-block output back into OpenAI-shaped `tool_calls` JSON. Verified in
    isolation: single-turn tool call, multi-turn tool-result round-trip (model correctly answered
    directly instead of re-calling once given a result), both correct.
  - **New config plumbing added to make this pluggable**: `settings.specialist_base_url`
    (`src/tools/config_template.yaml`, `src/engine/orchestrator.py`'s `_build_client`) — an escape
    hatch alongside the existing `settings.specialist_model` for a specialist model that needs a
    DIFFERENT endpoint (the translation proxy), not just a different model name on the same
    endpoint. Real bug caught and fixed while wiring this in: `_build_client`'s injected
    `AsyncOpenAI(base_url=...)` — the object that actually issues HTTP requests, not the wrapper
    `OpenAIChatCompletionClient` — was still hardcoded to `api_cfg["openai_base_url"]` even after
    adding the override parameter, so the first live-test attempt silently bypassed the proxy
    entirely and hit Ollama directly (same "peg-native format" error as before this whole effort).
    Fixed; `test_structural_checks.py` and `ruff check` both clean after.
  - **Live end-to-end result, real query, real pipeline** (same Rust-version + borrow-checker
    query used throughout the routing-classifier verification above): the fix held — proxy
    received real traffic, tool calls flowed correctly in both directions, run completed with a
    real report (not a crash, not a silent drop). **One genuine positive**: this run's
    `AcademicSearcher`/`DocumentAnalyzer` chain (via MiniCPM) surfaced a real academic source
    (`ETH Zürich "Implementing a Sound Borrow-Checker"`) that the earlier gpt-oss run never
    found, alongside the same arXiv LLBC paper both runs found.
  - **A real, distinctive new failure mode also surfaced, not predicted by the isolated tool-call
    test**: a nested `DocumentAnalyzer` sub-agent (routed through MiniCPM, since Analyzer roles
    share the specialist tier) called `read_workspace_file`/`grep_workspace_file`/
    `extract_structured_data` with `filename: "Analyze paper metadata"` — ITS OWN TASK LABEL, not
    a real file — repeatedly, never correcting after identical "not found" errors each time,
    until the sub-agent was re-dispatched as a fresh instance 10 separate times. Task-name/
    filename confusion with no self-correction, a new category distinct from anything the earlier
    routing-classifier or grounding-check work targeted.
  - **Reliability was meaningfully worse than the current tier under real load**: 53 tool errors
    this run vs. 0-8 in clean `deepdelve-gpt-oss` baseline runs, ~900s runtime vs. ~680-810s, 4
    `BuilderFix` + 4 `ReviewFix` remediation cycles to clear an `uncited_claims` check (Builder
    itself still runs on the main model, so this is downstream noise from messier findings
    content feeding it, not MiniCPM's tool-calling directly — but a real cost of using it anyway).
  - **Verdict**: the translation-proxy infrastructure is sound and kept as a real, reusable
    project artifact — genuinely solves the format-mismatch problem for any future MiniCPM-family
    (or similarly non-OpenAI-native) candidate. MiniCPM4-MCP itself is **not yet a viable
    specialist-role candidate** — directly the same standing lesson this project has hit
    repeatedly: an isolated tool-call test passing does not predict live multi-agent-role
    reliability. Live config's `specialist_model`/`specialist_base_url` reverted to unset
    (back to the known-good single-model baseline) after this evaluation.
  - **Not done, deferred**: re-converting a GGUF with the maker's 128K-validated LongRoPE factors
    (32K is architecturally native/what's baked into the current GGUF, not an Ollama-imposed
    ceiling — see the maker's own README) — user wants to revisit 128K-context options generally
    later, not specific to MiniCPM.
  - **CORRECTION, same day**: the verdict above was reached before reading OpenBMB's own reference
    implementation (`demo/minicpm4/MCP/generate_example.py` + model-card usage docs) — user
    caught this explicitly ("I told you to search implementations and you did the development
    believing you're a bad ass, don't do the mistake again, if it's new we need to read
    documentation"). Reading it afterward surfaced two real, concrete gaps in the proxy, not
    assumptions: (1) OpenBMB's own reference system prompt has explicit anti-repeat-tool-call
    guidance ("If a tool fails... DO NOT call it again with the same inputs... avoid redundant or
    circular behavior") that this proxy's system prompt never included; (2) their reference parser
    (`parse_tool_for_minicpm3`) handles Python-keyword-colliding argument names and hyphenated
    tool/argument names (real MCP tool-naming conventions) via a temp-rename round-trip that this
    proxy's simpler regex+`ast.literal_eval` parser silently dropped. Confirmed their own
    raw-prompt-plus-custom-parser integration pattern (`client.completions.create` with a
    `tokenizer.apply_chat_template`-rendered prompt, not the chat/tools API) validates this
    proxy's core architecture, though — not a wrong approach, an incomplete one.
    - **Both gaps fixed** in `finetune/minicpm_tool_proxy.py`: added the anti-repeat guidance
      verbatim to `build_functions_preamble`; replaced the parser with an AST-module-body walk
      (`parse_tool_call_block`) ported from their `parse_tool_for_minicpm3`/
      `resolve_ast_call`/`resolve_ast_by_type`, handling keyword-collision and hyphen
      round-tripping the same way. Verified in isolation: `search_papers(from="2020", to="2024")`
      and `get-weather(city="London")` — both previously silent parse failures — now parse
      correctly.
    - **THIRD live test, same query, with both fixes**: the SPECIFIC bug this was meant to fix
      (task-name-as-filename looping) did NOT recur — confirmed gone. But the run surfaced
      DIFFERENT reliability problems in its place: `web_search`/`fetch_url_to_workspace` quota
      exhausted (17 calls against a 15 limit, excessive re-querying rather than converging); the
      existing `topical_mismatch` completion check caught the draft report citing a Yahoo Sports
      article and an unrelated tech listicle as "Rust" sources (noisy search, safety net worked,
      but reveals messy upstream search behavior); and the final report itself regressed in
      accuracy versus the earlier successful run — cited a blog aggregator
      (`emergentmind.com`) instead of the real peer-reviewed arXiv paper the second run found
      correctly, and reported Rust 1.97.0 as current when 1.97.1 (confirmed correct in earlier
      runs) is the actual latest patch.
    - **Revised, still-honest verdict**: the doc-informed fixes solved the exact bug they
      targeted, but MiniCPM4-MCP's reliability in this real multi-step research role remains
      inconsistent run-to-run — one problem fixed, two different problems surfaced in its place.
      Still not a stable specialist-role candidate as of this evaluation. Live config's
      `specialist_model`/`specialist_base_url` reverted to unset again; proxy process stopped.

- **Completion-check remediation loop can exhaust `read_workspace_file`'s quota before the final
  Builder pass gets to actually read what it needs — found live, 2026-07-20, during the routing
  classifier's second re-test run.** A run that hits multiple completion-check remediation cycles
  (missing two-pass discipline, missing artifact, unsupported-claim flag) dispatches a corrective
  sub-agent per cycle (`FindingsWriterFix`, `BuilderFix`, `ReviewFix`), each burning its own
  `read_workspace_file` calls against the SAME shared quota (limit 30) as normal first-pass work.
  Confirmed live: 3 remediation cycles in one run exhausted the quota, and the final `BuilderFix`
  pass self-reported it in the report text ("Due to workspace tool quota limits, I was unable to
  re-read the source file") and silently dropped an entire correctly-researched section
  (`findings.md` had it; `final_report.md` didn't) rather than erroring loudly. Candidate fixes:
  separate quota pool for remediation sub-agents vs. first-pass work, or a harder failure mode
  (explicit error surfaced to the user) instead of a silent content drop when quota is hit
  mid-remediation.

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
     "Done" above) — correct and shipped, but on live re-test didn't rescue its motivating case
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
     "Stretch" section's GRPO entry: target `qwen3:4b`, reward function built around its specific
     documented failure (`thin_coverage` non-convergence). **`finetune/reward.py` and
     `finetune/extract_dataset.py` built and validated against real run logs 2026-07-18** (5 real
     examples extracted so far; public-dataset supplementation researched — see the Stretch entry
     for the full recipe). The actual GPU training environment (venv-must-be-on-root-ext4, ~13GB+)
     still waits on the user's own disk reorganization — the next concrete action once that's done.
  4. **Stay on `gpt-oss:20b` as-is** — the fallback baseline that's already true today regardless
     of how far 1-3 get: nothing is actually blocking the project's local-only goal right now.
  5. **RAG-augmented small model — raised by the user 2026-07-20, not yet scoped.** Initially
     framed as "identify what made the user's prior RAG attempt fail" without knowing the specifics
     — **found the actual prior attempt already documented in this same "Evaluated and rejected"
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
  - **`AgentMessageWidget` click-to-copy — DONE 2026-07-14, commit `577fd53`.** Mirrors
    `UserMessageWidget`'s existing `on_click` → `_copy_to_system_clipboard`/OSC52 fallback pattern
    exactly — one-click copy on the agent's actual answers/reports, not just the user's own
    prompt.
  - **Right-click paste — DONE 2026-07-14, commit `577fd53`.** New
    `engine/tui.py::_paste_from_system_clipboard` (read-side mirror of
    `_copy_to_system_clipboard`: `wl-paste --no-newline` / `xclip -o -selection clipboard`, no
    OSC52 equivalent since that escape sequence is write-only) wired into `PromptInput.on_click`
    on `button == 3` (right-click, confirmed against this project's installed
    `textual/_xterm_parser.py`'s SGR mouse-button mapping), inserting at cursor / replacing the
    current selection. Live-verified: a real `_copy_to_system_clipboard` → `_paste_from_system_clipboard`
    round trip returned the exact original text. Required installing `wl-clipboard` on the dev
    machine — neither it nor `xclip` was present beforehand, so this had never actually worked via
    either mechanism (copy silently fell back to OSC52, unverified; paste had no fallback at all).
    Worth checking for on any fresh setup — without one of these two tools, paste always shows a
    "clipboard paste failed" warning instead of pasting.
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
    rather than implement immediately, given Phase 6 (now shipped, see "Done") and the model
    bake-off (see the "Model bake-off & backend investigation log" section) were the priority at
    the time. Next session should scope a concrete subset (the `AgentMessageWidget` copy button +
    right-click paste are the two smallest, most directly user-requested items) before touching
    the framework-capability survey items, which need real prioritization first.
## Model Evaluation Standard (added 2026-07-21, applies to all bake-off entries going forward)

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

## Model bake-off & backend investigation log (completed 2026-07-11 through 2026-07-18)

Real, finished testing/investigation work — every entry below concluded (a model disqualified, a
backend confirmed/rejected, a benchmark scored), not open backlog. Kept separate from "Done" since
most entries are investigation conclusions rather than shipped code changes; kept separate from
"Planned" since none of it is still-to-do. See README's "Model choice" table for the current-state
summary; this section is the full evidence trail.

- **Local-model bake-off: Gemma 4 12B, Bonsai-8B, and `qwen3:4b` vs. `gpt-oss:20b`** (found/verified 2026-07-13,
  smoke-tested and partially live-tested 2026-07-14) — two real local-model candidates surfaced by
  a 3-model research pass, independently verified (not taken on trust — one of the three research
  responses fabricated citations, see below). **Gemma 4 12B** (Google, Apache 2.0, released
  April/June 2026): dense, encoder-free multimodal, ~7.1-7.6GB at Q4_K_M GGUF (~6.7GB on the QAT
  Q4_0 build) — comfortably inside the 16GB ceiling. **Bonsai-8B** (PrismML, Apache 2.0): trained
  natively at 1-bit precision, 1.15GB, scores 73.3% on BFCL (format-compliance tool-calling) —
  beating every model PrismML tested — but drops to 43.8% on NexusRaven (semantic API
  understanding) vs. Qwen3.5-9B's 75%, a real and confirmed weakness on complex tool semantics, not
  smoothed over in the source.
  - **Derived `deepdelve-*` tags created** (`FROM <base>`, `PARAMETER num_ctx 16384`, matching the
    project's existing `deepdelve-gpt-oss` pattern) for both, plus two more candidates the user
    separately surfaced: `granite3.1-dense:8b` (IBM, Apache 2.0, 5.0GB, 128K context, model card
    claims function-calling) and `phi4-mini:3.8b` (Microsoft, 2.5GB, 128K context, model card
    claims function-calling) — both attractive on paper for being lightweight with a large context
    window. Also fixed a real hygiene issue found along the way: the `SetneufPT`-uploaded Gemma 4
    Ollama tag ships a baked-in `SYSTEM "You are a coding agent. Be concise."` default (verified
    live it's fully overridden by DeepDelve's own system prompt at runtime, so not a functional
    bug — but cleaned up in `deepdelve-gemma4-12b`'s Modelfile regardless, since the default is
    actively misleading for a research agent).
  - **Tool-calling smoke test (2026-07-14), DeepDelve's real `delegate_tasks` schema (2-task nested
    array, `task_name`/`instructions`/`agent_id`), direct `/v1/chat/completions` calls**:
    **`granite3.1-dense` and `phi4-mini` both FAIL outright** — despite each model card explicitly
    claiming function-calling support, and Ollama's own capability introspection listing `tools`,
    both narrated the tool call as literal text (`<tool_call>[{"arguments":...` /
    `[{"type":"delegate_tasks","tasks":...`) instead of emitting a real structured `tool_calls`
    response, every single attempt. Identical failure *class* already documented for
    `devstral:24b` in this same file — a model that narrates perfectly-formatted JSON instead of
    calling the tool is exactly as unusable here as one that can't format JSON at all, since
    DeepDelve is 100% tool-call-driven with no narration fallback. **Both disqualified, pulls
    removed** (`ollama rm granite3.1-dense:8b deepdelve-granite3.1-dense phi4-mini:3.8b
    deepdelve-phi4-mini`) — not worth carrying disk space for models that fail the first, cheapest
    gate. **`deepdelve-bonsai-8b` and `deepdelve-gemma4-12b` both PASS** — real structured
    `tool_calls`, correctly shaped 2-task array, valid `task_name`/`agent_id` on both; Gemma 4's
    instructions fields were notably more detailed (289-356 chars) than Bonsai's (73-102 chars),
    a first hint in Bonsai's favor of the NexusRaven-flagged semantic-thinness concern above,
    though not yet confirmed at full-benchmark scale.
  - **First real end-to-end benchmark data point, Gemma 4 12B (2026-07-14)**: ran the standing
    sales-forecasting benchmark (`eval/sales_forecasting_benchmark.md`) live end-to-end, config
    pointed at `SetneufPT/Gemma4-12B-IT-QAT_Q4_64K_16GB-GPU:latest`. Result: **`Report: NOT
    WRITTEN`** after 33 minutes (1998s) — but a clean, honest failure, not a stall or a silently-
    accepted fabrication, and this run is what actually validated the same day's 5 reliability
    fixes end-to-end: `web_search` 26/26 calls succeeded with zero failures (the timeout fix never
    even needed to fire), 27 real sources fetched, the grounding check correctly rejected 4
    straight ungrounded `findings.md` attempts, and the process exited cleanly with a clear
    forensic verdict instead of hanging. The actual failure was model-specific: 22 occurrences of
    `delegate_tasks call rejected` (sub-agents repeatedly submitting placeholder/pronoun-only/
    cross-task-dependent instructions — the existing validator's already-detailed guidance, not a
    missing-nudge gap), and a visible reasoning-loop pattern near the end ("Wait, I'll just do it.
    *(Action)*", repeated ~13 times with no actual tool call) before `context_budget_chars` cut the
    turn short. Same failure *shape* as `mistral-nemo` (README "Model choice" table): passes an
    isolated schema smoke test, ceilings on the real multi-step benchmark.
  - **Bonsai-8B benchmark result (2026-07-14): `Report: NOT WRITTEN` after 484.3s — DISQUALIFIED
    for a more severe reason than Gemma 4's.** Ran the same standing sales-forecasting benchmark,
    config pointed at `deepdelve-bonsai-8b`. Research itself worked completely fine: 22 real
    findings recorded, 15 real sources fetched, zero `web_search` failures — the failure is
    entirely isolated to the FindingsWriter/PeerReviewer writer-tier roles. Traced through the
    persisted session log turn-by-turn (not just the final verdict): `FindingsWriterFix_attempt1`
    through `attempt8` each "Finished" and PeerReviewer "found no issues" each time, yet
    `check_missing_findings` kept re-firing every single retry and `findings.md` never existed on
    disk at all by the end. Root cause confirmed by reading the actual logged tool calls:
    `FindingsWriterFix_attempt1`'s only event was a bare, empty `text` response — it **never
    called `write_workspace_file`**. `ReviewFix_attempt1` **never called `read_workspace_file`**
    either — it went straight to `"REVIEW: CLEAN"\n\nThe file findings.md appears to be a
    well-structured report...` for a file it never opened and that never existed. This repeated
    across all 8 attempts before the retry budget exhausted. Distinct from and worse than every
    other failure flavor documented in this project so far (Gemma 4's reasoning loops, `qwen3:4b`'s
    repeated-identical-write-calls below, gpt-oss's hallucinated tool names): those all at least
    attempt real tool calls; Bonsai-8B skipped tool calls entirely in a role requiring
    read-then-reason-then-write composition, while its simpler single-shot Searcher/Analyzer tool
    calls (web_search, fetch, read/grep) worked reliably throughout the same run. Also exposes a
    real structural gap worth considering separately: `_dispatch_writer_review_fix`'s clean-check
    only string-matched `"REVIEW: CLEAN"` in the response text, with no verification that a
    `read_workspace_file` call actually happened first — a model confident enough to fabricate the
    sentinel could defeat the review entirely. This is a model-reliability finding, not a code bug,
    and the disqualification stands regardless. **Bonsai-8B ruled out as a `gpt-oss:20b`
    replacement.** **Hardening fixed 2026-07-14** (`src/engine/completion.py::_dispatch_writer_review_fix`,
    commit `bfd2cd5`): cross-checks the `read_workspace_file` quota's used-count delta around the
    PeerReviewer dispatch — a CLEAN verdict with zero new reads is now treated as ISSUES FOUND,
    forcing the existing corrective Fix pass instead of being trusted. Fails open when the quota
    isn't tracked at all, so a config without it doesn't get every review falsely distrusted. New
    tests in `test_structural_checks.py` (`_clean_check_read_verification_scenario`): a fabricated
    CLEAN with zero reads forces the corrective pass, a CLEAN backed by a real read is still
    trusted.
  - **`qwen3:4b` added as a fourth candidate (2026-07-14)**, specifically sought out as "Bonsai-like
    but more context": user asked for smaller/lighter alternatives with a bigger context window
    than Bonsai's 64K. Checked and rejected first: Microsoft's official `BitNet b1.58-2B-4T` doesn't
    even run on Ollama (needs Microsoft's own separate `bitnet.cpp` runtime, incompatible with
    llama.cpp) and caps around 4-8K context regardless; PrismML's own newer "Ternary Bonsai" family
    (1.58-bit, released 2026-04-16, same company as Bonsai-8B) turned out to be a context
    *downgrade*, not an upgrade — 4096 tokens via llama.cpp/Ollama, worse than the original 1-bit
    Bonsai-8B's 64K. `qwen3:4b` (Alibaba, Apache 2.0) is the real find: 2.5GB Q4_K_M, **262144
    native context** (4x Bonsai's 64K, in the same size class as the disqualified `phi4-mini`),
    established Ollama tool-calling track record in this project already (`qwen2.5-coder`,
    `qwen3.6` both work). Derived tag `deepdelve-qwen3-4b` created (`num_ctx 16384`, same pattern).
    **Passed the real `delegate_tasks` smoke test cleanly**: real structured `tool_calls`, correctly
    shaped 2-task array, valid `task_name`/`agent_id` — and showed real semantic routing judgment
    at this early stage, not just format compliance: correctly sent the more academic/technical task
    ("hybrid statistical+DL forecasting methods") to `AcademicSearcher` and the cultural/retail task
    to `WebSearcher`, rather than routing both identically. Instructions detail (143-171 chars) sits
    between Bonsai's terse style (73-102) and Gemma 4's richer one (289-356). Not yet run through
    the full sales-forecasting benchmark — that's the same next step as Bonsai-8B above.
    - **New reliability finding (2026-07-14, Phase 4 smoke-test session)**: as `FindingsWriter` on
      a trivially simple factual query ("boiling point of water at sea level"), `qwen3:4b` called
      `write_workspace_file` **10 times in a row** with near-identical content (confirmed via the
      persisted session log: every call succeeded cleanly, "Wrote 'findings.md' to disk.", no
      error/rejection anywhere) instead of recognizing the file was already correctly written and
      stopping — only the existing `write_workspace_file` quota (10) correctly halted it, with a
      clear "you MUST summarize... and state you had to stop due to quota limits" message. Not a
      hang, not a code bug — the quota mechanism worked exactly as designed; this is a genuine
      `qwen3:4b` tool-calling non-convergence pattern, distinct in shape from Gemma4's own
      documented reasoning-loop tendency (repeated `delegate_tasks`/narration without a real tool
      call) and gpt-oss's hallucinated-tool-name pattern — same broader "small local model doesn't
      recognize task completion" failure class, third distinct flavor of it now observed across
      three different models in this project. Real cost: burned enough wall-clock across 2 separate
      live smoke-test attempts (this model, this exact query) to exceed a 15-20 min budget each
      time, purely on redundant `write_workspace_file` calls before the run ever reached its later
      stages. Not yet run through the full sales-forecasting benchmark, so unclear if this is
      systemic to `qwen3:4b`'s FindingsWriter behavior specifically or an isolated occurrence.
    - **Full sales-forecasting benchmark result (2026-07-14): inconclusive, not a verdict.** Ran
      the same standing benchmark as Bonsai-8B/Gemma 4 above, config pointed at
      `deepdelve-qwen3-4b`. The research phase completed cleanly (Colombia-specific holidays/
      paydays identified from Banco de la República, cultural cross-check against Latin American
      market studies, top-5 ML techniques evaluated) and the Planner correctly recognized
      completion and stopped delegating. The Write→Review→Fix cycle then began
      (`FindingsWriterFix_attempt1` → `ReviewFix_attempt1` flagged issues → corrective pass), but
      the whole process was killed by the smoke test's own 40-minute outer `timeout` before it
      could finish. Confirmed via `journalctl -u ollama` this was NOT a hang: right up to the kill
      moment, Ollama was actively, continuously decoding a response (steady ~59-62 tok/s, climbing
      token count, no stall) — a fairly high volume of smaller, somewhat repetitive tool calls in
      earlier sub-agent turns (consistent with the redundant-tool-call finding above) ate enough of
      the budget that the writer-tier cycle didn't have room left to converge, not that the model
      got stuck. Recorded as inconclusive rather than a pass or fail — user chose not to re-run
      with a longer cap this session; **re-running with more wall-clock budget is the next concrete
      step before drawing any verdict on `qwen3:4b`** vs. `gpt-oss:20b`. Flagged as a real data
      point for the eventual full bake-off comparison, not yet a disqualification.
    - **Conclusive re-run (2026-07-18), no outer timeout this time: `Report: NOT WRITTEN` after
      1214.2s (20.2 min), retry budget exhausted (8/8) on an unresolved `thin_coverage` verdict.
      `qwen3:4b` is DISQUALIFIED as a `gpt-oss:20b` replacement.** Real research did happen (5
      sub-agent dispatches, `brave_web_search` calls fired throughout), but only 1 real source ever
      landed (`statista.com/.../music-events/colombia`) against 4 delegated tasks. The disqualifying
      behavior isn't the thin research itself, it's the model's response to being told about it:
      every one of the 8 `thin_coverage` retries got the same canned non-response verbatim ("No
      further tool calls needed... research scope is complete... complete with explicit
      acknowledgment of gaps") instead of either re-delegating differently or actually writing the
      honest-partial report the completion-check nudge was asking for. This is the SAME
      non-convergence pattern already flagged above (the 10x redundant `write_workspace_file` case)
      showing up in a third shape: doesn't recognize a real gap needs a different action, just
      repeats a canned "I'm done" response until the retry budget hard-stops it. Two contributing
      factors, kept separate from the model verdict since they're infra, not model quality: (1) a
      real MCP bug independent of the model — `brave_web_search`'s `country` parameter enum
      (`@brave/brave-search-mcp-server`, `settings.mcp_servers`) does NOT include `CO` (confirmed
      via the literal rejection error, `tool_error_samples`: `"Invalid value for 'country' ... 'CO'
      is not in ['AL..."`), so Colombia-targeted searches using an ISO alpha-2 country filter fail
      outright — a real gap worth a small fix (drop/remap the country param, or catch and retry
      without it) independent of which model is running; (2) one `read_workspace_file`/
      `grep_workspace_file` call hit a not-found error on a source filename, the same known fuzzy-
      filename class already documented elsewhere in this file. Neither infra issue excuses the
      model's response, though: `gpt-oss:20b`'s own re-runs on this exact query have hit partial
      fetch failures too and still produced a labeled, honest, written report rather than looping on
      a fixed refusal string. **Bake-off conclusion: `gpt-oss:20b` remains the only candidate of the
      seven-plus tried so far (`qwen3.6`, `mistral-nemo`, Gemma 4 12B, Bonsai-8B,
      `granite3.1-dense`, `phi4-mini`, `qwen3:4b`) with a full, real, benchmark-scale pass.**

- **`qwen3:8b` — new candidate found and tried 2026-07-18, DISQUALIFIED, same failure class as
  `qwen3:4b`.** Surfaced by a research pass for tool-calling-capable Ollama models not yet tried
  (Qwen3's 8B dense sibling, NOT the same model as `qwen3.6` (35b-a3b, already rejected) or
  `qwen3:4b` — distinct checkpoint, in the Ollama library directly, Apache 2.0, ~5.2GB Q4_K_M).
  **Passed the `delegate_tasks` tool-call smoke test cleanly**: real structured 2-task call,
  correctly shaped `task_name`/`instructions`/`agent_id`, well-specified instructions comparable in
  detail to Gemma 4's. Derived tag `deepdelve-qwen3-8b` created (`num_ctx 16384`, same pattern).
  **Full sales-forecasting benchmark: `Report: NOT WRITTEN` after 1037.4s, retry budget exhausted
  (8/8) on `thin_coverage`**, 5 sources fetched (better than `qwen3:4b`'s 1, still not enough — only
  2/6 delegated tasks produced a real source). Same disqualifying shape as `qwen3:4b`: doesn't act
  on the completion-check's corrective nudge. Distinctive final-turn behavior worth noting: instead
  of dispatching a writer role, the model's last response NARRATED full `findings.md` and
  `final_report.md` content inline as chat prose (headers, sections, a "Stop here." sign-off) —
  neither file exists on disk (confirmed, `ls` on the run folder). Not the same mechanism as
  Bonsai-8B's writer-role tool-skip (Bonsai had real `FindingsWriter` dispatches that skipped the
  tool call; this never got there, the Planner-level conversation narrated instead of accepting the
  `thin_coverage` verdict and letting the engine dispatch a writer for an honest partial artifact).
  One non-fatal MCP schema mismatch during the run (`brave_web_search`'s `result_filter` enum
  rejected an out-of-list value), handled cleanly via the existing detailed-tool-error mechanism,
  not a contributing cause. **`qwen3:8b` DISQUALIFIED as a `gpt-oss:20b` replacement** — updates the
  bake-off conclusion above: 8 candidates tried, `gpt-oss:20b` still the only full pass. Ministral-
  8B-Instruct-2410, watt-tool-8B, and Salesforce Llama-xLAM-2-8b-fc-r were also surfaced by the same
  research pass but not pulled/tested this session (the latter two are narrow function-calling
  finetunes, a real risk for the writer role per this project's own repeated lesson — not worth GPU
  time until a general-purpose candidate looks more promising than the two Qwen3 sizes just tried).

- **`llama3.2:3b` — new lightweight candidate tried 2026-07-18, DISQUALIFIED at the tool-call
  schema stage, root-caused rather than assumed.** Surfaced by a research pass specifically for
  models LIGHTER than the two disqualified Qwen3 sizes, targeting their exact failure mode
  ("doesn't follow a corrective instruction precisely") rather than a raw-capability gap —
  Llama 3.2 3B has the best documented IFEval/BFCL combination in its weight class and a native
  Ollama tool-call template. Derived tag `deepdelve-llama32-3b` created (`num_ctx 16384`).
  **Real, structured `tool_calls` responses (correct function name, valid top-level JSON) — but
  the `tasks` array parameter's VALUE is itself a JSON-encoded STRING** (`{"tasks":
  "[{\"task_name\": ...}]"}`) instead of a real array, reproduced 3/3 times against the exact
  `delegate_tasks` schema. **Root-caused, not just observed**: recreated the identical Pydantic
  model `agent_framework` builds from `delegate_tasks(tasks: list[dict])`'s own type hint and fed
  it the same malformed value — confirmed real rejection (`Input should be a valid list
  [type=list_type]`), the same message DeepDelve's own "detailed tool-call validation errors"
  feature would show the model live. **Then simulated the full round-trip**: fed the model that
  exact real error and asked it to retry. Result: it did not correct the array, it **abandoned
  structured tool-calling entirely** — wrote a Python code snippet in chat prose, then a
  hand-typed single-quoted (invalid JSON) pseudo-call as plain text. Same "narrate instead of
  call" disqualifying class as `granite3.1-dense`/`phi4-mini`, just reached one message later
  (after a correction) instead of immediately. **DISQUALIFIED without a full benchmark run** —
  same evidentiary bar this project already applies to schema-stage rejects (devstral, hermes3,
  etc. in the README table): a model that gets WORSE after seeing the exact right correction isn't
  worth 20-40 GPU-minutes to find out how it does on the full pipeline.
  - **Documentation check (2026-07-18)**: this is a known, unresolved upstream Ollama limitation,
    not something specific to this project's integration or to `llama3.2` itself.
    `ollama/ollama#6155` ("Support Nested Parameters for Tools," filed Aug 2024, still open, no
    maintainer fix) documents the identical stringified-nested-array symptom across
    `llama3.1:8b`/`70b`, `mistral-nemo`, and `llama3-groq-tool-use` — an Ollama-side
    parser/serialization limitation with array/nested-object tool parameters generally, not a
    single model's chat-template quirk. `ollama/ollama#7860` separately documents Llama 3.2
    mangling SCALAR parameter types too (ints returned as strings), so this model has broader
    type-fidelity problems beyond just nested arrays. **No documented workaround exists anywhere
    in the issue tracker or community discussion** (checked `#6155`, `#7860`, `#10552`, `#11805`,
    `#13519`) — the only mitigation found anywhere is a LangChain-side client library that
    re-parses shallow string-encoded JSON arguments after the fact, not a model-side or
    Ollama-side fix. Because this is a shared Ollama-level limitation (not model-specific), it
    could in principle affect ANY future candidate with an array-typed `delegate_tasks` argument,
    including intermittently against models that otherwise pass — worth remembering as context if
    a future candidate shows an occasional, not-fully-reproducible schema hiccup.
  - **Deferred, not implemented, structural candidate**: a defensive "if a list-typed tool
    argument arrives as a JSON-encoded string, parse it before validation" tolerance would be a
    generically useful robustness improvement given the above (helps any model that hits this
    known Ollama-side quirk, not just `llama3.2`) — but it wouldn't have rescued `llama3.2:3b`
    itself (the disqualifying event is the collapse-into-narration on retry, which happens after
    the string would already have been coerced), and the only interception point found
    (`agent_framework.FunctionTool.invoke`'s internal Pydantic validation, built from
    `delegate_tasks`'s own type hint) would require either widening that hint in a way that also
    changes the JSON schema shown to every OTHER model including the working default, or
    monkeypatching vendored `agent_framework` internals — real blast radius against a function
    every single model/role depends on. Not attempted this session; flagged for a dedicated
    reviewed session if a future candidate's only blocker turns out to be this exact quirk.
  - **Ollama-alternative backend research (2026-07-18), conclusion: don't switch, not yet
    justified.** Since the array-stringification bug looked Ollama-level rather than model-level,
    researched whether switching the local inference backend entirely would sidestep it.
    **`ollama/ollama#6155` is actually CLOSED** (merged via PR #13508, Dec 2025) — but the merged
    fix only adds nested-object SCHEMA DEFINITION support (`api/types.go`'s `Properties` field, so
    Ollama can describe a nested schema to the model); it does NOT touch the separate
    response-parsing path that turns the model's raw tool-call text back into `arguments`, which
    is exactly where `llama3.2:3b`'s failure was reproduced live on this project's installed
    version (0.31.2, months newer than the merge). Worth a fresh, narrower upstream issue with the
    exact repro if this recurs. **`llama.cpp`'s own server is not a cleaner alternative** — its
    issue tracker has its own open, unfixed array/nested-object tool-call serialization bugs
    (`ggml-org/llama.cpp#21384` closed as not-planned, `#20198`/`#22072` open, `#20359` on
    malformed JSON for large payloads) on essentially the same grammar/parsing machinery class,
    not a structurally different guarantee. **Native (non-Docker) vLLM on ROCm is now realistic**
    on this exact card (AMD ships installable ROCm wheels as of Jan 2026, gfx1200 on the officially
    supported list for ROCm 7.2+) — a real change since the earlier vLLM investigation, which only
    ever hit the NTFS+Docker-overlayfs blocker and never tried a native pip install. vLLM's
    grammar-constrained structured-output tool-calling (token-level schema constraint, not
    post-hoc regex/PEG re-serialization) is theoretically more robust for array-of-objects
    arguments than either Ollama's or llama.cpp's approach, but no direct comparative evidence was
    found confirming it actually avoids this exact failure mode — the recommendation is
    theoretical, not proven. Model-weight storage on the NTFS mount is a non-issue for any backend
    (the NTFS/symlink constraint was specific to Python venvs and llama.cpp's/HF's own auto-download
    symlink cache; a directly-specified local GGUF/safetensors file path has no symlink
    requirement). **Conclusion: not worth migrating now** — `qwen2.5:3b-instruct` and both `qwen3`
    sizes already don't hit this bug on Ollama as currently installed, so there's no live blocker
    actually forcing a backend change; revisit only if a future candidate's sole blocker turns out
    to be this exact array-stringification bug with no working Ollama-served alternative.
  - **HANDS-ON CROSS-BACKEND EXPERIMENT DONE (2026-07-18) — CONCLUSIVE: the bug is MODEL-side, not
    Ollama-side.** The prior research pass above was necessarily theoretical (no direct test of
    the actual failure). Ran a real, controlled A/B: downloaded `llama.cpp`'s official prebuilt
    ROCm 7.2 release (`ggml-org/llama.cpp` tag `b10068`, `llama-b10068-bin-ubuntu-rocm-7.2-x64.tar.gz`
    — matches this card's `gfx1200`/ROCm 7.2+ support directly, no build needed) and ran
    `llama-server --jinja` (the model's own embedded chat template, confirmed genuine by reading
    the GGUF's `tokenizer.chat_template` metadata directly — the real official Meta Llama
    tool-calling template, not a generic fallback) against the SAME two models already tested on
    Ollama, GGUF weights pulled fresh from Hugging Face onto the NTFS mount
    (`/mnt/nuevovol/llm-models/`, confirms model-weight NTFS storage really is a non-issue for any
    backend as predicted — plain `hf_hub_download` calls, no symlink involved). **Result: `llama3.2:3b`
    reproduces the IDENTICAL array-stringification bug 3/3 times on `llama.cpp`'s own server**
    (`{"tasks": "[{...}]"}`, a JSON-encoded string, not a real array) — same failure, completely
    different serving software, different parser, different (grammar-constrained, not regex-based)
    tool-call extraction mechanism. **`qwen2.5:3b-instruct` produces a clean, correctly-typed array
    3/3 times on the exact same `llama.cpp` server** — matching its behavior on Ollama. This is a
    clean, well-controlled result: same backend, same template-authenticity check, one model fails
    consistently and the other passes consistently — the variable that predicts the bug is the
    MODEL, not the serving software. Directly answers the concern that this project might be
    missing out on real model options because of an Ollama-specific defect: it isn't one. A model
    that fails this way on Ollama will very likely fail the same way on `llama.cpp` or (by
    extension, though not directly tested) vLLM, since the failure tracks the model's own learned
    generation behavior around nested-array arguments, not a serving-layer parsing quirk.
    **Conclusion reinforced, now with direct evidence instead of just literature research: no
    backend migration would have saved `llama3.2:3b`,** and there's still no live blocker forcing
    one for any candidate that currently works. `llama.cpp` binary and both test GGUFs left on the
    NTFS mount (`/mnt/nuevovol/llm-models/`, ~4GB total) in case a similar quick cross-backend check
    is useful again later — trivial against the drive's 1.1TB free.
  - **Third backend added to the A/B, same day: native (non-Docker) vLLM-on-ROCm, not just
    theorized — actually run.** The "no direct evidence" caveat above was addressed head-on rather
    than left as a gap. Built a real native vLLM install (`vllm==0.25.1+rocm723`, the official
    AMD-published ROCm wheel from `wheels.vllm.ai`, matching this exact `gfx1200` card) in a
    throwaway venv on the root disk (per the existing venv-must-be-on-ext4 rule; NTFS still can't
    hold the Python venv's symlinks). Getting it running required manually resolving a long chain
    of missing shared libraries one `ldd` sweep at a time (no root/sudo available in this
    environment) — OpenMPI runtime libs, several ROCm math libs (`rocFFT`, `rocRAND`,
    `rocSPARSE`, `hipFFT`/`hipRAND`/`hipSPARSE`/`hipSOLVER`/`hipSPARSELt`, `RCCL`, `rocm-core`,
    `roctracer`/`libroctx`) not present anywhere on this system outside Ollama's own bundled,
    incomplete copy — each fetched directly as a `.deb` from `repo.radeon.com`'s public ROCm 7.2
    apt pool and extracted with `dpkg-deb -x` into a scratch dir (no `apt install`/root needed),
    then wired in via `LD_LIBRARY_PATH`/`ROCM_HOME`. Confirmed working: `torch.cuda.is_available()`
    True, `gcnArchName` correctly `gfx1200`. Served `unsloth/Llama-3.2-3B-Instruct` (an ungated
    mirror; the official `meta-llama` repo is gated and wasn't authenticated in this environment)
    via `vllm serve --enable-auto-tool-choice --tool-call-parser llama3_json` — vLLM's own
    purpose-built parser for the Llama 3.x tool-call format, its most favorable possible
    configuration for this exact model family. **Result: identical bug, 3/3** — `{"tasks":
    "[{...}]"}`, a JSON-encoded string, not a real array, exactly matching Ollama and `llama.cpp`.
    **Three independent backends, three structurally different tool-call extraction mechanisms
    (Ollama's Go templating, `llama.cpp`'s GBNF grammar, vLLM's own structured-output constraint
    engine with a model-family-specific parser) — same model, same failure, every time.** This is
    now definitive, not theoretical: the bug is 100% attributable to `llama3.2:3b` itself, and no
    realistic backend migration would recover it. Root-disk cleanup done immediately after (venv +
    manually-fetched ROCm libs removed, ~16GB freed, root back to 61GB free) — same disk-hygiene
    lesson as the GRPO smoke test session, a throwaway experiment venv doesn't linger.

- **`qwen2.5:3b-instruct` — new lightweight candidate tried 2026-07-18, DISQUALIFIED, different
  failure class than `llama3.2:3b`.** Second candidate from the same "lighter than the disqualified
  Qwen3 sizes" research pass. **Passed the `delegate_tasks` schema test cleanly, 7/8 across two
  batches** (one silent empty-response outlier, otherwise a real, correctly-typed array every
  time) — does NOT reproduce the array-stringification bug that disqualified `llama3.2:3b`, so
  worth the full benchmark run this time. Derived tag `deepdelve-qwen25-3b` (`num_ctx 16384`).
  **Full sales-forecasting benchmark: `Report: NOT WRITTEN` after 254.6s, retry budget exhausted
  (8/8) on `missing_findings`** — much faster to fail than either Qwen3 size (255s vs. 1000+s),
  because the failure surface is different and narrower: real research DID happen (2 sources
  fetched cleanly, `en.wikipedia.org/wiki/Heuristic_(computer_science)` and
  `.../Public_holidays_in_Colombia`, 0 search failures), and the Planner correctly stopped
  delegating and let the engine dispatch `FindingsWriter` — but `FindingsWriter` never
  successfully produced `findings.md` across all 8 attempts. Confirmed via `_run_state.json`:
  20 tool errors recorded, the overwhelming majority `"'findings.md' not found"` from
  `ReviewFix_attempt{1..8}` trying to read a file that was never written. This is the **same root
  cause already documented for Bonsai-8B** (writer-tier sub-agent "Finishes" its turn without ever
  successfully calling `write_workspace_file`) — and it's a second live confirmation that the
  2026-07-14 hardening (`_dispatch_writer_review_fix`'s read-quota-delta cross-check, commit
  `bfd2cd5`) is working exactly as designed: every `ReviewFix` attempt got a genuine, correctly-
  surfaced "file not found" error rather than a false "REVIEW: CLEAN" on a file that was never
  read. **`qwen2.5:3b-instruct` DISQUALIFIED as a `gpt-oss:20b` replacement** — updates the bake-off
  conclusion: 10 candidates tried total (counting `llama3.2:3b`), `gpt-oss:20b` still the only full
  pass. Net signal from both new lightweight candidates this session: smaller models in the 2-5B
  range are failing at TWO distinct, well-characterized points in the pipeline (schema-stage
  double-encoding for `llama3.2:3b`; writer-role tool-call omission for `qwen2.5:3b-instruct` and
  `Bonsai-8B`) rather than one common weakness — there's no single fix that would rescue this whole
  size class, which is itself useful evidence for the fine-tuning plan above: `qwen3:4b` remains
  the better fine-tuning target precisely because ITS failure (thin_coverage non-convergence) is
  the single narrowest, most well-characterized gap of any candidate tried so far.

- **`gpt-oss:20b` re-confirmed live (2026-07-14), same benchmark, same session**: `deepdelve-gpt-oss`
  produced a real, grounded `final_report.md` in 1079.1s, 15 sources fetched, 0 search failures,
  passing the NLI entailment check along the way (one `nli_unsupported` retry, corrected). First
  fresh confirmation this session that the documented default actually still passes end-to-end,
  directly alongside the same-day Bonsai-8B/`qwen3:4b`/Gemma-4 attempts on the identical query —
  the only one of the four to produce a written report at all. Content covers the heuristic-
  optimization side of the query well (PSO, GA, moving-average, rule-of-thumb) but drops the
  Colombia-specific cultural-context research the run itself actually did earlier (holidays/
  paydays from Banco de la República were researched but never made it into the final report) and
  doesn't surface the gold reference's DL-architecture families — a real report, correctly
  grounded, but likely a partial (not top) score against the full manual rubric if formally scored.
  Not manually scored this session (would need a careful pass against
  `eval/reference/sales_forecasting_deepseek.md`).
  - **Formally scored 2026-07-18, per `eval/sales_forecasting_benchmark.md`'s rubric: 6/10 ("usable
    with manual verification").** Tier 1 structural integrity 2/2 (`findings.md`+`final_report.md`
    both exist, 18/18 fetched URLs clean, none flagged `stub` among the ones actually cited, run
    converged clean by completion-check attempt 3). Tier 2 architecture coverage vs. reference
    **0/2**: the report covers 3 optimization/feature-search heuristics (GA time-lag selection,
    TS_Adam, Randomized Uphill Climbing) but none of the reference's 4 forecasting-architecture
    families (TFT, N-HiTS, DQN, EventCast/multimodal) — a real, grounded, but structurally
    different literature set, not a fabrication. Tier 3 heuristic-optimization coverage 2/2 (GA
    applied to LSTM hyperparameter tuning, matching the query's actual framing). Tier 4 Colombia
    cultural context **0/2**, and this is the more interesting result: `_run_state.json` shows
    `timeanddate.com/holidays/colombia/2024` WAS fetched cleanly (not a stub) alongside one stubbed
    ADP payroll-calendar fetch, yet neither `findings.md` nor `final_report.md` mentions Colombia
    even once — a second, independent live confirmation of the shared-quota-pool starvation bug
    logged above (a different run, different model context than the original find), not a new bug.
    Tier 5 quantitative grounding 2/2 (every reported figure traces to an `*Evidence:*` line from a
    real fetched source). Net: the defense layers correctly prevented fabrication on a topically
    disjoint literature set; the score ceiling here is entirely the quota-starvation bug, not a
    grounding failure.

- **Heterogeneous role tiering (option 2 above) — implementation and A/B test detail.** A UNIFORM
  small-model dispatcher was tried and rejected 2026-07-11 (nemo scored 2/10 across every role);
  this instead tiers by role, keeping `gpt-oss:20b` for Planner/Builder/FindingsWriter/PeerReviewer
  (the roles needing multi-step self-correction) and routing WebSearcher/AcademicSearcher/
  DocumentAnalyzer/DataAnalyzer to a new optional `settings.specialist_model`.
  - **Implementation**: `_build_client(model_override=None)` (`src/engine/orchestrator.py`) now
    takes an optional model override; `create_local_agent` builds a second client only when
    `specialist_model` is set and differs from `api.openai_model` (a no-op object-reuse
    otherwise); `_run_single_task` picks the specialist client when `agent_id` is in the new
    `_SPECIALIST_MODEL_ROLES` set (the deliberate complement of the existing
    `_NON_RESEARCH_DISPATCH_ROLES`). TUI/CLI status lines updated in parity to show
    `<model> (+specialist: <model>)` when configured. `config_template.yaml` documents the key.
    No `SubAgentConfig`/Pydantic changes needed — the routing decision lives entirely at the one
    dispatch point. `test_structural_checks.py` passes unchanged.
  - **Design flaw, foreseeable before any A/B test ran — added retrospectively 2026-07-21, per
    user pushback that this should have been caught at design time, not after measuring it.** The
    2026-07-18 "agreed order" (structural fix → tiering → fine-tuning → stay on gpt-oss) approved
    trying tiering as a strategy step, not this specific pairing's reasoning — that reasoning was
    never spelled out before implementation. The VRAM probe below was run BEFORE writing code and
    already showed the disqualifying fact: this card cannot hold `gpt-oss:20b` and any second
    model resident at once. Given that, pairing a "heavy" coordinator model that must stay loaded
    for Planner/Builder/FindingsWriter/PeerReviewer with a "light" specialist for the remaining
    roles was never actually lighter in aggregate VRAM terms — `gpt-oss:20b` doesn't get unloaded
    between specialist calls, so the specialist tier only adds a second model competing for the
    same fixed budget, guaranteeing constant eviction/reload thrashing regardless of which small
    model was chosen. The 4.2x slowdown below is the confirming measurement of a result the probe's
    own numbers already implied — it should have been treated as a go/no-go gate before running the
    A/B, not just a footnote alongside it. **Standing implication for any future specialist-model
    retry**: before implementing, check whether the specialist's footprint fits ALONGSIDE the
    coordinator model's resident footprint (not just its own footprint against the total VRAM
    budget) — if the coordinator model can't be unloaded between specialist dispatches, tiering
    cannot reduce peak VRAM pressure, only add to it.
  - **VRAM probe done BEFORE writing any code**: confirmed live via `ollama ps`/`rocm-smi` that
    this card does NOT keep two different Ollama models resident simultaneously — `gpt-oss:20b`
    (12GB) and `qwen3:4b` (5.1GB loaded, inflated by KV cache) together exceed the ~15.9GiB budget,
    so Ollama evicts the previous model on every switch. Measured reload cost: ~5-23s per switch.
  - **Real timed A/B run (2026-07-18)**, same sales-forecasting benchmark, `gpt-oss:20b` +
    `specialist_model: deepdelve-qwen3-4b`, confirmed via `ollama ps` mid-run that both roles
    really did route to their intended model. **Result: 4513.1s (75.2 min) vs. the pure
    `gpt-oss:20b` baseline's 1079.1s — 4.2x SLOWER**, not faster, driven by the reload tax
    compounding across an unusually retry-heavy run (`thin_coverage` → `missing_findings` →
    `missing_artifact` before converging) plus qwen3:4b needing repeated redispatches
    (`background_heuristics#2/#3/#4`) to produce anything usable for its assigned angle.
    **Worse, the run converged CLEANLY (no fabrication, real grounding, `Report:` written) but the
    content itself silently dropped the query's entire main topic**: `findings.md` and
    `final_report.md` are 100% about Colombian holidays/payroll, with ZERO mention of heuristic
    algorithms or deep learning, despite `_run_state.json` confirming the specialist model DID
    eventually fetch two genuinely relevant real sources for that angle
    (`sciencedirect.com/.../S1546221825008872`, `forecastio.ai/blog/time-series-forecasting`) that
    even show up in `RunState.data["findings"]`. The content existed; the writer-tier synthesis
    (on `gpt-oss:20b`, the coordinator model, not the specialist) dropped it anyway. This is a NEW
    instance of the pattern already tracked elsewhere in this file (real fetched content silently
    absent from final synthesis) — previously always tied to an observable quota-exhaustion
    trigger, but no quota exhaustion is visible in this run's own attempt log, suggesting the
    underlying issue may be a broader writer-tier prioritization/attention problem, not solely the
    already-scoped quota-fairness bug. Not investigated further this session — flagged as a new,
    distinct candidate worth its own root-cause pass.
  - **Conclusion: tiering the code is correct and reusable, but THIS pairing
    (`gpt-oss:20b`+`qwen3:4b`) on THIS hardware is a net loss** — slower AND lower quality than
    just running `gpt-oss:20b` alone. `specialist_model` left unset in the live config (defaulting
    to today's single-model behavior). Worth retrying only if: a specialist model with a smaller
    combined VRAM footprint (fits alongside `gpt-oss:20b` without eviction) is found, or the
    newly-surfaced writer-tier content-dropping bug gets root-caused and fixed first — as scoped,
    tiering does not currently deliver the hoped-for benefit.

## Candidates from the 2026-07-12 reference-repo review (see README References)

- **Engine-driven iterative deepening** (from `dzhng/deep-research`): a STRUCTURAL refine loop —
  each round's findings + the Searchers' FOLLOW-UP DIRECTIONS get composed by the ENGINE into the
  next round's Planner input, with geometric narrowing (their `newBreadth = ceil(breadth/2)`,
  depth counter). DeepDelve currently trusts the Planner model to loop, and local models
  demonstrably under-loop (run 15: 1 niche of 4-6). Could integrate with `--depth`.
- **Tongyi-DeepResearch-30B-A3B as a benchmark candidate** (from `Alibaba-NLP/DeepResearch`):
  30B MoE / 3.3B active — same size class as deepdelve-qwen3.6, but trained specifically for
  long-horizon research. **Architecture, read directly from the primary paper (arXiv:2510.24701)
  during the comparative survey, `RESEARCH.md` §7, 2026-07-20**: this is a SINGLE fine-tuned model
  operating via ReAct or an "IterResearch"-based Heavy test-time-scaling mode — not a multi-agent
  system in DeepDelve's sense at all (no Planner delegating to typed specialists with independent
  context). No published runtime grounding/citation-verification layer comparable to DeepDelve's
  own — reliability, to the extent it's addressed, comes from the training pipeline (continual
  agentic pre-training + on-policy GRPO) rather than a deployment-time safeguard. Backed by an
  18-paper research program (WebWalker, WebDancer, WebSailor, WebShaper, WebResearcher, and more) —
  a frontier-lab-scale effort DeepDelve isn't attempting to match; if adopted, it would be solving
  DeepDelve's reliability gap by swapping in a much larger purpose-trained model rather than by
  DeepDelve's own verification-layer approach, and would still need DeepDelve's own grounding checks
  layered on top if citation-level provenance matters for the use case (Tongyi's benchmarks measure
  answer-correctness, not per-citation provenance the way DeepDelve's own checks do). **Chat-template/tool-call compatibility check done, 2026-07-12 — the
  flagged risk is resolved**: `deepdelve-tongyi` (built pre-outage from
  `hf.co/mradermacher/Tongyi-DeepResearch-30B-A3B-GGUF:Q4_K_M`, 18.6GB, `num_ctx 16384`) reports
  Ollama capabilities `['completion', 'tools', 'thinking']` — the community GGUF's chat template
  parses the model's native `<tool_call>` XML into real structured `tool_calls` (verified live via
  a direct `/api/chat` call with a tool schema: returned a proper `tool_calls` array, not raw XML
  text). A real `--depth quick` trial run (`compare_the_vector_search_capabilities_of_elastics_...`)
  confirmed `delegate_tasks` actually gets invoked with 2 real specialist tasks, 2 real fetches,
  and `write_todos` populated correctly — passing the exact bar `devstral:24b` failed (README
  "Model choice": zero real `delegate_tasks` calls, narrated JSON instead). The run didn't finish
  within a 5-minute smoke-test window — Tongyi's `<think>` traces are verbose (one single-tool-call
  test round-tripped a 1000+ token thinking block for "15 + 27") — so a real benchmark round needs
  a longer time budget than the other local candidates, not a template fix. Config for testing:
  `~/.deepdelve/config-tongyi.yaml` (not in git, mirrors the live config with `openai_model:
  deepdelve-tongyi`).
  - **Two real benchmark attempts, both inconclusive on quality — the model is not currently
    usable at either quant tried, for two different reasons.** Q4_K_M: killed at 1h6min (the
    `max_run_minutes` bug this exposed and fixed, see "Repo governance + CI" entry above) — GPU
    was genuinely computing the whole time, real progress happened (delegate_tasks invoked, 2
    fetches), just far too slow to be practical. Then tried `deepdelve-tongyi-iq3`
    (`hf.co/mradermacher/Tongyi-DeepResearch-30B-A3B-i1-GGUF:IQ3_M`, 13.5GB — passed the same
    isolated tool-call smoke test, and was noticeably faster/less verbose on that trivial test:
    2.7s vs. 5.9s eval time for "15+27") expecting it to be the practical answer. **It was worse
    on the real workload**: 37+ minutes against the actual Planner system prompt with ZERO
    progress — no `write_todos`, no `delegate_tasks`, no run folder content at all (`_run_state.json`
    stayed at its initialized empty state the whole time), unlike Q4_K_M which at least made real
    tool calls in a comparable window. Killed manually. The isolated single-tool-call smoke test
    (README's `curl .../api/chat` snippet) evidently does NOT predict real-workload viability at
    this quant level — a real trial against the actual multi-thousand-token Planner prompt is the
    only test that means anything, and neither quant has passed one yet. Not recommended for
    further local benchmarking without a materially different quant or a context/prompt-length
    investigation into why the full system prompt specifically breaks it.
## Stretch

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
       One 2026 lead worth reading (not downloading): "Multi-Turn Reinforcement Learning for
       Tool-Calling Agents with Iterative Reward Calibration" (arXiv:2604.02869, per-turn credit
       assignment for tool calls, closer to this problem than math/code RLVR).
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
## Evaluated and rejected

- Large/small model dispatcher: rejected 2026-07-11 — benchmark showed small models fail sub-agent reasoning (nemo 2/10); revisit only if a small model scores ≥5 on the Colombia rubric solo.
- Knowledge cache (any backend): rejected — poisoned benchmarks/grounding; deleted in commit 929b987; do not reintroduce.
- **Bibliographic-API citation verification** (Semantic Scholar/OpenAlex/Crossref/arXiv, from
  `imbad0202/academic-research-skills`): rejected as a bundled default for the academic output
  mode — a genuinely stronger check than DeepDelve's own fetch-based grounding for *published*
  academic sources, but adds an external API dependency (rate limits, another failure mode to
  handle) for a benefit that only applies to formal papers, not the market-research/general-web
  sources most DeepDelve runs actually cite. Revisit as an opt-in flag specifically for
  `--style academic` if that mode's own fetch-based grounding proves insufficient in practice.
- **`SkyworkAI/DeepResearchAgent`** (reviewed 2026-07-12): a general self-evolution agent runtime
  (RSPL/SEPL protocol layers, RL-based prompt/solution optimizers, versioned tracing) with example
  agents for trading/ESG/mobile — not a deep-research-specialized project despite the name.
  Rejected: same reasoning as the existing "no DI framework, no plugin system" stance above: its
  tracing/versioning goal is already served by `_run_state.json`, and its optimizer/self-evolution
  loop is out of scope for a project explicitly avoiding RL infrastructure outside the "Stretch"
  item above.
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
  was added to "Planned" above.
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
