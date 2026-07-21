"""
Runtime inference for the non-generative routing classifier (RESEARCH.md §6, ROADMAP.md "Planned",
2026-07-20) — a frozen sentence-embedding model + logistic regression trained offline by
finetune/train_agent_routing_classifier.py on real historical (instructions, agent_id) pairs.

Used by engine/orchestrator.py's `delegate_tasks` to catch a hallucinated `agent_id`
("searcher", "PeerReviewer", invented role names — ~4.9% of real historical calls) before the task
is dispatched, per the project's decided "reject-and-nudge" policy: this module only PREDICTS,
`delegate_tasks` itself decides what to do with the prediction.

Lazy, process-wide singleton, same pattern as grounding.py's `_get_nli_model`/
`_get_topical_relevance_model` — loaded at most once per process, fails OPEN (returns None) on any
load error (missing artifact, missing sentence-transformers/scikit-learn dependency, no network for
the first-run HuggingFace download). An environment or config that doesn't have this classifier
available runs unaffected — `delegate_tasks` falls back to its existing pre-classifier validation
behavior entirely, same philosophy as every other soft-dependency check in this codebase.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))  # for `import config`
import config  # noqa: E402

_DEFAULT_ARTIFACT_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "finetune", "artifacts",
    "agent_routing_classifier.joblib",
)

# The 4 classes the classifier is trained on — matches finetune/reward.py's VALID_AGENT_IDS. Real,
# live-confirmed nuance found while building the training data (2026-07-20): these 4 span TWO
# DIFFERENT delegation levels, not one flat roster. The Planner's own delegate_tasks calls choose
# among {WebSearcher, AcademicSearcher, PeerReviewer, Builder, FindingsWriter} (src/app.py's
# Planner sub_agents) — only 2 of those 5 are classifier classes at all. A Searcher's OWN nested
# delegate_tasks calls choose among {DocumentAnalyzer, DataAnalyzer} (WebSearcher/AcademicSearcher's
# own sub_agents) — a disjoint 2-class pair. A caller whose real roster doesn't overlap this set
# at all (PeerReviewer/Builder/FindingsWriter dispatching their own delegate_tasks, if that ever
# happens) has no meaningful use for this classifier — restrict the prediction to the caller's
# actual candidate classes via `candidate_classes`, never to the full 4-class space unconditionally.
KNOWN_AGENT_IDS = frozenset({"WebSearcher", "AcademicSearcher", "DocumentAnalyzer", "DataAnalyzer"})

_classifier = None
_embedder = None
_load_failed = False


def _get_classifier_settings() -> dict:
    return config.cfg.get("settings", {}).get("agent_routing_classifier", {})


def _load():
    """Loads the joblib artifact (embedding model name + fitted sklearn classifier) plus the
    sentence-transformers embedder itself. Both must load successfully for the classifier to be
    usable — a partial load (artifact present but sentence-transformers missing, or vice versa) is
    treated the same as no artifact at all."""
    global _classifier, _embedder, _load_failed
    if _classifier is not None or _load_failed:
        return
    try:
        import joblib
        from sentence_transformers import SentenceTransformer

        settings = _get_classifier_settings()
        artifact_path = settings.get("artifact_path") or _DEFAULT_ARTIFACT_PATH
        artifact = joblib.load(artifact_path)
        _embedder = SentenceTransformer(artifact["embedding_model_name"], device="cpu")
        _classifier = artifact["classifier"]
    except Exception:
        _load_failed = True


def predict_agent_id(instructions: str, candidate_classes: frozenset[str] | None = None) -> tuple[str, float] | None:
    """Returns (predicted_agent_id, confidence) for a delegate_tasks task's `instructions` text, or
    None if the classifier is unavailable (disabled, artifact missing, or a dependency failed to
    load) — the caller must treat None as "no signal, fall back to existing behavior," never as a
    prediction of any particular class.

    `candidate_classes` restricts the prediction to a SUBSET of KNOWN_AGENT_IDS — the caller's own
    real available roles, intersected with KNOWN_AGENT_IDS by the caller of this function (see
    KNOWN_AGENT_IDS' own comment for why this matters: the classifier's 4 classes span two
    different delegation levels, and a candidate class the caller doesn't actually have available
    is not a meaningful suggestion). If the intersection is empty, returns None — there is nothing
    this classifier can usefully say about a caller whose roster doesn't overlap its training
    classes at all. Defaults to the full KNOWN_AGENT_IDS set if not given."""
    if not _get_classifier_settings().get("enabled", False):
        return None
    candidates = KNOWN_AGENT_IDS if candidate_classes is None else (candidate_classes & KNOWN_AGENT_IDS)
    if not candidates:
        return None
    _load()
    if _classifier is None or _embedder is None:
        return None
    embedding = _embedder.encode([instructions])
    probabilities = _classifier.predict_proba(embedding)[0]
    allowed_indices = [i for i, label in enumerate(_classifier.classes_) if label in candidates]
    if not allowed_indices:
        return None  # trained classes and this caller's candidates never overlap
    best_idx = max(allowed_indices, key=lambda i: probabilities[i])
    predicted_label = str(_classifier.classes_[best_idx])  # sklearn returns numpy.str_; plain str for callers
    confidence = float(probabilities[best_idx])
    return predicted_label, confidence


if __name__ == "__main__":
    # Smallest-thing-that-fails-if-broken self-test, same spirit as finetune/reward.py — confirms
    # the module fails open cleanly when disabled/unavailable, without requiring a real trained
    # artifact or network access to run in CI/a fresh checkout.
    import config as _config_module

    _config_module.cfg = {"settings": {"agent_routing_classifier": {"enabled": False}}}
    assert predict_agent_id("Find the current price of gold") is None, (
        "disabled via config must return None without attempting to load anything"
    )

    _config_module.cfg = {"settings": {"agent_routing_classifier": {
        "enabled": True, "artifact_path": "/nonexistent/path/to/nothing.joblib",
    }}}
    assert predict_agent_id("Find the current price of gold") is None, (
        "a missing artifact must fail open (return None), never raise"
    )
    assert predict_agent_id("Find the current price of gold", candidate_classes=frozenset()) is None, (
        "an empty candidate-class intersection must return None without even attempting to load"
    )
    assert predict_agent_id(
        "Critique findings.md for completeness", candidate_classes=frozenset({"PeerReviewer"}),
    ) is None, "a candidate set entirely outside KNOWN_AGENT_IDS must return None"

    print("All agent_routing self-tests passed (real-artifact prediction quality, per-class "
          "precision/recall, and the hallucinated-case regression check are all in "
          "finetune/train_agent_routing_classifier.py's own held-out evaluation, not here).")
