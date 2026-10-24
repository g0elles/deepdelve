#!/bin/bash
# Phase D held-out batch for the defect-audit plan: current code, live config, saved evidence the detectors were NOT built on. Hard stop 75 min per run.
export DEEPDELVE_FORCE_VIRTUAL_DISPLAY=1
unset WAYLAND_DISPLAY DISPLAY
cd "/mnt/nuevovol/Projects/AI shit/Building_Tools/deep-research-agent"
OUT=eval/audit_runs
SHA0=$(sha1sum ~/.deepdelve/config.yaml | cut -c1-10)
while IFS='|' read -r id ev q; do
  ollama stop deepdelve-gpt-oss:latest >/dev/null 2>&1; sleep 3
  echo "=== $id start $(date +%T)"
  timeout 4500 ~/.venvs/deepdelve/bin/python src/app.py --auto-approve --pipeline-evidence "$ev" -p "$q" > $OUT/ho_$id.log 2>&1
  echo "=== $id exit $? $(date +%T) newest=$(ls -t research_output | head -1) config_unchanged=$([ "$(sha1sum ~/.deepdelve/config.yaml | cut -c1-10)" = "$SHA0" ] && echo yes || echo NO)"
done <<'Q'
h10|eval/heldout3_runs/h10|Compare Norway's and the Netherlands' approaches to electric vehicle incentives and the resulting EV market share.
h11|eval/heldout3_runs/h11|What are the legal requirements for parental leave in Germany, Sweden and Japan, including duration and pay?
h14|eval/heldout3_runs/h14|What were the causes and consequences of the Irish Great Famine of 1845-1852, with population and emigration figures?
h15|eval/heldout3_runs/h15|What are the latest developments in the international negotiations for a plastics pollution treaty?
h16|eval/heldout3_runs/h16|What were Poland's, Chile's and Kenya's population, urbanization rate and life expectancy in the most recent reported year?
h17|eval/heldout3_runs/h17|¿Cómo funciona el sistema de transporte público de Medellín y cuáles son sus principales modos e indicadores de uso?
h20|eval/heldout4_runs/rerun2/h20|What are the legal requirements for minimum paid annual leave in France, Brazil and South Korea, including days and pay?
h24|eval/heldout4_runs/rerun2/h24|What are the latest developments in the European Union's AI Act implementation?
h25|eval/heldout4_runs/rerun2/h25|What were Portugal's, Vietnam's and Ghana's GDP per capita, internet penetration and fertility rate in the most recent reported year?
h26|eval/heldout4_runs/rerun2/h26|¿Cómo funciona el sistema de salud pública de Colombia y cuáles son sus principales actores y cifras de cobertura?
Q
echo HELDOUT RUNS DONE
