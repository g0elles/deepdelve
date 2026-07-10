import contextvars
import json
import os
import time
from typing import Optional

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


def reset_fetched_urls() -> None:
    fetched_urls_ctx.set([])


def record_fetched_url(url: str, filename: str) -> None:
    lst = fetched_urls_ctx.get()
    if lst is None:
        lst = []
        fetched_urls_ctx.set(lst)
    lst.append({"url": url, "filename": filename, "timestamp": time.time()})


def get_fetched_urls() -> list[dict]:
    return fetched_urls_ctx.get() or []


class RunState:
    """Structured, JSON-persisted record of a single research run. Written to
    `_run_state.json` inside the run's workspace folder so it survives independently of the
    model's own conversation transcript."""

    def __init__(self, run_dir: str):
        self.run_dir = run_dir
        # Completion-check attempt counter lives on the instance, not a module-level dict keyed
        # by id(run_state) — id() can be reused after garbage collection between runs, which would
        # let a stale attempt count leak into an unrelated later run.
        self.attempt = 0
        self.data = {
            "query": None,
            "plan": None,
            "findings": [],
            "fetched_urls": [],
            "completion_check_attempts": [],
            "started_at": time.time(),
        }

    def set_query(self, query: str) -> None:
        self.data["query"] = query

    def set_plan(self, plan: str) -> None:
        self.data["plan"] = plan

    def add_finding(self, source_url: str, summary: str) -> None:
        self.data["findings"].append({"source_url": source_url, "summary": summary, "timestamp": time.time()})

    def record_attempt(self, attempt_number: int, problem: Optional[str], fetched_url_count: int) -> None:
        self.data["completion_check_attempts"].append({
            "attempt": attempt_number,
            "problem": problem,
            "fetched_url_count": fetched_url_count,
            "timestamp": time.time(),
        })

    def sync_fetched_urls(self) -> None:
        self.data["fetched_urls"] = get_fetched_urls()

    def save(self) -> None:
        try:
            os.makedirs(self.run_dir, exist_ok=True)
            path = os.path.join(self.run_dir, "_run_state.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
        except Exception:
            pass
