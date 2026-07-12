# Project rules

- Any change to the completion checks (`src/engine/completion.py` — a check function,
  `COMPLETION_CHECKS`/`GROUNDING_CHECKS`, or a Verdict's problem/messages) must run
  `venv/Scripts/python test_structural_checks.py` before commit, and a new check needs a
  row in that file's verdict matrix. This chain has shipped the swallowed-elif bug twice
  (bd307f4, run 13); the matrix is the pin.
