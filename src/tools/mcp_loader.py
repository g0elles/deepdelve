import functools

import config as app_config

# -------------------------------------------------------------
# Generic MCP tool loader — deliberately does NOT bundle any bespoke academic/search API client.
# agent_framework already ships real MCP client support (MCPStdioTool for a local server process,
# MCPStreamableHTTPTool for a remote HTTP/SSE server); this just wires settings.mcp_servers entries
# into whichever sub-agent they're scoped to, via engine/orchestrator.py's _run_single_task.
#
# Researched, concretely recommended servers to point this at (see config_template.yaml for
# ready-to-uncomment examples) instead of building custom API integrations from scratch:
#   - Semantic Scholar MCP (academic papers/citations/authors, 200M+ papers, no API key required
#     for the public tier) — a better fit for AcademicSearcher than a bespoke OpenAlex tool.
#   - Brave Search MCP (a real search index instead of DDGS scraping) — optional upgrade path for
#     WebSearcher, requires a free-tier Brave Search API key.
# Neither is enabled by default — installing/connecting an external MCP server is a real side
# effect (subprocess spawn, possibly an API key) that should be an explicit opt-in via config.
# -------------------------------------------------------------


# @brave/brave-search-mcp-server's `country` param is a fixed zod enum (confirmed live 2026-07-18,
# read from the installed package's dist/tools/web/params.js) that does NOT include every ISO
# alpha-2 code a model might reasonably emit for a country-scoped query — 'CO' (Colombia) is a
# confirmed real gap, not a hypothetical one: it broke every Colombia-targeted search in two live
# benchmark runs outright (MCP error -32602, "Invalid value for 'country' ... 'CO' is not in
# [...]"). Full valid list per that source: ALL, AR, AU, AT, BE, BR, CA, CL, DK, FI, FR, DE, HK,
# IN, ID, IT, JP, KR, MY, MX, NL, NZ, NO, CN, PL, PT, PH, RU, SA, ZA, ES, SE, CH, TW, TR, GB, US.
# Rather than hand-maintain a full ISO->supported-enum remap (fragile against upstream schema
# changes, and most codes we'd want ARE covered), just drop unsupported values so the search still
# runs unscoped instead of failing outright — a global/unscoped Brave search is a strictly better
# outcome than a hard tool-call rejection that burns the model's turn for nothing.
_BRAVE_SEARCH_COUNTRY_ENUM = frozenset({
    "ALL", "AR", "AU", "AT", "BE", "BR", "CA", "CL", "DK", "FI", "FR", "DE", "HK", "IN", "ID", "IT",
    "JP", "KR", "MY", "MX", "NL", "NZ", "NO", "CN", "PL", "PT", "PH", "RU", "SA", "ZA", "ES", "SE",
    "CH", "TW", "TR", "GB", "US",
})


def _wrap_brave_search_tool(tool):
    """Sanitizes arguments before they reach the Brave MCP subprocess's own zod validation — see
    _BRAVE_SEARCH_COUNTRY_ENUM above for why. Every model-invoked call to any function this MCP
    server advertises funnels through `MCPTool.call_tool(tool_name, **kwargs)` (confirmed by
    reading agent_framework's own `_mcp.py`: `FunctionTool`'s per-tool wrapper always calls
    `self.call_tool(_remote_tool_name, **call_kwargs)`), so wrapping `call_tool` itself catches
    every Brave function in one place, not just `brave_web_search`."""
    original_call_tool = tool.call_tool

    @functools.wraps(original_call_tool)
    async def call_tool(tool_name, **kwargs):
        country = kwargs.get("country")
        if country and str(country).upper() not in _BRAVE_SEARCH_COUNTRY_ENUM:
            kwargs = {k: v for k, v in kwargs.items() if k != "country"}
        return await original_call_tool(tool_name, **kwargs)

    tool.call_tool = call_tool
    return tool


def _build_mcp_tool(spec: dict):
    from agent_framework import MCPStdioTool, MCPStreamableHTTPTool

    name = spec.get("name")
    if not name:
        raise ValueError(f"mcp_servers entry missing required 'name': {spec}")

    transport = spec.get("transport", "stdio")
    if transport == "http":
        url = spec.get("url")
        if not url:
            raise ValueError(f"mcp_servers entry '{name}' has transport=http but no 'url'")
        tool = MCPStreamableHTTPTool(name=name, url=url)
    else:
        command = spec.get("command")
        if not command:
            raise ValueError(f"mcp_servers entry '{name}' has transport=stdio but no 'command'")
        tool = MCPStdioTool(name=name, command=command, args=spec.get("args"), env=spec.get("env"))

    if "brave" in name.lower():
        tool = _wrap_brave_search_tool(tool)
    return tool


def get_mcp_specs_for_agent(agent_name: str) -> list[dict]:
    """Returns settings.mcp_servers entries scoped to this sub-agent name via each entry's own
    'agents' allowlist, or entries with no 'agents' key (available to every sub-agent)."""
    specs = app_config.cfg.get("settings", {}).get("mcp_servers", []) or []
    return [s for s in specs if not s.get("agents") or agent_name in s["agents"]]


def build_mcp_tools_for_agent(agent_name: str) -> list:
    """Instantiate (but do not connect) every MCP tool configured for this sub-agent. Connection
    lifecycle (connect/close) is the caller's responsibility — see _run_single_task's AsyncExitStack
    usage, which connects them for exactly the duration of that one delegated task."""
    return [_build_mcp_tool(s) for s in get_mcp_specs_for_agent(agent_name)]
