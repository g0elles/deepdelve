"""MCP fetch capture (tools/mcp_loader.py::_wrap_capture_fetches): an opted-in fetch tool's article text lands in sources/ and its article URLs become recorded fetches; everything else passes through. Offline: a fake MCP tool."""
import asyncio, json, os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))
import config
from agent_framework import Content
from tools import fs
from tools.mcp_loader import _wrap_capture_fetches, _article_urls, _build_mcp_tool
from utils.run_state import fetched_urls_ctx

config.load_config()
config.cfg["settings"]["workspace"] = {"type": "memory", "dir": "."}
BODY = "## Full-Text Articles\n\nThe editing efficiency was high in this review of clinical translation. " * 20
DATA = {"articles": [{"pmcUrl": "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC1/", "pubmedUrl": "https://pubmed.ncbi.nlm.nih.gov/9/", "doi": "10.1234/abc",
                      "references": [{"doi": "10.9999/cited-only"}]}]}


class Fake:
    name = "pubmed"

    async def call_tool(self, tool_name, **kw):
        return [Content.from_text(text=BODY), Content.from_text(text=json.dumps(DATA))]


def reset():
    fs._IN_MEMORY_FS.clear()
    fetched_urls_ctx.set([])


# 1. only the article's own URL fields count, in order, doi as doi.org
urls = _article_urls({"pmcUrl": "https://a/1", "doi": "10.1/x", "other": "https://not-a-field"})
assert urls == ["https://a/1", "https://doi.org/10.1/x"], urls
# 2. an opted-in tool: text saved, URLs recorded as fetches, model gets a pointer not the text
reset()
t = _wrap_capture_fetches(Fake(), ["pubmed_fetch_fulltext"])
out = asyncio.run(t.call_tool("pubmed_fetch_fulltext", pmcids=["PMC1"]))
assert len(out) == 1 and "workspace file" in out[0].text and "editing efficiency" not in out[0].text, out[0].text
rec = [e["url"] for e in fetched_urls_ctx.get()]
assert "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC1/" in rec and "https://doi.org/10.1234/abc" in rec, rec
assert "https://doi.org/10.9999/cited-only" not in rec, rec  # a reference list entry was never fetched
assert any("editing efficiency" in v for v in fs._IN_MEMORY_FS.values())
# 3. a tool not in the list passes through untouched and records nothing
reset()
t = _wrap_capture_fetches(Fake(), ["pubmed_fetch_fulltext"])
out = asyncio.run(t.call_tool("pubmed_search_articles", query="x"))
assert len(out) == 2 and not fetched_urls_ctx.get() and not fs._IN_MEMORY_FS
# 4. an opted-in tool whose output names no article URL passes through, unrecorded
reset()
class NoUrl(Fake):
    async def call_tool(self, tool_name, **kw):
        return [Content.from_text(text=BODY), Content.from_text(text=json.dumps({"articles": []}))]
out = asyncio.run(_wrap_capture_fetches(NoUrl(), ["f"]).call_tool("f"))
assert len(out) == 2 and not fetched_urls_ctx.get()
# 5. the spec key reaches the loader (wrapped tool differs from the class method)
tool = _build_mcp_tool({"name": "pm", "transport": "stdio", "command": "true", "capture_fetches": ["f"]})
assert tool.call_tool.__wrapped__.__self__ is tool
print("test_mcp_capture: ok")

if __name__ == "__main__":
    pass
