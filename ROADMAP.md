# DeepDelve Roadmap

Status as of 2026-07-14.

## Done

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

## Findings from live testing (not yet acted on / informational)

- **Grounding check verifies provenance, not topical relevance.** A live GOA (Grasshopper Optimization Algorithm) research query got a citation from `globaldrivetozero.org` — actually fetched, and sharing surface terms like "GOA"/"Goa" — that's actually about the Indian state of Goa's EV policy, not the algorithm. The URL-presence + term-overlap check passed it because it only checks "was this fetched" and "do terms overlap," not "is this source about the same subject." Acronym collisions are the clearest way to trigger this; unclear how common the failure mode is outside them.
- **JS-gated pages return bot-challenge stubs, not content.** Several fetches (Cloudflare "Just a moment...", a "Human Verification" page, a Prezi slide deck) came back as 16-18 byte stubs since the fetcher doesn't execute JavaScript. *(Fixed for most cases — see "Done": headless/headed-browser fetch fallback, 2026-07-14. Recovers Springer (headless-sufficient) and MDPI (needed headed). NOT a universal fix: a genuine Cloudflare Turnstile challenge (ScienceDirect) resists both headless AND headed Chromium regardless of patience or `navigator.webdriver` spoofing — confirmed to be automation/CDP-fingerprint detection, not a solvable timing issue, and deliberately not pursued further; see the ScienceDirect sub-bullet above for the full investigation. Still correctly falls through to the stub flag rather than silently failing.)*
- **A citation being present in a report's "Sources" list doesn't mean it was fetched.** Across several market-research runs, more than half of named sources were routinely never actually fetched (recalled from the model's training data) — and when independently fact-checked, specific statistics tied to unfetched sources were measurably wrong, usually understated.
- **Hard exclusion rules ("do not research sector X") repeatedly fail to hold**, confirmed across at least 2 independent runs with different prompt wordings: an explicitly-excluded "Agricultural"/"agribusiness" sector got researched and included in the final report anyway — once purely from memory, once with the model actually delegating and fetching a real source for the excluded sector. Simply naming the exclusion in the prompt isn't enough; needs either a stronger structural check (reject a delegated task whose topic matches an excluded keyword) or repeated reinforcement at report-writing time, not just at planning time.
- **Non-URL "citations" evade the grounding check entirely.** A live report sourced several claims to `"Expert opinion from a cold storage facility manager in Colombia"` — not URL-shaped, so `extract_cited_urls` never sees it, even though it's exactly as ungrounded as a fabricated URL. The grounding check's whole model is "cross-reference cited URLs against fetched URLs" — a citation with no URL at all currently gets a free pass. **Fixed — see "Done" above (`non_url_citation_check`).**
- **Scaling down scope (12 sectors → 5) improved surface polish, not actual grounding rate.** A 5-sector re-run produced far more plausible-looking, consistently-formatted citations than a 12-sector run, but cross-referencing against `_run_state.json`'s real `fetched_urls` showed most of them were still fabricated — only 5 URLs were ever fetched all run, while the final report cited well over twice that many distinct domains. Fewer sectors did not proportionally reduce the fabrication rate.

- **gpt-oss hallucinates entire tool names, not just filenames (2026-07-12).** Distinct from the
  fuzzy-filename problem fixed this session (a real tool called with a garbled argument) — this is
  the model inventing a function that was never in its schema at all: `grep_search?` and `justify`
  both fired as literal function-call names in one live run (heuristic-algorithms sales-forecasting
  query), 3 occurrences total. Each one only cost a turn (clean error, `malformed_tool_call_nudge`
  path, sub-agent recovered without stalling) but three in a single run is a real pattern worth its
  own investigation, not noise to fold into the filename fix. Open question: is this addressable
  with tighter tool-schema framing in the prompt, or a harder reliability ceiling for this model
  class — unclear without a dedicated look.
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

- **Address the grounding check's topical-relevance gap** — some form of "is this source actually about the claimed subject," not just "was it fetched and does it share terms." Unclear whether this needs an LLM judge (this local model class has proven unreliable as its own judge elsewhere in this project) or a cheaper heuristic. *(Partially mitigated 2026-07-12: scope matching is now case-insensitive and charset-correct, and stub shells can no longer ground anything.)* **Concrete candidate mechanism found 2026-07-13** (verified real, not an LLM judge): a lightweight CPU cross-encoder reranker (`BAAI/bge-reranker-v2-m3`, ~278M params) scoring (claim, source) pairs directly — as a semantic sanity check layered *after* the existing term-overlap check, the same way the NLI entailment check is already layered on top of it. Would have caught the GOA-the-algorithm-vs-Goa-the-Indian-state acronym collision the existing stack missed.
- **Claim-level grounding upgrade: atomic-claim decomposition + evidence assignment** (found
  2026-07-13, informed by two independently-verified sources converging on the same idea —
  FActScore's decompose-then-verify pattern (arXiv:2305.14251, already cited in README for the NLI
  check) and Rasheed et al.'s claim-evidence provenance framing, *From Fluent to Verifiable:
  Claim-Level Auditability for Deep Research Agents* (arXiv:2602.13855)) — the current grounding
  stack verifies whole lines/paragraphs against a source; this decomposes report text into atomic
  factual claims first, then binds each claim to its single best-supporting source (not just "a"
  cited source on the same line), exposing citation-sharing and citation-drift cases line-scoped
  checks can miss. No new external dependency, no LLM-judge-of-truth — the decomposition step only
  splits propositions, it doesn't decide what's true. Fits the "structural fix over prompt tuning"
  philosophy directly; highest-priority new item from the 2026-07-13 research pass.
- **Cross-source contradiction detection** (found 2026-07-13, FEVER-style: Thorne et al., NAACL
  2018, `fever.ai`) — currently missing entirely: when two fetched sources disagree on a figure
  (e.g. one says $12B revenue, another says $13.4B), the report silently picks one. Cluster claims
  by subject, detect disagreement among their bound evidence, and require the Builder to surface
  the conflict explicitly rather than smoothing it over. Not an LLM judging which source is right —
  just flagging that they disagree. Fits the integrity-first priority.
- **Coverage accounting / ResearchMap** (found 2026-07-13) — track topic-completeness (e.g. per
  planned research slot: status, evidence count, confidence) so the completion check can require a
  coverage threshold, not just "enough tokens written." Complements the Builder loop without
  touching it — a Planner-side addition. Moderate effort (needs a Planner output schema change).
- **xQuAD-style result-diversity reranking** (found 2026-07-13, Santos, Peng, Macdonald, Ounis,
  *Explicit Search Result Diversification through Sub-Queries*, ECIR 2010) — distinct from the
  topical-relevance cross-encoder above: this targets search-result *diversity*, not relevance.
  Reranks candidate results to maximize coverage of unexplored aspects of a query instead of
  returning 5 near-duplicate results for the same angle — a discovery-quality lever, addresses the
  "Scaling down scope did not improve grounding rate" / thin-discovery findings already in this
  file. Low-medium cost, pure reranker, no LLM changes.
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
    isolated schema smoke test, ceilings on the real multi-step benchmark. Not yet run for
    Bonsai-8B — that's the next concrete step, same query, before drawing a final verdict on either
    candidate vs. the current `gpt-oss:20b` default.
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
