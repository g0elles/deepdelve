"""Raw /api/chat sweep of every think setting on the served model (CLAUDE.md rule: thinking controls are raw-tested, never assumed).
Settings: false (what the code sent until 2026-09-30), omitted (model default), low, medium, high. 3 reps x 2 prompts. Appends JSONL."""
import json, sys, time, urllib.request
MODEL = sys.argv[1] if len(sys.argv) > 1 else "deepdelve-gpt-oss:latest"
OUT = sys.argv[2] if len(sys.argv) > 2 else "eval/thinking_effort/raw_sweep.jsonl"
SETTINGS = [("false", False), ("omitted", None), ("low", "low"), ("medium", "medium"), ("high", "high")]
TOOL = {"type": "function", "function": {"name": "write_workspace_file", "description": "Write a text file to the workspace.",
        "parameters": {"type": "object", "required": ["filename", "content"], "properties": {
            "filename": {"type": "string"}, "content": {"type": "string"}}}}}
PROMPTS = {
    "prose": [{"role": "user", "content": "Write a 250-word summary of the EU AI Act's implementation timeline with the key dates."}],
    "tool": [{"role": "user", "content": "Save a 120-word note about the EU AI Act's high-risk obligations to the file notes.md using the tool."}],
}
def call(prompt, think):
    body = {"model": MODEL, "stream": False, "options": {"num_predict": 12000}, "messages": PROMPTS[prompt]}
    if prompt == "tool":
        body["tools"] = [TOOL]
    if think is not None:
        body["think"] = think
    t = time.time()
    r = json.load(urllib.request.urlopen(urllib.request.Request("http://localhost:11434/api/chat", json.dumps(body).encode(), {"Content-Type": "application/json"}), timeout=900))
    m = r["message"]; calls = m.get("tool_calls") or []
    ok = bool(calls) and calls[0]["function"]["name"] == "write_workspace_file" and calls[0]["function"]["arguments"].get("filename") == "notes.md" \
        and len((calls[0]["function"]["arguments"].get("content") or "").split()) >= 40
    return {"prompt": prompt, "wall_s": round(time.time() - t, 1), "eval_count": r.get("eval_count"), "thinking_chars": len(m.get("thinking") or ""),
            "content_words": len((m.get("content") or "").split()), "tool_ok": ok if prompt == "tool" else None, "done": r.get("done_reason")}
with open(OUT, "a") as f:
    for rep in range(3):
        for label, val in SETTINGS:
            for prompt in PROMPTS:
                try: row = call(prompt, val)
                except Exception as e: row = {"prompt": prompt, "error": str(e)[:200]}
                row.update(setting=label, rep=rep, model=MODEL); f.write(json.dumps(row) + "\n"); f.flush()
                print(label, rep, row, flush=True)
print("SWEEP DONE", flush=True)
