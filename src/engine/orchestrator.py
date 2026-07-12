import os
import asyncio
import re
from agent_framework.openai import OpenAIChatCompletionClient
from agent_framework import tool, AgentSession
from tools import WORKSPACE_TOOLS, tool_quotas_ctx, with_quota, think_tool, QuotaAbortException
from utils.run_state import run_state_ctx, get_fetched_urls, task_fetched_urls_ctx, scope_entities_ctx
from prompts import PLANNER_INSTRUCTIONS, SUBAGENT_INSTRUCTIONS, SUBAGENT_DELEGATION_INSTRUCTIONS
import datetime
import config
import contextvars

# Module-level session for conversational memory persistence
_session = None
delegation_depth_ctx = contextvars.ContextVar('delegation_depth_ctx', default=0)
available_sub_agents_ctx = contextvars.ContextVar('available_sub_agents_ctx', default=[])

def apply_tool_permissions(tools: list) -> list:
    """Dynamically applies approval boundaries mapped in config.yaml."""
    perms = config.cfg.get("settings", {}).get("permissions", {})
    for t in tools:
        if hasattr(t, "name") and hasattr(t, "approval_mode"):
            if perms.get(t.name) == "require_approval":
                t.approval_mode = "always_require"
            else:
                t.approval_mode = "never_require"
    return tools

def _sanitize_name(name: str) -> str:
    """Ensure the name matches ^[a-zA-Z0-9_-]+$ for OpenAI API."""
    return re.sub(r'[^a-zA-Z0-9_-]', '_', name)

_SCOPE_STOPWORDS = {
    # Common sentence-initial instruction verbs — without filtering these, an instruction like
    # "Compare X and Y in Colombia" would treat "Compare" as a required scope entity too, and
    # since the relevance check is OR-across-entities, a common English word like "compare"
    # appearing ANYWHERE in a fetched page would silently make every check pass, defeating it.
    "Find", "Research", "Assess", "Investigate", "Analyze", "Analyse", "Evaluate", "Search",
    "Determine", "Identify", "Explore", "Review", "Examine", "Report", "Summarize", "Summarise",
    "Include", "Compare", "Check", "Verify", "Confirm", "List", "Describe", "Extract",
    "Prioritize", "Prioritise", "Map", "Document", "Provide", "Give", "Discuss", "Outline",
    "Detail", "Consider", "Look", "Locate", "Gather", "Collect", "Explain", "Show", "Get",
    "Ensure", "Estimate", "Calculate", "Measure", "Trace", "Track", "Note", "Verify",
    "The", "This", "That", "These", "Those", "Please", "For", "With", "Using", "Based",
}

def _extract_scope_entities(instructions: str) -> set:
    """Single- or multi-word capitalized proper-noun-looking terms from a delegated task's own
    instructions (e.g. 'Colombia') — the specific entity the caller explicitly required this task
    to be about. Deliberately looser than utils/grounding.py's extract_salient_terms (which
    requires 2+ capitalized words), since a country name is often a single word and that's exactly
    the case this check exists for.  Filters common instruction-verb words that would otherwise
    false-positive as "the entity" (e.g. the first word of "Find neglected markets...")."""
    words = re.findall(r'\b[A-Z][a-zA-Z]{2,}\b', instructions or "")
    return {w for w in words if w not in _SCOPE_STOPWORDS}

# Only unambiguous exclusion phrasings — deliberately NOT "avoid"/"ignore", which appear inside
# legitimate research topics themselves ("how to avoid mosquito bites") and would poison the
# extracted set. Same keep-false-positives-rare principle as the grounding checks.
_EXCLUSION_CUE_RE = re.compile(
    r"\b(?:exclud\w+|except(?:\s+for)?|not\s+including|(?:do\s+not|don'?t)\s+"
    r"(?:include|research|cover)|leave\s+out|other\s+than)\s*:?\s+([^.;\n]{2,200})",
    re.IGNORECASE,
)

def _extract_excluded_topics(query: str) -> set:
    """Topics the user's own query explicitly ruled out (e.g. '... excluding Agritech, HealthTech
    and EdTech'), lowercased. Confirmed live three separate times: prompt wording alone does not
    stop the Planner from delegating explicitly-excluded sectors anyway, so delegate_tasks enforces
    this structurally by skipping any task whose topic matches one of these."""
    topics = set()
    for m in _EXCLUSION_CUE_RE.finditer(query or ""):
        for part in re.split(r",|\band\b|\bor\b", m.group(1)):
            part = part.strip(" .:;*-—\t").lower()
            part = re.sub(r"^(?:the|any|all)\s+", "", part)
            # ponytail: naive trailing-noun strip so "EdTech sectors" matches a task about "EdTech";
            # a real noun-phrase parser if this misses too much in practice.
            part = re.sub(r"\s+(?:sectors?|markets?|industr(?:y|ies)|topics?|areas?|fields?)$", "", part)
            if len(part) >= 3:
                topics.add(part)
    return topics

_BARE_REFERENT_RE = re.compile(
    r"\b(?:it|its|they|them|the\s+(?:above|previous|aforementioned|same))\b", re.IGNORECASE
)

def _lacks_concrete_subject(instructions: str) -> bool:
    """True when a short instruction leans on a pronoun with nothing anywhere to anchor it — the
    sub-agent runs with NO shared context, so 'Summarize its headline feature' becomes a literal
    web search for a referent that only existed in the caller's head. Confirmed live (2026-07-11):
    that exact delegated task came back with Microsoft Research patent statistics presented as
    Python's headline feature. Kept deliberately conservative (short instructions only, and any
    proper noun / digit / quoted term counts as an anchor) — the placeholder detector's
    false-positive history (see delegate_tasks) shows what an over-eager batch rejection costs."""
    instr = (instructions or "").strip()
    if len(instr) >= 120 or not _BARE_REFERENT_RE.search(instr):
        return False
    if re.search(r"\d", instr) or re.search(r"['\"“][^'\"”]+['\"”]", instr):
        return False
    # A capitalized word that isn't sentence-initial is a real subject ("its" then refers within).
    for sentence in re.split(r"[.!?:;\n]+", instr):
        for w in sentence.split()[1:]:
            if re.match(r"[A-Z][a-zA-Z]{2,}", w):
                return False
    return True

def _get_quota_format_vars() -> dict:
    """Extract all quotas from config as {tool_name_quota: int} format variables.

    Each key in settings.quotas (e.g. 'web_search') becomes a prompt variable
    named '{web_search_quota}' with its integer limit value. Both flat integers
    and dict-with-limit configs are handled transparently.
    """
    quotas = config.cfg.get("settings", {}).get("quotas", {})
    result = {}
    for key, val in quotas.items():
        result[key + "_quota"] = val.get("limit", 0) if isinstance(val, dict) else val
    return result

def _safe_format(template: str, **kwargs) -> str:
    """Format a template string, leaving unknown {keys} as literal text.

    Unlike str.format(), this does NOT crash on missing keys. Unknown
    placeholders stay as-is (e.g. '{custom_var}' remains '{custom_var}').
    This prevents a single missing key from nuking the entire prompt.
    """
    class _SafeDict(dict):
        def __missing__(self, key):
            return '{' + key + '}'
    return template.format_map(_SafeDict(**kwargs))

def _get_default_options():
    options = {"temperature": config.cfg.get("settings", {}).get("temperature", 0.0)}
    # OpenAI's official API rejects "chat_template_kwargs"
    if "api.openai.com" not in config.cfg.get("api", {}).get("openai_base_url", ""):
        options["extra_body"] = {
            "chat_template_kwargs": {"enable_thinking": config.cfg["settings"].get("enable_thinking", False)}
        }
    return options

def malformed_tool_call_nudge(e: BaseException) -> str | None:
    """One-turn recovery message for a model emitting syntactically invalid tool-call JSON.
    Confirmed live (2026-07-11, gpt-oss on Ollama): a bad backslash escape inside think_tool
    arguments made the runtime return HTTP 500 ('error parsing tool call'), which killed an
    otherwise-successful 16-minute run at the report-rewrite stage. The model's NEXT sample very
    often parses fine — dying on the first one throws the whole run away for a transient slip."""
    if "error parsing tool call" in str(e).lower():
        return (
            "SYSTEM: your last tool call was rejected by the runtime because its arguments were "
            "not valid JSON (typically a stray backslash escape). Re-issue the tool call with "
            "plain, valid JSON string arguments — avoid backslashes except \\\\, \\\", and \\n."
        )
    return None


def _build_client():
    # Injected AsyncOpenAI so the SDK's own exponential backoff (which honors Retry-After) covers
    # 429/5xx. Confirmed live 2026-07-11: NIM's free-tier rate limit 429-crashed an entire
    # multi-agent run when the default 2 retries ran out — hosted endpoints throttle far below
    # what concurrent sub-agents generate. api.max_retries in config.yaml overrides the default.
    from openai import AsyncOpenAI
    api_cfg = config.cfg["api"]
    api_key = os.getenv("OPENAI_API_KEY", "dummy")
    return OpenAIChatCompletionClient(
        base_url=api_cfg["openai_base_url"],
        api_key=api_key,
        model=api_cfg["openai_model"],
        async_client=AsyncOpenAI(
            base_url=api_cfg["openai_base_url"],
            api_key=api_key,
            max_retries=api_cfg.get("max_retries", 6),
        ),
    )


def build_quota_pool() -> dict:
    """Build the initial {tool_name: {used, limit, rules}} quota pool from config.yaml.
    Shared across the Planner and every dispatched specialist for the life of one run."""
    quotas = config.cfg.get("settings", {}).get("quotas", {})
    pool = {}
    for key, val in quotas.items():
        if isinstance(val, dict):
            pool[key] = {"used": 0, "limit": val.get("limit", 0), "rules": val.get("rules", {})}
        else:
            pool[key] = {"used": 0, "limit": val}
    return pool


def topup_quota_pool(pool: dict) -> dict:
    """Give each tool in `settings.retry_quota_topup` a small fresh allotment on top of whatever's
    left, instead of leaving a completion-check retry to share the same already-exhausted budget as
    the failed attempt it's correcting. This fixes the old project's bug where
    `tool_quotas_ctx.set(...)` happened once before the whole retry loop and was never replenished
    between attempts (see plan doc diagnosis point 2) — a complex query that burned its budget on a
    flawed first pass never got a real second chance."""
    topup = config.cfg.get("settings", {}).get("retry_quota_topup", {})
    for tool_name, amount in topup.items():
        if tool_name in pool:
            pool[tool_name]["limit"] += amount
    return pool

def create_local_agent(builder, subagent_callback=None, session_data=None):
    """
    Returns (agent, session). Session is None when conversational memory is disabled.
    Agent is re-created each call to pick up config changes (thinking toggle).
    """
    global _session
    client = _build_client()

    # -------------------------------------------------------------
    # SDK Bounded Dispatcher
    # -------------------------------------------------------------
    # [!CAUTION] RULES FOR LLM CODING ASSISTANTS EDITING THIS:
    # 1. This engine module is OUT OF BOUNDS. Do not hardcode sub-agents here.
    # 2. Sub-agents MUST be defined in `src/app.py` via `SubAgentConfig`.
    # 3. The logic below dynamically reads the builder config and mounts the TUI streams.
    # -------------------------------------------------------------
    # -------------------------------------------------------------
    # Bounded Concurrent Sub-Agent Dispatcher
    # Utilizes inherited contextvars for shared cumulative quotas to prevent limit overruns.
    sem = asyncio.Semaphore(config.cfg.get("settings", {}).get("concurrency", {}).get("max_concurrent_tasks", 1))

    holds_token = contextvars.ContextVar('holds_token', default=False)

    async def _run_single_task(task_name: str, instructions: str, agent_id: str = None) -> str:
        async with sem:
            parent_depth = delegation_depth_ctx.get()
            depth_token = delegation_depth_ctx.set(parent_depth + 1)
            token_setter = holds_token.set(True)
            # Pre-declared so the `finally` block's reset is always safe even on an early return
            # below (bad agent_id) — found via a real end-to-end test: a model invented a fictional
            # agent_id ("AI-3") not matching any real specialist, hit the early-return error path,
            # and finally's `available_sub_agents_ctx.reset(children_token)` crashed with
            # UnboundLocalError since children_token was never assigned on that path. This bug was
            # latent in the reference project this was forked from too — never previously observed
            # because bad agent_id values apparently never came up in its own testing.
            children_token = None
            # Per-task-scoped fetch tracking (see utils/run_state.py's task_fetched_urls_ctx
            # header comment) — NOT a before/after length delta on the shared run-wide list, which
            # races under concurrent delegate_tasks dispatch.
            task_urls_token = task_fetched_urls_ctx.set([])
            # Expose this task's scope entities to web_search for the query-level scope warning
            # (see utils/run_state.py's scope_entities_ctx header comment).
            scope_token = scope_entities_ctx.set(_extract_scope_entities(instructions))
            try:
                current_date = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                # Look up the target agent from the CALLER's available sub-agents (scoped, not global)
                caller_sub_agents = available_sub_agents_ctx.get()
                target_config = None
                if agent_id and caller_sub_agents:
                    for conf in caller_sub_agents:
                        if conf.name == agent_id:
                            target_config = conf
                            break
                    if target_config is None:
                        return f"## Error for {task_name}\nFailed to delegate: Sub-agent named '{agent_id}' does not exist. Available sub-agents for this caller: {[c.name for c in caller_sub_agents]}.\n---"
                else:
                    target_config = caller_sub_agents[0] if caller_sub_agents else None

                sub_tools = apply_tool_permissions(target_config.tools.copy() if target_config else [])
                # Only inject delegate_tasks if the TARGET agent has its own children
                target_children = target_config.sub_agents if target_config else []
                if target_children and delegate_tasks not in sub_tools:
                    sub_tools.append(delegate_tasks)
                if think_tool not in sub_tools:
                    sub_tools.append(think_tool)

                # Scope the available sub-agents for the target agent's own delegate_tasks calls
                children_token = available_sub_agents_ctx.set(target_children)

                sub_instr = ""
                if target_config:
                    sub_instr = _safe_format(
                        target_config.instructions,
                        date=current_date,
                        task_name=task_name,
                        workspace_dir=config.cfg.get("settings", {}).get("workspace", {}).get("dir", "."),
                        delegation_instructions=SUBAGENT_DELEGATION_INSTRUCTIONS.format(
                            max_concurrency=config.cfg.get("settings", {}).get("concurrency", {}).get("max_concurrent_tasks", 1)
                        ),
                        **_get_quota_format_vars()
                    )
                else:
                    sub_instr = _safe_format(
                        SUBAGENT_INSTRUCTIONS,
                        date=current_date,
                        task_name=task_name,
                        workspace_dir=config.cfg.get("settings", {}).get("workspace", {}).get("dir", "."),
                        delegation_instructions=SUBAGENT_DELEGATION_INSTRUCTIONS.format(
                            max_concurrency=config.cfg.get("settings", {}).get("concurrency", {}).get("max_concurrent_tasks", 1)
                        ),
                        **_get_quota_format_vars()
                    )

                # MCP tools (settings.mcp_servers, scoped per sub-agent name — see
                # tools/mcp_loader.py) are connected only for the lifetime of this one delegated
                # task and closed automatically on exit, success or failure. No-op when
                # mcp_servers is unconfigured (the default) — AsyncExitStack does nothing.
                from contextlib import AsyncExitStack
                from tools.mcp_loader import build_mcp_tools_for_agent
                mcp_tools = build_mcp_tools_for_agent(target_config.name if target_config else "SubAgent")

                async with AsyncExitStack() as mcp_stack:
                    for mt in mcp_tools:
                        await mcp_stack.enter_async_context(mt)

                    sub_agent = client.as_agent(
                        name=_sanitize_name(f"SubAgent_{task_name}"),
                        instructions=sub_instr,
                        tools=sub_tools + mcp_tools,
                        default_options=_get_default_options()
                    )
                    final_text = ""
                    current_input = instructions
                    has_requests = True
                    malformed_retries = 0
                    while has_requests:
                        has_requests = False
                        user_input_requests = []

                        try:
                            stream = sub_agent.run(current_input, stream=True)
                            async for update in stream:
                                if subagent_callback:
                                    await subagent_callback(update, is_subagent=True, agent_name=f"SubAgent_{task_name}")
                                for c in update.contents:
                                    if c.type == "text" and c.text:
                                        final_text += c.text

                                if getattr(update, "user_input_requests", None):
                                    user_input_requests.extend(update.user_input_requests)
                        except QuotaAbortException as e:
                            return f"## Error for {task_name}\nTask forcefully aborted: {str(e)}\n---"
                        except Exception as e:
                            nudge = malformed_tool_call_nudge(e)
                            if nudge and malformed_retries < 2:
                                malformed_retries += 1
                                from agent_framework import Message
                                new_inputs = [current_input] if isinstance(current_input, str) else list(current_input)
                                new_inputs.append(Message("user", [{"type": "text", "text": nudge}]))
                                current_input = new_inputs
                                has_requests = True
                                continue
                            raise

                        if user_input_requests:
                            has_requests = True
                            responses = []
                            if subagent_callback:
                                responses = await subagent_callback(None, is_subagent=True, agent_name=f"SubAgent_{task_name}", approval_requests=user_input_requests)

                            new_inputs = [current_input] if isinstance(current_input, str) else list(current_input)
                            if responses:
                                new_inputs.extend(responses)
                            current_input = new_inputs

                if subagent_callback:
                    await subagent_callback(None, is_subagent=True, agent_name=f"SubAgent_{task_name}", is_done=True)

                new_urls = task_fetched_urls_ctx.get() or []

                # Upstream verification (Tier-2 Searcher output only, detected generically via
                # target_children rather than hardcoded agent names — Analyzers are leaf nodes with
                # no children). Catches a hallucinated citation in a specialist's summary before it
                # ever reaches the Planner's context, instead of only at final-report time — error
                # propagation into the Planner's findings.md was previously undetectable until the
                # very end of the run, by which point the retry budget was already partly spent.
                gc_cfg = config.cfg.get("settings", {}).get("grounding_check", {})
                # enabled is the grounding_check section's master switch (2026-07-12 audit, G2)
                if target_children and gc_cfg.get("enabled", True) and gc_cfg.get("verify_specialist_output", True):
                    from utils.grounding import real_grounding_problem
                    problem = await real_grounding_problem(final_text)
                    if problem and problem != "no_urls":
                        final_text += (
                            f"\n\n[SYSTEM VERIFICATION WARNING: this summary attributes a claim to "
                            f"a source that does not match anything actually fetched this run, or to "
                            f"something that isn't a real URL at all ({problem}). Do not treat the "
                            f"associated claim as sourced when writing findings.md.]"
                        )

                    # Topical-relevance check: an instruction requiring "Colombia" is not the same
                    # as a fetched source actually BEING about Colombia — confirmed live, forcing
                    # the scope entity into every sub-task's instructions still let a US article, a
                    # Mexico-specific paper, and a generic global page through, because search
                    # result ranking and blind top-1 auto-fetch don't check relevance either. This
                    # is a cheap, deterministic substitute: does anything actually fetched for THIS
                    # task even mention the entity the instructions said it must be about?
                    if gc_cfg.get("verify_scope_relevance", True):
                        scope_entities = _extract_scope_entities(instructions)
                        if scope_entities and new_urls:
                            from tools.fs import get_workspace_file_content
                            # Case-insensitive on both sides (C8 companion fix): "Colombia"
                            # must match a page's lowercase "colombia" — the entity comes from
                            # instruction casing, the page from editorial casing.
                            lowered = [t.lower() for t in scope_entities]
                            matched = any(
                                any(term in (get_workspace_file_content(u["filename"]) or "").lower() for term in lowered)
                                for u in new_urls
                            )
                            if not matched:
                                entities_str = "/".join(sorted(scope_entities))
                                final_text += (
                                    f"\n\n[SYSTEM RELEVANCE WARNING: none of the sources fetched for "
                                    f"this task actually mention {entities_str}, despite the task "
                                    f"instructions requiring it. This may be an off-topic or "
                                    f"wrong-country source — verify before presenting these findings "
                                    f"as being about {entities_str}.]"
                                )

                # Populate the structured findings store (previously dead code — RunState.add_finding
                # was defined but never called) so a completion-check retry or later debugging has real
                # {source_url, summary} records instead of only the model's own narration. Preferring
                # URLs actually fetched during THIS task over the task name keeps findings traceable to
                # a real source when one exists (Searcher-tier calls); Analyzer-tier calls fetch nothing
                # themselves, so they fall back to the task name as the record's identifier.
                run_state = run_state_ctx.get()
                if run_state is not None:
                    if new_urls:
                        for u in new_urls:
                            run_state.add_finding(u["url"], final_text[:1500])
                    else:
                        run_state.add_finding(task_name, final_text[:1500])

                return f"## Result for {task_name}\n{final_text}\n---"
            finally:
                if children_token is not None:
                    available_sub_agents_ctx.reset(children_token)
                holds_token.reset(token_setter)
                delegation_depth_ctx.reset(depth_token)
                task_fetched_urls_ctx.reset(task_urls_token)
                scope_entities_ctx.reset(scope_token)

    # -------------------------------------------------------------
    # [!CAUTION] CONCURRENCY ARCHITECTURE FOR LLM CODING ASSISTANTS:
    # This template utilizes a global `asyncio.Semaphore` to rigidly enforce max limits.
    # To prevent deeply nested delegation streams from deadlocking (e.g. parent awaits child
    # and starves the token pool), `delegate_tasks` utilizes contextvars
    # to mathematically surrender its token while waiting, allowing children to safely execute.
    # -------------------------------------------------------------
    @tool(name="delegate_tasks", description="Delegate multiple independent tasks to specialized sub-agents to be executed concurrently. Pass a list of dictionaries, each with 'task_name', 'instructions', and 'agent_id' (see your Delegation Routing block for valid agent_id values).")
    @with_quota
    async def delegate_tasks(tasks: list[dict]) -> str:
        # -------------------------------------------------------------
        # Validate BEFORE dispatching anything. Found via a real end-to-end test: a model emitted
        # tasks shaped like {"due": 1, "task": "..."} instead of {"task_name", "instructions",
        # "agent_id"} — completely wrong field names. The old code silently defaulted to
        # task_name="Unknown_Task", instructions="" and dispatched it anyway, so the sub-agent
        # searched for the literal string "Unknown_Task" and got garbage, unrelated results, which
        # then poisoned everything downstream (the Planner had nothing real to report). A malformed
        # call must fail loudly and immediately, not silently degrade into wasted quota on garbage
        # work — this lets the model see exactly what was wrong and retry with the correct shape.
        # -------------------------------------------------------------
        # Numbered-placeholder detector: found live, a model planning "12 candidate sectors"
        # dispatched tasks literally named/instructed around "sector 1", "sector 2", etc. instead
        # of naming the real sectors first — every resulting web_search query was the literal,
        # meaningless phrase "market size of sector 1 in colombia", returning garbage (a currency
        # site, an energy-agency report, unrelated Wikipedia pages) across all 12. Same principle
        # as the malformed-schema check below: reject loudly before wasting quota on it, rather
        # than relying on the prompt rule alone (see prompts.py's DISPATCH step for the prompt-side
        # half of this fix).
        # Matches "sector 1" / "sector_1" (post-normalization) / "sector #4" AND "sector X" /
        # "item Y" — a bare capital letter is as common a placeholder as a number (confirmed
        # live). The keyword itself stays case-insensitive; the single-letter placeholder stays
        # case-SENSITIVE (only a bare capital, e.g. "sector X") so this doesn't false-positive on
        # a keyword incidentally followed by a lowercase word-initial letter in real prose.
        # Checked against `instructions` ONLY, not `task_name` — found live, a real 12-task batch
        # ("Analyze market 1: Logistics and supply chain management in Colombia", ...) was rejected
        # wholesale because task_name used "market 1"/"market 2" as an ordinary numbered list label
        # while instructions fully named the real, specific topic ("Assess the needs of logistics
        # and supply chain management in Colombia..."). task_name is never what actually drives a
        # Searcher's search query (only instructions is — see WEB_SEARCHER_INSTRUCTIONS' Workflow),
        # so a numbered task_name label is harmless; the original documented failure case (the
        # literal garbage query "market size of sector 1 in colombia") had the placeholder in
        # instructions itself, which this narrower check still catches.
        _placeholder_re = re.compile(
            r'\b(?:[Ss]ector|[Ii]tem|[Mm]arket|[Tt]opic|[Oo]ption|[Cc]andidate|[Cc]ategory|'
            r'[Pp]roduct|[Ss]ervice|[Aa]rea)\s*(?:#?\d+\b|[A-Z]\b)'
        )
        # Catches the OTHER half of the same real failure: a task instructed to act "for each
        # identified sector" (or similar) inside a batch dispatched via asyncio.gather — but
        # delegate_tasks runs every task in a batch CONCURRENTLY, so a sibling "identify the
        # sectors" task's results don't exist yet when this one runs. This is exactly the
        # sequential-vs-concurrent rule already in SUBAGENT_DELEGATION_INSTRUCTIONS, just not
        # being followed — confirmed live, a real run's web_search literally queried "Evaluate
        # competition level for sector X" because of this.
        _cross_task_dependency_re = re.compile(r'\bfor each\b.{0,40}\bidentif', re.IGNORECASE)

        errors = []
        for i, t in enumerate(tasks):
            if not isinstance(t, dict):
                errors.append(f"Task {i}: not an object — got {type(t).__name__}.")
                continue
            missing = [k for k in ("task_name", "instructions") if not t.get(k)]
            if missing:
                errors.append(
                    f"Task {i} {t!r}: missing or empty required field(s) {missing}. "
                    f"Each task MUST be shaped exactly as "
                    f"{{\"task_name\": \"...\", \"instructions\": \"...\", \"agent_id\": \"...\"}}."
                )
                continue
            # Normalize underscores/hyphens to spaces first — "sector_1" (a real observed
            # instructions string) otherwise never matches \bsector\b, since '_' counts as a word
            # character and leaves no boundary between "analyze_" and "sector".
            placeholder_text = str(t.get("instructions", "")).replace("_", " ").replace("-", " ")
            m = _placeholder_re.search(placeholder_text)
            if m:
                errors.append(
                    f"Task {i} ({t.get('task_name')!r}): looks like an unresolved placeholder "
                    f"({m.group(0)!r}), not a real research topic — a Searcher given this will "
                    f"search for that literal meaningless phrase. If you're enumerating multiple "
                    f"items, you must know and use each item's REAL name (e.g. 'fintech for gig "
                    f"workers', not 'sector 3' or 'sector X') before dispatching. If you don't "
                    f"know the real names yet, delegate a background task to identify them first."
                )
                continue
            if _lacks_concrete_subject(str(t.get("instructions", ""))):
                errors.append(
                    f"Task {i} ({t.get('task_name')!r}): instructions rely on a pronoun ('it'/'its'/"
                    f"'them') with no concrete subject anywhere — the sub-agent executing this has "
                    f"NO memory of your conversation or of sibling tasks, so it cannot know what "
                    f"the pronoun refers to and will search for the literal phrase. Restate the "
                    f"full subject explicitly in EVERY task's instructions (e.g. 'Summarize Python "
                    f"3.14\\'s headline feature', not 'Summarize its headline feature')."
                )
                continue
            dep_m = _cross_task_dependency_re.search(" ".join(str(t.get(k, "")) for k in ("task_name", "instructions")))
            if dep_m:
                errors.append(
                    f"Task {i} ({t.get('task_name')!r}): says \"{dep_m.group(0)}\" — this task "
                    f"depends on another task's output, but ALL tasks in one delegate_tasks call "
                    f"run CONCURRENTLY, so a sibling 'identify X' task's results do not exist yet "
                    f"when this one runs. Split this into two SEQUENTIAL delegate_tasks calls: "
                    f"first the identification task alone, wait for its real result, THEN a second "
                    f"call with one task per REAL item it found."
                )
        if errors:
            return (
                "Error: delegate_tasks call rejected — none of these tasks were dispatched (no quota "
                "was consumed on wasted work). Fix the shape and call delegate_tasks again with valid "
                "tasks:\n" + "\n".join(errors)
            )

        # Structural enforcement of the user's own exclusion rules — confirmed live three times
        # that the prompt-level rule alone doesn't hold (4 explicitly-excluded sectors researched
        # and included anyway). Excluded tasks are SKIPPED individually, not batch-rejected: the
        # sibling tasks are legitimate, and the placeholder-detector incident (see above) showed a
        # wholesale rejection makes the model abandon delegation and fabricate instead.
        run_state = run_state_ctx.get()
        excluded_topics = _extract_excluded_topics(run_state.data.get("query", "")) if run_state else set()
        skipped = []
        coroutines = []
        for t in tasks:
            name = t.get("task_name")
            instr = t.get("instructions")
            aid = t.get("agent_id", None)
            task_text = f"{name} {instr}".lower()
            # A task may legitimately restate the query's own exclusions ("... exclude fintech,
            # last-mile delivery ..."), which would substring-match every excluded topic and skip
            # a perfectly in-scope task — confirmed live 2026-07-11: a Planner's discovery task was
            # rejected twice for quoting the exclusion list, burning delegate_tasks quota and turns.
            # Strip exclusion clauses from the task's text before matching, so the gate only fires
            # when an excluded topic is the task's actual subject.
            task_text = _EXCLUSION_CUE_RE.sub(" ", task_text)
            hit = next((topic for topic in excluded_topics if topic in task_text), None)
            if hit:
                skipped.append(
                    f"## Skipped {name}\nNot dispatched: this topic matches an explicit exclusion "
                    f"in the original query ({hit!r}). Do not research it, do not include it in "
                    f"findings.md or the final report, and do not re-delegate it.\n---"
                )
                continue
            coroutines.append(_run_single_task(name, instr, aid))

        if skipped and not coroutines:
            return (
                "Error: every task in this call matches a topic the original query explicitly "
                "excluded — none were dispatched. Re-read the query's exclusion list and delegate "
                "only topics that are actually in scope.\n" + "\n".join(skipped)
            )

        was_holding = holds_token.get()
        if was_holding:
            sem.release()

        try:
            results = await asyncio.gather(*coroutines, return_exceptions=True)
        finally:
            if was_holding:
                await sem.acquire()

        final_output = list(skipped)
        for res in results:
            if isinstance(res, Exception):
                final_output.append(f"## Error\nTask failed with exception: {res}\n---")
            else:
                final_output.append(str(res))

        return "\n\n".join(final_output)

    # -------------------------------------------------------------
    # [!CAUTION] RULES FOR LLM CODING ASSISTANTS EDITING THIS:
    # When adding or removing standard tools (e.g., pruning `web_search`), modify the `WORKSPACE_TOOLS` array or this `tools_list`.
    # DO NOT rewrite this entire function or file from scratch.
    # -------------------------------------------------------------
    # -------------------------------------------------------------
    # Planner retains full access to its declared tools, gains `delegate_tasks` if it has sub_agents
    tools_list = apply_tool_permissions(builder.tools.copy())
    # Human-in-the-loop plan-approval gate: reuses the SAME approval infrastructure already built
    # for settings.permissions (ApprovalWidget in TUI mode, the AUTO_APPROVE flag in headless mode)
    # rather than inventing new pause logic. Scoped to write_todos specifically (not delegate_tasks,
    # which is a single closure shared across every tier — gating it there would pause EVERY
    # delegation at every depth, not just the Planner's initial one). Only the Planner holds
    # write_todos (see app.py), so this can't accidentally gate a sub-agent's tool calls.
    if config.cfg.get("settings", {}).get("human_in_the_loop", False):
        for t in tools_list:
            if getattr(t, "name", None) == "write_todos" and hasattr(t, "approval_mode"):
                t.approval_mode = "always_require"
    if builder.sub_agents:
        tools_list.append(delegate_tasks)
    # Set the Planner's available sub-agents for scoped delegation
    available_sub_agents_ctx.set(builder.sub_agents)
    current_date = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    workspace_dir = config.cfg.get("settings", {}).get("workspace", {}).get("dir", ".")

    agent = client.as_agent(
        name=_sanitize_name(builder.name),
        instructions=_safe_format(
            builder.instructions,
            date=current_date,
            workspace_dir=workspace_dir,
            delegation_instructions=SUBAGENT_DELEGATION_INSTRUCTIONS.format(
                max_concurrency=config.cfg.get("settings", {}).get("concurrency", {}).get("max_concurrent_tasks", 1)
            ),
            **_get_quota_format_vars()
        ),
        tools=tools_list,
        default_options=_get_default_options()
    )

    session = None
    if config.cfg["settings"].get("enable_conversational_memory", False):
        if session_data is not None:
            _session = AgentSession.from_dict(session_data)
        elif _session is None:
            _session = agent.create_session()
        session = _session

    return agent, session

def reset_session():
    """Clear the conversation session (called by /new)."""
    global _session
    _session = None
