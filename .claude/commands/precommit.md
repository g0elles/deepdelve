---
description: Pre-commit gate (ruff, structural checks, surface-parity grep). Does not commit.
---
Run in order, stop on the first failure and report it:

1. `git diff --name-only HEAD` to list changed files.
2. `~/.venvs/deepdelve/bin/python -m ruff check` on the changed `.py` files.
3. If `src/engine/completion.py` changed: `~/.venvs/deepdelve/bin/python -m pytest tests/test_structural_checks.py -q`, and confirm any new check has a verdict-matrix row.
4. Parity: for any new CLI flag, slash command or config key in the diff, grep `run_cli`, `run_agent`/`SLASH_COMMANDS` and `src/api.py` for it; for config keys also `save_config()`'s allowlist and the live `~/.deepdelve/config.yaml`.
5. Name the OTHER files that consume the data/tool/state touched, and check each.
6. Report pass/fail per step. Do not commit or push; ask first.
