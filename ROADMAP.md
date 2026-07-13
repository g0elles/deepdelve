# DeepDelve Roadmap

Status as of 2026-07-12.

## Done

- **3-tier domain-specialized architecture**: `Planner -> {WebSearcher, AcademicSearcher, PeerReviewer} -> {DocumentAnalyzer, DataAnalyzer}`. `PeerReviewer` is a Planner-tier delegate (independent findings.md critique), not part of the Searcher→Analyzer chain.
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
  turn (sub-agents return findings immediately, headless Planner writes from what it has), a
  second overshoot forces the completion check's final verdict. TUI Planner exempt. Verified live
  with a 3000-char budget: honest "budget exhausted" report, no silent truncation.
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

## Findings from live testing (not yet acted on / informational)

- **Grounding check verifies provenance, not topical relevance.** A live GOA (Grasshopper Optimization Algorithm) research query got a citation from `globaldrivetozero.org` — actually fetched, and sharing surface terms like "GOA"/"Goa" — that's actually about the Indian state of Goa's EV policy, not the algorithm. The URL-presence + term-overlap check passed it because it only checks "was this fetched" and "do terms overlap," not "is this source about the same subject." Acronym collisions are the clearest way to trigger this; unclear how common the failure mode is outside them.
- **JS-gated pages return bot-challenge stubs, not content.** Several fetches (Cloudflare "Just a moment...", a "Human Verification" page, a Prezi slide deck) came back as 16-18 byte stubs since the fetcher doesn't execute JavaScript. No fallback exists today (e.g. a headless-browser fetch path).
- **A citation being present in a report's "Sources" list doesn't mean it was fetched.** Across several market-research runs, more than half of named sources were routinely never actually fetched (recalled from the model's training data) — and when independently fact-checked, specific statistics tied to unfetched sources were measurably wrong, usually understated.
- **Hard exclusion rules ("do not research sector X") repeatedly fail to hold**, confirmed across at least 2 independent runs with different prompt wordings: an explicitly-excluded "Agricultural"/"agribusiness" sector got researched and included in the final report anyway — once purely from memory, once with the model actually delegating and fetching a real source for the excluded sector. Simply naming the exclusion in the prompt isn't enough; needs either a stronger structural check (reject a delegated task whose topic matches an excluded keyword) or repeated reinforcement at report-writing time, not just at planning time.
- **Non-URL "citations" evade the grounding check entirely.** A live report sourced several claims to `"Expert opinion from a cold storage facility manager in Colombia"` — not URL-shaped, so `extract_cited_urls` never sees it, even though it's exactly as ungrounded as a fabricated URL. The grounding check's whole model is "cross-reference cited URLs against fetched URLs" — a citation with no URL at all currently gets a free pass. **Fixed — see "Done" above (`non_url_citation_check`).**
- **Scaling down scope (12 sectors → 5) improved surface polish, not actual grounding rate.** A 5-sector re-run produced far more plausible-looking, consistently-formatted citations than a 12-sector run, but cross-referencing against `_run_state.json`'s real `fetched_urls` showed most of them were still fabricated — only 5 URLs were ever fetched all run, while the final report cited well over twice that many distinct domains. Fewer sectors did not proportionally reduce the fabrication rate.

- **Line-scoped claim grounding (2026-07-12):** `claim_grounding_problem` compared WHOLE-report terms against each source, so generic shared terms masked per-claim fabrication (run 12's flagship figure was absent from its cited source but passed via other lines' overlap). Now each line with a fetched citation is checked against its own source(s) — the regulation-check pattern generalized; conservative as before (≥1 checkable term + zero overlap only, URL slugs stripped).
- **Structural eval scorer (2026-07-12):** new `eval_type: structural` in `eval/evaluate.py` — rubric tier 1 scored deterministically from `_run_state.json` + workspace files (cited⊆fetched, findings.md grounded, no salvage/quarantine banner, no unresolved final problem), which no other scorer read at all.

## Planned (not started)

- **Address the grounding check's topical-relevance gap** — some form of "is this source actually about the claimed subject," not just "was it fetched and does it share terms." Unclear whether this needs an LLM judge (this local model class has proven unreliable as its own judge elsewhere in this project) or a cheaper heuristic. *(Partially mitigated 2026-07-12: scope matching is now case-insensitive and charset-correct, and stub shells can no longer ground anything.)*
- **Headless-browser fetch fallback** for JS-gated pages that return bot-challenge stubs to a plain HTTP GET. *(Partially mitigated 2026-07-12: stub detection now at least FLAGS those pages and refuses to ground citations on them, instead of counting them as real fetches — run 15 flagged 10/21 fetches with zero false positives.)*
- **B4: unify the duplicated TUI/CLI run loop.** `src/engine/tui.py` hosts two ~150-line
  stream/approval/retry loops — `run_cli` (headless) and `run_agent`/`BasicTuiAgent` (interactive)
  — that duplicate most of the same run-lifecycle logic instead of sharing one implementation.
  Deliberately deferred 2026-07-12 (user chose "safe parts now, defer the risky merge"): this
  exact code has caused 2 historical regressions (checkmark-on-error bug, the `--resume-run`
  TUI-parity gap), so a structural merge needs its own careful pass rather than being bundled into
  an unrelated feature commit. Until merged, CLAUDE.md's TUI/CLI parity rule is the mitigation —
  every new CLI-surfaced capability must be checked against the TUI for an equivalent by hand.

## Candidates from the 2026-07-12 reference-repo review (see README References)

- **Engine-driven iterative deepening** (from `dzhng/deep-research`): a STRUCTURAL refine loop —
  each round's findings + the Searchers' FOLLOW-UP DIRECTIONS get composed by the ENGINE into the
  next round's Planner input, with geometric narrowing (their `newBreadth = ceil(breadth/2)`,
  depth counter). DeepDelve currently trusts the Planner model to loop, and local models
  demonstrably under-loop (run 15: 1 niche of 4-6). Could integrate with `--depth`.
- **Tongyi-DeepResearch-30B-A3B as a benchmark candidate** (from `Alibaba-NLP/DeepResearch`):
  30B MoE / 3.3B active — same size class as deepdelve-qwen3.6, but trained specifically for
  long-horizon research. **Chat-template/tool-call compatibility check done, 2026-07-12 — the
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

- **RL fine-tuning for tool-call reliability** (GRPO/PPO on the actual Planner/Searcher schema) — targets the fetch-skipping/tool-call-reliability root cause directly instead of catching it after the fact. Needs real training infrastructure; not started.
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
