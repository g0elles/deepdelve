#!/bin/bash
# Writing-step reports for the defect audit: current code, live config, saved evidence, one run per evidence set. Hard stop 75 min per run.
export DEEPDELVE_FORCE_VIRTUAL_DISPLAY=1
unset WAYLAND_DISPLAY DISPLAY
cd "/mnt/nuevovol/Projects/AI shit/Building_Tools/deep-research-agent"
OUT=eval/audit_runs
SHA0=$(sha1sum ~/.deepdelve/config.yaml | cut -c1-10)
while IFS='|' read -r id ev q; do
  ollama stop deepdelve-gpt-oss:latest >/dev/null 2>&1; sleep 3
  before=$(ls src/research_output | wc -l)
  echo "=== $id start $(date +%T)"
  timeout 4500 ~/.venvs/deepdelve/bin/python src/app.py --auto-approve --pipeline-evidence "$ev" -p "$q" > $OUT/$id.log 2>&1
  echo "=== $id exit $? $(date +%T) newest=$(ls -t src/research_output | head -1) config_unchanged=$([ "$(sha1sum ~/.deepdelve/config.yaml | cut -c1-10)" = "$SHA0" ] && echo yes || echo NO)"
done <<'Q'
h13|eval/heldout3_runs/h13|What is known about how psilocybin works in treating depression, and what do phase 2 and phase 3 trials report?
h22|eval/heldout4_runs/rerun2/h22|What is known about how GLP-1 receptor agonists work in treating obesity, and what do phase 3 trials report?
h21|eval/heldout4_runs/rerun2/h21|How do LSM trees and B-trees differ in write amplification and read performance, and which production databases use each?
h12|eval/heldout3_runs/h12|How do Raft and Paxos differ in leader election and log replication, and which production systems use each?
h18|eval/heldout3_runs/h18|What does the evidence say for and against congestion pricing on traffic and air quality in city centers?
h23|eval/heldout4_runs/rerun2/h23|What were the causes and consequences of the 1918 influenza pandemic, with mortality figures by region?
h27|eval/heldout4_runs/rerun2/h27|What does the evidence say for and against remote work on productivity and employee wellbeing?
Q
echo AUDIT RUNS DONE
