"""
Extracts (context, response) training examples for the fine-tuning plan in ROADMAP.md
("Scoped fine-tuning plan (2026-07-18)") from this project's own real run history — no hand-
authored scenarios needed. Two sources, cross-referenced:

  - research_output/*/_run_state.json   -- WHEN a completion-check problem fired (attempt number,
                                            problem label, short `detail` warning, timestamp).
  - ~/.deepdelve/sessions/session_*.json -- WHAT the model actually did in response (the raw
                                            `ui_events` stream: function_call/text/prompt events).

The two aren't directly joined by any stored ID (`_run_state.json` doesn't record its session_id),
so this correlates them the same way manually verified as reliable this session: match the run's
`query` text against the session's initial "User"/"prompt" event text, then use the
completion-check attempt's Unix timestamp (converted to the same local-time ISO format the session
log uses) to find the first `source == "Agent"` event at or after it — that IS the model's
response to that round's verdict, for `thin_coverage` specifically (the one problem type that
stays in the Planner's own conversation rather than being dispatched to a fresh-context writer
sub-agent — see engine/completion.py's _FINDINGS_WRITER_FIXABLE_PROBLEMS/_BUILDER_FIXABLE_PROBLEMS).

Usage:
  python finetune/extract_dataset.py                    # scan everything, print a summary
  python finetune/extract_dataset.py --out dataset.jsonl  # also write examples as JSONL
"""

import argparse
import datetime
import glob
import json
import os
import re
import sys

RESEARCH_OUTPUT_GLOB = "research_output/*/_run_state.json"
SESSIONS_DIR = os.path.expanduser("~/.deepdelve/sessions")


def _load_json(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _iter_session_files():
    for path in glob.glob(os.path.join(SESSIONS_DIR, "session_*.json")):
        data = _load_json(path)
        if data is not None:
            yield path, data


def _find_matching_session(query: str, started_at: float, sessions: list[tuple[str, dict]]) -> dict | None:
    """Matches a run_state's query against a session's first User/prompt event. Text alone is NOT
    sufficient — this project's own standing benchmark queries (sales-forecasting, Colombia B2B)
    get re-run verbatim across many sessions/models, confirmed live: an early version of this
    matcher silently paired a run with a session from 4 days earlier that happened to share the
    same first-prompt text. Candidates are filtered by text match, then the one whose session
    started closest to (and no later than, allowing a few seconds of clock/logging skew) the
    run's own `started_at` wins — sessions are created at the same moment a run starts, so this
    is a real, not approximate, disambiguator."""
    query_norm = query.strip()
    best, best_gap = None, None
    for _, data in sessions:
        events = data.get("ui_events", [])
        if not events:
            continue
        first = events[0]
        if first.get("source") != "User" or first.get("type") != "prompt":
            continue
        text = (first.get("data") or {}).get("text", "").strip()
        if not text or (text[:200] != query_norm[:200]):
            continue
        try:
            session_start = datetime.datetime.fromisoformat(first["timestamp"]).timestamp()
        except (KeyError, ValueError):
            continue
        gap = started_at - session_start
        if gap < -5:  # session started meaningfully AFTER the run — can't be its source
            continue
        if best_gap is None or gap < best_gap:
            best, best_gap = data, gap
    return best


def _first_agent_event_at_or_after(events: list[dict], unix_ts: float) -> dict | None:
    target_iso = datetime.datetime.fromtimestamp(unix_ts).isoformat()
    for e in events:
        if e.get("timestamp", "") >= target_iso and e.get("source") == "Agent":
            return e
    return None


def _parse_task_call(event: dict) -> dict | None:
    """Normalizes a function_call event's JSON-string arguments into a real dict, matching the
    shape finetune/reward.py's schema_compliance_reward expects — deliberately does NOT silently
    fix a JSON-string `tasks` field (that would defeat the entire point of scoring it)."""
    if not event or event.get("type") != "function_call":
        return None
    data = event.get("data", {})
    try:
        arguments = json.loads(data.get("arguments", "{}"))
    except json.JSONDecodeError:
        arguments = {"_unparseable": data.get("arguments")}
    return {"name": data.get("name"), "arguments": arguments}


def extract_thin_coverage_examples(run_state_path: str, sessions: list[tuple[str, dict]]) -> list[dict]:
    run_state = _load_json(run_state_path)
    if not run_state:
        return []
    query = run_state.get("query") or ""
    started_at = run_state.get("started_at")
    if not query or started_at is None:
        return []
    session = _find_matching_session(query, started_at, sessions)
    if session is None:
        return []
    events = session.get("ui_events", [])

    examples = []
    for attempt in run_state.get("completion_check_attempts", []):
        if attempt.get("problem") != "thin_coverage":
            continue
        detail = attempt.get("detail") or ""
        # detail's own text quotes the uncovered task names in single quotes, e.g.
        # "...('task one', 'task two')..." — see check_thin_coverage's f-string in completion.py.
        prior_task_instructions = re.findall(r"'([^']+)'", detail)
        response_event = _first_agent_event_at_or_after(events, attempt["timestamp"])
        if response_event is None:
            continue
        tool_call = _parse_task_call(response_event) if response_event.get("type") == "function_call" else None
        text = (response_event.get("data") or {}).get("text", "") if response_event.get("type") == "text" else ""
        examples.append({
            "source_run": run_state_path,
            "query": query,
            "attempt": attempt["attempt"],
            "problem": "thin_coverage",
            "detail": detail,
            "prior_task_instructions": prior_task_instructions,
            "response_tool_call": tool_call,
            "response_text": text,
        })
    return examples


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", help="Write extracted examples as JSONL to this path")
    parser.add_argument("--research-output-dir", default=".", help="Project root (default: cwd)")
    args = parser.parse_args()

    run_state_paths = sorted(glob.glob(os.path.join(args.research_output_dir, RESEARCH_OUTPUT_GLOB)))
    if not run_state_paths:
        print(f"No run_state.json files found under {args.research_output_dir}/research_output/ "
              f"— run from the project root, or pass --research-output-dir.", file=sys.stderr)
        sys.exit(1)

    sessions = list(_iter_session_files())
    print(f"Scanning {len(run_state_paths)} run(s) against {len(sessions)} persisted session log(s)...")

    all_examples = []
    matched_runs = 0
    for path in run_state_paths:
        examples = extract_thin_coverage_examples(path, sessions)
        if examples:
            matched_runs += 1
        all_examples.extend(examples)

    print(f"Matched {matched_runs}/{len(run_state_paths)} runs to a session log.")
    print(f"Extracted {len(all_examples)} thin_coverage response example(s).")

    if all_examples:
        from reward import thin_coverage_response_reward
        scored = [
            (ex, thin_coverage_response_reward(
                ex["prior_task_instructions"], ex["response_tool_call"], ex["response_text"]))
            for ex in all_examples
        ]
        good = sum(1 for _, r in scored if r == 1.0)
        print(f"Reward distribution: {good} positive / {len(scored) - good} negative "
              f"(scored with finetune/reward.py's current heuristics).")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            for ex in all_examples:
                f.write(json.dumps(ex) + "\n")
        print(f"Wrote {len(all_examples)} examples to {args.out}")


if __name__ == "__main__":
    main()
