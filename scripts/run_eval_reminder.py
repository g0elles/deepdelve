#!/usr/bin/env python
"""Claude Code hook (UserPromptSubmit + SessionStart): if a research run finished recently and nobody has run
scripts/evaluate_run.py on it yet, tell the model to evaluate it before anything else (CLAUDE.md rule).
Silent when there is nothing to evaluate. evaluate_run.py drops a `.evaluated` marker in the run dir."""
import glob
import json
import os
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WINDOW_S = 6 * 3600  # only runs that finished in the last 6 hours; older ones are history, not news


def run_active() -> bool:
    """True while a DeepDelve run process is alive (the state file only changes between completion
    attempts, minutes apart, so its mtime cannot tell 'running' from 'done')."""
    import subprocess
    out = subprocess.run(["pgrep", "-af", r"app\.cli_main|deepdelve-api|src\.api|pipeline\.run"], capture_output=True, text=True).stdout
    me = str(os.getpid())
    return any(l.split(None, 1)[0] != me and "pgrep" not in l and "run_eval_reminder" not in l and "evaluate_run" not in l for l in out.splitlines() if l.strip())


def pending():
    out = []
    for d in glob.glob(os.path.join(ROOT, "**", "research_output", "*", ""), recursive=True) + glob.glob(os.path.join(ROOT, "research_output", "*", "")):
        rep = os.path.join(d, "final_report.md")
        st = os.path.join(d, "_run_state.json")
        # final_report.md appears mid-run (completion-check attempts rewrite it), so only report once no run process is alive
        if (os.path.exists(rep) and os.path.exists(st) and not os.path.exists(os.path.join(d, ".evaluated"))
                and time.time() - os.path.getmtime(rep) < WINDOW_S):
            out.append(d.rstrip("/"))
    return sorted(set(out), key=os.path.getmtime)


if __name__ == "__main__":
    runs = [] if run_active() else pending()
    if runs:
        cmds = "; ".join(f"python scripts/evaluate_run.py '{os.path.relpath(r, ROOT)}'" for r in runs[-3:])
        msg = ("RUN FINISHED, NOT YET EVALUATED. Per CLAUDE.md, before replying to anything else: run " + cmds +
               ", read the report, and lead your reply with the verdict (answered every part? grounded? flags? vs earlier runs).")
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": msg}}))
