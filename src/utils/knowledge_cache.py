import json
import os
import re
import time
from typing import Optional

# -------------------------------------------------------------
# [!CAUTION] RULES FOR LLM CODING ASSISTANTS EDITING THIS:
# This is an internal engine utility, NOT an agent-facing tool — never decorate
# anything here with @tool or expose it to the agent. Reads/writes to this cache
# happen automatically in engine/tui.py, deterministically, outside model control.
# This is intentional: the model has repeatedly proven unreliable at remembering
# to call an explicit "check the cache" tool, so the engine does it for it.
#
# Two granularities (DelveAgent's Dual-Granularity Memory, arXiv:2606.18648):
#   - knowledge store (lookup/save below): verified {question -> answer} facts.
#   - experience store (lookup_experience/save_experience): successful {query_shape -> plan}
#     trajectories, so a structurally similar future query can seed its plan from a past
#     success instead of planning from zero every time.
# -------------------------------------------------------------

def _cache_path() -> str:
    import config
    default = f"~/.{config.APP_NAME}/knowledge_cache.json"
    raw = config.cfg.get("settings", {}).get("knowledge_cache", {}).get("path", default)
    return os.path.abspath(os.path.expanduser(raw.replace("{APP_NAME}", config.APP_NAME)))


def _experience_path() -> str:
    import config
    default = f"~/.{config.APP_NAME}/experience_cache.json"
    raw = config.cfg.get("settings", {}).get("experience_cache", {}).get("path", default)
    return os.path.abspath(os.path.expanduser(raw.replace("{APP_NAME}", config.APP_NAME)))


def _normalize(question: str) -> str:
    return re.sub(r"\s+", " ", question.strip().lower())


def _load(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save(path: str, cache: dict) -> None:
    parent_dir = os.path.dirname(path)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)


# --- Knowledge store: verified {question -> answer} facts ---

def lookup(question: str, max_age_days: float = 7) -> Optional[dict]:
    """Return the cached entry for this exact (normalized) question if it exists and isn't stale."""
    entry = _load(_cache_path()).get(_normalize(question))
    if not entry:
        return None
    age_days = (time.time() - entry.get("timestamp", 0)) / 86400
    if age_days > max_age_days:
        return None
    return entry


def save(question: str, answer: str) -> None:
    """Persist a verified (grounded, real-research) answer for future runs to reuse."""
    cache = _load(_cache_path())
    cache[_normalize(question)] = {
        "question": question,
        "answer": answer,
        "timestamp": time.time(),
    }
    _save(_cache_path(), cache)


# --- Experience store: successful {query_shape -> plan} trajectories ---

def classify_query_shape(query: str) -> str:
    """Coarse, deterministic query-shape classifier — NOT an LLM call, just cheap keyword
    heuristics. Good enough to bucket "similar" queries for plan-seeding; doesn't need to be
    precise since it only ever *suggests* a past plan, never forces it."""
    q = query.lower()
    academic_markers = ("paper", "arxiv", "study", "research on", "related papers", "citation", "journal")
    comparative_markers = (" vs ", " versus ", "compare", "comparison", "difference between")
    if any(m in q for m in academic_markers):
        return "academic-deep-dive"
    if any(m in q for m in comparative_markers):
        return "comparative"
    if len(q.split()) <= 8:
        return "simple-factual"
    return "general-deep-research"


def lookup_experience(query: str, max_entries: int = 3) -> list[dict]:
    """Return up to max_entries past successful plans for this query's shape, most recent first."""
    shape = classify_query_shape(query)
    cache = _load(_experience_path())
    entries = cache.get(shape, [])
    return sorted(entries, key=lambda e: e.get("timestamp", 0), reverse=True)[:max_entries]


def save_experience(query: str, plan: str, slot_count: int, outcome: str = "success", max_entries_per_shape: int = 5) -> None:
    """Persist a successful run's plan, keyed by query shape, for future plan-seeding.
    Only called after a run passes all completion checks (same discipline as the knowledge
    store — a weak/ungrounded run never pollutes this cache)."""
    shape = classify_query_shape(query)
    cache = _load(_experience_path())
    entries = cache.setdefault(shape, [])
    entries.append({
        "query": query,
        "plan_used": plan,
        "slot_count": slot_count,
        "outcome": outcome,
        "timestamp": time.time(),
    })
    entries.sort(key=lambda e: e.get("timestamp", 0), reverse=True)
    cache[shape] = entries[:max_entries_per_shape]
    _save(_experience_path(), cache)
