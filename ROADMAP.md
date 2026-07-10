# DeepDelve Roadmap

Status as of 2026-07-10. Each item states what "done" concretely means (acceptance criteria), not just
a description of the work — so this doc can be checked against reality later, not just re-read.

## Done

- [x] **3-tier domain-specialized architecture built and wired**: `Planner -> {WebSearcher,
  AcademicSearcher} -> {DocumentAnalyzer, DataAnalyzer}`, all tool sets and delegation routing confirmed
  by directly inspecting the built agent graph (`src/app.py` import test — see commit history).
  *Acceptance: `python -c "import app; ..."` prints the correct tool/sub-agent tree for every tier.* ✅ verified.
- [x] **Structural reliability fixes over the prototype**: per-attempt quota top-up, artifact
  quarantine-before-nudge, structured run-state (`_run_state.json`), a real grounding check.
  *Acceptance: each fix has a corresponding entry in README "Design rationale" with the specific bug
  it fixes and the commit that fixed it.* ✅ done (7 numbered entries in README as of this commit).
- [x] **Model choice re-tested against the actual new schema**, not inherited from the old project.
  *Acceptance: a reproducible curl-based test script + results table for all 3 locally-available models.*
  ✅ done — `mistral-nemo:12b`/`devstral:24b` 3/3, `hermes3:8b` 0/3 (README "Model choice").
- [x] **Grounding-check soft-pass bug found and fixed**: a never-fetched-but-live-resolving URL no
  longer passes as grounded.
  *Acceptance: a live test citing a real, unfetched, resolvable URL (e.g. Wikipedia) is flagged
  `not_grounded`, not silently accepted.* ✅ verified live (devstral "boiling point" run, commit `bf38c50`).
- [x] **`delegate_tasks` schema validation**: malformed task dicts are rejected with an actionable error
  instead of silently degrading to `task_name="Unknown_Task"` / empty instructions.
  *Acceptance: a live test where the model emits a malformed call shows (a) a clear rejection message,
  and (b) the model self-correcting on the next call.* ✅ verified live (World Cup query test).
- [x] **`children_token` `UnboundLocalError` crash fixed** (latent bug inherited from the reference
  project). *Acceptance: a live test with an invalid `agent_id` returns the clean "sub-agent does not
  exist" error instead of crashing.* ✅ verified live.
- [x] **Private GitHub repo live**: https://github.com/g0elles/deepdelve, all work pushed to `main`.
- [x] **Extensive live test battery across genuinely diverse, complex topics** (not repeats of the same
  query): a factual lookup, two different real academic papers (DelveAgent and the LLM-based Deep Search
  Agents survey — the latter's authors were confidently fabricated by the model while, separately, a real
  related-paper citation it produced turned out correct, illustrating exactly why the grounding check
  can't distinguish good from bad content without a real fetch), three current-event queries spanning
  sports (2026 World Cup) and software releases (Rust), and a technical comparison (Elasticsearch vs.
  pgvector) run twice for consistency. 8 live runs total, on top of the model-comparison trials. Found and
  fixed 3 additional real bugs beyond the initial round (the grounding soft-pass, the malformed-schema
  silent-degrade, the no-dispatch pattern, and the narrated-report salvage) — see entries throughout this
  doc and README "Design rationale" for each. ✅ 2026-07-10.

## In progress / open (with acceptance criteria)

- [x] **Root-cause the fetch-skipping behavior — fixed structurally, not by nudging.** Researched how
  three reference sources handle exactly this ("don't let the model stop at a summary/snippet") before
  building anything: `nashsu/llm_wiki`'s search always extracts full content, never truncates or leaves a
  snippet-only path; `CYC2002tommy/Deep-Research-Agent`'s SKILL.md has an explicit, all-caps
  **"FULL-TEXT READING IS MANDATORY — NO ABSTRACT-ONLY SHORTCUTS"** rule (same principle, applied to
  papers instead of web pages) plus a content-level claim-grounding check. Independently arriving at the
  same conclusion across 3 sources (this repo included) is strong signal the fix has to be structural:
  **`web_search` now auto-fetches the full content of its top result itself** (`settings.web_search.auto_fetch_top`,
  default 1) — there is no snippet-only path left for a model to stop at. Refactored `tools/web.py` so
  `fetch_url_to_workspace` and the new auto-fetch share one fetch/save implementation instead of drifting.
  *Acceptance met, verified live*: re-ran the exact Rust-version query that previously fabricated a wrong
  version from memory with zero real fetches — this time `web_search` auto-fetched a real page and the
  Colombia/World-Cup query's Searcher went on to call `fetch_url_to_workspace` **5 times** in one run
  (first real, repeated fetching observed in the entire test campaign, including a genuinely correct ESPN
  squad page). ✅ 2026-07-10.
- [x] **Bonus bug found and fixed while verifying the above**: auto-fetching Rust's official
  `blog.rust-lang.org/releases/latest/` returned a client-side ("click here to be redirected") landing
  page — not a real HTTP redirect, so `httpx`'s `follow_redirects=True` didn't catch it — with the actual
  answer (`Rust-1.97.0`) sitting unused in the stub's link target. Added `_looks_like_redirect_stub()`:
  detects a short, single-link "redirect" page and follows it one hop, recording *both* the original and
  resolved URL as fetched (a model may reasonably cite either). *Acceptance met*: isolated test confirmed
  the real announcement content (not the stub) is now retrieved; both URLs appear in `fetched_urls`. ✅ 2026-07-10.
- [x] **Content-level claim grounding implemented**, per `CYC2002tommy`'s SKILL.md claim-grounding phase
  ("cross-reference the specific claims made in the draft against the raw data collected"). The existing
  grounding check only verified a cited URL was fetched, not that its content actually supports the claim
  attached to it. Added a second, deeper layer (`engine/tui.py::_claim_grounding_problem`): for a citation
  that passed the fetch-presence check, extract salient terms (numbers/versions/proper nouns) from both
  the report's prose and the fetched source, and flag `claim_unsupported` if they share zero overlap.
  Deliberately a cheap deterministic check, not another LLM call — this local model class has already
  proven unreliable as a judge of its own output elsewhere in this project.
  *Acceptance met, with a real bug found and fixed during verification*: a synthetic test (fetched source
  about cooking pasta cited to support a Rust-version claim) initially passed uncaught — the first
  implementation skipped the check entirely whenever the source had zero extractable terms, which
  protected against penalizing thin pages but also let substantial-but-unrelated ones straight through.
  Fixed by keying the skip on content *length* (a real too-thin-to-judge case) instead of term count, so
  a substantial source with no matching terms is now correctly flagged. Re-verified with 3 synthetic
  scenarios (unrelated source → flagged, genuinely supporting source → passes, thin/stub source → passes)
  and 3 live runs confirming no regressions. **Live-trigger status, stated honestly**: it hasn't yet fired
  in real traffic — every live run so far that had a citation problem was already caught earlier by the
  URL-presence check (missing citations entirely, or citing a URL that was never fetched), meaning that
  cheaper check is still the dominant real-world failure mode. This layer remains verified-correct via
  direct testing but is a defense-in-depth addition, not yet proven to catch something the first layer
  wouldn't have. ✅ 2026-07-10.
- [ ] **New, smaller follow-up: blind top-1 auto-fetch sometimes fetches an irrelevant page.** Live-observed
  on the Colombia query: a sub-task named "Eliminating Team Identification" searched loosely and
  auto-fetched a Microsoft Teams/Office 365 documentation page as its (irrelevant) top DDGS result — a
  real trade-off of fetching automatically rather than the model judging relevance first. Not harmful (just
  a wasted quota unit; the other sub-task's auto-fetch found the correct ESPN squad page), but worth
  tracking. *Acceptance*: either tune search query construction in the WebSearcher/AcademicSearcher prompts
  to reduce this, or accept it as a bounded, low-cost trade-off (each occurrence costs exactly one
  `fetch_url_to_workspace` quota unit, already capped).
- [ ] **New observation, not yet investigated**: the same Colombia run also produced a framework-level
  message — `"Maximum consecutive function call errors reached (3). Stopping further function calls for
  this request."` — after the model emitted a couple of invalid `agent_id` values (`agent-34`, `agent-51`)
  in quick succession. This looks like an `agent_framework` library circuit-breaker, not code in this
  repo, and the run recovered fine afterward (delegation succeeded on the next attempt), but it's an
  unfamiliar failure surface worth understanding before it's dismissed as harmless.
  *Acceptance*: reproduce deliberately (e.g. force 3+ consecutive bad `agent_id` values) and confirm
  whether this circuit-breaker can strand a run with no recovery path, or document why it can't.
- [x] **Re-evaluate `devstral:24b`** — done, 3 independent live trials post-fix, result: 1/3 fully
  functional, not recommended for the Planner role (README "Model choice"). ✅ 2026-07-10.
- [x] **Four externally-suggested models tested** (`hermes3:8b`, `qwen2.5-coder:14b-instruct`,
  `llama3-groq-tool-use:8b`, `mistral:7b-instruct-v0.3-q5_K_M`) against both the isolated schema test and
  a live Planner-role trial. *Acceptance met*: all 4 tested, none beat `mistral-nemo:12b`; two failed the
  isolated test outright, the other two passed it but failed the live role test — see README "Model choice".
  ✅ 2026-07-10.
- [x] **Run the comparative and academic eval items** (`eval/dataset.jsonl` items 2 and 3).
  *Acceptance met*: `eval/results.jsonl` has real scores for both — comparative scored **0.000**
  (`not_delegated` on all 4 attempts — the Planner wrote a 3-slot plan but never actually called
  `delegate_tasks` even once), academic scored **0.500** (the `AcademicSearcher` found two genuinely
  correct, real URLs — the paper's actual HuggingFace page and its actual GitHub repo, both verified
  against the paper's own text — via search snippets, but never fetched them; the grounding check
  correctly quarantined the resulting report, and the Planner failed to recover a valid final report
  afterward, so a partially-correct answer only survived in stdout narration the LLM judge could see, not
  in an actual deliverable file). ✅ 2026-07-10, but see the two new items directly below — this run
  surfaced two *new* failure modes, not just confirmed the existing "doesn't fetch" one.
- [x] **"Wrote a plan, never dispatched it" failure mode — fixed.** Root cause: the Planner treated
  rewriting `_todos.md` as if it satisfied "take an action," and on the eval run had confabulated fake
  delegation narration ("After delegating the tasks to a human Searcher, here's what I've found:") despite
  `delegate_tasks` never being called. Fixed with an escalating nudge that fires once `write_todos` has
  been called ≥2 times with zero `delegate_tasks` calls, explicitly naming and forbidding the exact
  observed pattern (same principle as the earlier missing_artifact/re-delegation fix).
  *Acceptance met*: re-ran the exact query that previously never delegated across all 4 attempts — now
  calls `delegate_tasks` by the 3rd attempt and returns real results. ✅ 2026-07-10.
- [x] **Recovery-after-quarantine / narrated-but-never-written gap — fixed with a structural fallback.**
  A second live run of the comparative query hit this exact pattern again, even with `delegate_tasks`
  working correctly and real results in hand: the model narrated a complete, well-formatted report as
  chat text but never called `write_workspace_file`, across the entire budget — a pattern that also
  recurred in the *old* project despite multiple rounds of prompt-only fixes there. Rather than tune
  wording further, added `_salvage_narrated_report()`: when the budget is exhausted on a `missing_artifact`
  problem, the engine auto-persists the model's own last substantial narrated response into the artifact,
  clearly marked as unverified salvage content that bypassed the grounding check entirely.
  *Acceptance met*: re-ran the query that previously ended with "no report was produced this run" — now
  produces a real (clearly disclaimed) `final_report.md` instead of nothing. ✅ 2026-07-10. This also
  covers the original academic-eval-item scenario (two correct URLs discarded) going forward, though not
  independently re-verified on that exact query yet.
- [ ] **`findings` array in `_run_state.json` is currently always empty** — `RunState.add_finding()` exists
  but nothing calls it; the structured run-state currently only tracks plan/fetched-URLs/attempts, not a
  parsed record of each finding. Not blocking anything today (the grounding check doesn't need it), but
  it's a half-built piece of the "Workflow as Knowledge" idea from the original plan doc.
  *Acceptance*: either wire it up (parse each `delegate_tasks` result into a finding entry) with a live
  test showing non-empty `findings[]`, or remove the unused method and note why it wasn't needed.

## Planned (not started)

- [ ] **Constrain the Planner's replanning action space**, inspired by `nashsu/llm_wiki`'s review-queue
  design ("predefined action types: Create Page, Deep Research, Skip — constrained to prevent LLM
  hallucination of arbitrary actions"). Currently the adaptive planning loop's replanning step is
  free-text reasoning inside `think_tool`, not a constrained choice. The bounded-slot planning rule
  already partially does this at the *initial* planning stage; this would extend the same discipline to
  *replanning* specifically.
  *Acceptance*: the Planner's replan step is expressed as a call to a small enum-like tool/parameter
  (e.g. `{action: "add_slot"|"verify_conflict"|"accept_and_write", ...}`) instead of free text, with a
  live test showing it actually constrains behavior (i.e. an out-of-enum action is rejected the same way
  the `delegate_tasks` schema fix rejects malformed shapes).
- [ ] **Document/confirm multi-format source support** (PDF/DOCX/PPTX/XLSX), inspired by `llm_wiki`'s
  explicit format table. `markitdown[all]` (already a dependency) likely already covers this via
  `tools/web.py`'s `fetch_url_to_workspace`, but it's never been exercised or confirmed with a live fetch
  of a non-PDF, non-HTML document.
  *Acceptance*: a live test fetching a real `.docx` or `.xlsx` URL produces usable Markdown in the
  workspace, confirmed by inspecting the saved file; README updated to state this explicitly instead of
  leaving it implicit.
- [ ] **TUI smoke test** — every live test so far has used `--prompt`/headless mode. The interactive
  Textual TUI path (`run_agent`, not `run_cli`) shares the fixed completion-check logic via
  `run_completion_check()`, but has not itself been launched and driven interactively even once.
  *Acceptance*: `python src/app.py` launched, a query typed interactively, and the full flow (including
  the `ApprovalWidget`/nudge display) observed to render correctly, not just inferred from shared code.

## Stretch (explicitly out of near-term scope, tracked so it isn't lost)

- [ ] **RL fine-tuning for tool-call reliability** (GRPO/PPO on the actual Planner/Searcher schema,
  rule-based outcome rewards) — the field's actual primary fix for this exact class of problem per the
  roadmap survey (arXiv:2506.18096), targeting the fetch-skipping root cause directly instead of
  catching it after the fact. Needs real training infrastructure; not started.
  *Acceptance (if picked up)*: a fine-tuned checkpoint shows measurably higher real-fetch rate on the
  same novel-query test methodology used above (3+ independent trials, current-event queries), compared
  to the base model.
