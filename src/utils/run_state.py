import contextvars
import json
import os
import sys
import threading
import time
from typing import Optional

import config

# -------------------------------------------------------------
# Structural run-state tracking (engine-level, not agent-facing).
#
# Two purposes:
# 1. fetched_urls_ctx is populated deterministically by fetch_url_to_workspace itself
#    (tools/web.py), NOT reported by the model. This is what the real grounding check
#    (engine/tui.py) cross-references final_report.md's cited URLs against — replacing the old
#    project's weak "'http' in content" substring check, which a model could pass with a fully
#    hallucinated URL.
# 2. RunState persists {plan, findings, fetched_urls} as a JSON side-file per run (the
#    "Workflow as Knowledge" idea) so a completion-check retry — or a human debugging a failed
#    run later — can ground itself on real structured data instead of re-deriving it from the
#    model's own possibly-unreliable narration of what it did.
# -------------------------------------------------------------

fetched_urls_ctx = contextvars.ContextVar('fetched_urls', default=None)

# Per-task-scoped fetch tracking, separate from fetched_urls_ctx above. fetched_urls_ctx is one
# list shared for the whole run (correct for the run-wide grounding check, which wants every URL
# ever fetched). But engine/orchestrator.py's _run_single_task used to derive "URLs fetched by
# THIS task" via a before/after length delta on that SAME shared list — which races under
# concurrent delegate_tasks dispatch: confirmed live, a 3-task concurrent run produced 9 findings
# entries (3 tasks x 3 URLs) instead of 3, because each task's "new since my snapshot" slice
# picked up sibling tasks' fetches too, not just its own. task_fetched_urls_ctx.set([]) at the
# start of _run_single_task gives each task (each a separate asyncio Task, so each gets its own
# copied context) a genuinely independent list to append to — no shared-list race.
task_fetched_urls_ctx = contextvars.ContextVar('task_fetched_urls_ctx', default=None)

# Stable per-dispatch identity for the quota ring-fence (tools/core.py::check_quota, 2026-07-19 QA
# audit fix). ROADMAP's tracked open angle (a): the ring-fence originally rescued only the FIRST
# task per tool/run to hit the wall while showing real progress (a single `_rescued` bool on the
# shared quota entry) — a second/third task that also genuinely fetched something real before
# being redispatched got no rescue at all, the same shared-pool starvation bug angle (a)/(c) never
# closed. A per-task rescue needs a way to identify "this task" that survives across the awaits
# inside one _run_single_task call without colliding with a DIFFERENT task — id() of a
# contextvar's value was considered and rejected (this project's own RunState.attempt docstring
# already flags id() reuse after garbage collection as a real risk elsewhere); an incrementing
# counter set once per dispatch is unambiguous and cheap.
task_id_ctx = contextvars.ContextVar('task_id_ctx', default=None)
_next_task_id_counter = [0]


def _next_task_id() -> int:
    _next_task_id_counter[0] += 1
    return _next_task_id_counter[0]

# Exposes the current run's RunState to orchestrator.py's _run_single_task, which lives in a
# different module and previously had no way to record specialist findings into the structured
# store — the reason RunState.add_finding() existed but was dead code until this wiring.
run_state_ctx = contextvars.ContextVar('run_state_ctx', default=None)

# Scope entities of the CURRENT delegated task (e.g. {"Colombia"}), set by orchestrator's
# _run_single_task so web_search can warn when a search query drops the task's own required
# scope — live case: a Colombia-scoped task searched "predictive maintenance offshore wind
# turbine" and burned quota on another continent's industry. Lives here (not orchestrator) so
# tools/web.py can read it without a circular import.
scope_entities_ctx = contextvars.ContextVar('scope_entities', default=None)


def reset_fetched_urls() -> None:
    fetched_urls_ctx.set([])


def record_fetched_url(url: str, filename: str, stub: Optional[str] = None) -> None:
    entry = {"url": url, "filename": filename, "timestamp": time.time()}
    # A truthy stub is the REASON string from tools/web.py's _stub_reason (soft-404/paywall
    # shell). Key absent when not a stub, so pre-existing _run_state.json files (and any reader
    # doing entry.get("stub")) stay compatible. Confirmed live (run 14): a model-invented URL
    # answered 200 with 5KB of subscription chrome and was recorded as a real fetch — the
    # grounding layer needs to know a "successful" fetch had no real content behind it.
    if stub:
        entry["stub"] = stub

    lst = fetched_urls_ctx.get()
    if lst is None:
        lst = []
        fetched_urls_ctx.set(lst)
    lst.append(entry)

    task_lst = task_fetched_urls_ctx.get()
    if task_lst is not None:
        task_lst.append(entry)

    # Persist immediately: fetched_urls is the grounding check's source of truth, and a run can
    # die (crash, rate limit, power loss) long before the first completion-check save. ≤ the
    # fetch quota (~30) tiny JSON writes per run.
    rs = run_state_ctx.get()
    if rs is not None:
        rs.sync_fetched_urls()
        rs.save()


def get_fetched_urls() -> list[dict]:
    return fetched_urls_ctx.get() or []


def record_search_health(ok: bool) -> None:
    """Engine-side web_search success/failure counter, persisted into _run_state.json, so a run
    diagnosis can tell 'the model fabricated' apart from 'the search layer was down/throttled'
    without re-reading transcripts. Confirmed live 2026-07-11: DuckDuckGo rate-throttling made
    every model's Searchers loop and the resulting bad runs looked like model failures."""
    rs = run_state_ctx.get()
    if rs is None:
        return
    health = rs.data.setdefault("search_health", {"calls": 0, "failures": 0})
    health["calls"] += 1
    if not ok:
        health["failures"] += 1
    # Persisted per call: a throttled run makes many failed searches and zero fetches, so the
    # fetch-driven save above never fires — the environmental-failure signal must not be lost
    # if the run dies before its first completion check.
    rs.save()


def get_search_health() -> dict:
    rs = run_state_ctx.get()
    return (rs.data.get("search_health") if rs else None) or {"calls": 0, "failures": 0}


class RunState:
    """Structured, JSON-persisted record of a single research run. Written to
    `_run_state.json` inside the run's workspace folder so it survives independently of the
    model's own conversation transcript."""

    def __init__(self, run_dir: str):
        self.run_dir = run_dir
        # save() is called from worker threads too (record_fetched_url runs inside
        # asyncio.to_thread for web_search's auto-fetch) — serialize writers.
        self._lock = threading.Lock()
        # Completion-check attempt counter lives on the instance, not a module-level dict keyed
        # by id(run_state) — id() can be reused after garbage collection between runs, which would
        # let a stale attempt count leak into an unrelated later run.
        self.attempt = 0
        self.data = {
            "query": None,
            "plan": None,
            # Which model actually produced this run's data (2026-07-20, RAG findings cache) —
            # previously unrecorded anywhere; needed for transparency/debugging of cache entries
            # and read by eval/evaluate.py's per-run cache-disable safety net. Not load-bearing for
            # the cache's own correctness (see src/utils/rag_cache.py's module docstring for why).
            "model": config.cfg.get("api", {}).get("openai_model", ""),
            "findings": [],
            "fetched_urls": [],
            "completion_check_attempts": [],
            "started_at": time.time(),
            # Structured diagnostics (2026-07-12, live investigation of a run that gathered
            # substantial material but still failed): before this, answering "why did each
            # attempt fail" or "how many tool calls errored" required hand-parsing the raw
            # session-event JSON. These make _run_state.json alone sufficient.
            "tool_error_count": 0,
            "tool_error_samples": [],
            "subagent_invocations": {},
            # Engine-driven iterative deepening (2026-07-19, ROADMAP item 10): how many deepening
            # rounds this run has already dispatched (bounded by settings.max_deepening_rounds),
            # and which real follow_up_directions strings have already been turned into a
            # dispatched task -- prevents completion.py::_dispatch_deepening_round from
            # redispatching the same direction on every subsequent retry attempt.
            "deepening_round": 0,
            "consumed_directions": [],
        }

    def set_query(self, query: str) -> None:
        self.data["query"] = query

    def set_plan(self, plan: str) -> None:
        self.data["plan"] = plan

    def add_finding(self, source_url: str, summary: str, task_name: Optional[str] = None,
                     depth: Optional[int] = None, follow_up_directions: Optional[list] = None,
                     agent_id: Optional[str] = None) -> None:
        # task_name/depth (ROADMAP Phase 5, "Coverage accounting") are the anchor coverage() below
        # groups by -- depth==1 means a top-level task the Planner itself dispatched via
        # delegate_tasks, depth>1 a nested Analyzer-tier call one of THOSE tasks made in turn.
        # Optional (default None) so any other/future caller of add_finding that doesn't have this
        # context handy still works unchanged.
        #
        # follow_up_directions/agent_id (2026-07-19, engine-driven iterative deepening, ROADMAP
        # item 10): the real "FOLLOW-UP DIRECTIONS:" bullets a Searcher's own summary suggested
        # (see orchestrator.py's _extract_follow_up_directions), and which specialist produced this
        # finding -- both consumed by completion.py's _dispatch_deepening_round to route a derived
        # follow-up task to the same specialist type. Additive/optional: every existing consumer of
        # RunState.data["findings"] reads via .get(), so an older-shaped entry (or one from a
        # non-Searcher dispatch that never set these) stays fully compatible.
        self.data["findings"].append({
            "source_url": source_url, "summary": summary, "timestamp": time.time(),
            "task_name": task_name, "depth": depth,
            "follow_up_directions": follow_up_directions or [], "agent_id": agent_id,
        })

    def coverage(self) -> dict:
        """ROADMAP Phase 5 ("Coverage accounting / ResearchMap"): did the Planner's OWN top-level
        research plan actually pay off, not just "is whatever got written grounded"? Every
        existing completion check verifies citation/grounding quality of content that already
        exists; none measure whether the Planner's own delegated breadth actually produced real
        evidence. Deliberately reuses only already-reliable, model-independent structural data —
        depth (delegation_depth_ctx, set once per _run_single_task dispatch, engine-side) and
        whether a task's own findings ever carried a real fetched URL (source_url starting with
        "http" -- the _run_single_task call site only ever passes a real fetched URL there, or
        falls back to the bare task_name string otherwise, see engine/orchestrator.py's two
        add_finding call sites) — not a new Planner-authored schema, which this project's own
        established philosophy avoids (small local models have repeatedly proven unreliable at
        following new structured-output conventions; see PLANNER_INSTRUCTIONS' own `_todos.md`
        checklist convention, which has zero code-level validation behind it).

        Returns {total, covered, ratio, uncovered_task_names} over DISTINCT depth==1 task names
        only — a nested Analyzer sub-call is expected to reuse already-fetched content and have no
        new URL of its own, so counting it would make coverage look artificially low every time
        the Planner's own top-level tasks correctly delegate deeper analysis. `ratio` is 1.0 (not
        an error) when there are zero depth==1 findings at all — check_thin_coverage's own
        minimum-task-count gate is what decides whether a ratio is even meaningful to act on, not
        this method."""
        top_level = [f for f in self.data.get("findings", []) if f.get("depth") == 1 and f.get("task_name")]
        by_task: dict = {}
        for f in top_level:
            by_task.setdefault(f["task_name"], []).append(f)
        total = len(by_task)
        if total == 0:
            return {"total": 0, "covered": 0, "ratio": 1.0, "uncovered_task_names": []}
        uncovered = [
            name for name, task_findings in by_task.items()
            if not any((tf.get("source_url") or "").startswith("http") for tf in task_findings)
        ]
        covered = total - len(uncovered)
        return {"total": total, "covered": covered, "ratio": covered / total, "uncovered_task_names": uncovered}

    def record_attempt(self, attempt_number: int, problem: Optional[str], fetched_url_count: int,
                        detail: Optional[str] = None) -> None:
        self.data["completion_check_attempts"].append({
            "attempt": attempt_number,
            "problem": problem,
            "fetched_url_count": fetched_url_count,
            "timestamp": time.time(),
            "detail": detail,
        })

    def record_tool_error(self, summary: str) -> None:
        """Count + sample tool calls whose OWN result text is an error (this project's tools
        return formatted error strings instead of raising — see engine/tui.py's
        _looks_like_tool_error). Capped sample list so a run that errors constantly doesn't bloat
        _run_state.json; the count alone is enough to show the trend past that cap."""
        self.data["tool_error_count"] = self.data.get("tool_error_count", 0) + 1
        samples = self.data.setdefault("tool_error_samples", [])
        if len(samples) < 10:
            samples.append(summary[:200])

    def next_subagent_label(self, agent_name: str) -> str:
        """Disambiguate a sub-agent name that gets dispatched more than once in the same run
        (the original delegate_tasks batch, then again in a later re-delegation after a
        completion-check nudge) — e.g. 'SubAgent_background' -> 'SubAgent_background#2' on its
        second real dispatch. Without this, the session log/UI shows two separate ~2-3 minute
        invocations as one source label, making elapsed-time analysis meaningless (confirmed
        live: looked exactly like one continuous 19-minute sub-agent, was actually two short
        ones with an 11-minute gap where the Planner was busy elsewhere). Also persists the raw
        counts as a diagnostic in their own right — how many times was each task re-delegated.

        Uniqueness: the counter itself is race-free against concurrent delegate_tasks dispatch —
        _run_single_task (orchestrator.py) is only ever scheduled via asyncio.gather on the same
        event loop, never asyncio.to_thread, and this method has no `await` in its body, so its
        read-increment-write executes as one atomic step from every other coroutine's
        perspective (asyncio only switches between coroutines at an `await` point). What this
        does NOT protect against: a model naming two genuinely different tasks such that one's
        raw name collides with the auto-generated '#N' suffix of another (e.g. real tasks named
        'background' and 'background#2' in the same run) — guarded against explicitly below by
        skipping any candidate label that's already a key in this run's own tracked names,
        rather than assuming '#N' can never collide."""
        counts = self.data.setdefault("subagent_invocations", {})
        counts[agent_name] = counts.get(agent_name, 0) + 1
        n = counts[agent_name]
        if n == 1:
            return agent_name
        label = f"{agent_name}#{n}"
        while label in counts:
            n += 1
            label = f"{agent_name}#{n}"
        counts[agent_name] = n
        return label

    def sync_fetched_urls(self) -> None:
        self.data["fetched_urls"] = get_fetched_urls()

    def save(self) -> None:
        try:
            os.makedirs(self.run_dir, exist_ok=True)
            path = os.path.join(self.run_dir, "_run_state.json")
            with self._lock:
                # Write-then-rename so a crash mid-write can't leave a truncated/corrupt file
                # (os.replace is atomic on Windows and POSIX).
                tmp = path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(self.data, f, indent=2)
                os.replace(tmp, path)
        except Exception as e:
            # Never crash the run over its own bookkeeping, but never lose the failure silently
            # either — this file is the forensic record the whole scoring methodology relies on.
            print(f"[run_state] WARNING: failed to write _run_state.json: {e}", file=sys.stderr)
