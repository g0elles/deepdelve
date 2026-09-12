"""Shared run-lifecycle loop, extracted from three independently hand-duplicated copies
(run_cli, run_agent/BasicTuiAgent in this package's tui.py, and _run_research in api.py).

Phase 0 of the unification plan (see session_status/CURRENT.md, 2026-09-11): this is the
reference implementation, transcribed from run_cli's loop body (the most complete of the
three copies -- it's the only one with both the context-budget and wall-clock-deadline axes
plus the 2026-08-27 QuotaAbortException fallthrough fix). Not yet wired into any caller.

RunLoopSurface parameterizes exactly what differs between the three call sites: how a stream
update is rendered (stdout / Textual widgets / an SSE queue), how a tool-call approval is
resolved (auto-approve-only vs. TUI's client-side execution), whether a context/wall-clock
budget applies at all (run_agent has none by design -- a human can /stop), and whether the
completion check should be skipped this turn (a same-conversation follow-up with an existing
report). Everything else -- the malformed-tool-call/QuotaAbortException dispatch, the budget
nudge-then-cutoff mechanics, the completion-check invocation, and crash-time run_state.save()
on every exception path -- lives here once.
"""

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from agent_framework import Message

from engine.completion import run_completion_check
from engine.orchestrator import (
    classify_malformed_retry,
    iter_agent_stream,
    stream_content_chars,
)


@dataclass
class RunLoopSurface:
    """One instance per call site (run_cli / run_agent / _run_research)."""

    # Called once per stream Content item with the raw item; surface renders it however it
    # wants (stdout write, Textual widget mount/update, SSE queue.put) and does its own
    # persistence (log_stream_content etc.) inside this callback.
    on_stream_content: Callable[[Any], Awaitable[None]]

    # notify(msg) -> None. Passed straight through to run_completion_check's own `notify` and
    # also used for this loop's own budget/abort/retry announcements.
    notify: Callable[[str], None]

    # Called once per turn with the turn's pending function-call requests (the same objects
    # `update.user_input_requests` carries). Returns the list of Message objects to append to
    # current_input -- mirrors each surface's existing inline logic exactly (auto-approve/deny
    # response pair, or the TUI's widget-wait + client-side tool execution + two-message pair).
    handle_approvals: Callable[[list], Awaitable[list]]

    skip_completion_check: bool

    # None means "not applicable" (run_agent, by design -- a human can /stop). A real int/float
    # enables the corresponding guard, exactly like run_cli's `context_budget`/`budget_deadline`
    # locals today.
    context_budget: Optional[int]
    budget_deadline: Optional[float]

    query: str
    dispatch_task: Callable
    find_substantial_text: Callable[[], str]
    run_state: Any

    # Optional: called once per turn with that turn's full accumulated text (only when
    # non-empty), right after the malformed/quota exception handling. run_cli doesn't need this
    # (its find_substantial_text scans a persisted session-event log that on_stream_content
    # already writes to as text streams in); _run_research has no such persisted log, so it
    # accumulates its own in-memory turn-text history here instead, mirroring what used to be an
    # inline `if turn_text: planner_text_history.append(turn_text)` in its own loop body.
    on_turn_text: Optional[Callable[[str], None]] = None


async def run_agent_loop(agent, session, current_input, surface: RunLoopSurface) -> None:
    """The one `while has_requests:` loop. Mutates `run_state`/`current_input` in place via the
    surface; callers don't need this function's return value (it has none) -- all three read
    final state off `run_state`/their own conversation state after this returns.

    Every path out of this function -- normal completion, a give-up on malformed retries, a
    QuotaAbortException, an unrecognized exception -- leaves `run_state.save()` called before
    control leaves the loop (the malformed/quota branches set `run_state.attempt` and fall
    through to the completion check; a genuinely unrecognized exception hits the `except
    Exception` below). This closes a confirmed gap: _run_research previously only saved
    run_state on `asyncio.CancelledError`, so a generic exception (e.g. classify_malformed_
    retry's `reraise=True` path) reached its worker with no forensic _run_state.json update.
    """
    run_state = surface.run_state
    has_requests = True
    malformed_retries = 0
    run_stream_chars = 0
    budget_nudged = False

    try:
        while has_requests:
            has_requests = False
            user_input_requests = []
            turn_text = ""

            try:
                stream = agent.run(current_input, session=session, stream=True)
                try:
                    async for update in iter_agent_stream(stream, surface.budget_deadline):
                        run_stream_chars += stream_content_chars(update)
                        if surface.context_budget and run_stream_chars > surface.context_budget:
                            surface.notify(
                                f"context_budget_chars ({surface.context_budget}) exceeded — "
                                f"cutting the current turn short."
                            )
                            break
                        for content in update.contents:
                            if content.type == "text" and content.text:
                                turn_text += content.text
                            await surface.on_stream_content(content)
                        if getattr(update, "user_input_requests", None):
                            user_input_requests.extend(update.user_input_requests)
                except asyncio.TimeoutError:
                    surface.notify("max_run_minutes exceeded — cutting the current turn short.")
            except BaseException as e:
                from tools import QuotaAbortException

                if isinstance(e, QuotaAbortException) or type(e).__name__ == "QuotaAbortException":
                    # Deliberately no `break`/`continue`: falling through reaches the same
                    # quarantine-restore/salvage path the malformed-tool-call give-up branch
                    # below uses, so an abort still gets a real completion-check verdict instead
                    # of a silent "Report: NOT WRITTEN" (run_cli's 2026-08-27 fix).
                    surface.notify(f"Task forcefully aborted: {str(e)}")
                    has_requests = False
                    if run_state is not None:
                        run_state.attempt = 10**6
                else:
                    result = classify_malformed_retry(e, malformed_retries, current_input)
                    malformed_retries = result.new_malformed_retries
                    if result.should_retry:
                        surface.notify(
                            f"Model emitted a malformed tool call — retrying the turn "
                            f"({malformed_retries}/2)."
                        )
                        current_input = result.new_current_input
                        has_requests = True
                        continue
                    if result.reraise:
                        raise
                    # Retry budget exhausted for this recognized failure class -- degrade to the
                    # final-verdict path instead of crashing the run. Deliberately does NOT
                    # `continue` (has_requests is already False): falling through into the rest
                    # of this iteration's body is what reaches the completion-check call below.
                    surface.notify(
                        f"Model kept emitting malformed tool calls after {malformed_retries} "
                        f"retries — giving up on this turn and finishing with whatever exists "
                        f"(quarantine-restore/salvage still applies)."
                    )
                    if run_state is not None:
                        run_state.attempt = 10**6

            if turn_text and surface.on_turn_text is not None:
                surface.on_turn_text(turn_text)

            if surface.context_budget and run_stream_chars > surface.context_budget:
                if not budget_nudged:
                    # One wrap-up turn: no more research tools, write the artifacts NOW from
                    # what already exists. A second overshoot forces the completion check
                    # straight to its final verdict (same mechanism as the wall-clock deadline
                    # below) -- never nudge-loop.
                    budget_nudged = True
                    run_stream_chars = 0
                    import config

                    req_artifact = config.get_required_artifact()
                    endgame = (
                        f"SYSTEM: you have reached your context budget for this run. Do NOT call "
                        f"delegate_tasks or any research tool again. Write findings.md (if missing) "
                        f"and '{req_artifact}' RIGHT NOW from the delegated results you already "
                        f"have, then stop. An incomplete but grounded report now beats a truncated "
                        f"context."
                    )
                    surface.notify("Context budget reached — forcing wrap-up turn.")
                    new_inputs = [current_input] if isinstance(current_input, str) else list(current_input)
                    new_inputs.append(Message("user", [{"type": "text", "text": endgame}]))
                    current_input = new_inputs
                    has_requests = True
                    continue
                run_state.attempt = 10**6

            if user_input_requests:
                has_requests = True
                # current_input, not surface.query: on a resumed/follow-up run the two differ,
                # and rebuilding from the bare query would silently drop context.
                new_inputs = [current_input] if isinstance(current_input, str) else list(current_input)
                new_inputs.extend(await surface.handle_approvals(user_input_requests))
                current_input = new_inputs

            if not has_requests and not surface.skip_completion_check:
                if surface.budget_deadline and time.monotonic() > surface.budget_deadline:
                    surface.notify(
                        "max_run_minutes exceeded — no more retries; finishing with whatever "
                        "exists (salvage still applies)."
                    )
                    # Forces run_completion_check straight past its retry branch into the
                    # final-verdict path (labeling + salvage), same as an exhausted attempt
                    # budget.
                    run_state.attempt = 10**6

                prior_input_len = len(current_input) if isinstance(current_input, list) else 1
                should_continue, current_input = await run_completion_check(
                    query=surface.query,
                    current_input=current_input,
                    run_state=run_state,
                    notify=surface.notify,
                    last_assistant_text=turn_text,
                    dispatch_task=surface.dispatch_task,
                    budget_deadline=surface.budget_deadline,
                    find_substantial_text=surface.find_substantial_text,
                )
                if should_continue:
                    has_requests = True
                    # context_budget_chars blind spot: a message run_completion_check injects
                    # directly into current_input bypasses the normal stream_content_chars
                    # accounting above (it never passed through the stream loop at all). Count
                    # only what was actually appended, so the budget can't be silently bypassed
                    # by that injection path.
                    if isinstance(current_input, list) and len(current_input) > prior_input_len:
                        for injected_msg in current_input[prior_input_len:]:
                            for c in getattr(injected_msg, "contents", None) or []:
                                text = getattr(c, "text", None)
                                if text:
                                    run_stream_chars += len(text)
    except Exception:
        if run_state is not None:
            run_state.sync_fetched_urls()
            run_state.save()
        raise
