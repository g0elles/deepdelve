from agent_framework import tool
from tools.core import with_quota

@tool
@with_quota
def think_tool(reflection: str) -> str:
    """Use this to record deliberate thinking / reasoning about the current situation and potentially next steps in a concise way."""
    return f"Reflection recorded: {reflection}"


_REPLAN_ACTIONS = {"add_slot", "verify_conflict", "finalize_report"}

@tool
@with_quota
def replan_action(action: str, target_slot: str = "") -> str:
    """Signal a structural planning transition after reviewing delegated research results, in
    addition to (not instead of) your think_tool reasoning. Makes your replanning decision
    checkable by the system rather than relying only on free-text narration.

    Args:
        action: One of "add_slot" (a new research angle is needed — name it in target_slot),
            "verify_conflict" (two findings disagree — target_slot names the slot to re-verify),
            or "finalize_report" (every slot has a real, source-backed answer — proceed to
            findings.md / final_report.md).
        target_slot: The plan slot this action applies to. Required for add_slot and
            verify_conflict; ignored for finalize_report.
    """
    if action not in _REPLAN_ACTIONS:
        return f"Error: action must be one of {sorted(_REPLAN_ACTIONS)}, got '{action}'."
    if action in ("add_slot", "verify_conflict") and not target_slot:
        return f"Error: '{action}' requires a non-empty target_slot naming which slot this applies to."
    slot_note = f" (slot: {target_slot})" if target_slot else ""
    return f"Replan action recorded: {action}{slot_note}. Proceed with the corresponding next step now."
