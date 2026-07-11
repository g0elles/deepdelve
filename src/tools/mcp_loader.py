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
        return MCPStreamableHTTPTool(name=name, url=url)

    command = spec.get("command")
    if not command:
        raise ValueError(f"mcp_servers entry '{name}' has transport=stdio but no 'command'")
    return MCPStdioTool(name=name, command=command, args=spec.get("args"), env=spec.get("env"))


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
