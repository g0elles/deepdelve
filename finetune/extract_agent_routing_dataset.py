"""
Extracts real (instructions, agent_id) pairs from this project's own `delegate_tasks` history for
the non-generative routing-classifier proposal (RESEARCH.md §6, merged into ROADMAP.md "Planned"
2026-07-20). Unlike extract_dataset.py's other extractors, this needs NO research_output/
correlation — every real delegate_tasks call the Planner ever made is already sitting directly in
the session logs (`source == "Agent"`, `type == "function_call"`, `data.name == "delegate_tasks"`),
so this reads `~/.deepdelve/sessions/session_*.json` straight, no run_state.json matching needed.

Splits into two outputs:
  - agent_routing.jsonl: pairs whose agent_id is one of reward.py's VALID_AGENT_IDS (the actual
    training signal), deduplicated for near-identical instruction text (reuses reward.py's own
    SequenceMatcher-based similarity threshold), stratified into a train/held-out split.
  - agent_routing_hallucinated.jsonl: pairs whose agent_id is NOT a real role — kept separately, not
    used for training (a classifier can only ever emit a class it was trained on, so these can't be
    positive examples of anything), but preserved for train_agent_routing_classifier.py's own
    regression check: does the classifier produce a sane real-class prediction from the
    instruction text alone, ignoring the model's own bad label, for every one of these real
    historical failures?
  - agent_routing_conflicting.jsonl: pairs from a manually-verified session where a real Planner
    routing mistake got baked into the log as if it were correct ground truth (see
    _KNOWN_LABEL_CONFLICT_SESSION_SUBSTRING's own comment for the full investigation, including
    two general-purpose automated approaches that were tried and rejected). Excluded from training,
    kept here for inspection.

Usage:
  python finetune/extract_agent_routing_dataset.py                          # scan, print a summary
  python finetune/extract_agent_routing_dataset.py --out-dir finetune/data/  # also write JSONL
"""

import argparse
import glob
import json
import os
import random
import sys

from reward import VALID_AGENT_IDS, _similarity

SESSIONS_DIR = os.path.expanduser("~/.deepdelve/sessions")

# Threshold for treating two instruction strings as near-duplicates of the same real dispatch —
# same value reward.py's thin_coverage_response_reward already uses for its own re-delegation
# similarity check, kept consistent rather than picking a new number for a similar judgment call.
_DEDUP_SIMILARITY_THRESHOLD = 0.8

HELD_OUT_FRACTION = 0.2
RANDOM_SEED = 20260720  # fixed for reproducibility across re-runs, not tuned


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


def _iter_delegate_tasks_calls(session_path: str, session: dict):
    """Yields (task_name, instructions, agent_id) for every real task dict inside every
    delegate_tasks call in this session log — from ANY caller, not just the Planner's own
    top-level turn (`source == "Agent"`). Real, live-confirmed finding while building this
    extractor: DocumentAnalyzer/DataAnalyzer only ever appear as targets of a NESTED delegate_tasks
    call made by WebSearcher/AcademicSearcher's own dispatch (`source == "SubAgent_<task_name>"`,
    per src/app.py's `sub_agents=[document_analyzer, data_analyzer]` on both searcher roles) — an
    initial version of this extractor filtered to `source == "Agent"` only and silently missed
    100% of DocumentAnalyzer/DataAnalyzer examples as a result (0 found vs. the real, substantial
    count once this filter was removed). Deliberately does NOT silently fix a JSON-string `tasks`
    field or a missing agent_id — a task with either shape is not a clean (instructions, agent_id)
    pair and is skipped, matching extract_dataset.py's own `_parse_task_call` philosophy of not
    repairing the exact thing this data is meant to characterize."""
    for event in session.get("ui_events", []):
        if event.get("type") != "function_call":
            continue
        data = event.get("data", {})
        if data.get("name") != "delegate_tasks":
            continue
        try:
            arguments = json.loads(data.get("arguments", "{}"))
        except json.JSONDecodeError:
            continue
        tasks = arguments.get("tasks")
        if not isinstance(tasks, list):
            continue  # the llama3.2:3b JSON-string-tasks failure — not a usable pair either way
        for t in tasks:
            if not isinstance(t, dict):
                continue
            instructions = t.get("instructions")
            agent_id = t.get("agent_id")
            if not instructions or not agent_id:
                continue
            yield session_path, t.get("task_name"), instructions, agent_id


def _dedup(pairs: list[dict]) -> list[dict]:
    """Drops near-identical instruction text within the SAME agent_id class — two genuinely
    different classes legitimately sharing similar-sounding instructions (e.g. a WebSearcher and a
    DocumentAnalyzer both told to "find pricing data") are not duplicates of each other and must
    both survive; only a near-repeat of the same (instructions, agent_id) pair is dropped."""
    kept: list[dict] = []
    for pair in pairs:
        if any(
            kept_pair["agent_id"] == pair["agent_id"]
            and _similarity(kept_pair["instructions"], pair["instructions"]) >= _DEDUP_SIMILARITY_THRESHOLD
            for kept_pair in kept
        ):
            continue
        kept.append(pair)
    return kept


# Real, manually-verified data-quality issue, 2026-07-20: one session had a real Planner routing
# mistake — 6 near-identical "Evaluate the X market in Colombia... market size, current state,
# gaps, competition" tasks labeled AcademicSearcher (each a different sector noun: waste
# management, healthcare, cybersecurity, renewable energy, logistics, VR/AR education), sitting
# alongside a 7th, differently-phrased but same-intent task in the SAME session correctly labeled
# WebSearcher ("Research the current state of Colombia's market and economy..."). These are
# market-sizing/business-research tasks, not literature searches — none of the 6 use any
# academic-search language (no "paper," "peer-reviewed," "journal," "academic," etc.) at all.
#
# TWO automated general-purpose approaches were tried and rejected before landing on this direct
# exclusion, and the reasoning is worth keeping: (1) SequenceMatcher literal-text similarity never
# caught these at all — each mentions a different sector noun, so character overlap stays well
# below _DEDUP_SIMILARITY_THRESHOLD despite being the same underlying task TEMPLATE. (2) Embedding
# (all-MiniLM-L6-v2) cosine similarity, tried next, scored the true conflict at only 0.44-0.59 —
# but a blanket cross-role comparison at threshold 0.5 flagged 849 of 1,109 real pairs, because
# legitimate two-stage pairs at DIFFERENT delegation levels (a WebSearcher fetching a source, then
# a nested DocumentAnalyzer reading it — e.g. "Search for the boiling point of water" vs. "Read
# the file '...boiling_point...md'. Extract the value") score just as high (0.68) purely from
# topical overlap. Restricting to same-routing-level pairs (WebSearcher<->AcademicSearcher only,
# DocumentAnalyzer<->DataAnalyzer only) removed that false-positive class, but this project's own
# real historical benchmark queries cluster so heavily on one topic domain (heuristic algorithms /
# sales forecasting / Colombia) that even WITHIN a routing level, a correctly-labeled "list the top
# 5 heuristic algorithms" (WebSearcher) and a correctly-labeled "find academic papers on heuristic
# algorithms" (AcademicSearcher) score similarly high on pure topical similarity to the genuine
# conflict — there is no threshold that separates "same underlying ask routed inconsistently" from
# "different ask, same topic domain, correctly routed differently" using text/embedding similarity
# alone in a dataset this topically concentrated. Forcing a general heuristic here would trade a
# small, well-evidenced, fixable problem for a much larger, noisier one (rejecting real, correctly-
# labeled examples this project can't easily replace). The one manually-verified session is
# excluded directly instead — an honest, inspectable fix for a real, specific, confirmed issue
# rather than an unreliable general detector.
_KNOWN_LABEL_CONFLICT_SESSION_SUBSTRING = "session_451f304f"


def _drop_known_label_conflicts(pairs: list[dict]) -> tuple[list[dict], list[dict]]:
    """Excludes the one manually-verified session with a confirmed AcademicSearcher/WebSearcher
    labeling inconsistency (see _KNOWN_LABEL_CONFLICT_SESSION_SUBSTRING's own comment for the full
    investigation). Returns (clean_pairs, excluded_pairs) — the second list is preserved for
    inspection, not silently discarded."""
    clean = [p for p in pairs if _KNOWN_LABEL_CONFLICT_SESSION_SUBSTRING not in p["source_session"]]
    excluded = [p for p in pairs if _KNOWN_LABEL_CONFLICT_SESSION_SUBSTRING in p["source_session"]]
    return clean, excluded


def _stratified_split(pairs: list[dict], held_out_fraction: float, seed: int) -> tuple[list[dict], list[dict]]:
    rng = random.Random(seed)
    by_class: dict[str, list[dict]] = {}
    for pair in pairs:
        by_class.setdefault(pair["agent_id"], []).append(pair)
    train, held_out = [], []
    for agent_id, examples in by_class.items():
        examples = examples[:]
        rng.shuffle(examples)
        n_held_out = max(1, round(len(examples) * held_out_fraction)) if len(examples) > 1 else 0
        held_out.extend(examples[:n_held_out])
        train.extend(examples[n_held_out:])
    return train, held_out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", help="Write agent_routing*.jsonl into this directory")
    args = parser.parse_args()

    sessions = list(_iter_session_files())
    if not sessions:
        print(f"No session logs found under {SESSIONS_DIR} — nothing to extract.", file=sys.stderr)
        sys.exit(1)
    print(f"Scanning {len(sessions)} session log(s)...\n")

    valid_pairs, hallucinated_pairs = [], []
    for session_path, session in sessions:
        for src, task_name, instructions, agent_id in _iter_delegate_tasks_calls(session_path, session):
            record = {
                "source_session": src, "task_name": task_name,
                "instructions": instructions, "agent_id": agent_id,
            }
            if agent_id in VALID_AGENT_IDS:
                valid_pairs.append(record)
            else:
                hallucinated_pairs.append(record)

    print(f"Real (instructions, agent_id) pairs found: {len(valid_pairs)} valid, "
          f"{len(hallucinated_pairs)} hallucinated agent_id ({len(hallucinated_pairs) / max(1, len(valid_pairs) + len(hallucinated_pairs)):.1%} of total).")

    clean_pairs, conflicting_pairs = _drop_known_label_conflicts(valid_pairs)
    if conflicting_pairs:
        print(f"\nExcluded {len(conflicting_pairs)} pairs from a manually-verified label-conflict "
              f"session (see _KNOWN_LABEL_CONFLICT_SESSION_SUBSTRING's own comment):")
        for p in conflicting_pairs:
            print(f"  {p['agent_id']:20} | {p['instructions'][:90]}")
        print()

    deduped = _dedup(clean_pairs)
    print(f"After near-duplicate removal (threshold {_DEDUP_SIMILARITY_THRESHOLD}): {len(deduped)} valid pairs "
          f"(dropped {len(clean_pairs) - len(deduped)}).")

    by_class: dict[str, int] = {}
    for pair in deduped:
        by_class[pair["agent_id"]] = by_class.get(pair["agent_id"], 0) + 1
    print("Class distribution:", ", ".join(f"{k}={v}" for k, v in sorted(by_class.items(), key=lambda kv: -kv[1])))

    train, held_out = _stratified_split(deduped, HELD_OUT_FRACTION, RANDOM_SEED)
    print(f"Stratified split: {len(train)} train / {len(held_out)} held-out.")

    hallucinated_labels = sorted({p["agent_id"] for p in hallucinated_pairs})
    print(f"Distinct hallucinated agent_id values seen: {hallucinated_labels}")

    if args.out_dir:
        os.makedirs(args.out_dir, exist_ok=True)
        for name, examples in (
            ("agent_routing", train),
            ("agent_routing_heldout", held_out),
            ("agent_routing_hallucinated", hallucinated_pairs),
            ("agent_routing_conflicting", conflicting_pairs),
        ):
            path = os.path.join(args.out_dir, f"{name}.jsonl")
            with open(path, "w", encoding="utf-8") as f:
                for ex in examples:
                    f.write(json.dumps(ex) + "\n")
            print(f"Wrote {len(examples)} examples to {path}")


if __name__ == "__main__":
    main()
