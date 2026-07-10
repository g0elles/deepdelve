import warnings
warnings.filterwarnings("ignore", message=".*is experimental and may change.*")

from engine.sdk import AgentBuilder, SubAgentConfig
from tools import (
    read_workspace_file,
    write_workspace_file,
    list_workspace_files,
    grep_workspace_file,
    fetch_url_to_workspace,
    web_search,
    write_todos,
    read_todos,
    think_tool,
)
from prompts import (
    PLANNER_INSTRUCTIONS,
    WEB_SEARCHER_INSTRUCTIONS,
    ACADEMIC_SEARCHER_INSTRUCTIONS,
    DOCUMENT_ANALYZER_INSTRUCTIONS,
    DATA_ANALYZER_INSTRUCTIONS,
)
import config

# Delegation chain: Planner -> {WebSearcher, AcademicSearcher} -> {DocumentAnalyzer, DataAnalyzer}
# (strictly one direction). Leaf agents are defined first so they can be referenced by their
# parents' sub_agents=[...]. Same 3-tier depth as the reference project this was forked from, but
# tiers 2 and 3 are now small named panels instead of one monolithic agent each — see README
# "Architecture" for the reasoning (domain specialization, not fewer hops, is the reliability bet
# here).
# NOTE: Do NOT pre-format instructions here (e.g. PLANNER_INSTRUCTIONS.format(...)).
# The engine formats runtime variables like {date} or {task_name} dynamically at runtime.

# Tier 3 (leaf) — read/grep downloaded files only. No web tools, no delegation.
document_analyzer = SubAgentConfig(
    name="DocumentAnalyzer",
    instructions=DOCUMENT_ANALYZER_INSTRUCTIONS,
    tools=[read_workspace_file, grep_workspace_file, think_tool]
)

data_analyzer = SubAgentConfig(
    name="DataAnalyzer",
    instructions=DATA_ANALYZER_INSTRUCTIONS,
    tools=[read_workspace_file, grep_workspace_file, think_tool]
)

# Tier 2 — web search + fetch only. No file-reading tools, which forces delegation of analysis
# to the Tier 3 panel. Both specialists can dispatch to either Analyzer, chosen per fetched
# content type (prose vs. structured data — see each specialist's Delegation Routing block).
web_searcher = SubAgentConfig(
    name="WebSearcher",
    instructions=WEB_SEARCHER_INSTRUCTIONS,
    tools=[web_search, fetch_url_to_workspace, think_tool],
    sub_agents=[document_analyzer, data_analyzer]
)

academic_searcher = SubAgentConfig(
    name="AcademicSearcher",
    instructions=ACADEMIC_SEARCHER_INSTRUCTIONS,
    tools=[web_search, fetch_url_to_workspace, think_tool],
    sub_agents=[document_analyzer, data_analyzer]
)

# Tier 1 — plans, tracks todos, writes the final report. No web tools and no file-reading tools,
# which forces it to delegate all research to a Tier 2 specialist, routed by query type.
app = AgentBuilder(
    name=config.APP_TITLE,
    description=config.APP_DESCRIPTION,
    instructions=PLANNER_INSTRUCTIONS,
    tools=[write_workspace_file, list_workspace_files, write_todos, read_todos, think_tool],
    sub_agents=[web_searcher, academic_searcher]
)

def cli_main():
    app.start()

if __name__ == "__main__":
    cli_main()
