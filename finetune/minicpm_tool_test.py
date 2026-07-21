"""
Standalone smoke test for MiniCPM4-MCP's NATIVE tool-calling format (not OpenAI-style
tool_calls). The model's own embedded chat template outputs:

    <|thought_start|>
    ...reasoning...
    <|thought_end|>
    <|tool_call_start|>
    ```python
    func_name(param=value, ...)
    ```
    <|tool_call_end|>
    ...optional trailing text...

Ollama's generic /api/chat `tools` param assumes OpenAI-style JSON tool_calls and fails
outright against this ("peg-native format" error). This script bypasses that: builds the
system prompt in the model's own "# Functions" format (lifted directly from its GGUF-embedded
jinja template) as plain text, calls /api/chat with NO `tools` param, and parses the raw
Python-code-block response back into (name, args).

This mirrors the same "isolated tool-call test, multiple trials, both roles" methodology this
project's own model bake-off already used for every other candidate (mistral-nemo, devstral,
llama3-groq-tool-use, etc.) - single-shot success is not sufficient evidence.
"""
import ast
import json
import re
import sys
import urllib.request

MODEL = "minicpm4-mcp"
OLLAMA_URL = "http://localhost:11434/api/chat"

FUNCTIONS_PREAMBLE = """
# Functions
Here is a list of functions that you can invoke:
```python
from enum import Enum
from typing import List, Dict, Optional
from pydantic import BaseModel, Field

def get_weather(city: str):
    \"\"\"Get current weather for a city.

    Args:
    city: str
    \"\"\"

def delegate_tasks(tasks: List[Dict]):
    \"\"\"Delegate one or more research tasks to specialist sub-agents.

    Args:
    tasks: List[Dict]
    \"\"\"
```

# Function Call Rule and Output Format
- If the user's question can be answered without calling any function, please answer the \
user's question directly. In this situation, you should return your thought and answer the \
user's question directly.
- If the user cannot be answered without calling any function, and the user does not provide \
enough information to call functions, please ask the user for more information. In this \
situation, you should return your thought and ask the user for more information.
- If the user's question cannot be answered without calling any function, and the user has \
provided enough information to call functions to solve it, you should call the functions. In \
this situation, the assistant should return your thought and call the functions.
- Use default parameters unless the user has specified otherwise.
- You should answer in the following format:

<|thought_start|>
{explain why the user's question can be answered without calling a function or why you should \
ask the user for more information or why you should call one or more functions and your plan to \
solve the user's question.}
<|thought_end|>
<|tool_call_start|>
```python
func1(params_name=params_value, params_name2=params_value2...)
func2(params)
```
<|tool_call_end|>
{answer the user's question directly or ask the user for more information}
""".strip()

TOOL_CALL_RE = re.compile(
    r"<\|tool_call_start\|>\s*```python\s*(.*?)```\s*<\|tool_call_end\|>", re.DOTALL
)
CALL_RE = re.compile(r"(\w+)\((.*)\)", re.DOTALL)


def call_ollama(user_prompt: str) -> str:
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": FUNCTIONS_PREAMBLE},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "options": {"temperature": 0.2},
    }
    req = urllib.request.Request(
        OLLAMA_URL,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        body = json.loads(resp.read())
    return body["message"]["content"]


def parse_tool_calls(raw: str) -> list[tuple[str, dict]] | None:
    m = TOOL_CALL_RE.search(raw)
    if not m:
        return None
    block = m.group(1).strip()
    calls = []
    for call_m in CALL_RE.finditer(block):
        name, argstr = call_m.group(1), call_m.group(2)
        args = {}
        if argstr.strip():
            try:
                tree = ast.parse(f"f({argstr})", mode="eval")
                call_node = tree.body
                for kw in call_node.keywords:
                    args[kw.arg] = ast.literal_eval(kw.value)
            except Exception as e:
                return [("__PARSE_ERROR__", {"error": str(e), "raw": argstr})]
        calls.append((name, args))
    return calls


CASES = [
    ("What's the weather like in London right now?", "get_weather"),
    ("What's the weather like in Paris?", "get_weather"),
    (
        "Delegate a task to research the current stable version of Rust, "
        "and a separate task to find peer-reviewed research on Rust's borrow checker.",
        "delegate_tasks",
    ),
    ("What is 2 + 2?", None),  # should NOT call a tool
    (
        "Delegate a task named 'market_sizing' to research the electric vehicle market in Germany.",
        "delegate_tasks",
    ),
]

if __name__ == "__main__":
    passed = 0
    for prompt, expected in CASES:
        print(f"\n=== PROMPT: {prompt!r} ===")
        print(f"expected tool: {expected}")
        try:
            raw = call_ollama(prompt)
        except Exception as e:
            print(f"REQUEST FAILED: {e}")
            continue
        print(f"--- raw response ---\n{raw}\n---")
        calls = parse_tool_calls(raw)
        if expected is None:
            ok = calls is None
        else:
            ok = bool(calls) and calls[0][0] == expected and calls[0][0] != "__PARSE_ERROR__"
        print(f"parsed: {calls}")
        print("PASS" if ok else "FAIL")
        passed += ok
    print(f"\n{passed}/{len(CASES)} passed")
    sys.exit(0 if passed == len(CASES) else 1)
