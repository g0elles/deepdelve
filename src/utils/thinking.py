"""Which kind of thinking control an Ollama model has, asked of the model instead of assumed.

Ollama's /api/show lists `thinking.values` for some models (gpt-oss: low/medium/high; gemma4: false/true) and only the bare
`thinking` capability for others (ornith, granite). Level models have no off mode; boolean models take false. A value of the
wrong kind is accepted silently (ornith with "low" turns thinking ON), so callers state an intent and this module picks the value.
"""
import json
import logging
import urllib.request

log = logging.getLogger(__name__)
_LEVEL_ORDER = ("low", "medium", "high")
_cache: dict = {}


def _show(model: str, base: str) -> dict:
    req = urllib.request.Request(f"{base}/api/show", json.dumps({"model": model}).encode(), {"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=10))


def think_kind(model: str, base: str = "http://localhost:11434", show=_show):
    """'levels', 'bool', or None (no thinking control, or the server could not be asked)."""
    key = (base, model)
    if key not in _cache:
        try:
            d = show(model, base)
        except Exception as e:  # server down / not Ollama: callers fall back to sending nothing
            log.warning("thinking control of %s unknown (%s)", model, e)
            return None
        vals = (d.get("thinking") or {}).get("values")
        if vals and any(isinstance(v, str) for v in vals):
            _cache[key] = ("levels", [v for v in _LEVEL_ORDER if v in vals])
        elif "thinking" in (d.get("capabilities") or []):
            _cache[key] = ("bool", [])  # capability without listed values: boolean (raw-tested for ornith-1.5:9b)
        else:
            _cache[key] = (None, [])
    return _cache[key][0]


def minimal_think(model: str, base: str = "http://localhost:11434", show=_show):
    """The `think` value for the least reasoning the model allows: lowest level, False, or None to send nothing."""
    kind = think_kind(model, base, show)
    if kind == "levels":
        return _cache[(base, model)][1][0]
    return False if kind == "bool" else None


def check_setting(model: str, effort, base: str = "http://localhost:11434", show=_show):
    """Loud mismatch guard for settings.thinking_effort: a named level on a boolean model silently turns thinking on."""
    kind = think_kind(model, base, show)
    if effort and kind == "bool":
        raise ValueError(f"settings.thinking_effort={effort!r} but {model} has a boolean thinking control: a level turns thinking ON. Unset it and use enable_thinking.")
    if not effort and kind == "levels":
        log.warning("%s has level-based thinking (no off mode): enable_thinking=false sends no reasoning instruction; set settings.thinking_effort", model)
