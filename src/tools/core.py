import contextvars
import functools
import asyncio

from utils.run_state import task_id_ctx

# Shared tool-error sentinel, added 2026-07-29. Every tool in this project returns a formatted
# error STRING instead of raising (see engine/tui.py's _looks_like_tool_error) — but before this,
# each tool file invented its own crash-path prefix independently (a "grep failed" variant, a
# "fetch failed" variant, a "search failed" variant, and an internal-exception variant each spelled
# differently), and _looks_like_tool_error had to hand-list all of them (a 2026-07-19 QA audit
# found three that were missing entirely, showing a green checkmark on a crashed tool call). Per
# LLM tool-calling error-handling literature surveyed this session: converging on one shared
# PREFIX string is still the same fragile hand-parsed-text pattern, just with fewer variants -- the
# next new tool can just as easily invent one more. This constant exists so error-detection code
# checks for ONE stable marker instead of guessing at wording; every tool's error path should
# prefix its message with this (see fs.py/data.py/todos.py/web.py for the established call shape:
# f"{TOOL_ERROR_PREFIX}<what failed>: {type(e).__name__}: {e}"). Deliberately NOT a full
# traceback (see with_quota's own docstring) -- a traceback leaks local absolute filesystem paths
# into the model's context, which can end up copied verbatim into a shared report.
TOOL_ERROR_PREFIX = "Error: "

# --- TOOL QUOTA SYSTEM ---
# Protects local LLM workflows from infinite retry loops (e.g., repeatedly failing to parse a URL)
tool_quotas_ctx = contextvars.ContextVar('tool_quotas', default=None)

class QuotaAbortException(BaseException):
    """Raised when a tool is called repeatedly despite being over quota, indicating an LLM loop."""
    pass

# Tools eligible for the exact-repeat FREE-DEDUP below (2026-08-17, live incident) -- deliberately
# a tiny opt-in allowlist, not applied to every quota'd tool. read_workspace_file/grep_workspace_file
# are both idempotent, read-only, gated identically by _check_analyzer_read_cap (tools/fs.py) --
# the same filename/start_line/end_line (or pattern) always returns the same content unless
# something else wrote to that file in between, and a write/edit IS a different tool call, so it
# doesn't reset this tool's own tracked key, meaning even a legitimate re-read after an edit still
# executes normally and returns fresh content; only the quota bookkeeping is slightly more
# generous, never wrong. Other quota'd tools (web_search, fetch_url_to_workspace, write/edit_
# workspace_file) are NOT safe to add here without separate review -- identical args there don't
# reliably mean "wasted call" (a write/edit is not idempotent, and web_search/fetch results can
# legitimately change run to run).
_DEDUP_ELIGIBLE_TOOLS = frozenset({"read_workspace_file", "grep_workspace_file"})

# No-progress guard (2026-08-17, RESEARCH.md §18c): a GENERAL mechanism, unlike the free-dedup
# above -- applies to every quota'd tool, not an allowlist, because "the model called this tool
# with the exact same arguments and got the exact same error N times in a row" is itself the
# no-progress signal regardless of which tool it is (confirmed live: edit_workspace_file's own
# "old_string not found"/"appears N times" errors are exactly this shape). Named/framed after the
# published pattern this project independently rediscovered narrowly (the free-dedup above) before
# generalizing: "a no-progress guard that hashes repeated (tool, args, error) tuples... halts when
# the same tuple repeats 2 to 3 times." Threshold picked at the literature's conservative end (2,
# not 3) since this project's own quota system already has its own separate, coarser
# `used > limit + 3` hard abort as a backstop -- this guard exists to catch the loop EARLIER and
# with a more specific, actionable message than a generic "quota exceeded."
_NO_PROGRESS_ERROR_STREAK_LIMIT = 2

def check_quota(tool_name: str, call_key: tuple | None = None) -> str | None:
    """Check if the specific tool has exceeded its per-invocation quota.

    `call_key` (2026-08-17, live incident, session_status 2026-08-17 "run5" investigation):
    optional, only meaningful for `_DEDUP_ELIGIBLE_TOOLS`. Confirmed live via a real session
    transcript: a FindingsWriter dispatch called `read_workspace_file(findings.md, 1, 200)` with
    the EXACT SAME arguments 2-3 times in a row -- not re-reading after a change, just
    re-verifying unchanged state -- and burned its entire quota this way before it could finish
    its actual edit work ("Quota reached... used the 'read_workspace_file' tool 41/47 times"),
    forcing an early cutoff and another retry round. When the immediately preceding call to this
    SAME tool had the identical `call_key` (and this tool is in `_DEDUP_ELIGIBLE_TOOLS`) and the
    pool is still under its limit, this call is treated as a free repeat -- no `used` increment --
    while the wrapped function still runs normally and returns real (possibly updated) content.
    `_last_call_key` is stored on the tool's own pool entry, the same established pattern
    `_rescued_task_ids` below already uses for per-tool private bookkeeping."""
    ctx = tool_quotas_ctx.get()
    # DEEPDELVE_QUOTA_DEBUG=1: one line per quota check to stderr, with the pool's object id —
    # the 'shared cumulative pool' design silently degrades to 'unlimited' for any tool call the
    # framework happens to execute outside the run's context (ctx=None), and that is invisible
    # without this. Found via run 12: 36 executed web_searches against a hard cap of 21.
    import os as _os
    if _os.environ.get("DEEPDELVE_QUOTA_DEBUG"):
        import sys as _sys
        state = f"pool_id={id(ctx)} used={ctx.get(tool_name, {}).get('used')}/{ctx.get(tool_name, {}).get('limit')}" if ctx else "ctx=None (UNLIMITED)"
        print(f"[quota_debug] {tool_name}: {state}", file=_sys.stderr)
    if ctx and tool_name in ctx:
        entry = ctx[tool_name]
        if (call_key is not None and entry.get("_error_streak_key") == call_key
                and entry.get("_error_streak_count", 0) >= _NO_PROGRESS_ERROR_STREAK_LIMIT):
            # No-progress guard (see _NO_PROGRESS_ERROR_STREAK_LIMIT's own docstring) -- this exact
            # (tool, args) pair has already failed with the same error this many times in a row.
            # Does NOT consume a quota unit: the model isn't over quota, it's stuck, a different
            # problem with a different fix (change the arguments / a different tool / move on).
            return (
                f"Error: you have called '{tool_name}' with the exact same arguments and gotten "
                f"the exact same error {entry['_error_streak_count']} times in a row. Repeating "
                f"this identical call again will not produce a different result -- change your "
                f"arguments, use a different tool, or move on to your next step."
            )
        if tool_name in _DEDUP_ELIGIBLE_TOOLS and call_key is not None:
            if entry["used"] < entry["limit"] and entry.get("_last_call_key") == call_key:
                return None  # exact repeat of the immediately preceding call -- no new quota spent
            entry["_last_call_key"] = call_key
        if entry["used"] >= entry["limit"]:
            # Ring-fence against the shared-pool starvation bug (ROADMAP "Findings from live
            # testing", confirmed live 2026-07-14, 2026-07-18, and again 2026-07-21 in a fresh
            # gpt-oss benchmark run). The pool is ONE dict shared cumulatively across every
            # sub-agent dispatch this run (by design, see build_quota_pool's docstring) — a task
            # can be cut off by a sibling task's share of the same pool before it ever gets a real
            # turn at all.
            #
            # Generalized 2026-07-19 QA audit (per-task fairness): originally a single `_rescued`
            # bool meant only the FIRST task per tool/run to hit the wall got rescued. Now tracked
            # per-task via task_id_ctx (a stable per-dispatch id, see utils/run_state.py) in a
            # set, so EVERY distinct task gets exactly one rescue — bounded (each task id can only
            # ever be rescued once per tool, same "one small one-time top-up" ceiling as before).
            #
            # ROADMAP's tracked open angle (a), CLOSED 2026-07-21: the rescue used to additionally
            # require task_fetched_urls_ctx to be non-empty ("has this task's dispatch already
            # fetched a real URL"), on the theory that only PROVEN progress deserves a top-up. Live
            # evidence found this backwards: web_search itself never touches task_fetched_urls_ctx
            # (only fetch_url_to_workspace does), so a task blocked on its OWN FIRST web_search
            # call — the exact shape seen live, four sibling comparison tasks starved by one
            # heavily-redispatched sibling that ate the shared pool first — could never satisfy
            # that condition no matter what, since it never got far enough to fetch anything. A
            # task that hasn't started yet needs a fair first shot MORE than one already proven to
            # be working, not less. Dropped the requirement: every distinct task_id now gets one
            # guaranteed grace top-up the first time it hits the wall, proven progress or not —
            # still bounded to exactly once per task_id per tool (rescued_ids), and the existing
            # `used > limit + 3` hard abort below still catches genuine infinite-loop spinning.
            task_id = task_id_ctx.get()
            rescued_ids = entry.setdefault("_rescued_task_ids", set())
            if task_id is not None and task_id not in rescued_ids:
                rescued_ids.add(task_id)
                entry["limit"] += 2
                entry["used"] += 1
                return None
            entry["used"] += 1
            if entry["used"] > entry["limit"] + 3:
                raise QuotaAbortException(f"Agent trapped in loop. Quota exceeded multiple times for {tool_name}.")
            return (
                f"Error: Quota reached. You have used the '{tool_name}' tool "
                f"{entry['limit']} times out of your limit. "
                f"You MUST summarize what you've done and state clearly that you "
                f"had to stop due to quota limits."
            )
        entry["used"] += 1
    return None

# Structural gate for FindingsWriter's Write/Fix dispatch (2026-07-22): a prompt-only reorder of
# FINDINGS_WRITER_INSTRUCTIONS asking the model to write findings.md from the compiled evidence
# base BEFORE reading raw source files did NOT change behavior in live re-test -- gpt-oss:20b's
# first tool call was still read_workspace_file, against a shared read_workspace_file quota
# already exhausted by the run's earlier search phase, producing a 1-entry findings.md again.
# Mirrors this project's standing pattern of backing an unreliable prompt-only nudge with a
# structural check (check_quota itself; _dispatch_writer_review_fix's reads_before/reads_after
# CLEAN-review distrust) instead of trusting compliance alone. None (not armed) for every role
# except an active FindingsWriter dispatch -- Builder's own instructions correctly require
# reading findings.md FIRST, so Builder must never be gated by this.
writer_gate_ctx = contextvars.ContextVar('writer_gate_ctx', default=None)

def check_writer_gate(tool_name: str, filename: str | None = None) -> str | None:
    """Blocks read_workspace_file/grep_workspace_file until the active gate's write_workspace_file
    (or edit_workspace_file -- see below) call has happened. No-op (returns None) unless a caller
    armed the gate via writer_gate_ctx.set for this specific dispatch.

    `filename` (2026-08-16 follow-up incident): a read/grep of the GATE'S OWN target file (e.g.
    findings.md, for a per-facet ADD-ONLY dispatch) is exempt from the block entirely -- it's the
    only way `edit_workspace_file` can ever get a valid `old_string` anchor for a file the
    dispatch's fresh context has never seen, and it is categorically NOT the "abandon the compiled
    evidence to go hunt raw source files instead" case this gate exists to prevent. Without this
    exemption the gate was a real, live-confirmed deadlock: the per-facet dispatch's own
    instructions say to use edit_workspace_file, but it structurally cannot find a valid anchor
    without reading the target file first, and the gate refused every such read -- the model just
    retried a few blocked reads, then gave up with NOTHING written for that facet at all. Reads of
    any OTHER file (a raw source under sources/) still block exactly as before."""
    gate = writer_gate_ctx.get()
    if not gate or gate.get("write_done"):
        return None
    target_file = gate.get("target_file")
    if tool_name in ("read_workspace_file", "grep_workspace_file") and target_file and filename == target_file:
        return None
    # recommended_tool (2026-08-16 follow-up incident, see _dispatch_writer_review_fix's own
    # docstring): edit_workspace_file satisfying the gate (below) isn't enough on its own if the
    # BLOCK MESSAGE still hardcodes "write_workspace_file" -- a per-facet ADD-ONLY dispatch, told
    # by its own instructions to use edit_workspace_file, hit this message, was pointed at the
    # WRONG tool, and confirmed live: rather than either following the (wrong) advice or its own
    # (right) instructions, it just kept retrying blocked reads a few times, then gave up with
    # NOTHING written for that facet at all -- worse than the original overwrite risk, not better.
    # Each dispatch site names its own real tool via recommended_tool; defaults to
    # write_workspace_file for the original two-pass full-write case.
    recommended_tool = gate.get("recommended_tool") or "write_workspace_file"
    if tool_name in ("read_workspace_file", "grep_workspace_file"):
        return (
            "Error: write your first complete findings.md from the evidence base in your task "
            f"instructions BEFORE reading any source file directly -- call {recommended_tool} "
            "now. Raw source files are only for enrichment AFTER that first write."
        )
    # Both tools always satisfy the gate regardless of which one was recommended (2026-08-16):
    # a model that reaches for the OTHER real write tool anyway still made real forward progress,
    # and refusing that would just recreate the same trap for the opposite tool choice.
    if tool_name in ("write_workspace_file", "edit_workspace_file"):
        gate["write_done"] = True
    return None


def refund_quota(tool_name: str) -> None:
    """Give back one quota unit when a tool call failed for environmental reasons (provider
    throttling/outage) rather than model misuse — the budget exists to stop model loops, not to
    punish the model for infrastructure weather."""
    ctx = tool_quotas_ctx.get()
    if ctx and tool_name in ctx and ctx[tool_name]["used"] > 0:
        ctx[tool_name]["used"] -= 1


def _get_tool_rule(tool_name: str, rule_key: str, default_val: int) -> int:
    """Extract custom quota rules (like max_lines) for a specific tool."""
    ctx = tool_quotas_ctx.get()
    if ctx and tool_name in ctx and "rules" in ctx[tool_name]:
        return ctx[tool_name]["rules"].get(rule_key, default_val)
    return default_val

def _first_arg_filename(args, kwargs) -> str | None:
    """Every gated tool (read_workspace_file, grep_workspace_file, write_workspace_file,
    edit_workspace_file) takes `filename` as its first positional/keyword parameter -- pulled out
    once here so `with_quota`'s wrapper can pass it to `check_writer_gate` without hardcoding a
    specific tool's full signature."""
    if args:
        return args[0]
    return kwargs.get("filename")


def _build_call_key(args, kwargs) -> tuple:
    """A hashable key identifying one tool call's own arguments -- used by both the free-repeat
    dedup (`_DEDUP_ELIGIBLE_TOOLS`-gated in `check_quota`) and the general no-progress guard
    (ungated, every tool). Computed unconditionally now (2026-08-17) -- it used to only be built
    for dedup-eligible tools, but the no-progress guard needs it for every tool, so there is no
    longer a reason to gate the computation itself, only its two DIFFERENT uses inside
    `check_quota`."""
    return (args, tuple(sorted(kwargs.items())))


def _record_call_outcome(tool_name: str, call_key: tuple, result) -> None:
    """Updates the no-progress guard's own per-tool streak tracking, called AFTER the wrapped
    function returns (this is the one piece `check_quota` itself can't do -- it runs BEFORE the
    real call, with no way to know whether it will fail). A result string starting with
    `TOOL_ERROR_PREFIX` extends the streak if it matches the SAME `call_key` as the last recorded
    error for this tool, else starts a fresh streak of 1; any non-error result resets the streak to
    0 -- a single real success is proof the model isn't stuck anymore, regardless of how many
    failures came before it."""
    ctx = tool_quotas_ctx.get()
    if not ctx or tool_name not in ctx:
        return
    entry = ctx[tool_name]
    is_error = isinstance(result, str) and result.startswith(TOOL_ERROR_PREFIX)
    if not is_error:
        entry["_error_streak_count"] = 0
        entry["_error_streak_key"] = None
        return
    if entry.get("_error_streak_key") == call_key:
        entry["_error_streak_count"] = entry.get("_error_streak_count", 0) + 1
    else:
        entry["_error_streak_key"] = call_key
        entry["_error_streak_count"] = 1


def with_quota(func):
    """Decorator to enforce quotas dynamically based on the function's name, and surface a
    caught exception's message (type + str, no full traceback -- see the 2026-08-02 audit note
    below) back to the model as a normal tool-error string instead of crashing the dispatch."""
    if asyncio.iscoroutinefunction(func):
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            call_key = _build_call_key(args, kwargs)
            if err := check_quota(func.__name__, call_key): return err
            if err := check_writer_gate(func.__name__, _first_arg_filename(args, kwargs)): return err
            try:
                result = await func(*args, **kwargs)
                _record_call_outcome(func.__name__, call_key, result)
                return result
            except Exception as e:
                # Message + exception TYPE only, no full traceback -- a traceback leaks local
                # absolute filesystem paths into the model's context, which can end up copied
                # verbatim into findings.md/final_report.md (2026-08-02 audit). The full
                # traceback is still available via traceback.format_exc() to anyone reading the
                # actual server-side logs, just not handed to the model.
                result = f"{TOOL_ERROR_PREFIX}{func.__name__} failed internally: {type(e).__name__}: {e}"
                _record_call_outcome(func.__name__, call_key, result)
                return result
        return async_wrapper
    else:
        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            call_key = _build_call_key(args, kwargs)
            if err := check_quota(func.__name__, call_key): return err
            if err := check_writer_gate(func.__name__, _first_arg_filename(args, kwargs)): return err
            try:
                result = func(*args, **kwargs)
                _record_call_outcome(func.__name__, call_key, result)
                return result
            except Exception as e:
                result = f"{TOOL_ERROR_PREFIX}{func.__name__} failed internally: {type(e).__name__}: {e}"
                _record_call_outcome(func.__name__, call_key, result)
                return result
        return sync_wrapper
