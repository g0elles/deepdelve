# Project rules

- Any change to the completion checks (`src/engine/completion.py` — a check function,
  `COMPLETION_CHECKS`/`GROUNDING_CHECKS`, or a Verdict's problem/messages) must run
  `test_structural_checks.py` before commit, and a new check needs a row in that file's
  verdict matrix. This chain has shipped the swallowed-elif bug twice (bd307f4, run 13);
  the matrix is the pin.
- Interpreters (dual-boot project dir): Linux `~/.venvs/deepdelve/bin/python`; Windows
  `venv/Scripts/python` (the repo-local `venv/` is the Windows one — don't delete it).
- **TUI/CLI feature parity is mandatory, not optional.** `src/engine/tui.py` hosts two entry
  points — `run_cli` (headless) and `run_agent`/`BasicTuiAgent` (interactive TUI) — that
  duplicate a lot of the same run-lifecycle logic instead of sharing one implementation (see
  ROADMAP.md "B4 unify duplicated run loop", still open). Until that unification happens, any
  new CLI flag or headless-only capability (e.g. `--resume-run`, `--depth`, `--style`,
  `--seed-url`) MUST be checked against the TUI for an equivalent — either wire it in as a slash
  command / config toggle, or explicitly note in the same commit why it's headless-only and
  intentionally so. Concretely confirmed missing during real use, 2026-07-12: `--resume-run`
  existed in the CLI for a full session before anyone noticed the TUI had no way to reattach an
  interrupted run at all. **Before considering any new CLI-surfaced feature done, grep both
  `run_cli` and `run_agent`/`SLASH_COMMANDS` for it.**
- **When adding a feature, trace its blast radius across the OTHER surfaces of the system before
  calling it done** — not just the one code path being changed. Concretely: a new tool return
  value or error format needs checking against every place that inspects the shape of tool
  results (the TUI's `ToolCallWidget` success/fail rendering, `log_stream_content`'s persisted
  event log, `utils/grounding.py`'s citation/error detection); a new config key needs checking
  against both `run_cli` and the TUI, and against `save_config()`'s persistable-keys allowlist;
  a new completion-check problem needs a verdict-matrix row (see rule above) AND a check that
  `_QUARANTINE_PROBLEMS`/salvage paths handle it. The recurring failure pattern this project has
  hit more than once is building a feature correctly for the path directly in front of you while
  leaving a sibling path (the other CLI mode, the log, the eval scorer, the TUI widget) silently
  unaware of it. Before treating a change as complete, name out loud which OTHER files consume
  the same data/tool/state you just touched, and check each one.
- **`session_status/CURRENT.md` is the running scratchpad for in-progress work — keep it updated,
  not just README/ROADMAP.** Whole `session_status/` directory is gitignored. Update
  `session_status/CURRENT.md`:
  - After any change judged important enough to matter to a future session (a real fix, a shipped
    feature, a live-tested finding) — not necessarily every commit, but don't let real work go
    unrecorded either.
  - Whenever a plan is formed (via ExitPlanMode or otherwise) — record the plan itself, not just
    the fact one exists.
  - Before a session ends or a push happens — a short summary of what changed and why.
  - Entries must be short and to the point, but concrete: reference the exact file path and the
    line/function/section touched (e.g. `src/utils/grounding.py:59-63`,
    `_strip_trailing_punct`), so a cold read of the entry tells you exactly where to go — not just
    what happened.
  - When `CURRENT.md` gets large or a work phase clearly closes out, archive it to
    `session_status/<date>.md` (`YYYY-MM-DD`, matching existing archives) and start a fresh
    `CURRENT.md` containing only what's still open — mirrors the README/ROADMAP-vs-session-status
    split: durable, load-bearing facts belong in README/ROADMAP; `CURRENT.md` is short-lived
    working memory for what's still in flight.
