"""utils/thinking.py: the think value is derived from what Ollama reports for the model. Offline: the /api/show call is faked."""
import os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from utils import thinking as th

SHOWS = {  # payload shapes observed on Ollama 0.40.9, 2026-10-25
    "levels": {"capabilities": ["completion", "tools", "thinking"], "thinking": {"values": ["high", "low", "medium"], "default": "medium"}},
    "bool_listed": {"capabilities": ["completion", "thinking"], "thinking": {"values": [False, True], "default": True}},
    "bool_bare": {"capabilities": ["tools", "thinking", "completion"]},
    "none": {"capabilities": ["completion", "tools"]},
}
def show_for(name):
    return lambda model, base: SHOWS[name]

th._cache.clear()
assert th.minimal_think("a", show=show_for("levels")) == "low"          # level model has no off: lowest level
assert th.minimal_think("b", show=show_for("bool_listed")) is False
assert th.minimal_think("c", show=show_for("bool_bare")) is False       # capability without values: boolean (ornith)
assert th.minimal_think("d", show=show_for("none")) is None             # no thinking control: send nothing
def down(model, base): raise OSError("refused")
assert th.minimal_think("e", show=down) is None                         # server unreachable: send nothing, do not crash
# the guard: a level on a boolean model is an error, a level model without effort only warns
th.check_setting("c", None, show=show_for("bool_bare"))
th.check_setting("a", "low", show=show_for("levels"))
th.check_setting("a", None, show=show_for("levels"))
try:
    th.check_setting("c", "low", show=show_for("bool_bare")); raise SystemExit("expected ValueError")
except ValueError as e:
    assert "boolean" in str(e)
# pipeline: the intent string is resolved, an explicit value passes through
from pipeline import llm
sent = []
class R:
    def __init__(s, body): s.b = body
    def read(s): return b'{"message": {"content": "{}"}}'
    def __enter__(s): return s
    def __exit__(s, *a): pass
import urllib.request
llm.urllib.request.urlopen = lambda req, timeout=0: (sent.append(__import__("json").loads(req.data)), R(None))[1]
th._cache.clear(); th._cache[("http://localhost:11434", "m")] = ("bool", [])
llm.call_json("m", "p", {}, think="minimal"); assert sent[-1]["think"] is False
llm.call_json("m", "p", {}, think="high"); assert sent[-1]["think"] == "high"
llm.call_json("m", "p", {}); assert "think" not in sent[-1]
print("test_thinking: ok")

if __name__ == "__main__":
    pass
