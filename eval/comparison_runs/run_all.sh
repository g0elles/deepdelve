#!/bin/bash
# Phase 3 comparison runs (PLAN.md). Repo root: bash eval/comparison_runs/run_all.sh <scratch_dir_with_config_<tag>.yaml>
# v2 (2026-11-20): every model at settings.model_options (card sampling, max ctx). v1 (16K, T 0) log: runs_v1_16k_t0.tsv, dirs cmp_*.
S=$1; PY=~/.venvs/deepdelve/bin/python; export DEEPDELVE_FORCE_VIRTUAL_DISPLAY=1
declare -A Q=(
 [c2]="Does semaglutide reduce alcohol consumption in people with alcohol use disorder, and how does the evidence compare with naltrexone?"
 [c3]="How did Iceland's recovery from the 2008 banking crisis compare with Ireland's on unemployment and GDP growth?"
 [c4]="¿Cómo se compara el efecto del impuesto a las bebidas azucaradas en México con el del Reino Unido sobre el consumo de azúcar?")
LOG=eval/comparison_runs/runs.tsv; P=cmp2
for r in 1 2; do for c in c2 c3 c4; do for m in ornith-1.5:9b gemma4:12b qwen2.5-14b-agent:latest; do
  t=${m%%[-:]*}; E=research_output/${P}_${t}_${c}_r$r
  [ -f $E/metrics.json ] && grep -q "	$E	" $LOG 2>/dev/null && continue
  t0=$(date +%s)
  (cd src && $PY -m pipeline.run "${Q[$c]}" --model $m --out ../$E > ../$E.pipeline.log 2>&1); pe=$?
  t1=$(date +%s)
  $PY src/app.py --config $S/config_$t.yaml --prompt "${Q[$c]}" --pipeline-evidence $E --writer-mode compiled --auto-approve > $E.app.log 2>&1; ae=$?
  R=$(ls -td research_output/*/ | grep -v "/cmp" | head -1)
  printf "%s\t%s\t%s\t%s\tpipe_exit=%s %ss\tapp_exit=%s %ss\t%s\n" "$c" "$m" "$r" "$E" $pe $((t1-t0)) $ae $(( $(date +%s)-t1 )) "$R" >> $LOG
  ollama stop $m >/dev/null 2>&1
done; done; done
echo ALL DONE
