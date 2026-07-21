import sys
import warnings
warnings.filterwarnings("ignore", message=".*is experimental and may change.*")

# Windows consoles/pipes default to cp1252, which can't encode the banner/report Unicode.
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

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
    extract_structured_data,
    search_verified_findings,
)
from prompts import (
    PLANNER_INSTRUCTIONS,
    WEB_SEARCHER_INSTRUCTIONS,
    ACADEMIC_SEARCHER_INSTRUCTIONS,
    DOCUMENT_ANALYZER_INSTRUCTIONS,
    DATA_ANALYZER_INSTRUCTIONS,
    PEER_REVIEWER_INSTRUCTIONS,
    BUILDER_INSTRUCTIONS,
    FINDINGS_WRITER_INSTRUCTIONS,
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
    # extract_structured_data is the one tool DocumentAnalyzer does NOT have — a real, structural
    # (not just prompt-driven) distinction between the two Analyzer roles: table/code/JSON/CSV
    # extraction vs. prose reading.
    tools=[read_workspace_file, grep_workspace_file, extract_structured_data, think_tool]
)

# Tier 2 — web search + fetch only. No file-reading tools, which forces delegation of analysis
# to the Tier 3 panel. Both specialists can dispatch to either Analyzer, chosen per fetched
# content type (prose vs. structured data — see each specialist's Delegation Routing block).
web_searcher = SubAgentConfig(
    name="WebSearcher",
    instructions=WEB_SEARCHER_INSTRUCTIONS,
    tools=[web_search, fetch_url_to_workspace, think_tool, search_verified_findings],
    sub_agents=[document_analyzer, data_analyzer]
)

academic_searcher = SubAgentConfig(
    name="AcademicSearcher",
    instructions=ACADEMIC_SEARCHER_INSTRUCTIONS,
    tools=[web_search, fetch_url_to_workspace, think_tool, search_verified_findings],
    sub_agents=[document_analyzer, data_analyzer]
)

# Planner-tier delegate (not Tier 2/3) — an independent critique pass on findings.md, run by a
# fresh context rather than the same conversation that produced the findings. See
# PEER_REVIEWER_INSTRUCTIONS and PLANNER_INSTRUCTIONS Pass 2 for how it's used.
peer_reviewer = SubAgentConfig(
    name="PeerReviewer",
    instructions=PEER_REVIEWER_INSTRUCTIONS,
    tools=[read_workspace_file, grep_workspace_file, think_tool]
)

# Planner-tier delegate, NOT routed to by the Planner itself (it has no "Builder" agent_id in its
# Delegation Routing block — see PLANNER_INSTRUCTIONS). Dispatched exclusively by
# engine/completion.py's Write->Review->Fix loop, in a fresh context, after the Planner's own turn
# has ended with findings.md ready — this is what actually writes final_report.md now. Must be
# registered here (in the Planner's own sub_agents list) since completion.py's dispatch_task
# resolves agent_id against available_sub_agents_ctx, which is scoped to the Planner's own
# sub_agents at the point completion.py calls it. See PEER_REVIEWER_INSTRUCTIONS/BUILDER_INSTRUCTIONS
# header comments in prompts.py for the full loop description.
builder_agent = SubAgentConfig(
    name="Builder",
    instructions=BUILDER_INSTRUCTIONS,
    tools=[read_workspace_file, grep_workspace_file, write_workspace_file, think_tool]
)

# Planner-tier delegate, same non-routed pattern as builder_agent above (2026-07-14 architecture
# change). Writes `findings.md` from this run's REAL structured results
# (engine/completion.py::_build_findings_source_material, sourced from RunState — NOT the
# Planner's own conversation, which FindingsWriter never sees). Dispatched exclusively by
# engine/completion.py's Write->Review->Fix loop when missing_findings/findings_ungrounded fires.
# The Planner itself now has NO write_workspace_file tool at all (see `app` below) — it can only
# plan and delegate, structurally, the same way it's already structurally forced to delegate
# research by having no web_search/fetch_url_to_workspace. Motivated by a real live failure: giving
# the Planner the findings.md-writing job meant a findings.md retry grew the PLANNER's own
# conversation exactly the way Builder was invented to prevent for final_report.md — confirmed the
# same day a benchmark run hit 4 consecutive findings_ungrounded retries and exhausted its budget
# with nothing ever written.
findings_writer_agent = SubAgentConfig(
    name="FindingsWriter",
    instructions=FINDINGS_WRITER_INSTRUCTIONS,
    tools=[read_workspace_file, grep_workspace_file, write_workspace_file, think_tool]
)

# Tier 1 — plans, tracks todos, delegates all research. No web tools, no file-reading tools, and
# (as of 2026-07-14) no write_workspace_file either — the Planner's job is structurally limited to
# planning and delegation only. Both findings.md (FindingsWriter) and final_report.md (Builder)
# are produced by dedicated writer roles, dispatched by the completion-check system, never by the
# Planner itself — see findings_writer_agent/builder_agent above for why.
app = AgentBuilder(
    name=config.APP_TITLE,
    description=config.APP_DESCRIPTION,
    instructions=PLANNER_INSTRUCTIONS,
    tools=[list_workspace_files, write_todos, read_todos, think_tool],
    sub_agents=[web_searcher, academic_searcher, peer_reviewer, builder_agent, findings_writer_agent]
)

def cli_main():
    app.start()

if __name__ == "__main__":
    cli_main()
