"""
Trains the non-generative routing classifier for `delegate_tasks`'s `agent_id` (RESEARCH.md §6,
ROADMAP.md "Planned", 2026-07-20) — a frozen sentence-embedding model + logistic regression, NOT
SetFit (its 8-16-example-per-class regime is well below this project's real data volume) and NOT a
fully fine-tuned encoder (unnecessary machinery at ~1,100 examples / 4 classes, per the literature
checked during the review).

Reads finetune/data/agent_routing.jsonl (train) + agent_routing_heldout.jsonl (evaluation), written
by extract_agent_routing_dataset.py. Evaluates PER-CLASS precision/recall on the held-out split, not
just aggregate accuracy, given the real class imbalance (DocumentAnalyzer/WebSearcher far more
common than AcademicSearcher). Also runs every real hallucinated-agent_id case
(agent_routing_hallucinated.jsonl) through the trained classifier as a regression check — there is
no "correct" label for these (the model's own agent_id was never real), so this only confirms the
classifier produces a real class from the instruction text alone and never crashes, for manual
review of whether the prediction looks reasonable.

Saves the fitted classifier + embedding-model name as one joblib artifact so the runtime module
(src/utils/agent_routing.py) can load both pieces together.

Usage:
  python finetune/train_agent_routing_classifier.py
"""

import json
import os
import sys

EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
ARTIFACTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "artifacts")
ARTIFACT_PATH = os.path.join(ARTIFACTS_DIR, "agent_routing_classifier.joblib")


def _load_jsonl(path: str) -> list[dict]:
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def main():
    train = _load_jsonl(os.path.join(DATA_DIR, "agent_routing.jsonl"))
    held_out = _load_jsonl(os.path.join(DATA_DIR, "agent_routing_heldout.jsonl"))
    hallucinated = _load_jsonl(os.path.join(DATA_DIR, "agent_routing_hallucinated.jsonl"))

    if not train:
        print(f"No training data at {DATA_DIR}/agent_routing.jsonl — run "
              f"extract_agent_routing_dataset.py --out-dir finetune/data/ first.", file=sys.stderr)
        sys.exit(1)

    try:
        from sentence_transformers import SentenceTransformer
        from sklearn.linear_model import LogisticRegression
        from sklearn.metrics import classification_report
        import joblib
    except ImportError as e:
        print(f"Missing dependency: {e}. Requires sentence-transformers and scikit-learn "
              f"(see pyproject.toml).", file=sys.stderr)
        sys.exit(1)

    print(f"Loading embedding model {EMBEDDING_MODEL_NAME} (CPU)...")
    embedder = SentenceTransformer(EMBEDDING_MODEL_NAME, device="cpu")

    train_texts = [ex["instructions"] for ex in train]
    train_labels = [ex["agent_id"] for ex in train]
    print(f"Embedding {len(train_texts)} training examples...")
    train_embeddings = embedder.encode(train_texts, show_progress_bar=False)

    clf = LogisticRegression(class_weight="balanced", max_iter=1000)
    clf.fit(train_embeddings, train_labels)
    print(f"Trained on {len(train_texts)} examples across {len(set(train_labels))} classes.")

    if held_out:
        held_out_texts = [ex["instructions"] for ex in held_out]
        held_out_labels = [ex["agent_id"] for ex in held_out]
        held_out_embeddings = embedder.encode(held_out_texts, show_progress_bar=False)
        predictions = clf.predict(held_out_embeddings)
        print(f"\n--- Held-out evaluation ({len(held_out)} examples) ---")
        print(classification_report(held_out_labels, predictions, zero_division=0))
    else:
        print("\nNo held-out data available — skipping evaluation.", file=sys.stderr)

    if hallucinated:
        print(f"\n--- Regression check: {len(hallucinated)} real hallucinated-agent_id cases ---")
        print("(no 'correct' label exists for these — reviewing that the classifier abstains to a")
        print(" real class from instruction text alone, never crashes, never inherits the bad label)\n")
        hallucinated_texts = [ex["instructions"] for ex in hallucinated]
        hallucinated_embeddings = embedder.encode(hallucinated_texts, show_progress_bar=False)
        hallucinated_predictions = clf.predict(hallucinated_embeddings)
        hallucinated_confidences = clf.predict_proba(hallucinated_embeddings).max(axis=1)
        for ex, pred, conf in zip(hallucinated, hallucinated_predictions, hallucinated_confidences):
            print(f"  original agent_id={ex['agent_id']!r:25} -> classifier predicts {pred!r} "
                  f"(confidence {conf:.2f}) | instructions: {ex['instructions'][:80]!r}")

    os.makedirs(ARTIFACTS_DIR, exist_ok=True)
    joblib.dump({"embedding_model_name": EMBEDDING_MODEL_NAME, "classifier": clf}, ARTIFACT_PATH)
    print(f"\nSaved artifact to {ARTIFACT_PATH}")


if __name__ == "__main__":
    main()
