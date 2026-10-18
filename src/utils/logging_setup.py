"""Observability floor: one place to turn on logging for every entrypoint (run_cli, the TUI, the
HTTP API). Before this, `logging` was imported in one file and called once -- every silently
swallowed exception elsewhere left zero trace, which is unworkable for a long-running server
process (src/api.py) or for the verification models in utils/grounding.py whose silent load
failure quietly degrades every citation check for the rest of the process with no signal at all.

Call configure_logging() exactly once, as early as possible in each entrypoint's main(). Every
other module just does `logger = logging.getLogger(__name__)` and calls it -- no wrapper needed,
stdlib propagation handles the rest."""
import logging
import os

import config


def configure_logging(level: int = logging.INFO) -> None:
    """Idempotent: safe to call from multiple entrypoints (e.g. a test importing both tui and
    api) without duplicating handlers."""
    root = logging.getLogger()
    if root.handlers:
        return
    log_path = os.path.join(config.get_workspace_dir(), "deepdelve.log")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)
    root.setLevel(level)
