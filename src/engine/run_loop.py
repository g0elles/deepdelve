"""Shared run-lifecycle loop, extracted from three independently hand-duplicated copies
(run_cli, run_agent/BasicTuiAgent in this package's tui.py, and _run_research in api.py).

RunLoopSurface parameterizes exactly what differs between the three call sites: how a stream
update is rendered (stdout / Textual widgets / an SSE queue), how a tool-call approval is
resolved (auto-approve-only vs. TUI's client-side execution), whether a context/wall-clock
budget applies at all (run_agent has none by design -- a human can /stop), and whether the
completion check should be skipped this turn (a same-conversation follow-up with an existing
report). Everything else -- the malformed-tool-call/QuotaAbortException dispatch, the budget
nudge-then-cutoff mechanics, the completion-check invocation, and crash-time run_state.save()
on every exception path -- lives here once.

A handful of fields default to None and only matter for run_agent (Phase 3), which has real,
deliberate behavioral divergences from run_cli/_run_research (Phases 1-2): it never re-raises an
unrecognized exception (shows an error widget and lets the run continue normally instead), it
renders one whole stream update as a unit (Textual widget lifecycle spans multiple Content items)
rather than per-Content-item, and it needs one-time-per-turn widget setup/teardown hooks neither
headless surface needs. Leaving these None preserves run_cli/_run_research's exact shipped
behavior unchanged.
"""

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from agent_framework import Message

from engine.completion import run_completion_check
from engine.orchestrator import (
    MalformedRetryResult,
    classify_malformed_retry,
    iter_agent_stream,
    stream_content_chars,
)


@dataclass
class RunLoopSurface:
    """One instance per call site (run_cli / run_agent / _run_research)."""

    # Called once per stream Content item with the raw item; surface renders it however it
    # wants (stdout write, SSE queue.put) and does its own persistence (log_stream_content etc.)
    # inside this callback. Ignored (on_stream_update used instead) when on_stream_update is set.
    on_stream_content: Callable[[Any], Awaitable[None]]

    # notify(msg) -> None. Passed straight through to run_completion_check's own `notify` and
    # also used for this loop's own budget/abort/retry announcements (unless the more specific
    # render_* hooks below are set).
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

    # Optional: called once per stream update with the WHOLE update object, instead of
    # on_stream_content being called once per Content item within it. run_agent needs this --
    # its Textual widget lifecycle (ProcessingWidget/ThinkingWidget/ToolCallWidget) spans
    # multiple Content items within one update and is managed by one existing function
    # (handle_agent_update) that already expects the whole update object. run_cli/_run_research
    # don't set this; the shared loop falls back to iterating update.contents itself and calling
    # on_stream_content per item, their existing behavior.
    on_stream_update: Optional[Callable[[Any], Awaitable[None]]] = None

    # Optional: called once at the very start of each turn, before agent.run(...). run_agent
    # uses this to mount a fresh ProcessingWidget and reset its per-turn widget-state fields --
    # neither headless surface allocates any per-turn UI state, so this stays unused there.
    on_turn_start: Optional[Callable[[], Awaitable[None]]] = None

    # Optional: called once after the stream is guaranteed fully exhausted (the inner `async for`
    # completed with no exception) -- NOT called on a TimeoutError or any other exception from
    # that loop. run_agent uses this for two things that must happen only once the stream
    # genuinely ended: flushing the session log (session.state is only populated by the agent
    # framework's after_run hooks once the generator exhausts) and stopping a ProcessingWidget
    # that never received a first-token callback (a turn with literally no content, e.g. after
    # tool quotas are exhausted). Neither headless surface needs this.
    on_stream_exhausted: Optional[Callable[[], Awaitable[None]]] = None

    # Optional: called instead of the default `surface.notify(f"Model emitted a malformed tool
    # call — retrying the turn ({n}/2).")` when retrying. run_agent uses this to stop the current
    # ProcessingWidget and mount a styled warning Static instead of a plain notify bubble.
    render_retry_notice: Optional[Callable[[int], None]] = None

    # Optional: called instead of the default `surface.notify(f"Task forcefully aborted: {e}")`
    # on a QuotaAbortException. run_agent uses this to mark the current tool-call widget as
    # errored (or mount a styled error Static if there isn't one) instead of a plain notify.
    render_quota_abort: Optional[Callable[[BaseException], None]] = None

    # Optional: called (with the exception and its MalformedRetryResult) instead of the default
    # "reraise on result.reraise, else notify-and-give-up" handling, when should_retry is False.
    # run_agent deliberately never re-raises an unrecognized exception here (documented, pre-
    # existing behavior predating this unification): it shows the SAME generic error widget for
    # both an unrecognized exception and a recognized-but-retry-exhausted one, and lets the run
    # continue with a normal (not forced) completion check in the unrecognized case. The shared
    # loop still applies `run_state.attempt = 10**6` afterward when `result.force_final_verdict`
    # is set, regardless of which path rendered the error -- only the rendering/reraise decision
    # is delegated here.
    on_malformed_give_up: Optional[Callable[[BaseException, "MalformedRetryResult"], None]] = None

    # Optional: called with no args to get the current turn's assistant text for
    # run_completion_check's `last_assistant_text` param, instead of the shared loop's own
    # turn_text accumulator (which sums every "text" Content item seen this turn). run_agent
    # already tracks this via its AgentMessageWidget state (`state["current_msg"]`, managed by
    # handle_agent_update) and uses that directly rather than a second, redundant accumulation.
    get_last_assistant_text: Optional[Callable[[], str]] = None


async def run_agent_loop(agent, session, current_input, surface: RunLoopSurface) -> None:
    """The one `while has_requests:` loop. Mutates `run_state`/`current_input` in place via the
    surface; callers don't need this function's return value (it has none) -- all three read
    final state off `run_state`/their own conversation state after this returns.

    Every path out of this function -- normal completion, a give-up on malformed retries, a
    QuotaAbortException, an unrecognized exception -- leaves `run_state.save()` called before
    control leaves the loop (the malformed/quota branches set `run_state.attempt` and fall
    through to the completion check; a genuinely unrecognized exception hits the `except
    Exception` below, unless a surface's own on_malformed_give_up hook opts out of reraising).
    This closes a confirmed gap: _run_research previously only saved run_state on
    `asyncio.CancelledError`, so a generic exception (e.g. classify_malformed_retry's
    `reraise=True` path) reached its worker with no forensic _run_state.json update.
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

            if surface.on_turn_start is not None:
                await surface.on_turn_start()

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
                        if surface.on_stream_update is not None:
                            for content in update.contents:
                                if content.type == "text" and content.text:
                                    turn_text += content.text
                            await surface.on_stream_update(update)
                        else:
                            for content in update.contents:
                                if content.type == "text" and content.text:
                                    turn_text += content.text
                                await surface.on_stream_content(content)
                        if getattr(update, "user_input_requests", None):
                            user_input_requests.extend(update.user_input_requests)
                    if surface.on_stream_exhausted is not None:
                        await surface.on_stream_exhausted()
                except asyncio.TimeoutError:
                    surface.notify("max_run_minutes exceeded — cutting the current turn short.")
            except BaseException as e:
                if isinstance(e, asyncio.CancelledError):
                    # Must NOT be swallowed here -- run_agent's /stop (self.workers.cancel_all())
                    # and _run_research's /cancel (task.cancel()) both rely on this propagating.
                    # Explicit and first: a surface with on_malformed_give_up set (run_agent)
                    # never re-raises for an unrecognized exception by design (see that field's
                    # docstring), and classify_malformed_retry has no special knowledge of
                    # CancelledError -- without this check, run_agent's hook would render it as a
                    # generic error widget instead of ever letting it propagate. run_cli/
                    # _run_research don't set that hook, so for them this is a no-behavior-change
                    # promotion of what classify_malformed_retry's own reraise=True fallback
                    # already achieved implicitly (str(CancelledError()) never matches a
                    # recognized nudge).
                    raise
                from tools import QuotaAbortException

                if isinstance(e, QuotaAbortException) or type(e).__name__ == "QuotaAbortException":
                    # Deliberately no `break`/`continue`: falling through reaches the same
                    # quarantine-restore/salvage path the malformed-tool-call give-up branch
                    # below uses, so an abort still gets a real completion-check verdict instead
                    # of a silent "Report: NOT WRITTEN" (run_cli's 2026-08-27 fix).
                    if surface.render_quota_abort is not None:
                        surface.render_quota_abort(e)
                    else:
                        surface.notify(f"Task forcefully aborted: {str(e)}")
                    has_requests = False
                    if run_state is not None:
                        run_state.attempt = 10**6
                else:
                    result = classify_malformed_retry(e, malformed_retries, current_input)
                    malformed_retries = result.new_malformed_retries
                    if result.should_retry:
                        if surface.render_retry_notice is not None:
                            surface.render_retry_notice(malformed_retries)
                        else:
                            surface.notify(
                                f"Model emitted a malformed tool call — retrying the turn "
                                f"({malformed_retries}/2)."
                            )
                        current_input = result.new_current_input
                        has_requests = True
                        continue
                    if surface.on_malformed_give_up is not None:
                        surface.on_malformed_give_up(e, result)
                    else:
                        if result.reraise:
                            raise
                        # Retry budget exhausted for this recognized failure class -- degrade to
                        # the final-verdict path instead of crashing the run. Deliberately does
                        # NOT `continue` (has_requests is already False): falling through into
                        # the rest of this iteration's body is what reaches the completion-check
                        # call below.
                        surface.notify(
                            f"Model kept emitting malformed tool calls after {malformed_retries} "
                            f"retries — giving up on this turn and finishing with whatever "
                            f"exists (quarantine-restore/salvage still applies)."
                        )
                    if result.force_final_verdict and run_state is not None:
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

                last_assistant_text = (
                    surface.get_last_assistant_text()
                    if surface.get_last_assistant_text is not None
                    else turn_text
                )
                prior_input_len = len(current_input) if isinstance(current_input, list) else 1
                should_continue, current_input = await run_completion_check(
                    query=surface.query,
                    current_input=current_input,
                    run_state=run_state,
                    notify=surface.notify,
                    last_assistant_text=last_assistant_text,
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
