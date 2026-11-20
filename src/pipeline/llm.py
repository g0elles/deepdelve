import json
import urllib.request

from utils.thinking import minimal_think

OLLAMA_URL = "http://localhost:11434/api/chat"


def model_options(model: str) -> dict:
    """settings.model_options[model]: that model's Ollama options (num_ctx, temperature, top_p, top_k, min_p, presence_penalty,
    repeat_penalty), from its card, at the largest num_ctx that loads 100% on GPU. Matches with or without ':latest'."""
    import config
    table = config.cfg.get("settings", {}).get("model_options") or {}
    for name in (model, model.removesuffix(":latest"), model if ":" in model else model + ":latest"):
        if name in table:
            return dict(table[name] or {})
    return {}


def call_json(model: str, prompt: str, schema: dict, num_ctx: int = 16384, retries: int = 1, think=None, timeout: int = 90, temperature: float = 0, seed: int | None = None) -> dict:
    """Schema-constrained call. Uses `format` and NO `tools` (Ollama #13750 breaks the combo).
    A model listed in settings.model_options runs at ITS options; the num_ctx/temperature arguments only apply to unlisted models."""
    body = {
        "model": model, "stream": False, "format": schema,
        "options": {"temperature": temperature, "num_ctx": num_ctx, **model_options(model), **({"seed": seed} if seed is not None else {})},
        "messages": [{"role": "user", "content": prompt}],
    }
    if think == "minimal":  # intent, resolved to this model's own control (levels, boolean, or none): utils/thinking.py
        think = minimal_think(model)
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
