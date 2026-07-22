import contextvars
import functools
import asyncio

from utils.run_state import task_fetched_urls_ctx, task_id_ctx

# --- TOOL QUOTA SYSTEM ---
# Protects local LLM workflows from infinite retry loops (e.g., repeatedly failing to parse a URL)
tool_quotas_ctx = contextvars.ContextVar('tool_quotas', default=None)

class QuotaAbortException(BaseException):
    """Raised when a tool is called repeatedly despite being over quota, indicating an LLM loop."""
    pass

def check_quota(tool_name: str) -> str | None:
    """Check if the specific tool has exceeded its per-invocation quota."""
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

def with_quota(func):
    """Decorator to enforce quotas dynamically based on the function's name and surface full diagnostic tracebacks safely."""
    import traceback
    if asyncio.iscoroutinefunction(func):
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            if err := check_quota(func.__name__): return err
            try:
                return await func(*args, **kwargs)
            except Exception:
                return f"CRITICAL TOOL EXECUTION ERROR: {func.__name__} failed internally.\n\nException Details:\n{traceback.format_exc()}"
        return async_wrapper
    else:
        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            if err := check_quota(func.__name__): return err
            try:
                return func(*args, **kwargs)
            except Exception:
                return f"CRITICAL TOOL EXECUTION ERROR: {func.__name__} failed internally.\n\nException Details:\n{traceback.format_exc()}"
        return sync_wrapper
