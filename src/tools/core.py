import contextvars
import functools
import asyncio

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
        if ctx[tool_name]["used"] >= ctx[tool_name]["limit"]:
            ctx[tool_name]["used"] += 1
            if ctx[tool_name]["used"] > ctx[tool_name]["limit"] + 3:
                raise QuotaAbortException(f"Agent trapped in loop. Quota exceeded multiple times for {tool_name}.")
            return (
                f"Error: Quota reached. You have used the '{tool_name}' tool "
                f"{ctx[tool_name]['limit']} times out of your limit. "
                f"You MUST summarize what you've done and state clearly that you "
                f"had to stop due to quota limits."
            )
        ctx[tool_name]["used"] += 1
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
