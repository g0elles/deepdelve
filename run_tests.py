#!/usr/bin/env python
"""Run every test_*.py in the repo (helpers live in support_for_tests.py so they are not discovered as tests) (offline, no model needed) and fail loudly. pytest is not installed, so this is the one entry point.

    python run_tests.py              # all files
    python run_tests.py --clean      # fresh HOME (no dependence on your ~/.deepdelve), model caches offline: what a clean clone sees
    python run_tests.py --strict     # a SKIPPED line (a model missing from the local cache) counts as a failure
    python run_tests.py -k grounding # only files whose name contains the substring

Guards (each was a real failure on 2026-09-30):
  * a test file that defines test_ functions but has no runner block would run NOTHING and exit 0 -> reported as a failure;
  * a test that rewrites your live ~/.deepdelve/config.yaml (the /toggle_thinking palette test used to) -> reported as a failure.
"""
import argparse
import ast
import glob
import hashlib
import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
LIVE_CONFIG = os.path.expanduser("~/.deepdelve/config.yaml")


def _sha(path):
    try:
        with open(path, "rb") as f:
            return hashlib.sha1(f.read()).hexdigest()[:10]
    except OSError:
        return None


def _has_runner(path):
    """(defines test_ functions, has an `if __name__ == "__main__"` block) for one test file."""
    tree = ast.parse(open(path, encoding="utf-8").read())
    tests = any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test_") for n in tree.body)
    main = any(isinstance(n, ast.If) and "__main__" in ast.unparse(n.test) for n in tree.body)
    return tests, main


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clean", action="store_true", help="fresh HOME, HF offline")
    ap.add_argument("--strict", action="store_true", help="SKIPPED counts as failure")
    ap.add_argument("-k", default="", help="substring filter on file names")
    ap.add_argument("--timeout", type=int, default=900)
    a = ap.parse_args()

    files = sorted(f for f in glob.glob(os.path.join(ROOT, "test_*.py")) if a.k in os.path.basename(f))
    env = dict(os.environ)
    fresh_home = None
    if a.clean:
        fresh_home = tempfile.mkdtemp(prefix="cleanhome.")
        env.update(HOME=fresh_home, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    config_before = _sha(LIVE_CONFIG)
    results, skipped_total = [], 0
    for f in files:
        name = os.path.basename(f)
        has_tests, has_main = _has_runner(f)
        if has_tests and not has_main:
            results.append((name, "FAIL", "defines test_ functions but has no runner block, so it runs nothing", 0.0, 0))
            continue
        t0 = time.time()
        try:
            r = subprocess.run([sys.executable, f], cwd=os.path.join(ROOT, "src"), env=env, capture_output=True, text=True, timeout=a.timeout)
            out = (r.stdout or "") + (r.stderr or "")
            skips = sum(1 for line in out.splitlines() if line.startswith("SKIPPED:"))
            skipped_total += skips
            tail = [ln for ln in out.splitlines() if ln.strip() and "Loading weights" not in ln][-1:] or [""]
            status = "PASS" if r.returncode == 0 else "FAIL"
            if status == "PASS" and a.strict and skips:
                status = "FAIL"
            results.append((name, status, tail[0][:110] if status == "FAIL" else (f"{skips} skipped" if skips else ""), time.time() - t0, skips))
            if status == "FAIL" and r.returncode != 0:
                sys.stdout.write(f"\n--- {name} output (last 15 lines) ---\n" + "\n".join(out.splitlines()[-15:]) + "\n")
        except subprocess.TimeoutExpired:
            results.append((name, "FAIL", f"timed out after {a.timeout}s", time.time() - t0, 0))
    config_after = _sha(LIVE_CONFIG)
    if not a.clean and config_before != config_after:
        results.append(("(live config guard)", "FAIL", f"{LIVE_CONFIG} changed during the run ({config_before} -> {config_after}): a test wrote it", 0.0, 0))

    width = max((len(r[0]) for r in results), default=10)
    for name, status, note, secs, _ in results:
        print(f"{name:{width}s}  {status}  {secs:5.1f}s  {note}")
    failed = [r for r in results if r[1] == "FAIL"]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed" + (f", {skipped_total} skipped" if skipped_total else "") + (" (clean HOME)" if a.clean else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
