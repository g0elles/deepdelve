"""
Extracts (context, response) training examples for the fine-tuning plan in ROADMAP.md
("Scoped fine-tuning plan (2026-07-18)") from this project's own real run history — no hand-
authored scenarios needed. Two sources, cross-referenced:

  - research_output/*/_run_state.json   -- WHEN a completion-check problem fired (attempt number,
                                            problem label, short `detail` warning, timestamp) and
                                            WHICH tool errors occurred (`tool_error_samples`).
  - ~/.deepdelve/sessions/session_*.json -- WHAT the model actually did in response (the raw
                                            `ui_events` stream: function_call/text/prompt events).

Three extractors, one per reward dimension in finetune/reward.py that needs real (not synthetic)
examples (schema_compliance_reward doesn't need extraction — it's checkable from ANY delegate_tasks
call shape directly, synthetic cases already cover it in reward.py's own self-tests):

  - extract_thin_coverage_examples: correlates by TEXT + TIMESTAMP (the two aren't joined by any
    stored ID, so this matches the run's `query` against the session's first prompt event, then
    finds the first `source == "Agent"` event at/after the completion-check attempt's timestamp —
    the model's response stays in the Planner's OWN conversation for this problem type only).
  - extract_writer_role_examples: correlates by DISPATCH NAME (`SubAgent_{writer_role}Fix_
    attempt{N}`, exact and reliable — the session log names each writer dispatch directly).
  - extract_tool_name_examples: negatives need no correlation at all (the hallucinated tool name
    is already in `tool_error_samples`' own error text, alongside a `[SourceLabel]` prefix this
    resolves to a calling role where possible — see ROLE_TOOLS in reward.py for why role-scoping
    matters here); positives are sampled from the matched session log's own real `function_call`
    events, same role attribution, so the SAME tool name can correctly be a positive example in
    one role's context and a negative in another's (e.g. `delegate_tasks`: real for Planner, not
    real for Builder — a live bug found while building this, not a hypothetical).

Usage:
  python finetune/extract_dataset.py                          # scan everything, print a summary
  python finetune/extract_dataset.py --out-dir finetune/data/  # also write examples as JSONL
"""

import argparse
import datetime
import glob
import json
import os
import re
import sys

from reward import real_tool_name_reward

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


_HALLUCINATED_TOOL_RE = re.compile(r'Requested function "([^"]+)" not found')

# Writer-role dispatch source labels are exact and parseable (see extract_writer_role_examples'
# own docstring); this is the only class of dispatch this extractor can reliably attribute to a
# specific role from the source label alone — a generic `SubAgent_{task_name}` label (Searcher/
# Analyzer dispatches) reflects the PARENT's chosen task name, not the target agent_id, so those
# stay role-unknown and let reward.py's real_tool_name_reward fall back to its own KNOWN_TOOLS
# check instead of a wrong role guess. ROLE_TOOLS/KNOWN_TOOLS themselves live in reward.py — this
# is the same scoring logic that runs live during training, not a separate copy.
_WRITER_DISPATCH_RE = re.compile(r"^SubAgent_(Builder|FindingsWriter)Fix_attempt\d+(_reviewed)?$")


def _infer_role(source_label: str) -> str | None:
    m = _WRITER_DISPATCH_RE.match(source_label)
    if m:
        return m.group(1)
    if source_label == "Agent":
        return "Planner"
    return None


def extract_writer_role_examples(run_state_path: str, sessions: list[tuple[str, dict]]) -> list[dict]:
    """Extracts (dispatch, wrote_file, response_text) examples for missing_findings/
    missing_artifact — the writer-role "Finished without ever calling write_workspace_file"
    failure (Bonsai-8B, qwen2.5:3b-instruct; see finetune/reward.py's writer_role_response_reward).
    Matched by DISPATCH NAME (`SubAgent_{writer_role}Fix_attempt{N}`), not timestamp — the session
    log names each writer dispatch directly via engine/completion.py::_dispatch_writer_review_fix's
    own `dispatch_task(f"{writer_role}Fix_attempt{attempt + 1}", ...)` call, so this is exact, not
    a heuristic correlation like the thin_coverage extractor has to use."""
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
        problem = attempt.get("problem")
        if problem not in ("missing_findings", "missing_artifact"):
            continue
        writer_role = "FindingsWriter" if problem == "missing_findings" else "Builder"
        dispatch_name = f"{writer_role}Fix_attempt{attempt['attempt'] + 1}"
        source_label = f"SubAgent_{dispatch_name}"
        dispatch_events = [e for e in events if e.get("source") == source_label]
        if not dispatch_events:
            continue  # this attempt's dispatch wasn't captured in this session log
        wrote_file = any(
            e.get("type") == "function_call" and (e.get("data") or {}).get("name") == "write_workspace_file"
            for e in dispatch_events
        )
        text_events = [e for e in dispatch_events if e.get("type") == "text"]
        response_text = text_events[-1]["data"].get("text", "") if text_events else ""
        examples.append({
            "source_run": run_state_path,
            "query": query,
            "attempt": attempt["attempt"],
            "problem": problem,
            "writer_role": writer_role,
            "wrote_file": wrote_file,
            "response_text": response_text,
        })
    return examples


_ERROR_SOURCE_RE = re.compile(r"^\[([^\]]+)\]")


def extract_tool_name_examples(run_state_path: str, sessions: list[tuple[str, dict]]) -> list[dict]:
    """Role-scoped examples for real_tool_name_reward. Negative examples mined from
    `tool_error_samples` (`Error: Requested function "{name}" not found.`,
    engine/orchestrator.py's tool_result_error_nudge) — each error string's own `[SourceLabel]`
    prefix identifies the calling dispatch, resolved to a role via `_infer_role` where possible so
    a genuinely real-but-wrong-role tool (e.g. Builder calling `delegate_tasks`, which it
    structurally doesn't have — found live while building this extractor, see ROLE_TOOLS' own
    comment) is labeled hallucinated-FOR-THAT-ROLE rather than contradicting a same-string
    genuine-Planner-tool positive example elsewhere in the dataset. Positive examples are real
    `function_call` events sampled from the actually-matched session log (not injected), same
    role-attribution logic, capped per run to avoid one chatty run dominating the dataset."""
    run_state = _load_json(run_state_path)
    if not run_state:
        return []
    examples = []
    for sample in run_state.get("tool_error_samples", []):
        m = _HALLUCINATED_TOOL_RE.search(sample)
        if not m:
            continue
        source_m = _ERROR_SOURCE_RE.match(sample)
        role = _infer_role(source_m.group(1)) if source_m else None
        tool_name = m.group(1)
        examples.append({
            "source_run": run_state_path, "tool_name": tool_name, "role": role,
            "is_hallucinated": real_tool_name_reward(tool_name, role=role) == 0.0,
        })

    query = run_state.get("query") or ""
    started_at = run_state.get("started_at")
    if query and started_at is not None:
        session = _find_matching_session(query, started_at, sessions)
        if session is not None:
            positive_cap = 3
            for e in session.get("ui_events", []):
                if positive_cap <= 0:
                    break
                if e.get("type") != "function_call":
                    continue
                tool_name = (e.get("data") or {}).get("name")
                if not tool_name:
                    continue
                role = _infer_role(e.get("source", ""))
                if real_tool_name_reward(tool_name, role=role) == 0.0:
                    continue  # a real call that failed the role check isn't a clean positive
                examples.append({
                    "source_run": run_state_path, "tool_name": tool_name, "role": role,
                    "is_hallucinated": False,
                })
                positive_cap -= 1
    return examples


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
    parser.add_argument("--out-dir", help="Write each dimension's examples as JSONL into this directory")
    parser.add_argument("--research-output-dir", default=".", help="Project root (default: cwd)")
    args = parser.parse_args()

    run_state_paths = sorted(glob.glob(os.path.join(args.research_output_dir, RESEARCH_OUTPUT_GLOB)))
    if not run_state_paths:
        print(f"No run_state.json files found under {args.research_output_dir}/research_output/ "
              f"— run from the project root, or pass --research-output-dir.", file=sys.stderr)
        sys.exit(1)

    sessions = list(_iter_session_files())
    print(f"Scanning {len(run_state_paths)} run(s) against {len(sessions)} persisted session log(s)...\n")

    thin_coverage, writer_role, tool_name = [], [], []
    matched_thin_coverage, matched_writer_role = 0, 0
    for path in run_state_paths:
        tc = extract_thin_coverage_examples(path, sessions)
        if tc:
            matched_thin_coverage += 1
        thin_coverage.extend(tc)

        wr = extract_writer_role_examples(path, sessions)
        if wr:
            matched_writer_role += 1
        writer_role.extend(wr)

        tool_name.extend(extract_tool_name_examples(path, sessions))

    from reward import thin_coverage_response_reward, writer_role_response_reward

    print(f"--- thin_coverage ({matched_thin_coverage}/{len(run_state_paths)} runs matched) ---")
    print(f"Extracted {len(thin_coverage)} example(s).")
    if thin_coverage:
        scored = [thin_coverage_response_reward(ex["prior_task_instructions"], ex["response_tool_call"],
                                                  ex["response_text"]) for ex in thin_coverage]
        good = sum(1 for r in scored if r == 1.0)
        print(f"Reward distribution: {good} positive / {len(scored) - good} negative.\n")

    print(f"--- writer_role ({matched_writer_role}/{len(run_state_paths)} runs matched) ---")
    print(f"Extracted {len(writer_role)} example(s).")
    if writer_role:
        scored = [writer_role_response_reward(ex["wrote_file"], ex["response_text"]) for ex in writer_role]
        good = sum(1 for r in scored if r == 1.0)
        print(f"Reward distribution: {good} positive / {len(scored) - good} negative.\n")

    print(f"--- tool_name (negatives from tool_error_samples, positives from matched session logs) ---")
    print(f"Extracted {len(tool_name)} example(s).")
    if tool_name:
        scored = [real_tool_name_reward(ex["tool_name"], role=ex["role"]) for ex in tool_name]
        good = sum(1 for r in scored if r == 1.0)
        print(f"Reward distribution: {good} positive / {len(scored) - good} negative.\n")

    total = len(thin_coverage) + len(writer_role) + len(tool_name)
    print(f"=== Total real extracted examples across all dimensions: {total} ===")

    if args.out_dir:
        os.makedirs(args.out_dir, exist_ok=True)
        for name, examples in (("thin_coverage", thin_coverage), ("writer_role", writer_role),
                                ("tool_name", tool_name)):
            path = os.path.join(args.out_dir, f"{name}.jsonl")
            with open(path, "w", encoding="utf-8") as f:
                for ex in examples:
                    f.write(json.dumps(ex) + "\n")
            print(f"Wrote {len(examples)} examples to {path}")


if __name__ == "__main__":
    main()
