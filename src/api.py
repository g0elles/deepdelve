"""Optional HTTP API for DeepDelve (settings.pdf_engine's sibling: an opt-in surface, not the
primary interface -- the TUI/CLI stay the default). Serves both external API callers and the
static web UI (src/static/index.html) from one process.

Deliberately does NOT touch run_cli/run_agent/orchestrator.py's internals -- reuses the same
already-proven lower-level primitives those two entry points each independently orchestrate
(create_local_agent, iter_agent_stream, run_completion_check, RunState, build_quota_pool,
_slugify_run_dir_name, _ingest_local_doc, _write_bibliography, _export_pdf). This is a
deliberate THIRD copy of that orchestration shape, not a fourth shared abstraction -- see
~/.claude/plans/cosmic-growing-canyon.md's Phase 4 section for why (extracting one now would mean
touching the two already-shipped, already-tested entry points as a side effect of an unrelated
change).

Concurrency: orchestrator.py's `_session` (conversational-memory cache) and tui.py's
`_session_events`/`_current_session_id`/`_current_call_by_source`/`_current_text_by_source` are
module-level GLOBALS, not contextvars -- confirmed by reading create_local_agent's and
log_stream_content's actual bodies. Two truly concurrent research runs in the same process would
corrupt each other's state. This API deliberately never runs two jobs at once: one in-process
FIFO queue, one worker coroutine. Also calls reset_session() before every job -- without it,
conversational memory (if enabled) would otherwise leak one API job's context into the next
unrelated job, since _session persists across calls within this long-running process the way it
never does across run_cli's own one-shot-per-process headless invocations.
"""
import asyncio
import copy
import os
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse, StreamingResponse, HTMLResponse
import json

import config
from agent_framework import Message
import engine.orchestrator as orchestrator_module
from engine.orchestrator import create_local_agent, iter_agent_stream, build_quota_pool, stream_content_chars
from engine.completion import run_completion_check
from engine.tui import (
    _slugify_run_dir_name, _current_run_dir, _ingest_local_doc, _write_bibliography, _export_pdf,
    _looks_like_tool_error, apply_depth_preset, load_resume_state, build_resume_input,
    _scale_resume_quota_pool,
)
from tools.core import tool_quotas_ctx
from tools.fs import session_dir_ctx
from utils.run_state import RunState, reset_fetched_urls, merge_resumed_state

app = FastAPI(title="DeepDelve API")


@app.middleware("http")
async def _require_api_password(request: Request, call_next):
    """settings.api_password (config_template.yaml, unset by default -- see its own comment for
    why): once set, every request except the static UI itself needs a matching password, checked
    via the X-API-Password header or a ?pw= query param -- the latter exists ONLY because
    EventSource (used by /research/{id}/stream) cannot set custom headers at all, not as a
    general alternative to the header. Directly motivated by 5b.4's full settings CRUD (API keys
    included) combined with LAN/phone reachability: without this, anyone on the same network who
    finds the port can read and rewrite those keys. Still layered on top of, not instead of, the
    --i-understand-the-risk non-loopback bind guard in main() below."""
    required = config.cfg.get("settings", {}).get("api_password")
    if required and request.url.path != "/":
        supplied = request.headers.get("X-API-Password") or request.query_params.get("pw")
        if supplied != required:
            from fastapi.responses import JSONResponse
            return JSONResponse({"detail": "Missing or incorrect X-API-Password"}, status_code=401)
    return await call_next(request)


# run_id -> {"status": "queued"|"running"|"done"|"failed", "queue": asyncio.Queue, "error": str|None}
_jobs: dict[str, dict] = {}
_job_queue: asyncio.Queue = asyncio.Queue()
_worker_started = False


async def _ensure_worker():
    global _worker_started
    if not _worker_started:
        _worker_started = True
        asyncio.create_task(_worker())


async def _worker():
    while True:
        run_id, query, opts = await _job_queue.get()
        job = _jobs[run_id]
        job["status"] = "running"
        task = asyncio.ensure_future(_run_research(run_id, query, opts, job["queue"]))
        job["task"] = task
        try:
            await task
            job["status"] = "done"
        except asyncio.CancelledError:
            job["status"] = "cancelled"
            await job["queue"].put({"type": "system", "text": "Run cancelled."})
        except Exception as e:
            job["status"] = "failed"
            job["error"] = f"{type(e).__name__}: {e}"
            await job["queue"].put({"type": "system", "text": f"Run failed: {job['error']}"})
        await job["queue"].put({"type": "done", "status": job["status"]})


def _agent_session_path(run_id: str) -> str:
    return os.path.join(_current_run_dir(run_id), "_agent_session.json")


async def _run_research(run_id: str, query: str, opts: dict, events: asyncio.Queue):
    # mode: "fresh" (default) | "resume" | "followup" -- see module docstring's Concurrency
    # section and ~/.claude/plans/cosmic-growing-canyon.md's Phase 5b.3 for why "followup" is the
    # only mode that skips reset_session()/reset_fetched_urls() and instead reconstructs its
    # session from THIS run's own persisted _agent_session.json rather than trusting
    # orchestrator_module._session to have survived untouched since the original run -- an
    # unrelated job could have run (and reset it) in between, since this is a shared FIFO queue.
    mode = opts.get("mode", "fresh")
    session_data = None
    if mode == "followup":
        path = _agent_session_path(run_id)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                session_data = json.load(f)
    else:
        orchestrator_module.reset_session()

    if opts.get("depth"):
        apply_depth_preset(config.cfg, opts["depth"])
    if opts.get("style"):
        config.cfg.setdefault("settings", {})["report_style"] = opts["style"]

    quota_pool = build_quota_pool()
    if mode == "resume":
        _scale_resume_quota_pool(quota_pool)
    quota_token = tool_quotas_ctx.set(quota_pool)
    if mode != "followup":
        reset_fetched_urls()
    session_token = session_dir_ctx.set(run_id)
    run_state_token = None

    async def api_subagent_callback(update, is_subagent=True, is_done=False, **kwargs):
        agent_name = kwargs.get("agent_name") or getattr(update, "author_name", None) or "Sub-Agent"
        requests = kwargs.get("approval_requests", [])
        if requests:
            # settings.permissions is a headless-friendly auto-approve/require_approval switch
            # elsewhere in this project; this API always auto-approves (no interactive operator
            # to ask) -- same posture run_cli takes with --auto-approve.
            responses = [Message("user", [req.to_function_approval_response(True)]) for req in requests]
            return responses
        if is_done:
            await events.put({"type": "system", "agent": agent_name, "text": "Finished."})
            return
        if update is None:
            return
        for content in update.contents:
            if content.type == "text" and content.text:
                await events.put({"type": "text", "agent": agent_name, "text": content.text})
            elif content.type == "function_call":
                await events.put({
                    "type": "tool_call", "agent": agent_name,
                    "name": getattr(content, "name", None),
                })
            elif content.type == "function_result":
                await events.put({
                    "type": "tool_result", "agent": agent_name,
                    "result": str(getattr(content, "result", ""))[:500],
                })

    max_run_minutes = config.cfg.get("settings", {}).get("max_run_minutes", 0) or 0
    budget_deadline = (time.monotonic() + max_run_minutes * 60) if max_run_minutes else None

    from utils.run_state import run_state_ctx
    try:
        agent, session, dispatch_task = create_local_agent(
            builder=app.state.builder, subagent_callback=api_subagent_callback, session_data=session_data
        )
        run_state = RunState(_current_run_dir(run_id))

        if mode == "resume":
            prior_state = opts["prior_state"]
            current_input = build_resume_input(query, prior_state)
            merge_resumed_state(run_state, prior_state)
            run_state.set_query(query)
        elif mode == "followup":
            # Continue the SAME _run_state.json record rather than starting a fresh one --
            # mirrors the TUI's own _conv_run_state continuation for same-conversation
            # follow-ups (engine/tui.py's is_followup branch).
            current_input = query
            existing_path = os.path.join(_current_run_dir(run_id), "_run_state.json")
            if os.path.exists(existing_path):
                with open(existing_path, encoding="utf-8") as f:
                    run_state.data = json.load(f)
        else:
            current_input = query
            run_state.set_query(query)

        run_state_token = run_state_ctx.set(run_state)
        run_state.save()

        # A follow-up in a conversation whose report already exists is Q&A over the gathered
        # research, not a new research run -- the artifact/grounding contract was already
        # enforced when the report was first produced. Mirrors tui.py's own skip_completion_check
        # exactly (run_agent's is_followup branch) -- get this wrong (as an earlier version of
        # this function did) and a follow-up question forces the full findings.md/final_report.md
        # rewrite pipeline against a query it was never scoped for, which just repeatedly rejects
        # the rewrite and leaves the ORIGINAL report untouched while burning the run's retry
        # budget -- confirmed live this session ("second highest mountain" followup: findings.md
        # rewritten and rejected 3 times, final_report.md's mtime never changed).
        from tools.fs import get_workspace_files
        skip_completion_check = mode == "followup" and config.get_required_artifact() in get_workspace_files()

        if mode == "fresh":
            for u in opts.get("seed_urls") or []:
                from tools.web import fetch_url_to_workspace, _slugify_for_filename
                from tools.core import refund_quota
                result = await fetch_url_to_workspace.func(url=u, filename=_slugify_for_filename(u, "seed"))
                refund_quota("fetch_url_to_workspace")
                ok = not _looks_like_tool_error(str(result))
                await events.put({"type": "system", "text": f"Seed {'fetched' if ok else 'FAILED'}: {u}"})
                if ok:
                    current_input += f"\n\nSEED SOURCE (already fetched into the workspace under sources/): {u}"

            for p in opts.get("seed_doc_paths") or []:
                ok, result = _ingest_local_doc(p)
                await events.put({"type": "system", "text": f"Seed document {'loaded' if ok else 'FAILED'}: {result}"})
                if ok:
                    current_input += f"\n\nSEED DOCUMENT (already loaded into the workspace): {result}"

        from engine.orchestrator import get_context_budget
        context_budget = get_context_budget()
        run_stream_chars = 0
        budget_nudged = False
        has_requests = True

        # Every turn's full Planner text, oldest first -- NOT just the current turn's turn_text.
        # tui.py's own _find_last_substantial_text exists specifically because passing only the
        # immediately-preceding turn's text to the final-verdict salvage loses a genuinely good
        # narrated report from an EARLIER turn when a later retry's turn (e.g. one that's pure
        # tool calls, or a short "quota exhausted, stopping" acknowledgment) produces little or no
        # text of its own -- confirmed as a real bug there against a live session log. This
        # function never had the equivalent: it only ever passed the CURRENT turn_text as
        # last_assistant_text, with no find_substantial_text callback at all, so it silently
        # inherited the same already-fixed-elsewhere bug. Confirmed live 2026-08-02: a run whose
        # Planner narrated a long, real synthesis several turns before quota exhaustion ended with
        # zero files written at all, because the actual final turn was short and there was nothing
        # else to fall back to.
        planner_text_history = []

        while has_requests:
            has_requests = False
            user_input_requests = []
            turn_text = ""

            stream = agent.run(current_input, session=session, stream=True)
            async for update in iter_agent_stream(stream, budget_deadline):
                run_stream_chars += stream_content_chars(update)
                for c in getattr(update, "contents", None) or []:
                    if getattr(c, "type", None) == "text" and getattr(c, "text", None):
                        turn_text += c.text
                        await events.put({"type": "text", "agent": "Planner", "text": c.text})
                    elif hasattr(c, "function_call") and c.function_call is not None:
                        user_input_requests.append(c)

            if turn_text:
                planner_text_history.append(turn_text)

            if user_input_requests:
                has_requests = True
                new_inputs = [current_input] if isinstance(current_input, str) else list(current_input)
                for req in user_input_requests:
                    new_inputs.append(Message("user", [req.to_function_approval_response(True)]))
                current_input = new_inputs
                continue

            if skip_completion_check:
                continue  # has_requests already False -- the answer was the streamed turn_text itself

            def _api_notify(msg: str):
                asyncio.create_task(events.put({"type": "system", "text": msg}))
                # api.py has no persisted session-transcript equivalent to tui.py's
                # ~/.deepdelve/sessions/session_*.json -- an event only ever reaches whoever
                # happens to have an SSE connection open at that exact moment, and is gone once
                # drained. That made a real incident (2026-08-02: a run ended with zero artifacts
                # and no one watching live) impossible to post-mortem after the fact. These are
                # exactly the completion-check's own diagnostic messages (retry-budget-exhausted,
                # quarantine-restore, salvage notices) -- capped small since _run_state.json is
                # already written frequently and this must not make it meaningfully bigger.
                log = run_state.data.setdefault("_notify_log", [])
                log.append(msg)
                del log[:-20]

            def _find_substantial_text(min_len: int = 200) -> str:
                for text in reversed(planner_text_history):
                    if len(text.strip()) >= min_len:
                        return text.strip()
                return ""

            # Two-stage nudge-then-cutoff for context_budget_chars, matching run_cli's own
            # mechanism (tui.py's run_agent) instead of a blunt one-shot cutoff (2026-08-04):
            # confirmed live against a verbose hosted model (DeepSeek-V4-Flash) that the old
            # unconditional force-jump gave check_task_verification_flagged's own correctly-
            # firing redo directive ZERO real completion-check attempts before salvage -- the
            # budget blew before the Planner's first completion-check-eligible turn even
            # happened, so a check that was actively working (verified-task count improved
            # run-over-run) never got a chance to act. One bounded wrap-up turn (not unbounded --
            # a SECOND overshoot still forces the hard cutoff) costs at most one extra Planner
            # turn, which does not meaningfully weaken the shared-queue protection the original
            # blunt-cutoff comment was protecting against.
            if context_budget and run_stream_chars > context_budget:
                if not budget_nudged:
                    budget_nudged = True
                    run_stream_chars = 0
                    req_artifact = config.get_required_artifact()
                    endgame = (
                        f"SYSTEM: you have reached your context budget for this run. Do NOT call "
                        f"delegate_tasks or any research tool again. Write findings.md (if missing) "
                        f"and '{req_artifact}' RIGHT NOW from the delegated results you already "
                        f"have, then stop. An incomplete but grounded report now beats a truncated "
                        f"context."
                    )
                    await events.put({"type": "system", "text": "Context budget reached — forcing wrap-up turn."})
                    new_inputs = [current_input] if isinstance(current_input, str) else list(current_input)
                    new_inputs.append(Message("user", [{"type": "text", "text": endgame}]))
                    current_input = new_inputs
                    has_requests = True
                    continue
                run_state.attempt = 10**6
            elif budget_deadline and time.monotonic() > budget_deadline:
                run_state.attempt = 10**6

            should_continue, current_input = await run_completion_check(
                query=query, current_input=current_input, run_state=run_state, notify=_api_notify,
                last_assistant_text=turn_text, dispatch_task=dispatch_task,
                budget_deadline=budget_deadline, find_substantial_text=_find_substantial_text,
            )
            if should_continue:
                has_requests = True

        run_state.save()
        _write_bibliography(run_state)
        pdf_path, pdf_err = _export_pdf(run_id)
        if pdf_err:
            await events.put({"type": "system", "text": f"PDF export skipped: {pdf_err}"})

        # Persist the conversational session (if any) so a LATER /followup call can reconstruct
        # it exactly, regardless of what other job the shared worker queue processes in between
        # (see this function's own mode=="followup" branch above and the module docstring).
        if session is not None:
            try:
                with open(_agent_session_path(run_id), "w", encoding="utf-8") as f:
                    json.dump(session.to_dict(), f)
            except Exception:
                pass
    except asyncio.CancelledError:
        # /cancel relies on this propagating -- must not be swallowed. Still save whatever
        # forensics exist first, same contract run_agent's own crash handling already follows
        # (never lose the evidence a partial run left behind).
        if run_state_token is not None:
            run_state.save()
        raise
    finally:
        tool_quotas_ctx.reset(quota_token)
        session_dir_ctx.reset(session_token)
        if run_state_token is not None:
            run_state_ctx.reset(run_state_token)


@app.post("/research")
async def start_research(
    query: str = Form(...),
    depth: Optional[str] = Form(None),
    style: Optional[str] = Form(None),
    seed_urls: list[str] = Form(default=[]),
    files: list[UploadFile] = File(default=[]),
):
    await _ensure_worker()
    run_id = _slugify_run_dir_name(query)
    if run_id in _jobs and _jobs[run_id]["status"] in ("queued", "running"):
        raise HTTPException(409, f"A run with this exact query is already {_jobs[run_id]['status']}: {run_id}")

    seed_doc_paths = []
    for f in files:
        data = await f.read()
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix="_" + (f.filename or "upload"))
        tmp.write(data)
        tmp.close()
        seed_doc_paths.append(tmp.name)

    _jobs[run_id] = {"status": "queued", "queue": asyncio.Queue(), "error": None, "task": None, "query": query}
    await _job_queue.put((run_id, query, {
        "mode": "fresh", "depth": depth, "style": style,
        "seed_urls": seed_urls, "seed_doc_paths": seed_doc_paths,
    }))
    return {"run_id": run_id, "status": "queued"}


@app.post("/research/{run_id}/resume")
async def resume_research(run_id: str):
    await _ensure_worker()
    if run_id in _jobs and _jobs[run_id]["status"] in ("queued", "running"):
        raise HTTPException(409, f"Run {run_id} is already {_jobs[run_id]['status']}")
    try:
        resolved_run_id, prior_state = load_resume_state(run_id)
    except Exception as e:
        raise HTTPException(404, f"Cannot resume {run_id}: {type(e).__name__}: {e}")
    query = prior_state.get("query")
    if not query:
        raise HTTPException(400, f"Run {run_id} recorded no query -- cannot resume automatically.")

    _jobs[resolved_run_id] = {"status": "queued", "queue": asyncio.Queue(), "error": None, "task": None, "query": query}
    await _job_queue.put((resolved_run_id, query, {"mode": "resume", "prior_state": prior_state}))
    return {"run_id": resolved_run_id, "status": "queued"}


@app.post("/research/{run_id}/followup")
async def followup_research(run_id: str, query: str = Form(...)):
    await _ensure_worker()
    if not os.path.isdir(_current_run_dir(run_id)):
        raise HTTPException(404, "Unknown run_id")
    # _jobs is in-memory only and doesn't survive a server restart, unlike the run's own data on
    # disk -- a run started in a prior process lifetime has no entry here at all, which must NOT
    # be treated as "still running" (same reasoning /resume already applies by reading disk state
    # directly rather than trusting _jobs alone).
    prior_job = _jobs.get(run_id)
    if prior_job is not None and prior_job["status"] in ("queued", "running"):
        raise HTTPException(400, f"Run {run_id} must be finished before a follow-up (status: {prior_job['status']})")

    _jobs[run_id] = {"status": "queued", "queue": asyncio.Queue(), "error": None, "task": None, "query": query}
    await _job_queue.put((run_id, query, {"mode": "followup"}))
    return {"run_id": run_id, "status": "queued"}


@app.post("/research/{run_id}/cancel")
async def cancel_research(run_id: str):
    job = _jobs.get(run_id)
    if job is None:
        raise HTTPException(404, "Unknown run_id")
    task = job.get("task")
    if task is None or task.done():
        raise HTTPException(409, f"Run is not currently running (status: {job['status']})")
    task.cancel()
    return {"status": "cancelling"}


@app.get("/research/{run_id}/status")
async def research_status(run_id: str):
    job = _jobs.get(run_id)
    if job is None:
        raise HTTPException(404, "Unknown run_id")
    return {"status": job["status"], "error": job["error"]}


@app.get("/active")
async def active_research():
    # The single-worker-queue design (see module docstring) means at most one run is ever
    # "queued" or "running" at a time process-wide, so this is unambiguous. Lets the frontend
    # discover and reattach to an in-flight run on page load -- confirmed live, 2026-08-02, this
    # was missing entirely: a resume triggered from outside the browser (or just a page reload
    # mid-run) left the UI showing an idle form with no way to tell a run was still going.
    for run_id, job in _jobs.items():
        if job["status"] in ("queued", "running"):
            return {"run_id": run_id, "query": job.get("query", run_id)}
    return {"run_id": None}


@app.get("/runs")
async def list_runs():
    base = config.get_workspace_dir()
    req_artifact = config.get_required_artifact()
    if not os.path.isdir(base):
        return []
    run_dirs = sorted(
        (d for d in Path(base).iterdir() if d.is_dir() and (d / "_run_state.json").exists()),
        key=os.path.getmtime, reverse=True,
    )
    results = []
    for d in run_dirs[:50]:
        query = None
        try:
            with open(d / "_run_state.json", encoding="utf-8") as f:
                query = json.load(f).get("query")
        except Exception:
            pass
        results.append({
            "run_id": d.name,
            "timestamp": datetime.fromtimestamp(os.path.getmtime(d)).isoformat(),
            "has_report": (d / req_artifact).exists(),
            "query": query,
            "status": _jobs.get(d.name, {}).get("status"),
        })
    return results


_SECRET_PATHS = [("api", "openai_api_key"), ("settings", "tavily_api_key"), ("settings", "brave_api_key")]
_SECRET_SENTINEL = "***set***"


def _mask_secrets(cfg_dict: dict) -> dict:
    masked = copy.deepcopy(cfg_dict)
    for section, key in _SECRET_PATHS:
        if masked.get(section, {}).get(key):
            masked[section][key] = _SECRET_SENTINEL
    return masked


@app.get("/settings")
async def get_settings():
    return _mask_secrets(config.cfg)


@app.post("/settings")
async def post_settings(overrides: dict):
    # A masked sentinel round-tripped back unchanged must never overwrite the real secret with
    # the literal string "***set***" -- strip it out of the incoming payload so save_full_config
    # leaves that key alone (same "no key = keep whatever's already on disk" semantics
    # save_full_config's own deep-merge already gives every other setting).
    for section, key in _SECRET_PATHS:
        if overrides.get(section, {}).get(key) == _SECRET_SENTINEL:
            del overrides[section][key]
    config.save_full_config(overrides)
    return {"status": "saved"}


@app.get("/research/{run_id}/stream")
async def research_stream(run_id: str):
    job = _jobs.get(run_id)
    if job is None:
        raise HTTPException(404, "Unknown run_id")

    async def gen():
        q = job["queue"]
        while True:
            event = await q.get()
            yield f"data: {json.dumps(event)}\n\n"
            if event.get("type") == "done":
                break

    return StreamingResponse(gen(), media_type="text/event-stream")


def _artifact_path(run_id: str, filename: str) -> str:
    # Gating on run_id being in _jobs (in-memory, this-process-lifetime only) was wrong here --
    # same bug class as /followup's original version: a run from a PRIOR server process, or one
    # never touched by this API instance at all (started via the TUI/CLI), has real artifacts on
    # disk but no _jobs entry, so every report/bib/pdf link for it 404'd with a misleading
    # "Unknown run_id" (confirmed live -- clicking "View report" from the Runs tab). The
    # workspace directory existing on disk is the only real signal that matters here.
    if not os.path.isdir(_current_run_dir(run_id)):
        raise HTTPException(404, "Unknown run_id")
    path = os.path.join(_current_run_dir(run_id), filename)
    if not os.path.exists(path):
        raise HTTPException(404, f"{filename} not available for this run (not written, or run isn't done yet)")
    return path


# api_route with an explicit HEAD, not @app.get -- FastAPI does NOT auto-add HEAD support to a
# GET route (confirmed live, 2026-08-02: every "does this artifact exist" check in the frontend
# uses fetch(..., {method: "HEAD"}) specifically to avoid downloading full content just to check
# presence, and all of them silently 405'd, so no Report/Bibliography/PDF button ever appeared).
@app.api_route("/research/{run_id}/report", methods=["GET", "HEAD"])
async def research_report(run_id: str):
    req_artifact = config.get_required_artifact()
    return FileResponse(_artifact_path(run_id, req_artifact), media_type="text/markdown")


@app.api_route("/research/{run_id}/bib", methods=["GET", "HEAD"])
async def research_bib(run_id: str):
    return FileResponse(_artifact_path(run_id, "references.bib"), media_type="application/x-bibtex")


@app.api_route("/research/{run_id}/pdf", methods=["GET", "HEAD"])
async def research_pdf(run_id: str):
    req_artifact = config.get_required_artifact()
    pdf_name = os.path.splitext(req_artifact)[0] + ".pdf"
    return FileResponse(_artifact_path(run_id, pdf_name), media_type="application/pdf")


_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


@app.get("/")
async def index():
    index_path = os.path.join(_STATIC_DIR, "index.html")
    if not os.path.exists(index_path):
        raise HTTPException(404, "Web UI not installed (src/static/index.html missing)")
    # No-cache: this is a single-file app actively under development, served from the same URL
    # every time -- a browser silently serving a stale cached copy after an edit (confirmed live,
    # 2026-08-02) is a worse failure mode than the negligible cost of refetching a ~15KB file on
    # every load.
    return HTMLResponse(
        open(index_path, encoding="utf-8").read(),
        headers={"Cache-Control": "no-store"},
    )


def main():
    import argparse
    import uvicorn
    # app.py's own module-level AgentBuilder instance is confusingly named `app` there (it's
    # passed around everywhere else as a `builder` parameter, e.g. create_local_agent(builder=...)
    # -- NOT the same thing as this module's `app` FastAPI instance above).
    from app import app as builder

    parser = argparse.ArgumentParser(description="DeepDelve API server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8420)
    parser.add_argument("--i-understand-the-risk", action="store_true",
                         help="Required to bind a non-loopback host -- this server has no auth "
                              "layer and can trigger outbound web fetches / burn local GPU time "
                              "on anyone's request who can reach it.")
    args = parser.parse_args()

    if args.host not in ("127.0.0.1", "localhost", "::1") and not args.i_understand_the_risk:
        raise SystemExit(
            f"Refusing to bind {args.host}: this API has no auth layer (see src/api.py's module "
            f"docstring). Pass --i-understand-the-risk to bind a non-loopback host anyway."
        )

    app.state.builder = builder
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
