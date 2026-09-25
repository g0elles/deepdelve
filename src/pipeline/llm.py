import json
import urllib.request

OLLAMA_URL = "http://localhost:11434/api/chat"


def call_json(model: str, prompt: str, schema: dict, num_ctx: int = 16384, retries: int = 1, think=None, timeout: int = 90) -> dict:
    """Schema-constrained call. Uses `format` and NO `tools` (Ollama #13750 breaks the combo)."""
    body = {
        "model": model, "stream": False, "format": schema,
        "options": {"temperature": 0, "num_ctx": num_ctx},
        "messages": [{"role": "user", "content": prompt}],
    }
    if think is not None:
        body["think"] = think
    last = None
    for _ in range(retries + 1):
        req = urllib.request.Request(OLLAMA_URL, json.dumps(body).encode(), {"Content-Type": "application/json"})
        content = json.load(urllib.request.urlopen(req, timeout=timeout))["message"]["content"]
        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            last = e
    raise ValueError(f"invalid JSON after {retries + 1} tries: {last}")
