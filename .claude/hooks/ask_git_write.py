#!/usr/bin/env python3
"""PreToolUse(Bash): force a confirmation prompt on git commit/push, even if an allow rule matches (CLAUDE.md: never commit/push unasked)."""
import json
import re
import sys

cmd = json.load(sys.stdin).get("tool_input", {}).get("command", "")
if re.search(r"\bgit\b[^;&|]*\b(commit|push)\b", cmd):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask",
                      "permissionDecisionReason": "git commit/push needs explicit user OK (project rule)"}}))
