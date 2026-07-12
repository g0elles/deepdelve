# Project rules

- Any change to the completion checks (`src/engine/completion.py` — a check function,
  `COMPLETION_CHECKS`/`GROUNDING_CHECKS`, or a Verdict's problem/messages) must run
  `test_structural_checks.py` before commit, and a new check needs a row in that file's
  verdict matrix. This chain has shipped the swallowed-elif bug twice (bd307f4, run 13);
  the matrix is the pin.
- Interpreters (dual-boot project dir): Linux `~/.venvs/deepdelve/bin/python`; Windows
  `venv/Scripts/python` (the repo-local `venv/` is the Windows one — don't delete it).
