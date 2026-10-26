"""settings.writer_mode == "compiled": _compile_pipeline_report writes findings.md and the required artifact from an evidence directory,
and is a no-op in the default "agents" mode. Offline: the model call is faked, the workspace is in memory."""
import json, os, sys, tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import config
from engine import tui
from tools import fs
from utils.run_state import RunState
import pipeline.llm as pl

config.load_config()
config.cfg["settings"]["workspace"] = {"type": "memory", "dir": "."}
EV = {"facets": [{"id": "f1", "name": "Traffic", "entity": "", "questions": ["q"]}],
      "evidence": {"f1": {"https://a.example/x": ["Traffic fell 16% in eight weeks."]}}, "titles": {"https://a.example/x": "Study A"}, "unanswered_facets": {}}
d = Path(tempfile.mkdtemp())
(d / "evidence.json").write_text(json.dumps(EV))
rs = RunState(d)
rs.set_query("Did traffic fall?")

config.cfg["settings"]["writer_mode"] = "agents"
assert tui._compile_pipeline_report(rs, str(d)) is None and not fs._IN_MEMORY_FS      # default: nothing written, no model call

pl.call_json = lambda model, prompt, schema, **kw: {"sentences": [{"cites": [1], "text": "Traffic fell 16% in eight weeks."}]}
config.cfg["settings"]["writer_mode"] = "compiled"
stats = tui._compile_pipeline_report(rs, str(d))
assert stats["units"] == 2 and stats.get("fallbacks", 0) == 0
files = {k.split("/")[-1]: v for k, v in fs._IN_MEMORY_FS.items()}
art = config.get_required_artifact() or "final_report.md"
assert art in files and "findings.md" in files, list(files)
from pipeline.bridge import source_filename
assert f"[Study A](https://a.example/x) (saved as {source_filename('https://a.example/x')})" in files["findings.md"] and "Traffic fell 16% in eight weeks.[1]" in files[art]
assert rs.data["compiled_writer"] == stats
print("test_writer_mode: ok")

if __name__ == "__main__":
    pass
