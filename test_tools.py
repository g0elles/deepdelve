"""Smallest thing that fails if tools/web.py's and tools/fs.py's pure-logic branches break.
Run: venv/Scripts/python test_tools.py (no framework needed) -- same convention as
test_structural_checks.py, split into its own file since that one is scoped to the
completion-check/orchestrator engine, not the tool layer (2026-08-02 audit: tools/web.py and
tools/fs.py had zero direct test coverage before this file).
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

import config
config.cfg.setdefault("settings", {})["workspace"] = {"type": "disk", "dir": "/tmp/deepdelve_test_workspace"}

from tools.fs import _get_safe_path, _lock_for_path
from tools.web import (
    _is_unsafe_fetch_host, _looks_like_redirect_stub, _stub_reason, _slugify_for_filename,
)


def _check_lock_for_path_serializes_same_path_only():
    """_lock_for_path (tools/fs.py, 2026-10-17 audit fix for edit_workspace_file's _IN_MEMORY_FS
    TOCTOU -- tool calls run on real OS threads, so two concurrent edits to the SAME path could
    otherwise both read stale content and the second write silently discards the first). Proves
    the actual property that matters: same path -> mutual exclusion (max concurrent 1), different
    path -> no exclusion at all (max concurrent 2) -- same "track concurrent entries with a sleep
    inside the critical section" proof pattern as test_api.py's own _queue_serialization_scenario."""
    def _max_concurrent(paths):
        concurrent = [0]
        max_concurrent = [0]
        lock_guard = threading.Lock()

        def worker(path):
            with _lock_for_path(path):
                with lock_guard:
                    concurrent[0] += 1
                    max_concurrent[0] = max(max_concurrent[0], concurrent[0])
                time.sleep(0.05)
                with lock_guard:
                    concurrent[0] -= 1

        threads = [threading.Thread(target=worker, args=(p,)) for p in paths]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return max_concurrent[0]

    same_path_max = _max_concurrent(("/ws/findings.md", "/ws/findings.md"))
    assert same_path_max == 1, f"same-path concurrent edits must serialize, got max_concurrent={same_path_max}"

    diff_path_max = _max_concurrent(("/ws/findings.md", "/ws/final_report.md"))
    assert diff_path_max == 2, f"different-path edits must NOT block each other, got max_concurrent={diff_path_max}"


def main():
    # --- path traversal (_get_safe_path, tools/fs.py) ---
    assert _get_safe_path("../../etc/passwd") == "", "leading '..' must be rejected"
    assert _get_safe_path("notes/../../etc/passwd") == "", "embedded '..' must be rejected"
    assert _get_safe_path("/etc/passwd") == "", "absolute unix path must be rejected"
    assert _get_safe_path(r"C:\evil") == "", "drive-qualified Windows path must be rejected"
    assert _get_safe_path("C:evil") == "", "drive-relative Windows path must be rejected"
    assert _get_safe_path(r"\\server\share") == "", "UNC-style path must be rejected"
    assert _get_safe_path("findings.md") != "", "a normal in-workspace filename must be allowed"
    assert _get_safe_path("sources/article.md") != "", "a normal subdirectory path must be allowed"

    # --- SSRF guard (_is_unsafe_fetch_host, tools/web.py -- 2026-08-02 audit fix) ---
    assert _is_unsafe_fetch_host("127.0.0.1"), "loopback must be blocked"
    assert _is_unsafe_fetch_host("localhost"), "localhost must be blocked"
    assert _is_unsafe_fetch_host("169.254.169.254"), "cloud metadata IP must be blocked"
    assert _is_unsafe_fetch_host("10.0.0.5"), "RFC1918 10.x must be blocked"
    assert _is_unsafe_fetch_host("172.16.0.1"), "RFC1918 172.16-31.x must be blocked"
    assert _is_unsafe_fetch_host("192.168.1.1"), "RFC1918 192.168.x must be blocked"
    assert _is_unsafe_fetch_host(""), "empty host must be treated as unsafe (nothing to allow)"
    assert not _is_unsafe_fetch_host("en.wikipedia.org"), "a real public host must NOT be blocked"
    assert not _is_unsafe_fetch_host("example.com"), "a real public host must NOT be blocked"

    # --- redirect-stub detection (_looks_like_redirect_stub, tools/web.py) ---
    assert _looks_like_redirect_stub(
        "Redirect\n\n[Click here](/2026/07/09/Rust-1.97.0/) to be redirected..."
    ) == "/2026/07/09/Rust-1.97.0/"
    assert _looks_like_redirect_stub("A real article with substantial prose content, no links at all.") is None
    assert _looks_like_redirect_stub("") is None
    # Two links is not a redirect stub shape (real pages often have >=2 links even when short).
    assert _looks_like_redirect_stub("[one](/a) and [two](/b)") is None

    # --- soft-404/paywall stub detection (_stub_reason, tools/web.py) ---
    assert _stub_reason("") == "empty page"
    assert _stub_reason("Page not found. 404 error.") is not None, "a real paywall/not-found marker must be flagged"
    real_article = " ".join(["This is a real sentence with enough words to count as prose."] * 15)
    assert _stub_reason(real_article) is None, "substantial real prose must never be flagged as a stub"

    # --- deterministic filename slugging (_slugify_for_filename, tools/web.py) ---
    a = _slugify_for_filename("https://example.com/article", "seed")
    b = _slugify_for_filename("https://example.com/article", "seed")
    c = _slugify_for_filename("https://example.com/different-article", "seed")
    assert a == b, "same URL must always produce the same filename (idempotent, cache-friendly)"
    assert a != c, "different URLs must not collide onto the same filename"

    _check_lock_for_path_serializes_same_path_only()

    print("All tool-layer assertions passed.")


if __name__ == "__main__":
    main()
