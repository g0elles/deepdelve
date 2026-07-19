"""
Reward functions for GRPO fine-tuning DeepDelve's Planner role against its own documented
failure modes — see ROADMAP.md "Scoped fine-tuning plan (2026-07-18)" and the bake-off entries
it references. Pure functions, no model/tokenizer/training-framework dependency, so they can be
unit-tested here and reused directly as a GRPO reward_fn once training actually starts.

Four scored dimensions, each tied to a real, live-confirmed failure rather than a hypothetical:
  1. schema_compliance_reward   -- llama3.2:3b (JSON-encoded STRING instead of a real array,
                                    confirmed on 3 independent backends) and granite3.1-dense/
                                    phi4-mini (narrated a tool call as text, no real tool_calls).
  2. real_tool_name_reward      -- gpt-oss's documented hallucinated-tool-name pattern
                                    (invented "grep_search?"/"justify" as literal function names).
  3. thin_coverage_response_reward -- qwen3:4b/qwen3:8b's shared failure: repeating a canned
                                    "research is complete" response verbatim, or narrating
                                    findings/report content directly instead of re-delegating with
                                    materially different instructions or cleanly stopping.
  4. writer_role_response_reward -- Bonsai-8B/qwen2.5:3b-instruct's shared failure as
                                    FindingsWriter/Builder: "Finishing" a dispatch WITHOUT ever
                                    calling write_workspace_file, either silently (empty response)
                                    or by narrating the artifact's content as chat text instead of
                                    writing it for real. The single most common writer-role
                                    failure mode found across this project's own bake-off.

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

# Narrates an INTENTION to re-delegate/act without ever emitting the real tool call — confirmed
# live during this project's own GRPO pre-training sanity check (base Qwen3-4B, 2026-07-18,
# thinking disabled): "I will delegate the tasks again for the uncovered angles..." and "I need to
# re-delegate the tasks... Let me start by..." with no `<tool_call>` anywhere in the completion.
# Functionally identical to the canned-refusal case (the real engine sees no delegate_tasks call
# either way and the run doesn't progress) but distinct phrasing that the canned-refusal/narrated-
# report markers don't catch — caught BEFORE any training run by generating real completions from
# the actual target model and reading them, not by assuming the reward function was already
# correct. A genuine clean stop doesn't describe a future action; it just stops.
_INTENT_WITHOUT_ACTION_MARKERS = (
    "i will delegate", "i'll delegate", "i need to delegate", "i need to re-delegate",
    "i will re-delegate", "let me delegate", "let me re-delegate", "let me start by",
    "i will rephrase", "i'll rephrase", "i will search", "i'll search again",
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


# Per-role tool lists, copied directly from src/app.py's SubAgentConfig definitions (the roles
# whose tool set is fixed and small enough to be worth hardcoding here; WebSearcher/AcademicSearcher/
# DocumentAnalyzer/DataAnalyzer are deliberately NOT included — a generic session-log source label
# for those roles reflects the PARENT's chosen task name, not the target agent_id, so there is no
# reliable way to attribute a call to one of them specifically from the log alone; those fall back
# to KNOWN_TOOLS). Real, live-found bug this fixes: `delegate_tasks` (a genuinely real tool,
# Planner-only) repeatedly appears in `tool_error_samples` as "Requested function \"delegate_tasks\"
# not found" from Builder/FindingsWriter dispatches — neither role has that tool at all (both are
# fixed to read/write/grep/think, see app.py's own SubAgentConfig comments). Scoring purely against
# a flat "is this tool real anywhere" set would wrongly reward that call, and would put
# contradictory labels on the identical string depending only on which role said it.
ROLE_TOOLS = {
    "Builder": frozenset({"read_workspace_file", "grep_workspace_file", "write_workspace_file", "think_tool"}),
    "FindingsWriter": frozenset({"read_workspace_file", "grep_workspace_file", "write_workspace_file", "think_tool"}),
    "PeerReviewer": frozenset({"read_workspace_file", "grep_workspace_file", "think_tool"}),
    "Planner": frozenset({"list_workspace_files", "write_todos", "read_todos", "think_tool", "delegate_tasks"}),
}

# Union of every real tool across every role — the fallback check when the calling role can't be
# reliably identified (WebSearcher/AcademicSearcher/DocumentAnalyzer/DataAnalyzer dispatches).
KNOWN_TOOLS = frozenset({
    "delegate_tasks", "web_search", "fetch_url_to_workspace", "write_workspace_file",
    "read_workspace_file", "grep_workspace_file", "list_workspace_files", "remove_workspace_file",
    "write_todos", "read_todos", "think_tool", "extract_structured_data",
})


def real_tool_name_reward(tool_name: str | None, role: str | None = None,
                           known_tools: frozenset[str] = KNOWN_TOOLS) -> float:
    """1.0 if tool_name is a real tool available to the CALLING ROLE specifically; 0.0 if
    hallucinated outright (gpt-oss's documented invented function names, e.g. "grep_search?") OR
    real elsewhere in the system but not available to this role (Builder/FindingsWriter calling
    `delegate_tasks` — a genuinely real tool, just not theirs). When `role` is None or not one of
    ROLE_TOOLS' known roles, falls back to the flat `known_tools` union check — the best available
    signal for a dispatch whose calling role can't be identified from context."""
    if not tool_name:
        return 0.0
    if role and role in ROLE_TOOLS:
        return 1.0 if tool_name in ROLE_TOOLS[role] else 0.0
    return 1.0 if tool_name in known_tools else 0.0


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def _looks_like_narrated_content(text: str) -> bool:
    return any(marker.search(text) for marker in _NARRATION_MARKERS)


def _is_canned_refusal(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _CANNED_REFUSAL_MARKERS)


def _narrates_intent_without_action(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _INTENT_WITHOUT_ACTION_MARKERS)


def thin_coverage_response_reward(
    prior_task_instructions: list[str],
    response_tool_call: dict | None,
    response_text: str,
    similarity_threshold: float = 0.8,
) -> float:
    """Scores a model's response to a thin_coverage-shaped corrective nudge (see
    engine/completion.py::check_thin_coverage — fires when a majority of delegated tasks came
    back with no real source). Two acceptable behaviors, three disqualifying patterns:

    - GOOD (1.0): a new `delegate_tasks` call whose task instructions are materially DIFFERENT
      from every prior failed task's instructions (below `similarity_threshold`) — a genuine
      attempt to re-scope, not a near-duplicate resend.
    - GOOD (1.0): no tool call, and the text is a short, clean stop with no narrated report/
      findings content and no narrated intent to act — correctly handing off to the engine's own
      Write-Review-Fix loop instead of trying to write the artifact itself in prose.
    - BAD (0.0): a repeated canned refusal (the exact qwen3:4b pattern), narrated findings/report
      content in the text response (the exact qwen3:8b pattern), or narrating an INTENTION to
      re-delegate/search without ever actually emitting the tool call (confirmed live during this
      project's own pre-training sanity check on the real target model, base Qwen3-4B: "I will
      delegate the tasks again..." with no `<tool_call>` anywhere — functionally identical to the
      canned-refusal case since the real engine sees no delegate_tasks call either way, just
      different phrasing the other two markers don't catch). None of these three counts as
      "re-delegated" or "cleanly stopped."
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

    # No tool call: only acceptable if it's a clean stop — not a canned refusal, not a narrated
    # draft, and not narrated intent to act that never actually produced the tool call.
    if (_is_canned_refusal(response_text) or _looks_like_narrated_content(response_text)
            or _narrates_intent_without_action(response_text)):
        return 0.0
    return 1.0 if response_text.strip() else 0.0


def writer_role_response_reward(wrote_file: bool, response_text: str) -> float:
    """Scores a FindingsWriter/Builder dispatch's response to being asked to write an artifact.
    1.0 only if it actually called `write_workspace_file` (`wrote_file=True` — the caller checks
    this from the real event stream, not from narration). 0.0 for both real failure shapes found
    in this project's own bake-off: a genuinely empty response (Bonsai-8B, qwen2.5:3b-instruct —
    the dispatch "Finished" with zero events) and a response that narrates the artifact's content
    as chat text instead of calling the tool (the same broad pattern documented for gpt-oss's
    endgame-collapse and the reference project this was forked from). Deliberately does not try to
    distinguish the two failure shapes with a partial score — both are equally "didn't do the one
    required thing," and this project's own structural fix (immediate narration salvage) already
    exists to recover the SECOND shape's content when possible; the reward here is about training
    the model to not need that recovery in the first place."""
    return 1.0 if wrote_file else 0.0


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
    assert real_tool_name_reward("web_search", known_tools=known) == 1.0
    assert real_tool_name_reward("grep_search?", known_tools=known) == 0.0, "hallucinated tool name must score 0"
    assert real_tool_name_reward(None, known_tools=known) == 0.0
    # Role-scoped: delegate_tasks is a REAL tool, but not Builder's — the live bug this fixes.
    assert real_tool_name_reward("delegate_tasks") == 1.0, "delegate_tasks with no role given falls back to KNOWN_TOOLS"
    assert real_tool_name_reward("delegate_tasks", role="Planner") == 1.0, "delegate_tasks IS Planner's tool"
    assert real_tool_name_reward("delegate_tasks", role="Builder") == 0.0, (
        "delegate_tasks is real but NOT Builder's tool — same string, role-dependent verdict")
    assert real_tool_name_reward("write_workspace_file", role="Builder") == 1.0

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
    # Real base Qwen3-4B completions from this project's own pre-training sanity check
    # (2026-07-18, thinking disabled) — narrates intent, never actually calls the tool.
    assert thin_coverage_response_reward(
        prior, None,
        "I will delegate the tasks again for the uncovered angles, phrased differently or with "
        "a narrower query if the first attempt was too broad or too specific to find anything."
    ) == 0.0, "narrating INTENT to re-delegate without a real tool call must score 0"
    assert thin_coverage_response_reward(
        prior, None,
        "I need to re-delegate the tasks for the uncovered angles. Let me start by identifying "
        "the specific areas that were not covered in the initial search."
    ) == 0.0, "same intent-without-action pattern, different phrasing, must also score 0"

    assert writer_role_response_reward(True, "Wrote 'findings.md' to disk.") == 1.0
    assert writer_role_response_reward(False, "") == 0.0, "genuinely empty response (Bonsai-8B) must score 0"
    assert writer_role_response_reward(False, "## Findings\n\nReal-looking narrated content...") == 0.0, (
        "narrating instead of writing must score 0 regardless of how good the narration looks")

    print("All reward-function self-tests passed.")
