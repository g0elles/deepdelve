import os
import asyncio
import re
import time
from dataclasses import dataclass
from typing import Optional
from agent_framework.openai import OpenAIChatCompletionClient
from agent_framework import tool, AgentSession, Message
from tools import with_quota, think_tool, QuotaAbortException
from utils.run_state import run_state_ctx, task_fetched_urls_ctx, scope_entities_ctx, task_id_ctx, _next_task_id
from prompts import (
    SUBAGENT_INSTRUCTIONS, SUBAGENT_DELEGATION_INSTRUCTIONS,
    STANDARD_REPORT_STYLE_INSTRUCTIONS, ACADEMIC_REPORT_STYLE_INSTRUCTIONS, ANSWER_REPORT_STYLE_INSTRUCTIONS,
    STANDARD_CITATION_FORMAT_INSTRUCTIONS, ACADEMIC_CITATION_FORMAT_INSTRUCTIONS, ANSWER_CITATION_FORMAT_INSTRUCTIONS,
)
import datetime
import config
import contextvars

# Module-level session for conversational memory persistence
_session = None
delegation_depth_ctx = contextvars.ContextVar('delegation_depth_ctx', default=0)
available_sub_agents_ctx = contextvars.ContextVar('available_sub_agents_ctx', default=[])

# Roles dispatched by engine/completion.py's _dispatch_writer_review_fix (Write->Review->Fix loop),
# not by the Planner's own delegate_tasks. They're called directly from the Planner's top-level
# context, so they land at delegation_depth_ctx==1 exactly like a genuine research dispatch -- depth
# alone can't tell them apart (see RunState.coverage()'s docstring). None of these roles do research
# or can ever have a real source URL, so they must be excluded from add_finding's coverage
# bookkeeping. Confirmed live 2026-07-14: check_thin_coverage fired counting
# 'FindingsWriterFix_attempt1'/'ReviewFix_attempt1'/'FindingsWriterFix_attempt1_reviewed' as 3 of 5
# "delegated research tasks" that produced no source.
_NON_RESEARCH_DISPATCH_ROLES = frozenset({"Builder", "FindingsWriter", "PeerReviewer"})

# Max chars of a specialist's final_text kept as a RunState finding's summary (FindingsWriter's
# only evidence for this task, see _build_findings_source_material). Any grounding-check warning
# appended to final_text (see verification_warnings below) is reserved room OUTSIDE this budget
# and concatenated back in full afterward — never truncated away with the rest of the body.
_FINDING_SUMMARY_BUDGET = 1500

# Matches the mandated trailing section of a Searcher's summary (WEB_SEARCHER_INSTRUCTIONS /
# ACADEMIC_SEARCHER_INSTRUCTIONS' Findings Format, src/prompts.py): a "FOLLOW-UP DIRECTIONS:"
# header followed by 1-3 "- ..." bullets, always the LAST section of the summary. Anchored to the
# header through end-of-string (re.DOTALL) since nothing legitimate follows it per the prompt's
# own format.
_FOLLOW_UP_DIRECTIONS_RE = re.compile(r'FOLLOW-UP DIRECTIONS:\s*\n(.*)', re.IGNORECASE | re.DOTALL)
_FOLLOW_UP_BULLET_RE = re.compile(r'^\s*-\s*(.+)$', re.MULTILINE)


def _extract_follow_up_directions(text: str) -> list:
    """Pull the real '- ...' bullets out of a Searcher's 'FOLLOW-UP DIRECTIONS:' section (2026-07-19,
    engine-driven iterative deepening feature — see ROADMAP item 10). Returns [] if the section is
    absent (a model that skipped it, or a non-Searcher dispatch) rather than guessing."""
    m = _FOLLOW_UP_DIRECTIONS_RE.search(text or "")
    if not m:
        return []
    return [b.strip() for b in _FOLLOW_UP_BULLET_RE.findall(m.group(1)) if b.strip()]


def _agent_routing_rejection_reason(
    declared_agent_id, caller_role_names: frozenset, prediction: tuple | None, min_confidence: float,
) -> str | None:
    """Pure decision logic for delegate_tasks's non-generative routing-classifier check (RESEARCH.md
    §6, ROADMAP.md "Pending", 2026-07-20) — pulled out of delegate_tasks's own closure so it's
    directly testable without needing the full async tool/contextvar machinery. Decided policy:
    reject-and-nudge, not silent override. Returns a rejection reason string if delegate_tasks
    should add this task to its error-accumulation list, or None if the task should proceed
    unchanged (the common case: no prediction available, or the declared agent_id already agrees
    with the classifier / the classifier abstained below min_confidence).

    `prediction` is `(predicted_agent_id, confidence)` from utils.agent_routing.predict_agent_id,
    already restricted to this caller's own real roster intersected with the classifier's known
    classes — or None if unavailable (classifier disabled/unloaded, or this caller's roster doesn't
    overlap the classifier's known classes at all, e.g. a Builder/FindingsWriter/PeerReviewer
    dispatch's own delegate_tasks call, if that ever happens)."""
    if prediction is None:
        return None
    predicted_agent_id, confidence = prediction
    declared_is_unknown = declared_agent_id not in caller_role_names
    strong_disagreement = confidence >= min_confidence and predicted_agent_id != declared_agent_id
    if not (declared_is_unknown or strong_disagreement):
        return None
    return (
        f"agent_id {declared_agent_id!r} "
        f"{'is not a valid specialist for this caller' if declared_is_unknown else 'looks wrong'} "
        f"— based on the instructions, this looks like a {predicted_agent_id!r} task "
        f"(confidence {confidence:.2f}). Valid agent_id values for this caller: "
        f"{sorted(caller_role_names)}."
    )


def _should_cache_finding(
    verification_warnings: str, new_urls: list, rag_cache_enabled: bool, finding_summary: str = "",
) -> bool:
    """Pure decision logic for the RAG findings cache's write hook (ROADMAP.md "Strategic options"
    item 5, 2026-07-20) — pulled out of _run_single_task so it's directly testable without needing
    the full async tool/contextvar machinery, same pattern as _agent_routing_rejection_reason above.

    Only ever True when the finding passed the EXACT SAME grounding+relevance gate every live
    finding already goes through (verification_warnings empty), a real new URL was actually fetched
    (new_urls non-empty — a task-name-only finding with no real source is never cache-worthy), the
    feature is enabled, AND finding_summary contains a real markdown-link citation
    (`[title](http...)`). That last check was added after a live run showed the grounding gate
    alone isn't sufficient: a Searcher's own pre-delegation NARRATION text ("I'll search for
    authoritative information about...") can carry a real new_urls entry and zero verification
    warnings, yet contain no actual finding at all — every genuine consolidated summary follows
    this project's own mandated Findings Format (a markdown-link bullet per source), so requiring
    it filters out narration without needing a fragile keyword blocklist. A cache entry is never
    less verified than a same-run finding."""
    return bool(
        rag_cache_enabled and new_urls and not verification_warnings and "](http" in finding_summary
    )


# Roles eligible for settings.specialist_model (2026-07-18, heterogeneous role tiering): the
# leaf-tier dispatches that only ever make single, independent tool calls (search, fetch, extract)
# rather than the multi-step self-correction the Planner/Builder/FindingsWriter/PeerReviewer roles
# need — the bake-off's own repeated finding is that small local models are reliable at exactly
# this kind of work and unreliable at the coordination roles. Deliberately the complement of
# _NON_RESEARCH_DISPATCH_ROLES plus the Planner itself (which never goes through
# _run_single_task at all, so isn't a member of either set).
_SPECIALIST_MODEL_ROLES = frozenset({"WebSearcher", "AcademicSearcher", "DocumentAnalyzer", "DataAnalyzer"})

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

def _ring_fenced_deadline(task_start: float, task_deadline: float, sub_agent_timeout_minutes: float,
                           sdk_timeout_ceiling_seconds: float) -> Optional[float]:
    """Pure arithmetic core of the task_deadline ring-fence (2026-07-21, "4th synthesis-vanishing
    mechanism" fix 1) -- split out from the async dispatch loop specifically so the SDK-timeout
    cap (the one real bug risk an ad-hoc extension could reintroduce, see
    _sdk_timeout_ceiling_seconds' own comment) is covered by a plain assertion instead of only
    being exercised live. Returns the new deadline, or None if extending wouldn't move it forward
    (caller should not extend in that case)."""
    extended = min(
        task_start + sub_agent_timeout_minutes * 2 * 60,
        task_start + sdk_timeout_ceiling_seconds - 60,
    )
    return extended if extended > task_deadline else None


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

def stream_content_chars(update) -> int:
    """Approximate context growth from one stream update: text, tool-call arguments, and tool
    results all re-enter the model's context on subsequent turns of the same agent run. Char
    count (not tokens) on purpose — deterministic, tokenizer-free, and the budget it feeds is a
    safety margin, not an exact fit."""
    n = 0
    for c in getattr(update, "contents", None) or []:
        for attr in ("text", "arguments"):
            v = getattr(c, attr, None)
            if isinstance(v, str):
                n += len(v)
        r = getattr(c, "result", None)
        if r is not None:
            n += len(r) if isinstance(r, str) else len(str(r))
    return n


def get_context_budget() -> int:
    """settings.context_budget_chars: per-agent-stream char budget (0/absent = off). Idea from
    Tongyi DeepResearch's react_agent.py (see README References), which counts tokens and at the
    limit forces 'stop tool calls, answer now'. DeepDelve runs local models at num_ctx ~16384 and
    previously had NO context accounting at all — on overflow Ollama silently truncates from the
    TOP, which can eat the system prompt mid-run and looks exactly like model collapse. This is a
    conservative proxy (per-stream streamed chars, not true prompt size): when exceeded, the turn
    is cut and the agent gets ONE wrap-up turn to return/write what it already has."""
    return config.cfg.get("settings", {}).get("context_budget_chars", 0) or 0


SUBAGENT_BUDGET_NUDGE = (
    "SYSTEM: you have reached your context budget for this task. Do NOT call any more tools. "
    "Immediately return your consolidated findings as your final message, from what you have "
    "already gathered: each finding with its source URL and exact figures/names, plus your "
    "FOLLOW-UP DIRECTIONS. An incomplete summary now beats a truncated context."
)


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


async def iter_agent_stream(stream, deadline: float | None):
    """Drive one agent-framework stream, yielding each update, cut off by an optional wall-clock
    deadline (time.monotonic()-based). Replaces a plain `async for` with a manually-driven
    stream.__aiter__() + asyncio.wait_for(..., timeout=remaining) loop so a stream that goes
    silent for a long time still gets cut off — a plain async-for only re-checks the deadline once
    it actually receives an update (found live 2026-07-12, ROADMAP.md). deadline=None means
    unbounded: asyncio.wait_for(fut, timeout=None) is a plain unbounded await per the stdlib docs,
    so a TUI caller (no max_run_minutes) gets behavior identical to a plain async-for. Raises
    asyncio.TimeoutError to the caller on cutoff (does not swallow it) — callers own their own
    notification text and run_state mutation; this generator only owns iteration mechanics.
    Shared by run_cli (headless, real deadline) and run_agent (TUI, deadline=None) in
    engine/tui.py — extracted 2026-07-14 (ROADMAP "B4") specifically so a future change to the
    deadline-racing mechanics can't land in only one of the two call sites, the same drift pattern
    that already once left run_agent without the malformed-tool-call retry logic below."""
    stream_iter = stream.__aiter__()
    while True:
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise asyncio.TimeoutError()
        else:
            remaining = None
        try:
            update = await asyncio.wait_for(stream_iter.__anext__(), timeout=remaining)
        except StopAsyncIteration:
            return
        yield update


@dataclass
class MalformedRetryResult:
    should_retry: bool
    new_current_input: object
    force_final_verdict: bool
    reraise: bool
    new_malformed_retries: int


def classify_malformed_retry(e: BaseException, malformed_retries: int, current_input,
                              max_retries: int = 2) -> MalformedRetryResult:
    """Pure decision logic for the malformed-tool-call retry pattern shared by run_cli and
    run_agent (engine/tui.py). Does not touch stdout/widgets/run_state — callers still do their
    own surface-specific notification and apply force_final_verdict/reraise to their own control
    flow. Kept pure so it's unit-testable with a fake exception, no event loop, no I/O. Extracted
    2026-07-14 (ROADMAP "B4") after this exact retry logic was once found missing from run_agent
    ("added later for parity") — a single implementation removes that drift risk going forward."""
    nudge = malformed_tool_call_nudge(e)
    if nudge and malformed_retries < max_retries:
        new_inputs = [current_input] if isinstance(current_input, str) else list(current_input)
        new_inputs.append(Message("user", [{"type": "text", "text": nudge}]))
        return MalformedRetryResult(True, new_inputs, False, False, malformed_retries + 1)
    if not nudge:
        return MalformedRetryResult(False, current_input, False, True, malformed_retries)
    return MalformedRetryResult(False, current_input, True, False, malformed_retries)


_FUNC_NOT_FOUND_RE = re.compile(r'Requested function "([^"]+)" not found')
_FILE_NOT_FOUND_RE = re.compile(r"^Error: '(.+)' not found\.$")


def tool_result_error_nudge(result_text: str) -> str | None:
    """One-turn recovery message for a sub-agent tool call that failed IN-BAND — a normal tool
    result whose text is an error string, not a raised exception. `malformed_tool_call_nudge`
    above only covers the (rarer) transport-level case where the SDK itself raises; a hallucinated
    tool name or a bad argument comes back as an ordinary successful `function_result` content
    item instead (see agent_framework/_tools.py: `Error: Requested function "{name}" not found.`
    and `Error: Argument parsing failed.`), so it never reaches `_run_single_task`'s `except` block
    and never triggered any nudge at all. Confirmed live 2026-07-13: a `SubAgent_BuilderFix` retry
    hallucinated a call to `delegate_tasks` (not in Builder's real tool list), a separate sub-agent
    called a malformed `grep_workspace?`, and `PeerReviewer` tried reading a nonexistent
    `workspace.txt` — each burned a turn with nothing telling the model what actually went wrong.

    Deliberately narrow (three specific, evidence-backed error shapes, not every possible tool
    failure) — a legitimate business-logic error (a real search that genuinely failed, a quota
    genuinely exhausted) already has its own handling elsewhere and retrying it blindly wouldn't
    help; these three are specifically "the model asked for something that doesn't exist and can
    immediately ask for the right thing instead" cases."""
    if not result_text:
        return None
    m = _FUNC_NOT_FOUND_RE.search(result_text)
    if m:
        return (
            f'SYSTEM: your last tool call named "{m.group(1)}", which does not exist. Only call '
            f"tools that are actually available to you in this conversation — check the exact "
            f"name (it may be a typo, or a tool you don't actually have access to) and retry with "
            f"a real one."
        )
    if result_text.startswith("Error: Argument parsing failed."):
        return (
            "SYSTEM: your last tool call's arguments were rejected — see the exception detail "
            "above for exactly which field was wrong. Fix that field and re-issue the call with "
            "corrected arguments."
        )
    if _FILE_NOT_FOUND_RE.match(result_text.strip()):
        return (
            "SYSTEM: the file you just tried to read does not exist in this workspace. Double-check "
            "the exact filename (it may be misspelled, or never actually written) before retrying, "
            "or proceed without it if it truly doesn't exist."
        )
    return None


def _build_client(model_override: str | None = None, base_url_override: str | None = None):
    """model_override: used by settings.specialist_model (heterogeneous role tiering) to build a
    SECOND client pointed at a different model than api.openai_model, for the leaf specialist
    roles only — see _SPECIALIST_MODEL_ROLES. Same endpoint/key/timeout for both by default; only
    the model name differs, since this project's whole reliability layer (grounding checks,
    quotas, etc.) is model-agnostic and doesn't need a second endpoint to make tiering meaningful.

    base_url_override: settings.specialist_base_url (2026-07-20) — an escape hatch for a
    specialist model that isn't OpenAI-tool-calling compatible on the SAME endpoint as the main
    model (e.g. a local translation proxy in front of a model with its own native tool-call
    format, like MiniCPM4-MCP's Python-code-block output). Only meaningful together with
    model_override; the main client never uses this."""
    # Injected AsyncOpenAI so the SDK's own exponential backoff (which honors Retry-After) covers
    # 429/5xx. Confirmed live 2026-07-11: NIM's free-tier rate limit 429-crashed an entire
    # multi-agent run when the default 2 retries ran out — hosted endpoints throttle far below
    # what concurrent sub-agents generate. api.max_retries in config.yaml overrides the default.
    from openai import AsyncOpenAI
    api_cfg = config.cfg["api"]
    model_name = model_override or api_cfg["openai_model"]
    base_url = base_url_override or api_cfg["openai_base_url"]
    api_key = os.getenv("OPENAI_API_KEY", "dummy")
    # Explicit timeout, comfortably LONGER than both settings.max_run_minutes AND
    # settings.sub_agent_timeout_minutes, not the openai SDK's own default (600s / 10 minutes) —
    # found live 2026-07-14, same investigation as _run_single_task's new wait_for-based deadline
    # below: a runaway Gemma4 generation was continuously, validly decoding tokens (confirmed via
    # journalctl -u ollama's print_timing log) when the SDK's own blunt 600s connection timeout
    # silently killed it and threw a raw exception, which then triggered an ungraceful retry that
    # ran into the SAME pattern again (a second, ~6m24s hang). Without this, the SDK's shorter
    # default ALWAYS wins the race against our own explicit, graceful cutoffs whenever either
    # budget is configured to more than 10 minutes (the template defaults are 45 and 10) — making
    # those mechanisms dead code in realistic configs, only ever exercised by an artificially short
    # override like a quick smoke test. Setting the SDK's timeout to run comfortably past both
    # ensures OUR explicit, graceful cutoffs are what actually fire first.
    _max_run_minutes = config.cfg.get("settings", {}).get("max_run_minutes", 0) or 0
    _sub_timeout_minutes = config.cfg.get("settings", {}).get("sub_agent_timeout_minutes", 0) or 0
    sdk_timeout = max(_max_run_minutes * 60, _sub_timeout_minutes * 60, 0) + 300
    sdk_timeout = max(sdk_timeout, 3600)
    return OpenAIChatCompletionClient(
        base_url=base_url,
        api_key=api_key,
        model=model_name,
        async_client=AsyncOpenAI(
            base_url=base_url,
            api_key=api_key,
            max_retries=api_cfg.get("max_retries", 6),
            timeout=sdk_timeout,
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
    Returns (agent, session, dispatch_task). Session is None when conversational memory is
    disabled. Agent is re-created each call to pick up config changes (thinking toggle).
    dispatch_task is the same closure `delegate_tasks` wraps (_run_single_task) — passing it to
    engine.completion.run_completion_check lets the Build->Review->Fix loop dispatch fresh-context
    Builder/PeerReviewer sub-agents directly, without going through the Planner's own conversation.
    """
    global _session
    client = _build_client()

    # Per-DISPATCH wall-clock ceiling for EVERY sub-agent call (Searcher/Analyzer/Builder/
    # FindingsWriter/PeerReviewer) -- settings.sub_agent_timeout_minutes, deliberately INDEPENDENT
    # of settings.max_run_minutes (see that key's own config_template.yaml comment for why: a
    # shared/anchored-to-run-start deadline races against the Planner's own top-level guard and
    # typically loses -- asyncio.wait_for's cancellation propagates from the outer coroutine down
    # through the inner one as soon as the OUTER timeout fires, pre-empting the inner deadline's
    # own check before it ever gets a chance to fire on its own terms; confirmed live 2026-07-14,
    # a same-value test showed the inner marker text never actually appeared in the session log).
    # This value alone (read once per run here, applied fresh per dispatch inside
    # _run_single_task below) is what actually closes the gap: _run_single_task's stream loop
    # previously had NO deadline of any kind, so a single sub-agent turn that ran away into a
    # pathologically long generation (confirmed live: one Gemma4 turn decoded 19,908+ tokens,
    # continuously and validly, no repetition/stall -- see journalctl -u ollama's own print_timing
    # log) was invisible to every budget in this project and relied entirely on the raw
    # openai-SDK HTTP client's blunt default ~600s connection timeout, which discards the ENTIRE
    # in-progress response and raises a generic error instead of cutting the turn short gracefully
    # with whatever text had already been generated (exactly the failure mode run_cli's own
    # 2026-07-12 fix docstring describes and was built to prevent, just never propagated to this
    # sibling code path -- see _build_client's own sdk_timeout comment for the matching fix on the
    # SDK's own blunt timeout, which would otherwise still win the race in realistic configs).
    _sub_agent_timeout_minutes = config.cfg.get("settings", {}).get("sub_agent_timeout_minutes", 0) or 0

    # Absolute ceiling for the one-time deadline ring-fence below (see task_deadline/
    # deadline_extended in _run_single_task) -- mirrors _build_client's own sdk_timeout formula
    # exactly (same inputs, same +300s/floor-3600s shape) so an extended task_deadline can never
    # be pushed past the openai SDK client's own blunt connection timeout. Without this cap, a
    # ring-fenced extension would silently reintroduce the exact SDK-wins-the-race bug
    # _build_client's sdk_timeout comment already documents fixing once (SDK timeout fires first,
    # discards the whole response, throws a raw exception instead of our graceful cutoff).
    _max_run_minutes_for_sdk_cap = config.cfg.get("settings", {}).get("max_run_minutes", 0) or 0
    _sdk_timeout_ceiling_seconds = max(
        _max_run_minutes_for_sdk_cap * 60, _sub_agent_timeout_minutes * 60, 0
    ) + 300
    _sdk_timeout_ceiling_seconds = max(_sdk_timeout_ceiling_seconds, 3600)

    # Computed here (not inline where used) so both the Planner's own instructions AND the
    # Builder sub-agent's instructions (formatted inside _run_single_task below) can reference
    # {report_style_instructions}/{citation_format_instructions} — Builder is now the only role
    # that writes final_report.md, so it needs the same style vars the Planner used to.
    report_style = config.cfg.get("settings", {}).get("report_style", "standard")
    _REPORT_STYLE_INSTRUCTIONS = {
        "academic": ACADEMIC_REPORT_STYLE_INSTRUCTIONS,
        "answer": ANSWER_REPORT_STYLE_INSTRUCTIONS,
    }
    _CITATION_FORMAT_INSTRUCTIONS = {
        "academic": ACADEMIC_CITATION_FORMAT_INSTRUCTIONS,
        "answer": ANSWER_CITATION_FORMAT_INSTRUCTIONS,
    }
    report_style_instructions = _REPORT_STYLE_INSTRUCTIONS.get(report_style, STANDARD_REPORT_STYLE_INSTRUCTIONS)
    citation_format_instructions = _CITATION_FORMAT_INSTRUCTIONS.get(report_style, STANDARD_CITATION_FORMAT_INSTRUCTIONS)
    # Framework default silently strips the actual validation-error detail from a rejected tool
    # call, replacing it with a bare "Error: Argument parsing failed." — confirmed live
    # 2026-07-12: this was the single most common error signature across an entire day of
    # multi-model benchmark testing (41 occurrences across today's session logs alone), and every
    # one of them was undiagnosable because the real Pydantic ValidationError (e.g. "query: Input
    # should be a valid string, got list") was thrown away instead of shown. This also directly
    # helps the MODEL self-correct on retry — a specific error is far more actionable than a
    # generic one — not just diagnostics for us. Applies to every agent built from this client
    # (Planner + every dispatched sub-agent share the one client instance created here).
    client.function_invocation_configuration["include_detailed_errors"] = True

    # settings.specialist_model (heterogeneous role tiering, 2026-07-18): optional second client,
    # pointed at a lighter/faster model, used ONLY for _SPECIALIST_MODEL_ROLES dispatches inside
    # _run_single_task below. Reuses the SAME client object (no-op) when unset or identical to the
    # main model, so the common case (one model for everything) allocates nothing extra. Real
    # cost, not free: confirmed live this GPU does not keep two different models resident in VRAM
    # simultaneously (a 12GB + 5GB pair already exceeds this card's ~16GB budget) — every switch
    # between this client and the main one costs a real model reload (~5-23s measured), paid once
    # per delegate_tasks round (Planner boundary), not per individual specialist tool call.
    specialist_model = config.cfg.get("settings", {}).get("specialist_model")
    specialist_base_url = config.cfg.get("settings", {}).get("specialist_base_url")
    if specialist_model and specialist_model != config.cfg["api"]["openai_model"]:
        specialist_client = _build_client(
            model_override=specialist_model, base_url_override=specialist_base_url
        )
        specialist_client.function_invocation_configuration["include_detailed_errors"] = True
    else:
        specialist_client = client

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
            # Stable per-dispatch identity for the quota ring-fence's per-task rescue tracking
            # (tools/core.py::check_quota) — see utils/run_state.py's task_id_ctx header comment.
            task_id_token = task_id_ctx.set(_next_task_id())
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

                # Disambiguate a task_name reused across multiple real dispatches (the original
                # delegate_tasks batch, then again in a later re-delegation after a
                # completion-check nudge) — e.g. 'SubAgent_background' -> 'SubAgent_background#2'.
                # Without this the session log/UI shows two separate short invocations as one
                # source label, making elapsed-time analysis meaningless (confirmed live: looked
                # exactly like one continuous 19-minute sub-agent, was actually two ~2-3 minute
                # ones with an 11-minute gap where the Planner was busy elsewhere).
                raw_agent_name = f"SubAgent_{task_name}"
                _rs = run_state_ctx.get()
                agent_name = _rs.next_subagent_label(raw_agent_name) if _rs is not None else raw_agent_name

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
                        report_style_instructions=report_style_instructions,
                        citation_format_instructions=citation_format_instructions,
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
                        report_style_instructions=report_style_instructions,
                        citation_format_instructions=citation_format_instructions,
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

                    # settings.specialist_model tiering: leaf specialist roles (see
                    # _SPECIALIST_MODEL_ROLES) get the lighter/faster client when configured;
                    # every other role (Builder/FindingsWriter/PeerReviewer, or an unrecognized
                    # agent_id) stays on the main client — same object as `client` when tiering
                    # isn't configured, so this is a no-op in the common case.
                    dispatch_client = specialist_client if agent_id in _SPECIALIST_MODEL_ROLES else client
                    sub_agent = dispatch_client.as_agent(
                        name=_sanitize_name(agent_name),
                        instructions=sub_instr,
                        tools=sub_tools + mcp_tools,
                        default_options=_get_default_options()
                    )
                    final_text = ""
                    current_input = instructions
                    has_requests = True
                    malformed_retries = 0
                    # Sibling of malformed_retries, for the in-band tool-error class
                    # tool_result_error_nudge covers (hallucinated tool name, rejected arguments,
                    # missing file) — see its docstring for why this needed its own counter/path
                    # rather than reusing the exception-only malformed_tool_call_nudge.
                    tool_error_retries = 0
                    pending_tool_error_nudge = None
                    # Context-budget guard (see get_context_budget): Analyzer-tier tasks are the
                    # likeliest overflow point — 30 capped reads of a 25KB source still exceed a
                    # 16K-token num_ctx inside ONE task stream. TUI main loop deliberately not
                    # guarded (a user at the keyboard can /stop), same policy as max_run_minutes.
                    context_budget = get_context_budget()
                    stream_chars = 0
                    budget_nudged = False
                    # Fresh per-DISPATCH deadline (not per internal retry-turn below, and not
                    # shared with any other concurrent/sequential dispatch) -- covers this whole
                    # sub-agent call's total wall-clock budget, same "one deadline for the whole
                    # thing" semantics max_run_minutes already applies to the Planner's own run.
                    task_start = time.monotonic()
                    task_deadline = (
                        task_start + _sub_agent_timeout_minutes * 60
                    ) if _sub_agent_timeout_minutes else None
                    # Ring-fence, mirroring tools/core.py::check_quota's existing one-time-per-task
                    # rescue (2026-07-21, "4th synthesis-vanishing mechanism"): a dispatch that has
                    # already fetched something real (task_fetched_urls_ctx non-empty) but hasn't
                    # finished synthesizing it gets ONE deadline extension instead of being cut off
                    # mid-turn -- otherwise the real fetch and its summary permanently split across
                    # two un-mergeable findings entries (see ROADMAP.md's writeup). Bounded twice
                    # over: only fires once per dispatch (deadline_extended), and never past
                    # _sdk_timeout_ceiling_seconds minus a safety margin (see that variable's own
                    # comment for why).
                    deadline_extended = False

                    def _try_extend_deadline_once() -> bool:
                        nonlocal task_deadline, deadline_extended
                        if deadline_extended or not task_deadline:
                            return False
                        if not (task_fetched_urls_ctx.get() or None):
                            return False
                        extended = _ring_fenced_deadline(
                            task_start, task_deadline, _sub_agent_timeout_minutes,
                            _sdk_timeout_ceiling_seconds,
                        )
                        if extended is None:
                            return False
                        task_deadline = extended
                        deadline_extended = True
                        return True

                    while has_requests:
                        has_requests = False
                        user_input_requests = []

                        try:
                            stream = sub_agent.run(current_input, stream=True)
                            # Manually driven __anext__ + asyncio.wait_for, NOT `async for update
                            # in stream` -- same fix as engine/tui.py's run_cli (2026-07-12), now
                            # ported here (2026-07-14) after the identical failure mode was
                            # confirmed live at this exact call site: a plain async-for only
                            # checks a deadline once it actually RECEIVES an update, so a stream
                            # that goes a very long time between updates (or one single very long
                            # generation) is invisible to any deadline until it finally yields
                            # something. Racing each __anext__() against the remaining run budget
                            # via asyncio.wait_for fires the cutoff on a real wall-clock timer
                            # regardless of how long the stream goes quiet.
                            stream_iter = stream.__aiter__()
                            while True:
                                if task_deadline:
                                    remaining = task_deadline - time.monotonic()
                                    if remaining <= 0:
                                        if _try_extend_deadline_once():
                                            continue
                                        final_text += (
                                            f"\n\n[SYSTEM: task '{task_name}' cut short -- "
                                            f"sub_agent_timeout_minutes ({_sub_agent_timeout_minutes}) exceeded.]")
                                        break
                                else:
                                    remaining = None
                                try:
                                    update = await asyncio.wait_for(stream_iter.__anext__(), timeout=remaining)
                                except asyncio.TimeoutError:
                                    if _try_extend_deadline_once():
                                        continue
                                    final_text += (
                                        f"\n\n[SYSTEM: task '{task_name}' cut short -- "
                                        f"sub_agent_timeout_minutes ({_sub_agent_timeout_minutes}) exceeded "
                                        f"(stream produced no update before the deadline).]")
                                    break
                                except StopAsyncIteration:
                                    break
                                if subagent_callback:
                                    await subagent_callback(update, is_subagent=True, agent_name=agent_name)
                                for c in update.contents:
                                    if c.type == "text" and c.text:
                                        final_text += c.text
                                    elif c.type == "function_result":
                                        # Overwritten on every function_result seen this turn, not
                                        # just set-once — a LATER successful call after an earlier
                                        # error means the model already self-corrected on its own
                                        # within the SDK's internal loop, so only the error still
                                        # standing at the end of the stream (if any) gets nudged.
                                        pending_tool_error_nudge = tool_result_error_nudge(
                                            str(getattr(c, "result", "") or "")
                                        )

                                if getattr(update, "user_input_requests", None):
                                    user_input_requests.extend(update.user_input_requests)
                                stream_chars += stream_content_chars(update)
                                if context_budget and stream_chars > context_budget:
                                    break
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

                        if pending_tool_error_nudge and tool_error_retries < 2:
                            tool_error_retries += 1
                            nudge, pending_tool_error_nudge = pending_tool_error_nudge, None
                            from agent_framework import Message
                            new_inputs = [current_input] if isinstance(current_input, str) else list(current_input)
                            new_inputs.append(Message("user", [{"type": "text", "text": nudge}]))
                            current_input = new_inputs
                            has_requests = True
                            continue
                        pending_tool_error_nudge = None

                        if context_budget and stream_chars > context_budget and not budget_nudged:
                            # One wrap-up turn: cut the stream, tell the sub-agent to return its
                            # findings NOW. A second overshoot falls through and returns whatever
                            # final_text accumulated — never loop on the nudge itself.
                            budget_nudged = True
                            stream_chars = 0
                            from agent_framework import Message
                            new_inputs = [current_input] if isinstance(current_input, str) else list(current_input)
                            new_inputs.append(Message("user", [{"type": "text", "text": SUBAGENT_BUDGET_NUDGE}]))
                            current_input = new_inputs
                            has_requests = True
                            final_text += f"\n\n[SYSTEM: task '{task_name}' hit its context budget — findings below were wrapped up early.]"
                            continue

                        if user_input_requests:
                            has_requests = True
                            responses = []
                            if subagent_callback:
                                responses = await subagent_callback(None, is_subagent=True, agent_name=agent_name, approval_requests=user_input_requests)

                            new_inputs = [current_input] if isinstance(current_input, str) else list(current_input)
                            if responses:
                                new_inputs.extend(responses)
                            current_input = new_inputs

                if subagent_callback:
                    await subagent_callback(None, is_subagent=True, agent_name=agent_name, is_done=True)

                new_urls = task_fetched_urls_ctx.get() or []

                # Computed unconditionally (NOT gated behind grounding_check.enabled below) --
                # the add_finding fallback further down needs this as the real source when an
                # Analyzer fetched nothing itself, regardless of whether the reconstructed-URL
                # warning check that also reads it is turned on.
                from utils.grounding import extract_cited_urls
                reference_urls = {u.rstrip("/") for u in extract_cited_urls(instructions)}

                # Upstream verification (Tier-2 Searcher output only, detected generically via
                # target_children rather than hardcoded agent names — Analyzers are leaf nodes with
                # no children). Catches a hallucinated citation in a specialist's summary before it
                # ever reaches the Planner's context, instead of only at final-report time — error
                # propagation into findings.md was previously undetectable until the very end of
                # the run, by which point the retry budget was already partly spent.
                # Collected separately from final_text (rather than appended in place) so the
                # add_finding call below can guarantee these warnings survive its 1500-char
                # truncation. Confirmed live (2026-07-18 fine-tune benchmark): a Searcher's own
                # final_text hallucinated citations to URLs never fetched, this exact
                # verification warning correctly fired, but it was appended to the END of a
                # final_text already >1500 chars — `final_text[:1500]` then silently sliced the
                # warning off before it ever reached FindingsWriter, and the fabricated citations
                # went in ungrounded. A warning that gets silently truncated away is worse than no
                # check at all — it looks like defense-in-depth while doing nothing.
                verification_warnings = ""

                gc_cfg = config.cfg.get("settings", {}).get("grounding_check", {})
                # enabled is the grounding_check section's master switch (2026-07-12 audit, G2)
                if target_children and gc_cfg.get("enabled", True) and gc_cfg.get("verify_specialist_output", True):
                    from utils.grounding import real_grounding_problem
                    problem = await real_grounding_problem(final_text)
                    if problem and problem != "no_urls":
                        verification_warnings += (
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
                                verification_warnings += (
                                    f"\n\n[SYSTEM RELEVANCE WARNING: none of the sources fetched for "
                                    f"this task actually mention {entities_str}, despite the task "
                                    f"instructions requiring it. This may be an off-topic or "
                                    f"wrong-country source — verify before presenting these findings "
                                    f"as being about {entities_str}.]"
                                )

                # Downstream half of the SAME real bug class as the delegate_tasks filename/URL
                # checks above (2026-07-19 audit) — that fix stops a Searcher handing an Analyzer a
                # GUESSED reference; this catches the mirror direction, an Analyzer reporting a
                # GUESSED/reconstructed URL back UP in its own summary instead of the real one it
                # was told to analyze. Both WEB_SEARCHER_INSTRUCTIONS and DATA_ANALYZER_INSTRUCTIONS
                # already warn against this in prompt text ("NEVER guess or reconstruct a URL from a
                # filename") but had no structural backstop — unlike the Searcher-tier check above
                # (gated on target_children, since Analyzers are leaf nodes with none), which is why
                # this needs its own branch with a different reference set: an Analyzer never fetches
                # anything itself (no fetch_url_to_workspace tool), so `get_fetched_urls()` is the
                # wrong ground truth here — the correct one is the URL(s) actually present in THIS
                # task's own `instructions`, which the delegate_tasks fix above now guarantees are
                # real. No live-observed occurrence of this direction yet (unlike the other, which
                # was caught live) — added proactively since it's the same mechanism, same fix shape.
                elif (not target_children and target_config and target_config.name in ("DocumentAnalyzer", "DataAnalyzer")
                      and gc_cfg.get("enabled", True) and gc_cfg.get("verify_specialist_output", True)):
                    from utils.grounding import _urls_prefix_match
                    if reference_urls:
                        reconstructed = [
                            u for u in extract_cited_urls(final_text)
                            if u.rstrip("/") not in reference_urls
                            and not any(_urls_prefix_match(u.rstrip("/"), r) for r in reference_urls)
                        ]
                        if reconstructed:
                            verification_warnings += (
                                f"\n\n[SYSTEM VERIFICATION WARNING: this summary cites "
                                f"{reconstructed[0]!r}, which does not match the source URL you were "
                                f"actually given to analyze ({', '.join(sorted(reference_urls))}) — "
                                f"this looks like a reconstructed or guessed URL, not the real one. "
                                f"Do not treat the associated claim as sourced when writing "
                                f"findings.md.]"
                            )

                final_text += verification_warnings

                # Populate the structured findings store (previously dead code — RunState.add_finding
                # was defined but never called) so a completion-check retry or later debugging has real
                # {source_url, summary} records instead of only the model's own narration. Preferring
                # URLs actually fetched during THIS task over the task name keeps findings traceable to
                # a real source when one exists (Searcher-tier calls); Analyzer-tier calls fetch nothing
                # themselves, so they fall back to the task name as the record's identifier.
                # task_name/depth (ROADMAP Phase 5, "Coverage accounting") let RunState.coverage()
                # group findings by which top-level (depth==1) task produced them, and tell a real
                # fetched-URL finding apart from a task-name fallback one -- see that method's own
                # docstring for why depth==1 only (a nested Analyzer's lack of a new URL is
                # expected, not a coverage gap).
                #
                # `_FINDING_SUMMARY_BUDGET` chars total, but the verification warnings (if any) are
                # NEVER part of the truncated slice — they're guaranteed room by reserving their
                # length off the body's share first, then concatenated back in full. See the
                # comment on `verification_warnings` above for why this ordering matters.
                #
                # 2026-07-19 (engine-driven iterative deepening, ROADMAP item 10): FOLLOW-UP
                # DIRECTIONS is specified to be the LAST section of a Searcher's summary, which
                # means it was the FIRST thing this same truncation silently dropped for any real
                # summary over ~1500 chars — the identical failure shape as the verification-warning
                # bug this budget already carves an exception for. Extracted and stored separately
                # (RunState.add_finding's own field, never subject to this budget), same treatment.
                follow_up_directions = _extract_follow_up_directions(final_text)
                body_budget = _FINDING_SUMMARY_BUDGET - len(verification_warnings)
                body_text = final_text[:len(final_text) - len(verification_warnings)] if verification_warnings else final_text
                finding_summary = body_text[:body_budget] + verification_warnings

                this_depth = delegation_depth_ctx.get()
                run_state = run_state_ctx.get()
                if run_state is not None and agent_id not in _NON_RESEARCH_DISPATCH_ROLES:
                    if new_urls:
                        for u in new_urls:
                            run_state.add_finding(u["url"], finding_summary, task_name=task_name, depth=this_depth,
                                                   follow_up_directions=follow_up_directions, agent_id=agent_id)
                    elif reference_urls:
                        # An Analyzer fetches nothing itself, but the Searcher that delegated to it
                        # is instructed to pass the real source URL IN its instructions (see
                        # prompts.py: "The Analyzer NEEDS the URL to include it in its summary") --
                        # reference_urls (above) already extracts exactly that. Recovering the real
                        # URL here instead of falling to task_name is the difference between a
                        # traceable finding and the 2026-07-21 fabrication bug: task_name silently
                        # standing in for source_url reached FindingsWriter with no marker that it
                        # wasn't a real URL, and got cited as one (5/19 findings in a live qwen3:8b
                        # run). See _build_findings_source_material's own non-http handling for the
                        # remaining defense-in-depth case (no reference URL either).
                        for u in reference_urls:
                            run_state.add_finding(u, finding_summary, task_name=task_name, depth=this_depth,
                                                   follow_up_directions=follow_up_directions, agent_id=agent_id)
                    else:
                        run_state.add_finding(task_name, finding_summary, task_name=task_name, depth=this_depth,
                                               follow_up_directions=follow_up_directions, agent_id=agent_id)

                # RAG findings cache (ROADMAP.md "Strategic options" item 5, 2026-07-20): only cache
                # a finding that passed the EXACT SAME grounding+relevance gate above with zero
                # warnings — a cache entry is exactly as verified as a same-run finding, never less.
                # Deliberately caches the atomic (source_url, summary) pair, not a whole answer, so a
                # future cache hit still requires the Searcher to incorporate/cite it itself, the
                # same as a fresh web_search result — this is what makes it safe across different
                # models, unlike the deleted knowledge_cache (929b987) it replaces.
                rag_cfg = config.cfg.get("settings", {}).get("rag_cache", {})
                if _should_cache_finding(
                    verification_warnings, new_urls, rag_cfg.get("enabled", False), finding_summary
                ):
                    from utils import rag_cache

                    model = config.cfg.get("api", {}).get("openai_model", "")
                    for u in new_urls:
                        rag_cache.save(task_name, u["url"], finding_summary, model)

                return f"## Result for {task_name}\n{final_text}\n---"
            finally:
                if children_token is not None:
                    available_sub_agents_ctx.reset(children_token)
                holds_token.reset(token_setter)
                delegation_depth_ctx.reset(depth_token)
                task_fetched_urls_ctx.reset(task_urls_token)
                task_id_ctx.reset(task_id_token)
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
                continue
            # Real, confirmed live failure (2026-07-19 corpus audit: 83/563 Analyzer-delegating
            # instructions across 19/76 runs referenced a URL never actually fetched this task) —
            # a Searcher sees a URL in a search-result SNIPPET and delegates an Analyzer task to
            # "read" it as if it had already been fetched, when fetch_url_to_workspace was never
            # called on it. The Analyzer then burns its whole quota guessing filename variants for
            # a file that structurally cannot exist (confirmed live: 9+ grep_workspace_file calls,
            # no Finished). Checked only for Analyzer-tier targets (DocumentAnalyzer/DataAnalyzer)
            # — those are the roles whose entire job is reading an already-fetched file, so this is
            # the one delegation shape where "the referenced URL must already be fetched" is a real
            # invariant, not a false constraint (WebSearcher/AcademicSearcher tasks legitimately
            # instruct new research on URLs nothing has fetched yet). Scoped to the CALLING task's
            # own real fetches (task_fetched_urls_ctx, not the whole run's) — a Searcher can only
            # honestly hand its Analyzer children what it itself actually fetched.
            if t.get("agent_id") in ("DocumentAnalyzer", "DataAnalyzer"):
                from utils.grounding import extract_cited_urls, _urls_prefix_match
                own_fetched_entries = task_fetched_urls_ctx.get() or []
                own_fetched = {e["url"].rstrip("/") for e in own_fetched_entries}
                cited = extract_cited_urls(str(t.get("instructions", "")))
                if cited:
                    unfetched = [
                        u for u in cited
                        if u.rstrip("/") not in own_fetched
                        and not any(_urls_prefix_match(u.rstrip("/"), f) for f in own_fetched)
                    ]
                    if unfetched:
                        errors.append(
                            f"Task {i} ({t.get('task_name')!r}): instructs {t.get('agent_id')} to "
                            f"read {unfetched[0]!r}, but that URL was never actually fetched this "
                            f"task — it looks like it came from a search-result snippet, not a real "
                            f"fetch_url_to_workspace call. {t.get('agent_id')} can only read files "
                            f"you actually fetched; if this source matters, call "
                            f"fetch_url_to_workspace on it yourself first, THEN delegate the "
                            f"analysis with the real saved filename."
                        )
                        continue
                # Sibling bug to the unfetched-URL check above, confirmed live in the SAME
                # benchmark run (2026-07-19): the URL WAS genuinely fetched, but the Searcher
                # constructed a GUESSED filename for the Analyzer task (a plausible-looking
                # pattern built from the URL's own path segments, e.g.
                # 'sources/bcpublication_org_BM_3487.md') instead of the REAL saved filename
                # fetch_url_to_workspace actually returned (e.g. 'sources/bcpublication_org_
                # 58286b27.md', hash-suffixed). Same root family as WEB_SEARCHER_INSTRUCTIONS'
                # existing "NEVER guess or reconstruct a URL from a filename" rule, just the
                # inverse direction, previously unguarded. Confirmed live: the Searcher dispatched
                # BOTH the guessed-filename task AND a correct-filename task for the identical URL
                # back to back — real wasted duplicate delegation, not just a wrong-filename typo.
                # Matched against the prompt's own fixed instruction template
                # ("Read the file '<path>'. Source URL: ...", see prompts.py's real Analyzer
                # dispatch examples) so this doesn't misfire on legitimately-phrased instructions.
                filename_m = re.search(r"[Rr]ead the file '([^']+)'", str(t.get("instructions", "")))
                if filename_m and cited:
                    claimed_filename = filename_m.group(1)
                    real_filename = next(
                        (e["filename"] for e in own_fetched_entries
                         if e["url"].rstrip("/") == cited[0].rstrip("/")
                         or _urls_prefix_match(cited[0].rstrip("/"), e["url"].rstrip("/"))),
                        None,
                    )
                    if real_filename and claimed_filename not in (real_filename, real_filename.split("/")[-1]):
                        errors.append(
                            f"Task {i} ({t.get('task_name')!r}): instructs {t.get('agent_id')} to "
                            f"read {claimed_filename!r}, but that filename was GUESSED, not the "
                            f"real one — the actual saved filename for that URL is "
                            f"{real_filename!r} (from your own fetch_url_to_workspace result). "
                            f"Never construct a filename from a URL's path; always use the exact "
                            f"filename the fetch tool returned."
                        )
                        continue
            # Non-generative routing classifier (RESEARCH.md §6, ROADMAP.md "Pending", 2026-07-20)
            # — catches a hallucinated agent_id ("searcher", "PeerReviewer", invented role names,
            # ~4.9% of real historical delegate_tasks calls) BEFORE dispatch, rather than only after
            # _run_single_task's own exact-string lookup fails per-task post-dispatch. Decision
            # logic lives in _agent_routing_rejection_reason (pure function, directly testable) —
            # this closure only gathers the caller's real roster and the classifier's prediction.
            agent_routing_cfg = config.cfg.get("settings", {}).get("agent_routing_classifier", {})
            if agent_routing_cfg.get("enabled", False):
                from utils.agent_routing import predict_agent_id, KNOWN_AGENT_IDS
                caller_role_names = frozenset(c.name for c in available_sub_agents_ctx.get())
                candidate_classes = caller_role_names & KNOWN_AGENT_IDS
                prediction = (
                    predict_agent_id(str(t.get("instructions", "")), candidate_classes)
                    if candidate_classes else None
                )
                reason = _agent_routing_rejection_reason(
                    t.get("agent_id"), caller_role_names, prediction,
                    agent_routing_cfg.get("min_confidence", 0.6),
                )
                if reason:
                    errors.append(f"Task {i} ({t.get('task_name')!r}): {reason}")
                    continue
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
            report_style_instructions=report_style_instructions,
            citation_format_instructions=citation_format_instructions,
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

    return agent, session, _run_single_task

def reset_session():
    """Clear the conversation session (called by /new)."""
    global _session
    _session = None
