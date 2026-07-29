"""Shared session-log loading, extracted 2026-07-29: _load_json/_iter_session_files/SESSIONS_DIR
were byte-identical copy-pasted between extract_dataset.py and extract_agent_routing_dataset.py."""

import glob
import json
import os

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
