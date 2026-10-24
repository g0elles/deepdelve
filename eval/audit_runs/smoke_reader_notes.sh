#!/bin/bash
# Live smoke test of the reader-notes block on the merged code: one evidence-only run through the real CLI (hard stop 75 min).
export DEEPDELVE_FORCE_VIRTUAL_DISPLAY=1
unset WAYLAND_DISPLAY DISPLAY
cd "/mnt/nuevovol/Projects/AI shit/Building_Tools/deep-research-agent"
ollama stop deepdelve-gpt-oss:latest >/dev/null 2>&1; sleep 3
echo "=== smoke start $(date +%T) HEAD $(git rev-parse --short HEAD)"
timeout 4500 ~/.venvs/deepdelve/bin/python src/app.py --auto-approve --pipeline-evidence eval/heldout4_runs/rerun2/h27 -p "What does the evidence say for and against remote work on productivity and employee wellbeing?" > eval/audit_runs/smoke_reader_notes.log 2>&1
echo "=== smoke exit $? $(date +%T) newest=$(ls -t research_output | head -1)"
