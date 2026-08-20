"""A/B test: does <Show Your Thinking> in FINDINGS_WRITER_INSTRUCTIONS affect the rate at which
qwen3-4b-combined-v2 narrates instead of calling write_workspace_file, on its own real evidence
base from the run where it was live-disqualified for this exact failure (2026-08-19)."""
import sys, os, json, re, requests

sys.path.insert(0, "src")
from utils.run_state import RunState
from engine.completion import _build_findings_source_material
from prompts import FINDINGS_WRITER_INSTRUCTIONS

RUN_STATE_PATH = "eval/runs/20260819_192103/item0_run1/workspace/i_m_choosing_a_city_for_a_3_month_remote_5613f5_20260819_192103/_run_state.json"
MODEL = "deepdelve-qwen3-4b-combined-v2"
N_REPS = 9

with open(RUN_STATE_PATH) as f:
    saved = json.load(f)
rs = RunState(run_dir=os.path.dirname(RUN_STATE_PATH))
rs.data.update(saved)
evidence = _build_findings_source_material(rs)

task_name = "Write findings.md from this run's real research results."
base_instructions = FINDINGS_WRITER_INSTRUCTIONS.format(
    date="2026-08-19",
    task_name=task_name,
    delegation_instructions="",
    read_workspace_file_quota=30,
    grep_workspace_file_quota=30,
    write_workspace_file_quota=10,
    edit_workspace_file_quota=10,
) + "\n\n" + evidence

variant_with = base_instructions
variant_without = re.sub(
    r"<Show Your Thinking>.*?</Show Your Thinking>\n\n", "", base_instructions, flags=re.DOTALL
)
assert variant_without != variant_with, "block removal had no effect -- regex didn't match"
assert "<Show Your Thinking>" not in variant_without

TOOL = [{
    "type": "function",
    "function": {
        "name": "write_workspace_file",
        "description": "Save content to your workspace.",
        "parameters": {
            "type": "object",
            "properties": {
                "filename": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["filename", "content"],
        },
    },
}]


def run_variant(label, instructions):
    hits = 0
    for i in range(1, N_REPS + 1):
        resp = requests.post(
            "http://localhost:11434/api/chat",
            json={
                "model": MODEL,
                "messages": [{"role": "user", "content": instructions}],
                "tools": TOOL,
                "think": True,
                "stream": False,
            },
            timeout=300,
        ).json()
        msg = resp.get("message", {})
        called = bool(msg.get("tool_calls"))
        hits += called
        print(f"{label} rep {i}: tool_call={'YES' if called else 'no'}")
    print(f"=== {label}: {hits}/{N_REPS} real tool calls ===\n")
    return hits


if __name__ == "__main__":
    with_hits = run_variant("WITH <Show Your Thinking>", variant_with)
    without_hits = run_variant("WITHOUT <Show Your Thinking>", variant_without)
    print(f"FINAL: with={with_hits}/{N_REPS}  without={without_hits}/{N_REPS}")
