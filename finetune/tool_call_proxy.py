"""
Generic OpenAI-compatible tool-calling proxy in front of Ollama, for models whose GGUF chat
template attempts a tool call but doesn't populate OpenAI's `tool_calls` response field - it just
prints its own format as plain `content` text. Ollama renders the prompt fine for these models
(confirmed per-model before use); the gap is purely on the output-extraction side.

Generalized over finetune/minicpm_tool_proxy.py's single-model shape: this project has hit this
same class of problem more than once (MiniCPM4-MCP's Python-code-block format, now xLAM's plain
JSON array) and will likely hit it again, so the server boilerplate (FastAPI app, streaming,
/health, /v1/models) is shared and each model's parsing logic is a small function registered in
FORMAT_PARSERS, selected via --format at startup. MiniCPM's own format is intentionally NOT ported
in here - that candidate is closed/disqualified, not queued for a retest; minicpm_tool_proxy.py
stays as its own file. Add the next oddball model by writing one parse function and one registry
entry, not a new server.

DeepDelve's engine (agent_framework_openai/_chat_completion_client.py) calls
`sub_agent.run(..., stream=True)` unconditionally (src/engine/orchestrator.py:1217, confirmed by
reading it directly) - there is no non-streaming mode in practice, so this proxy MUST implement the
streaming SSE path, unlike a "build it later if needed" corner.

Run standalone: `python finetune/tool_call_proxy.py --format xlam --port 8801
    --ollama-model robbiemu/Salesforce_Llama-xLAM-2:8b-fc-r-q8_0`
Point api.base_url at http://localhost:8801/v1 (primary-model candidate - NOT
settings.specialist_base_url, which only overrides search/analysis sub-roles and would leave the
Planner on the untouched model).
"""
import argparse
import json
import re
import time
import uuid
from typing import Callable, Optional

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

OLLAMA_URL = "http://localhost:11434/v1/chat/completions"
OLLAMA_MODEL = "robbiemu/Salesforce_Llama-xLAM-2:8b-fc-r-q8_0"
FORMAT = "xlam"


# --- Per-format parsers -------------------------------------------------------------------
# Each entry: content: str -> (content_or_None, tool_calls). tool_calls is [] when the model
# answered directly instead of calling a function (a valid outcome, not a parse failure).

_XLAM_WRAPPERS = [
    re.compile(r"```(?:json)?\s*([\s\S]*?)```"),
    re.compile(r"\[TOOL_CALLS\]([\s\S]*?)(?=\n|$)"),
    re.compile(r"<tool_call>([\s\S]*?)</tool_call>"),
]
_XLAM_THINK_RE = re.compile(r"</think>([\s\S]*)")


def _xlam_extract_json_array(text: str) -> Optional[str]:
    """Mirrors vLLM's own xLAMToolParser.preprocess_model_output: try a </think> suffix, then
    each wrapper pattern, then a bare leading '['. Returns the raw JSON-array substring, or None
    if nothing in `text` looks like a tool-call array."""
    think_match = _XLAM_THINK_RE.search(text)
    candidates = [think_match.group(1).strip()] if think_match else []
    for pattern in _XLAM_WRAPPERS:
        candidates.extend(m.strip() for m in pattern.findall(text))
    candidates.append(text.strip())
    for candidate in candidates:
        if not candidate.startswith("["):
            continue
        try:
            json.loads(candidate)
        except json.JSONDecodeError:
            continue
        return candidate
    return None


def parse_xlam(content: str) -> tuple[Optional[str], list[dict]]:
    array_text = _xlam_extract_json_array(content)
    if array_text is None:
        return content, []
    try:
        items = json.loads(array_text)
    except json.JSONDecodeError:
        return content, []
    if not isinstance(items, list):
        return content, []
    tool_calls = []
    for item in items:
        if not isinstance(item, dict) or "name" not in item or "arguments" not in item:
            continue
        tool_calls.append(
            {
                "id": f"call_{uuid.uuid4().hex[:12]}",
                "type": "function",
                "function": {"name": item["name"], "arguments": json.dumps(item["arguments"])},
            }
        )
    if not tool_calls:
        return content, []
    remainder = content.replace(array_text, "").strip()
    return (remainder or None), tool_calls


FORMAT_PARSERS: dict[str, Callable[[str], tuple[Optional[str], list[dict]]]] = {
    "xlam": parse_xlam,
}

# Formats that need Ollama's raw completion endpoint with a manually-rendered prompt (like
# minicpm_tool_proxy.py's render_prompt) instead of straight passthrough to /v1/chat/completions.
# Empty for now - xLAM's tool-call attempt already works through Ollama's standard chat template.
NEEDS_RAW_RENDER: set[str] = set()


app = FastAPI(title="Generic tool-calling proxy")


async def call_ollama_chat(body: dict) -> dict:
    async with httpx.AsyncClient(timeout=600) as client:
        resp = await client.post(OLLAMA_URL, json=body)
        resp.raise_for_status()
        return resp.json()


def build_chat_completion(model: str, content: Optional[str], tool_calls: list[dict], base: dict) -> dict:
    message = {"role": "assistant", "content": content}
    finish_reason = "stop"
    if tool_calls:
        message["tool_calls"] = tool_calls
        finish_reason = "tool_calls"
    return {
        "id": base.get("id", f"chatcmpl-{uuid.uuid4().hex[:24]}"),
        "object": "chat.completion",
        "created": base.get("created", int(time.time())),
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": base.get("usage", {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}),
    }


def build_stream_chunks(model: str, content: Optional[str], tool_calls: list[dict]):
    chunk_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    created = int(time.time())
    base = {"id": chunk_id, "object": "chat.completion.chunk", "created": created, "model": model}
    delta = {"role": "assistant"}
    if content:
        delta["content"] = content
    if tool_calls:
        delta["tool_calls"] = [
            {"index": i, "id": tc["id"], "type": "function", "function": tc["function"]}
            for i, tc in enumerate(tool_calls)
        ]
    yield {**base, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]}
    finish_reason = "tool_calls" if tool_calls else "stop"
    yield {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}]}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    stream = bool(body.get("stream", False))
    model = body.get("model", OLLAMA_MODEL)

    if FORMAT in NEEDS_RAW_RENDER:
        raise NotImplementedError(
            f"format '{FORMAT}' is registered under NEEDS_RAW_RENDER but no raw-render path is "
            "implemented yet - port minicpm_tool_proxy.py's render_prompt/call_ollama pattern."
        )

    upstream_body = dict(body)
    upstream_body["model"] = OLLAMA_MODEL
    upstream_body["stream"] = False
    upstream = await call_ollama_chat(upstream_body)
    raw_content = upstream["choices"][0]["message"].get("content") or ""
    content, tool_calls = FORMAT_PARSERS[FORMAT](raw_content)

    if stream:
        async def gen():
            for chunk in build_stream_chunks(model, content, tool_calls):
                yield f"data: {json.dumps(chunk)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    return JSONResponse(content=build_chat_completion(model, content, tool_calls, upstream))


@app.get("/v1/models")
async def list_models():
    return JSONResponse(
        content={"object": "list", "data": [{"id": OLLAMA_MODEL, "object": "model", "owned_by": "ollama"}]}
    )


@app.get("/health")
async def health():
    return JSONResponse(content={"status": "healthy", "format": FORMAT, "ollama_model": OLLAMA_MODEL})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8801)
    parser.add_argument("--format", choices=sorted(FORMAT_PARSERS), default="xlam")
    parser.add_argument("--ollama-model", default=OLLAMA_MODEL)
    parser.add_argument("--ollama-url", default=OLLAMA_URL)
    args = parser.parse_args()
    FORMAT = args.format
    OLLAMA_MODEL = args.ollama_model
    OLLAMA_URL = args.ollama_url
    uvicorn.run(app, host="127.0.0.1", port=args.port)
