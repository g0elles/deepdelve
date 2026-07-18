"""
Reward functions for GRPO fine-tuning DeepDelve's Planner role against its own documented
failure modes — see ROADMAP.md "Scoped fine-tuning plan (2026-07-18)" and the bake-off entries
it references. Pure functions, no model/tokenizer/training-framework dependency, so they can be
unit-tested here and reused directly as a GRPO reward_fn once training actually starts.

Three scored dimensions, each tied to a real, live-confirmed failure rather than a hypothetical:
  1. schema_compliance_reward   -- llama3.2:3b (JSON-encoded STRING instead of a real array,
                                    confirmed on 3 independent backends) and granite3.1-dense/
                                    phi4-mini (narrated a tool call as text, no real tool_calls).
  2. real_tool_name_reward      -- gpt-oss's documented hallucinated-tool-name pattern
                                    (invented "grep_search?"/"justify" as literal function names).
  3. thin_coverage_response_reward -- qwen3:4b/qwen3:8b's shared failure: repeating a canned
                                    "research is complete" response verbatim, or narrating
                                    findings/report content directly instead of re-delegating with
                                    materially different instructions or cleanly stopping.

Deliberately excludes anything an LLM judge would be needed for (matches this project's own
established philosophy in utils/run_state.py's coverage() docstring: prefer structural,
model-independent signals over new judged/structured-output conventions small local models have
repeatedly proven unreliable at following).
"""

import re
from difflib import SequenceMatcher

VALID_AGENT_IDS = frozenset({"WebSearcher", "AcademicSearcher", "DocumentAnalyzer", "DataAnalyzer"})

REQUIRED_TASK_KEYS = frozenset({"task_name", "instructions", "agent_id"})

# Markers that a text-only response is narrating report/findings content instead of cleanly
# stopping delegation — confirmed live (qwen3:8b, 2026-07-18): its final turn included headers
# like these plus a "Stop here." sign-off, and neither findings.md nor final_report.md existed on
# disk. A clean stop is a short acknowledgment; narrated content looks like the artifact itself.
_NARRATION_MARKERS = (
    re.compile(r"^#{1,3}\s", re.MULTILINE),      # markdown headers
    re.compile(r"\*\*Source", re.IGNORECASE),
    re.compile(r"^\s*-\s*\*\*\[", re.MULTILINE),  # "- **[Title](url)" citation bullets
)

# A repeated canned refusal, confirmed live (qwen3:4b, 2026-07-18, 8/8 attempts): the exact same
# "No further tool calls needed... research scope is complete" text every single retry, ignoring
# the nudge's actual request to either re-delegate differently or acknowledge the gap explicitly.
_CANNED_REFUSAL_MARKERS = (
    "no further tool calls needed",
    "research scope is complete",
    "your research scope is complete",
)


def schema_compliance_reward(tool_call: dict | None) -> float:
    """1.0 if `tool_call` is a well-formed delegate_tasks call: real dict with name=="delegate_tasks",
    `arguments["tasks"]` is an actual list (not a JSON-encoded string — the exact llama3.2:3b
    failure, confirmed on Ollama/llama.cpp/vLLM alike), and every task has the three required keys
    with a real agent_id. 0.0 for anything else, including a `None` (the model narrated instead of
    producing a real tool_calls entry at all — the granite3.1-dense/phi4-mini failure)."""
    if not tool_call or tool_call.get("name") != "delegate_tasks":
        return 0.0
    tasks = (tool_call.get("arguments") or {}).get("tasks")
    if not isinstance(tasks, list) or not tasks:
        return 0.0  # catches the JSON-string-instead-of-array case directly
    for t in tasks:
        if not isinstance(t, dict) or not REQUIRED_TASK_KEYS.issubset(t.keys()):
            return 0.0
        if t.get("agent_id") not in VALID_AGENT_IDS:
            return 0.0
    return 1.0


def real_tool_name_reward(tool_name: str | None, known_tools: frozenset[str]) -> float:
    """1.0 if tool_name is a real, registered tool; 0.0 if hallucinated (gpt-oss's documented
    invented function names) or missing entirely."""
    return 1.0 if tool_name and tool_name in known_tools else 0.0


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def _looks_like_narrated_content(text: str) -> bool:
    return any(marker.search(text) for marker in _NARRATION_MARKERS)


def _is_canned_refusal(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _CANNED_REFUSAL_MARKERS)


def thin_coverage_response_reward(
    prior_task_instructions: list[str],
    response_tool_call: dict | None,
    response_text: str,
    similarity_threshold: float = 0.8,
) -> float:
    """Scores a model's response to a thin_coverage-shaped corrective nudge (see
    engine/completion.py::check_thin_coverage — fires when a majority of delegated tasks came
    back with no real source). Two acceptable behaviors, one disqualifying pattern:

    - GOOD (1.0): a new `delegate_tasks` call whose task instructions are materially DIFFERENT
      from every prior failed task's instructions (below `similarity_threshold`) — a genuine
      attempt to re-scope, not a near-duplicate resend.
    - GOOD (1.0): no tool call, and the text is a short, clean stop with no narrated report/
      findings content — correctly handing off to the engine's own Write-Review-Fix loop instead
      of trying to write the artifact itself in prose.
    - BAD (0.0): a repeated canned refusal (the exact qwen3:4b pattern) or narrated findings/report
      content in the text response (the exact qwen3:8b pattern) — the model neither re-delegated
      nor cleanly stopped.
    """
    if response_tool_call and response_tool_call.get("name") == "delegate_tasks":
        if schema_compliance_reward(response_tool_call) == 0.0:
            return 0.0
        new_tasks = response_tool_call["arguments"]["tasks"]
        new_instructions = [t["instructions"] for t in new_tasks]
        for new_instr in new_instructions:
            if any(_similarity(new_instr, prior) >= similarity_threshold for prior in prior_task_instructions):
                return 0.0  # near-duplicate of a task that already failed — not a real re-scope
        return 1.0

    # No tool call: only acceptable if it's a clean stop, not a canned refusal or narrated draft.
    if _is_canned_refusal(response_text) or _looks_like_narrated_content(response_text):
        return 0.0
    return 1.0 if response_text.strip() else 0.0


if __name__ == "__main__":
    # Smallest-thing-that-fails-if-broken self-test, same spirit as test_structural_checks.py —
    # no training framework needed to verify the reward logic itself is sound.
    assert schema_compliance_reward({
        "name": "delegate_tasks",
        "arguments": {"tasks": [{"task_name": "x", "instructions": "y", "agent_id": "WebSearcher"}]},
    }) == 1.0
    assert schema_compliance_reward({
        "name": "delegate_tasks", "arguments": {"tasks": "[{\"task_name\": \"x\"}]"},
    }) == 0.0, "JSON-string tasks (llama3.2:3b failure) must score 0"
    assert schema_compliance_reward(None) == 0.0, "no tool call at all must score 0"
    assert schema_compliance_reward({
        "name": "delegate_tasks", "arguments": {"tasks": [{"task_name": "x", "instructions": "y", "agent_id": "AI-3"}]},
    }) == 0.0, "invented agent_id must score 0"

    known = frozenset({"web_search", "delegate_tasks", "think_tool"})
    assert real_tool_name_reward("web_search", known) == 1.0
    assert real_tool_name_reward("grep_search?", known) == 0.0, "hallucinated tool name must score 0"
    assert real_tool_name_reward(None, known) == 0.0

    prior = ["Identify top 5 heuristic algorithms (metaheuristics) for deep learning sales forecasting"]
    good_reworded_call = {
        "name": "delegate_tasks",
        "arguments": {"tasks": [{
            "task_name": "x",
            "instructions": "Identify top 5 metaheuristics for retail sales forecasting with real-world implementations",
            "agent_id": "WebSearcher",
        }]},
    }
    assert thin_coverage_response_reward(prior, good_reworded_call, "") == 1.0, (
        "a materially reworded re-delegation (real qwen3:4b attempt-0 response) must score 1.0")

    near_dup_call = {
        "name": "delegate_tasks",
        "arguments": {"tasks": [{
            "task_name": "x",
            "instructions": "Identify top 5 heuristic algorithms (metaheuristics) for deep learning sales forecasting",
            "agent_id": "WebSearcher",
        }]},
    }
    assert thin_coverage_response_reward(prior, near_dup_call, "") == 0.0, (
        "re-sending a near-identical task must score 0.0")

    assert thin_coverage_response_reward(prior, None, "Understood, I have nothing further to add.") == 1.0
    assert thin_coverage_response_reward(
        prior, None, "No further tool calls needed. Your research scope is complete."
    ) == 0.0, "the exact live qwen3:4b canned refusal must score 0"
    assert thin_coverage_response_reward(
        prior, None, "**Findings.md**\n**Heuristic Algorithms**\n### 1. Heuristics\n- **[A Paper](https://x.com)**"
    ) == 0.0, "narrated report content (the exact live qwen3:8b pattern) must score 0"

    print("All reward-function self-tests passed.")
