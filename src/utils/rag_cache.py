"""
Persistent cross-run cache of VERIFIED findings (ROADMAP.md "Strategic options" item 5,
RESEARCH.md §8, 2026-07-20) — real semantic retrieval via frozen sentence-embeddings, replacing
the old exact-string-match `knowledge_cache`/`experience_cache` (deleted commit 929b987,
2026-07-11).

Deliberately NOT the same design as the deleted cache. That one stored whole final ANSWERS keyed
by raw query text and injected them automatically into every run's prompt regardless of model —
confirmed via git history (session_status/2026-07-13.md) to have caused real cache contamination
during model bake-off comparisons: a later model's trial would hit an earlier model's cached
answer for the same query and reproduce it near-verbatim, invalidating independent A/B comparison.

This cache stores one verified ATOMIC FINDING (source URL + Analyzer-produced summary) per topic.
A Searcher that gets a cache hit still has to read, cite, and incorporate that finding into its OWN
summary, exactly as it would with a fresh web_search result — there is no mechanism by which a
cache hit can make one model's output look like another model's writing. It is also never injected
automatically: `src/tools/rag.py`'s `search_verified_findings` is an explicit tool the Searcher
chooses to call, and a cache hit is never registered as "fetched this run" (unlike the old cache,
which defeated the grounding check this way) — it's presented as pre-verified content cited by its
real original source URL and real historical verification timestamp.

Lazy, process-wide singleton, same pattern as agent_routing.py/grounding.py's `_get_nli_model` —
loaded at most once per process, fails OPEN (returns None/[] ) on any load error (missing
sentence-transformers, corrupt cache file). An environment that doesn't have this cache available
runs unaffected — Searcher roles fall back to plain `web_search`, same philosophy as every other
soft-dependency check in this codebase.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))  # for `import config`
import config  # noqa: E402

_EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"

_embedder = None
_load_failed = False
_entries: list[dict] | None = None  # lazy-loaded cache contents, kept in memory once loaded
_matrix = None  # numpy float32 (N, 384) matrix of normalized embeddings, parallel to _entries


def _get_cache_settings() -> dict:
    return config.cfg.get("settings", {}).get("rag_cache", {})


def _cache_path() -> str:
    default = f"~/.{config.APP_NAME}/rag_cache.json"
    raw = _get_cache_settings().get("path") or default
    return os.path.abspath(os.path.expanduser(raw.replace("{APP_NAME}", config.APP_NAME)))


def _load_embedder():
    global _embedder, _load_failed
    if _embedder is not None or _load_failed:
        return
    try:
        from sentence_transformers import SentenceTransformer

        _embedder = SentenceTransformer(_EMBEDDING_MODEL_NAME, device="cpu")
    except Exception:
        _load_failed = True


def _load_entries() -> None:
    """Loads the cache file into memory (once) and builds the normalized embedding matrix used for
    brute-force cosine similarity. At DeepDelve's realistic scale (a few thousand verified findings)
    this is a single BLAS matrix-vector multiply per lookup, sub-millisecond — no vector-DB
    dependency is justified; numpy is already transitive via sentence-transformers/scikit-learn."""
    global _entries, _matrix
    if _entries is not None:
        return
    _entries = []
    path = _cache_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                _entries = json.load(f)
        except Exception:
            _entries = []
    if not _entries:
        _matrix = None
        return
    import numpy as np

    vectors = np.array([e["embedding"] for e in _entries], dtype="float32")
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    _matrix = vectors / norms


def _persist(entries: list[dict]) -> None:
    path = _cache_path()
    parent_dir = os.path.dirname(path)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2)


def lookup(
    query_text: str, top_k: int = 3, min_similarity: float = 0.75, max_age_days: float = 7
) -> list[dict]:
    """Returns up to top_k prior verified findings whose task_context is semantically similar to
    query_text, above min_similarity, and no older than max_age_days. Always returns a list (empty
    on any failure or no-match) — never fabricates, never raises."""
    if not _get_cache_settings().get("enabled", False):
        return []
    _load_embedder()
    if _embedder is None:
        return []
    _load_entries()
    if not _entries or _matrix is None:
        return []

    import numpy as np

    query_vec = _embedder.encode([query_text])[0].astype("float32")
    norm = float(np.linalg.norm(query_vec))
    if norm == 0:
        return []
    query_vec = query_vec / norm
    similarities = _matrix @ query_vec  # cosine similarity, both sides pre-normalized

    now = time.time()
    max_age_seconds = max_age_days * 86400
    candidates = []
    for idx, sim in enumerate(similarities):
        if sim < min_similarity:
            continue
        entry = _entries[idx]
        age_seconds = now - entry.get("timestamp", 0)
        if age_seconds > max_age_seconds:
            continue
        candidates.append((float(sim), entry))

    candidates.sort(key=lambda pair: pair[0], reverse=True)
    return [
        {
            "source_url": entry["source_url"],
            "summary": entry["summary"],
            "timestamp": entry["timestamp"],
            "similarity": sim,
        }
        for sim, entry in candidates[:top_k]
    ]


def save(task_context: str, source_url: str, summary: str, model: str) -> None:
    """Persists a verified finding for future runs to reuse. Caller (orchestrator.py) is
    responsible for only calling this after the SAME grounding+relevance checks a live finding
    already passes — this function itself does no verification, it trusts the caller's gate.
    Silent no-op on any failure (fail-open, matches this project's established convention)."""
    if not _get_cache_settings().get("enabled", False):
        return
    _load_embedder()
    if _embedder is None:
        return
    global _entries, _matrix
    try:
        embedding = _embedder.encode([task_context])[0].tolist()
        _load_entries()
        entries = list(_entries or [])
        entries.append(
            {
                "task_context": task_context,
                "source_url": source_url,
                "summary": summary,
                "embedding": embedding,
                "model": model,
                "timestamp": time.time(),
            }
        )
        _persist(entries)
        # Invalidate the in-memory cache so the next lookup() picks up this new entry immediately
        # (matters within a single long-running process, e.g. the TUI across multiple runs).
        _entries = None
        _matrix = None
    except Exception:
        pass


if __name__ == "__main__":
    # Smallest-thing-that-fails-if-broken self-test, same spirit as agent_routing.py's own —
    # confirms fail-open behavior without requiring network access or a populated cache file.
    import config as _config_module

    _config_module.cfg = {"settings": {"rag_cache": {"enabled": False}}}
    assert lookup("current stable version of Rust") == [], (
        "disabled via config must return [] without attempting to load anything"
    )
    save("current stable version of Rust", "https://example.com", "Rust 1.97.1", "test-model")
    assert lookup("current stable version of Rust") == [], (
        "save() must also no-op while disabled"
    )

    print("rag_cache fail-open self-tests passed (real embedding-based lookup/save quality is "
          "exercised by test_structural_checks.py's dedicated scenario, not here).")
