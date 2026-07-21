"""
OpenAI-compatible tool-calling proxy in front of MiniCPM4-MCP.

Why this exists: MiniCPM4-MCP's own chat template (embedded in its GGUF, confirmed by reading
it directly) does NOT emit OpenAI-style JSON `tool_calls` - it emits a Python-code-block format
(`<|thought_start|>...<|thought_end|><|tool_call_start|>func(arg=val)<|tool_call_end|>`). Ollama's
generic /v1/chat/completions `tools=` support assumes OpenAI JSON tool_calls and fails outright
against this model ("peg-native format" error, confirmed live 2026-07-20). This proxy sits between
DeepDelve's agent_framework_openai client and Ollama, translating in both directions using the
model's OWN documented format (not a generic JSON-prompting shim like most "small model function
calling" proxies use - see finetune/minicpm_tool_test.py for the isolated single-turn version this
was built up from, and the same file's docstring for the format reference).

Handles full multi-turn history (prior assistant tool_calls + tool-result messages), which the
isolated smoke test did not need. Supports both streaming and non-streaming, since DeepDelve's
engine (agent_framework_openai/_chat_completion_client.py) uses streaming by default.

Run standalone: `python finetune/minicpm_tool_proxy.py --port 8800`
Point settings.specialist_base_url at http://localhost:8800/v1 and settings.specialist_model at
"minicpm4-mcp" (the local Ollama tag) to route DeepDelve's specialist roles through it.
"""
import argparse
import ast
import json
import keyword
import re
import time
import uuid

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "minicpm4-mcp"

TOOL_CALL_RE = re.compile(
    r"<\|tool_call_start\|>\s*```python\s*(.*?)```\s*<\|tool_call_end\|>", re.DOTALL
)
THOUGHT_RE = re.compile(r"<\|thought_start\|>\s*(.*?)\s*<\|thought_end\|>", re.DOTALL)


def _resolve_ast_value(value):
    """Ported from OpenBMB's own generate_example.py (parse_tool_for_minicpm3's
    resolve_ast_by_type) - handles more AST node shapes than a plain ast.literal_eval on the
    keyword value (lists/dicts of non-literals, unary ops, nested calls-as-string)."""
    if isinstance(value, ast.Constant):
        return "..." if value.value is Ellipsis else value.value
    if isinstance(value, ast.UnaryOp):
        return -value.operand.value
    if isinstance(value, ast.List):
        return [_resolve_ast_value(v) for v in value.elts]
    if isinstance(value, ast.Dict):
        return {_resolve_ast_value(k): _resolve_ast_value(v) for k, v in zip(value.keys, value.values)}
    if isinstance(value, ast.Tuple):
        return tuple(_resolve_ast_value(v) for v in value.elts)
    if isinstance(value, ast.Name):
        return value.id
    try:
        return ast.literal_eval(value)
    except Exception:
        return ast.unparse(value)


def parse_tool_call_block(tool_call_string: str) -> list[dict]:
    """Parses the Python-code-block content between <|tool_call_start|> and <|tool_call_end|>.
    Ported from OpenBMB's own generate_example.py (parse_tool_for_minicpm3) rather than the
    simpler regex+literal_eval this proxy used before - that version silently dropped calls with
    Python-keyword-colliding argument names (e.g. a tool with a `class` or `from` parameter) or
    hyphenated tool/argument names (common in real MCP tool names, not valid Python identifiers,
    which the model encodes with underscores per its own training convention)."""
    tool_call_string = tool_call_string.strip()
    if tool_call_string.startswith("```"):
        tool_call_string = tool_call_string[3:].strip()
        if tool_call_string.startswith("python"):
            tool_call_string = tool_call_string[len("python") :].strip()
    if tool_call_string.endswith("```"):
        tool_call_string = tool_call_string[:-3].strip()

    for kw in keyword.kwlist:
        tool_call_string = tool_call_string.replace("," + kw + "=", "," + kw + "_=")
        tool_call_string = tool_call_string.replace(" " + kw + "=", " " + kw + "_=")
        tool_call_string = tool_call_string.replace("(" + kw + "=", "(" + kw + "_=")
    replaced = tool_call_string.replace("-", "_")
    need_unhyphenate = replaced != tool_call_string
    tool_call_string = replaced

    tool_calls = []
    try:
        parsed = ast.parse(tool_call_string)
    except SyntaxError:
        return tool_calls
    for stmt in parsed.body:
        if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)):
            continue
        call = stmt.value
        if not isinstance(call.func, ast.Name):
            continue
        name = call.func.id
        args = {}
        for kw_node in call.keywords:
            k = kw_node.arg
            for py_kw in keyword.kwlist:
                if k == py_kw + "_":
                    k = py_kw
            args[k] = _resolve_ast_value(kw_node.value)
        if need_unhyphenate:
            name = name.replace("_", "-")
        tool_calls.append(
            {
                "id": f"call_{uuid.uuid4().hex[:12]}",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)},
            }
        )
    return tool_calls

app = FastAPI(title="MiniCPM4-MCP tool-calling proxy")


def _json_schema_type_to_py(prop: dict) -> str:
    if "enum" in prop:
        return " | ".join(json.dumps(v) for v in prop["enum"])
    return {"string": "str", "number": "float", "integer": "int", "boolean": "bool"}.get(
        prop.get("type", "string"), "Any"
    )


def build_functions_preamble(tools: list[dict]) -> str:
    """Mirrors MiniCPM4-MCP's own embedded jinja template's "# Functions" block exactly (lifted
    from the GGUF's chat_template metadata), so the model sees the same prompt shape it was
    fine-tuned on rather than a generic paraphrase."""
    defs = []
    for t in tools:
        fn = t["function"] if t.get("type") == "function" else t
        name = fn["name"]
        params = fn.get("parameters", {}) or {}
        props = params.get("properties", {})
        required = set(params.get("required", []))
        sig_parts = []
        for pname, pspec in props.items():
            ann = _json_schema_type_to_py(pspec)
            if pname in required:
                sig_parts.append(f"{pname}: {ann}")
            else:
                sig_parts.append(f"{pname}: Optional[{ann}] = None")
        sig = ", ".join(sig_parts)
        doc_lines = [fn.get("description", "").strip()]
        if props:
            doc_lines.append("\nArgs:")
            for pname, pspec in props.items():
                doc_lines.append(f"{pname}: {pspec.get('description', '')}".rstrip())
        doc = "\n    ".join(doc_lines)
        defs.append(f'def {name}({sig}):\n    """{doc}\n    """')
    functions_block = "\n\n".join(defs)
    return f"""
# Functions
Here is a list of functions that you can invoke:
```python
from enum import Enum
from typing import List, Dict, Optional
from pydantic import BaseModel, Field

{functions_block}
```

# Function Call Rule and Output Format
- If the user's question can be answered without calling any function, please answer the user's question directly. In this situation, you should return your thought and answer the user's question directly.
- If the user cannot be answered without calling any function, and the user does not provide enough information to call functions, please ask the user for more information. In this situation, you should return your thought and ask the user for more information.
- If the user's question cannot be answered without calling any function, and the user has provided enough information to call functions to solve it, you should call the functions. In this situation, the assistant should return your thought and call the functions.
- Use default parameters unless the user has specified otherwise.
- You MUST consider previous tool_calls and tool responses when deciding what to do next. Use this history to avoid redundant or circular behavior.
- If a tool returns an error OR fails to provide useful or new information (e.g., empty results, no content, or repeated output), DO NOT call it again with the same inputs. Avoid repeating the same failed tool calls. If a tool fails, try alternative tools or arguments if available.
- If ALL relevant tools have been tried and none provide helpful results, you may gracefully conclude with a best-effort response, acknowledging that tools did not yield a definitive answer, instead of repeating the same failed call.
- You should answer in the following format:

<|thought_start|>
{{explain why the user's question can be answered without calling a function or why you should ask the user for more information or why you should call one or more functions and your plan to solve the user's question.}}
<|thought_end|>
<|tool_call_start|>
```python
func1(params_name=params_value, params_name2=params_value2...)
func2(params)
```
<|tool_call_end|>
{{answer the user's question directly or ask the user for more information}}
""".strip()


def render_prompt(messages: list[dict], tools: list[dict] | None) -> str:
    """Manually renders the full conversation using MiniCPM4-MCP's own turn format (rather than
    relying on Ollama's /api/chat + our simplified Modelfile TEMPLATE, which only supports single
    System/Prompt/Response turns - see ROADMAP.md/session notes on why the full jinja macro
    couldn't be loaded into Ollama directly). Sent to Ollama's /api/generate with raw=True so
    Ollama does zero templating of its own; this function is the only source of truth for the
    prompt shape."""
    system_content = ""
    rest = messages
    if messages and messages[0]["role"] == "system":
        system_content = messages[0]["content"] or ""
        rest = messages[1:]

    parts = []
    if system_content or tools:
        block = system_content
        if tools:
            block = (block + "\n\n" if block else "") + build_functions_preamble(tools)
        parts.append(f"<|im_start|>system\n{block}<|im_end|>\n")

    # tool_call_id -> function name, so a later tool-result message can be rendered with context
    # (MiniCPM's template has no dedicated "tool" branch beyond the generic role passthrough, so
    # we fold the calling function's name into the result text for the model's benefit).
    call_id_to_name = {}
    for msg in rest:
        role = msg["role"]
        content = msg.get("content") or ""
        if role == "assistant" and msg.get("tool_calls"):
            thought = msg.get("_thought") or "Calling the required function(s)."
            call_lines = []
            for tc in msg["tool_calls"]:
                fn = tc["function"]
                call_id_to_name[tc.get("id", "")] = fn["name"]
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                argstr = ", ".join(f"{k}={json.dumps(v)}" for k, v in args.items())
                call_lines.append(f"{fn['name']}({argstr})")
            parts.append(
                f"<|im_start|>assistant\n<|thought_start|>\n{thought}\n<|thought_end|>\n"
                f"<|tool_call_start|>\n```python\n" + "\n".join(call_lines) + "\n```\n"
                f"<|tool_call_end|>\n{content}<|im_end|>\n"
            )
        elif role == "tool":
            fn_name = call_id_to_name.get(msg.get("tool_call_id", ""), "tool")
            parts.append(f"<|im_start|>tool\nResult of {fn_name}:\n{content}<|im_end|>\n")
        else:
            parts.append(f"<|im_start|>{role}\n{content}<|im_end|>\n")

    parts.append("<|im_start|>assistant\n")
    return "".join(parts)


def parse_model_output(raw: str) -> tuple[str | None, list[dict]]:
    """Returns (content_text_or_None, tool_calls). tool_calls is [] if the model answered
    directly instead of calling a function (a valid, expected outcome per the model's own
    output-format rules, not a parse failure)."""
    tool_calls = []
    m = TOOL_CALL_RE.search(raw)
    remainder = raw
    if m:
        remainder = raw[: m.start()] + raw[m.end() :]
        tool_calls = parse_tool_call_block(m.group(1))
    # Strip the thought block and tool-call markup from what we surface as message content.
    remainder = THOUGHT_RE.sub("", remainder).strip()
    content = remainder or None
    return content, tool_calls


async def call_ollama(prompt: str, temperature: float) -> str:
    async with httpx.AsyncClient(timeout=600) as client:
        resp = await client.post(
            OLLAMA_URL,
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "raw": True,
                "stream": False,
                "options": {"temperature": temperature},
            },
        )
        resp.raise_for_status()
        return resp.json()["response"]


def build_chat_completion(model: str, content: str | None, tool_calls: list[dict]) -> dict:
    message = {"role": "assistant", "content": content}
    finish_reason = "stop"
    if tool_calls:
        message["tool_calls"] = tool_calls
        finish_reason = "tool_calls"
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def build_stream_chunks(model: str, content: str | None, tool_calls: list[dict]):
    chunk_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    created = int(time.time())
    base = {"id": chunk_id, "object": "chat.completion.chunk", "created": created, "model": model}
    delta = {"role": "assistant"}
    if content:
        delta["content"] = content
    if tool_calls:
        delta["tool_calls"] = [
            {
                "index": i,
                "id": tc["id"],
                "type": "function",
                "function": tc["function"],
            }
            for i, tc in enumerate(tool_calls)
        ]
    yield {**base, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]}
    finish_reason = "tool_calls" if tool_calls else "stop"
    yield {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}]}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    messages = body.get("messages", [])
    tools = body.get("tools")
    temperature = body.get("temperature", 0.2)
    model = body.get("model", OLLAMA_MODEL)
    stream = bool(body.get("stream", False))

    prompt = render_prompt(messages, tools)
    raw = await call_ollama(prompt, temperature)
    content, tool_calls = parse_model_output(raw)

    if stream:
        async def gen():
            for chunk in build_stream_chunks(model, content, tool_calls):
                yield f"data: {json.dumps(chunk)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    return JSONResponse(content=build_chat_completion(model, content, tool_calls))


@app.get("/v1/models")
async def list_models():
    return JSONResponse(
        content={"object": "list", "data": [{"id": OLLAMA_MODEL, "object": "model", "owned_by": "ollama"}]}
    )


@app.get("/health")
async def health():
    return JSONResponse(content={"status": "healthy"})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8800)
    args = parser.parse_args()
    uvicorn.run(app, host="127.0.0.1", port=args.port)
