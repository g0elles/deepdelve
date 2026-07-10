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

## In progress / open (with acceptance criteria)

- [ ] **Root-cause the fetch-skipping behavior**, not just catch it. Currently: the grounding check
  reliably *catches* a Searcher that never called `fetch_url_to_workspace` and answered from snippets or
  its own training knowledge, and now discloses this clearly instead of hiding it — but nothing prevents
  it from happening in the first place, across every model tested so far.
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
- [ ] **New failure mode: "wrote a plan, never dispatched it."** The comparative eval item's Planner wrote
  a correct 3-slot `_todos.md` plan and then never called `delegate_tasks` at all across the full retry
  budget — distinct from the previously-documented pattern (delegates, but doesn't fetch/ground). Not yet
  root-caused.
  *Acceptance*: reproduce on 3 independent trials of the same or a similar comparative query; either find
  a prompt/structural fix that gets real dispatch happening, or document this as a second disclosed
  limitation alongside the fetch-skipping one.
- [ ] **Recovery-after-quarantine gap**: when the grounding check quarantines a bad artifact, the Planner
  is nudged to write a fresh one, but on the academic eval item it never did — the run ended with no
  `final_report.md` at all, discarding two genuinely correct URLs that were sitting right there in the
  model's own prior turn. The disclosure-on-exhaustion fix means this is surfaced honestly rather than
  hidden, but the *outcome* (throwing away correct partial findings) is still worse than it needs to be.
  *Acceptance*: either the two-pass `findings.md` write actually gets exercised reliably (see the
  `findings[]` item below — right now nothing confirms `findings.md` is even being written in practice)
  so a quarantined `final_report.md` still leaves recoverable raw findings behind, or a structural fallback
  that surfaces whatever's in `findings.md`/sub-agent results directly if the Planner can't produce a
  final write within budget.
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
