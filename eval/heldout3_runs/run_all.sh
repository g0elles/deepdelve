#!/bin/bash
cd "$(dirname "$0")/../../src"
while IFS=$'\t' read -r id q; do
  ~/.venvs/deepdelve/bin/python -m pipeline.run "$q" --out ../eval/heldout3_runs/$id > ../eval/heldout3_runs/$id.log 2>&1
  echo "$id done $?"
done < ../eval/heldout3_runs/queries.txt
