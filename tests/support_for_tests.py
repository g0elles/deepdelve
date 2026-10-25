"""Shared helpers for the test files. Models (embedder / cross-encoder) are loaded from the local HF cache only; when one is not cached the tests that
need it print a SKIPPED line instead of failing with a misleading assertion (the product fails open without a model by design). run_tests.py reports
every SKIPPED line and fails on any of them under --strict."""


def skip_notice(what: str) -> None:
    print(f"SKIPPED: {what} (model not in the local cache: set HF_HOME to the model cache, or run once online)")


def topical_model_available() -> bool:
    from utils.grounding import _get_topical_relevance_model
    return _get_topical_relevance_model() is not None


def rag_embedder_available() -> bool:
    from utils import rag_cache
    rag_cache._load_embedder()
    return rag_cache._embedder is not None
