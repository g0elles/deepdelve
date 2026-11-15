---
description: Evaluate a finished research run and lead with a verdict
argument-hint: <run_dir>
---
First confirm no run process is alive (`pgrep -af "app.cli_main|pipeline.run"`). Then:

1. `~/.venvs/deepdelve/bin/python scripts/evaluate_run.py $ARGUMENTS`
2. Read the run's `final_report.md`, the scorecard and every flagged item.
3. Lead the reply with a verdict: did it answer every part of the query, were claims grounded in the saved sources, what does the scorecard flag, how does it compare with earlier runs of the same query (the script prints that table).
4. Trace each flag to its cause in code or config, not just its symptom. "Completed" or a clean check chain is not an evaluation.
