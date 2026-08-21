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

Genuinely open items only. Closed items that were still narrating their full resolution history
here got moved out, most already live in the wiki's [Completed](https://github.com/g0elles/deepdelve/wiki/Completed)
or [Changelog](https://github.com/g0elles/deepdelve/wiki/Changelog); anything not yet migrated is
tracked in `session_status/CURRENT.md` until the next wiki pass picks it up.

- **`create_local_agent`'s 963-line nested-closure god-function, scoped 2026-07-29, not attempted.**
  `_run_single_task` (~490 lines) and `delegate_tasks` (~280 lines) are deeply nested closures
  inside `create_local_agent`, capturing dozens of enclosing locals by reference rather than as
  parameters. `test_structural_checks.py` never imports either closure directly, only small pure
  fragments already pulled out of it, the actual per-task dispatch/quota-ring-fencing/specialist-
  tiering behavior has zero direct test coverage. Recommended approach when picked up: write
  characterization tests first, pinning current behavior end to end since none exist, then
  decompose, attempting decomposition without a safety net on a function this size is exactly the
  kind of change that creates a new incident rather than closing one.

- **`completion.py`'s mixed responsibilities, scoped 2026-07-29, not attempted.** The file's own
  header describes it as a clean list of pure `Ctx -> Optional[Verdict]` check functions, but it
  also contains findings-authoring/evidence-assembly logic, disk-touching quarantine/restore/salvage
  helpers that reach into a private name in `tools.fs` at four call sites, async sub-agent dispatch
  orchestration, the task-verification ledger mutator, and the completion-check state machine tying
  all of it together, none individually bug-prone, but "add a new completion check" now requires
  understanding all of the above living in one namespace. Blast radius warning for whenever this is
  picked up: `test_structural_checks.py` imports 40+ private names directly across five modules; any
  split must update that file's imports in lockstep.

- **Config accessor migration, partially done.** 85 scattered `config.cfg.get("settings", {}).get(...)`
  call sites across the codebase, no single accessor, no consistent default handling, confirmed a
  real bug source (not just duplication) via `required_artifact`, which had 3 different literal
  fallback values scattered across 4 call sites. Fixed for that one setting
  (`config.get_required_artifact()`); the other ~81 sites are unaddressed. Recommend incremental
  migration, add an accessor the next time any of these call sites needs touching for an unrelated
  reason, rather than a big-bang rewrite most of them don't demonstrably need.

- **`run_cli`/`BasicTuiAgent` full run-lifecycle unification, re-scoped 2026-07-29, still open.** A
  dedicated audit found the two entry points aren't just stylistic duplicates in places that matter:
  the TUI's approval handling actually executes tools client-side and constructs full message pairs,
  the CLI's doesn't; the TUI has no context-budget/wall-clock-deadline concept by design, since a
  human can just stop it. The genuinely safe subset (the resume-merge allowlist, the
  `required_artifact` lookup, a missing `QuotaAbortException` handler, a missing crash-time
  `run_state.save()`) is already fixed and merged. Still open: unifying the stream-consumption loop
  and the approval-handling block behind explicit strategy objects, a real design decision (what
  varies between TUI and CLI), not a mechanical extraction. Recommended approach: design the
  strategy interface first, then extract the loop body to take it as a parameter, not "extract the
  whole function and see what breaks."

- **`check_findings_underuses_evidence` evidence-dropping, a monitoring point, not an active fix
  target.** One clean, non-overloaded 2-task run had FindingsWriter silently drop an entire covered
  topic from `findings.md`, not thin, not truncated, gone outright, with no infra confound found.
  Every other writer-stage failure investigated the same session traced to a specific, now-fixed
  mechanism; this is the one exception. Deliberately not fixed via a blind prompt rewrite per the
  Model Evaluation Standard's own "needs more than one occurrence" bar. Reopen trigger: a second
  clean, unconfounded run showing the same whole-topic-dropping shape.

- **A finer-grained, in-turn repetition guard, an unbuilt idea.** The completion-check-level
  full-rebuild escalation already shipped (see Completed), but a narrower, one-level-lower guard
  (catching e.g. a single dispatch calling `fetch_url_to_workspace` on the same URL 6 times within
  its own turn, the exact shape of a couple of real disqualifying incidents) is still a real,
  distinct, unbuilt idea if ever worth pursuing. Would be new, DeepDelve-original work.

- **RAG-augmented small model, raised 2026-07-20, not yet scoped.** The project's own prior "RAG
  failure" turned out to be a benchmark-isolation bug in a deleted exact-string-match cache, not a
  real RAG failure, see the wiki's [Architecture Synthesis](https://github.com/g0elles/deepdelve/wiki/Literature-Review-Architecture-Synthesis)
  page for the full literature review. Real RAG (embeddings/chunking/vector retrieval) is
  architecturally different from what failed before, so the old rejection doesn't automatically
  block it, but any persistent cross-run cache, RAG or not, must be explicitly isolated per model
  during comparative benchmarking or the same contamination bug recurs regardless of the retrieval
  technique underneath it. That's the one non-negotiable constraint from this project's own history.

- **TUI QoE improvements, researched 2026-07-14, not yet scoped.** A framework capability survey
  (Textual's own source, not assumed from memory) found several likely-already-working features
  needing live confirmation (click-drag select + copy) and several unused framework capabilities not
  yet scoped into concrete work (command palette, widget maximize, theming, inline autocomplete,
  `Tree`/`DataTable`/`TabbedContent`/`SelectionList` for existing ad hoc UI). The two smallest,
  most directly requested items (message copy button, right-click paste) have since shipped, see
  Completed. Next session should scope a concrete subset of what's left, not the whole survey at
  once.

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
