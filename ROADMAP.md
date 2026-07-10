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

- [ ] **Root-cause the fetch-skipping behavior**, not just catch it — now the single highest-confidence
  open item, reinforced by an extensive test battery (2026-07-10): 8 live end-to-end runs across factual,
  comparative, academic, and current-event queries, on 5 different models. In every single run where a
  citation was checked, `fetched_url_count` stayed at 0 — the Searcher never once called
  `fetch_url_to_workspace`, regardless of model. One run (Rust version query) makes the causal chain
  directly observable: real, live `web_search` results came back, but the snippet text didn't contain the
  specific fact needed (only the actual release-notes page would) — since no fetch happened, the Planner
  fell back to confidently fabricating a wrong version number and entirely invented release notes instead
  of admitting the snippets were insufficient. The grounding check catches this every time and now
  discloses it clearly (including salvaging narrated-but-unwritten reports, see below) — but nothing
  prevents the underlying behavior.
  *Acceptance*: either (a) a structural fix — e.g. the engine refuses to let a Searcher return findings
  to the Planner unless at least one `fetch_url_to_workspace` call happened for non-trivial queries — with
  a live test showing 3/3 independent trials on a novel query actually fetching before answering, or
  (b) a documented decision that this is accepted as a disclosed-not-prevented limitation for this model
  class, with the RL fine-tuning path (see Stretch below) as the actual fix.
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
