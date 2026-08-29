"""
Standalone smoke test for the xLAM format registered in finetune/tool_call_proxy.py. Unlike
minicpm_tool_test.py (which hits raw Ollama directly, bypassing translation entirely to test the
model's native output), this hits the PROXY's own /v1/chat/completions - the actual contract
DeepDelve's client depends on (OpenAI-shaped tool_calls, arguments as a JSON string) - so a pass
here means the whole path works, not just that the model attempts a tool call.

Start the proxy first: python finetune/tool_call_proxy.py --format xlam --port 8801

Mirrors this project's "isolated tool-call test, multiple trials" methodology used for every
other candidate.
"""
import json
import sys
import urllib.request

PROXY_URL = "http://localhost:8801/v1/chat/completions"

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get current weather for a city.",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delegate_tasks",
            "description": "Delegate one or more research tasks to specialist sub-agents.",
            "parameters": {
                "type": "object",
                "properties": {"tasks": {"type": "array", "items": {"type": "object"}}},
                "required": ["tasks"],
            },
        },
    },
]

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


def call_proxy(user_prompt: str, stream: bool) -> dict:
    payload = {
        "model": "xlam",
        "messages": [{"role": "user", "content": user_prompt}],
        "tools": TOOLS,
        "stream": stream,
        "temperature": 0.2,
    }
    req = urllib.request.Request(
        PROXY_URL,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        if not stream:
            return json.loads(resp.read())
        # Collapse SSE chunks into a single message shape for uniform assertions below.
        tool_calls_by_index: dict[int, dict] = {}
        content = None
        for line in resp:
            line = line.decode().strip()
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            chunk = json.loads(line[len("data: "):])
            delta = chunk["choices"][0]["delta"]
            if delta.get("content"):
                content = (content or "") + delta["content"]
            for tc in delta.get("tool_calls", []):
                tool_calls_by_index[tc["index"]] = tc
        message = {"role": "assistant", "content": content}
        if tool_calls_by_index:
            message["tool_calls"] = [tool_calls_by_index[i] for i in sorted(tool_calls_by_index)]
        return {"choices": [{"message": message}]}


def check_case(prompt: str, expected: str | None, stream: bool) -> bool:
    label = "stream" if stream else "non-stream"
    print(f"\n=== [{label}] PROMPT: {prompt!r} (expected tool: {expected}) ===")
    try:
        resp = call_proxy(prompt, stream)
    except Exception as e:
        print(f"REQUEST FAILED: {e}")
        return False
    message = resp["choices"][0]["message"]
    tool_calls = message.get("tool_calls") or []
    print(f"content={message.get('content')!r} tool_calls={tool_calls}")
    if expected is None:
        ok = not tool_calls
    else:
        ok = bool(tool_calls) and tool_calls[0]["function"]["name"] == expected
        if ok:
            # arguments MUST be a JSON string, not a dict - the real failure mode this proxy
            # exists to avoid (see tool_call_proxy.py docstring / OpenAI SDK Pydantic contract).
            args = tool_calls[0]["function"]["arguments"]
            ok = isinstance(args, str)
            json.loads(args)  # raises if not valid JSON, failing the test loudly
    print("PASS" if ok else "FAIL")
    return ok


if __name__ == "__main__":
    passed = 0
    total = 0
    for prompt, expected in CASES:
        for stream in (False, True):
            total += 1
            passed += check_case(prompt, expected, stream)
    print(f"\n{passed}/{total} passed")
    sys.exit(0 if passed == total else 1)
