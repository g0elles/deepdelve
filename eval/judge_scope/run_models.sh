#!/usr/bin/env bash
# Judge A/B across models, sequential (16 GB card): per model a raw-API check that the thinking value reaches the model, then
# ab_relevance.py with per-row decisions. ornith first again: its second run is the run-to-run noise check (H4).
# Usage (repo root): bash eval/judge_scope/run_models.sh [model ...]
set -u
PY=~/.venvs/deepdelve/bin/python
OUT=eval/judge_scope/ab_runs
mkdir -p "$OUT"
MODELS=("$@"); [ ${#MODELS[@]} -eq 0 ] && MODELS=(ornith-1.5:9b gemma4:12b qwen2.5:14b mistral-nemo:12b gpt-oss:20b)
for m in "${MODELS[@]}"; do
  tag=$(echo "$m" | tr ':/' '__')
  echo "=== $m $(date +%H:%M:%S)"
  rocm-smi --showmemuse 2>/dev/null | grep "VRAM%" | head -1
  # raw-API check: the think value the judge sends, and what comes back (thinking chars, output tokens)
  (cd src && $PY - "$m" <<'EOF'
import json, sys, urllib.request
from utils.thinking import minimal_think
m = sys.argv[1]; t = minimal_think(m)
body = {"model": m, "stream": False, "format": {"type": "object", "properties": {"keep": {"type": "array", "items": {"type": "integer"}}}, "required": ["keep"]},
        "options": {"temperature": 0, "num_ctx": 16384},
        "messages": [{"role": "user", "content": "Facet: heat pump cost\nCandidate sentences:\n0. Heat pumps cost $9,000.\n1. The sky is blue.\n\nReturn the numbers of relevant candidates."}]}
if t is not None:
    body["think"] = t
r = json.load(urllib.request.urlopen(urllib.request.Request("http://localhost:11434/api/chat", json.dumps(body).encode(), {"Content-Type": "application/json"}), timeout=300))
print(json.dumps({"model": m, "think_sent": t, "thinking_chars": len(r["message"].get("thinking") or ""), "eval_count": r.get("eval_count"), "content": r["message"]["content"][:80]}))
EOF
  ) | tee "$OUT/raw_$tag.json"
  $PY eval/judge_cases/ab_relevance.py --model "$m" --dump "$OUT/rows_$tag.jsonl" 2>&1 | grep -v "Loading weights" | tee "$OUT/summary_$tag.log"
  ollama stop "$m" 2>/dev/null
done
echo "=== done $(date +%H:%M:%S)"
