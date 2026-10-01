#!/usr/bin/env python
"""Pre-flight checks that must pass before ANY live run (smoke test, A/B, replay, TUI session that will fetch pages). Exit 1 on any FAIL.

    python preflight.py                      # everything, including the clean-HOME test suite
    python preflight.py --skip-tests         # fast checks only
    python preflight.py --require-popup-safe # also FAIL (not just warn) unless the launching environment is popup-safe
    python preflight.py --allow-dirty        # tracked-file edits are expected (you are mid-change)

Every check exists because of a real failure on 2026-09-30: a live run started on stale or dirty code, with enable_thinking flipped by a test, on a GPU
another run still held, next to an orphaned process, or with Chromium windows popping up on the desktop.
"""
import argparse
import json
import os
import subprocess
import sys
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
LIVE_CONFIG = os.path.expanduser("~/.deepdelve/config.yaml")
PROJECT_PROCESS_MARKERS = ("src/app.py", "run_ab", "logging_proxy.py", "replay_writer.py", "raw_sweep.py", "typography_probe.py",
                           "guard_mechanism_probe.py", "pipeline.run")
rows = []


def report(level, name, detail=""):
    rows.append(level)
    print(f"{level:4s}  {name:22s} {detail}")


def _self_chain():
    pids, p = set(), os.getpid()
    while p > 1:
        pids.add(p)
        try:
            p = int(open(f"/proc/{p}/stat").read().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            break
    return pids


def check_git(allow_dirty):
    head = subprocess.run(["git", "log", "--oneline", "-1"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT, capture_output=True, text=True).stdout.strip().splitlines()
    if dirty and not allow_dirty:
        report("FAIL", "clean tree", f"{len(dirty)} tracked file(s) modified (commit or stash first; results must map to one commit): {head[:60]}")
    else:
        report("PASS", "clean tree" if not dirty else "tree (dirty allowed)", head[:70])


def check_config():
    try:
        import yaml
        cfg = yaml.safe_load(open(LIVE_CONFIG)) or {}
    except Exception as e:
        return report("FAIL", "live config", f"cannot read {LIVE_CONFIG}: {e}")
    st = cfg.get("settings", {})
    shown = f"backend={cfg.get('api', {}).get('backend')} thinking_effort={st.get('thinking_effort')} num_ctx={st.get('num_ctx')} max_run_minutes={st.get('max_run_minutes')}"
    if st.get("enable_thinking"):
        report("FAIL", "enable_thinking", "is true in the live config (project rule: must be false). " + shown)
    else:
        report("PASS", "enable_thinking", "false. " + shown)


def check_processes():
    mine, found = _self_chain(), []
    for d in os.listdir("/proc"):
        if not d.isdigit() or int(d) in mine:
            continue
        try:
            cmd = open(f"/proc/{d}/cmdline", "rb").read().replace(b"\0", b" ").decode("utf-8", "replace")
        except OSError:
            continue
        if any(m in cmd for m in PROJECT_PROCESS_MARKERS) and "preflight.py" not in cmd:
            found.append(f"{d}:{cmd[:70]}")
    report("FAIL" if found else "PASS", "stray processes", "; ".join(found[:3]) if found else "none")


def check_gpu():
    loaded, used_ratio = None, None
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/ps", timeout=4) as r:
            loaded = [f"{m['name']}(ctx {m.get('context_length')})" for m in json.load(r).get("models", [])]
    except Exception as e:
        return report("FAIL", "ollama", f"not reachable on 11434: {type(e).__name__}")
    try:
        out = subprocess.run(["rocm-smi", "--showmeminfo", "vram"], capture_output=True, text=True, timeout=15).stdout
        total = used = None
        for line in out.splitlines():
            if line.startswith("GPU[0]") and "Total Memory" in line:
                total = int(line.rsplit(":", 1)[1])
            if line.startswith("GPU[0]") and "Total Used Memory" in line:
                used = int(line.rsplit(":", 1)[1])
        used_ratio = used / total if total and used is not None else None
    except Exception:
        pass
    if used_ratio is not None and used_ratio > 0.5 and not loaded:
        report("FAIL", "gpu", f"{used_ratio:.0%} of VRAM used but no Ollama model is loaded: something else holds the GPU")
    else:
        report("PASS", "gpu", f"vram {used_ratio:.0%} used; ollama loaded: {loaded or 'nothing'}" if used_ratio is not None else f"ollama loaded: {loaded or 'nothing'}")


def check_popup_safe(required):
    forced = os.environ.get("DEEPDELVE_FORCE_VIRTUAL_DISPLAY") == "1"
    ambient = [v for v in ("DISPLAY", "WAYLAND_DISPLAY") if os.environ.get(v)]
    if forced and not ambient:
        return report("PASS", "popup safety", "DEEPDELVE_FORCE_VIRTUAL_DISPLAY=1, no DISPLAY/WAYLAND_DISPLAY")
    how = "launch with: export DEEPDELVE_FORCE_VIRTUAL_DISPLAY=1; unset DISPLAY WAYLAND_DISPLAY  (setting DISPLAY alone is not enough on Wayland)"
    report("FAIL" if required else "WARN", "popup safety", ("this shell is not popup-safe; " if not forced else f"ambient {ambient}; ") + how)


def check_tests():
    r = subprocess.run([sys.executable, os.path.join(ROOT, "run_tests.py"), "--clean"], capture_output=True, text=True)
    summary = [ln for ln in r.stdout.splitlines() if "passed" in ln][-1:] or [r.stdout[-120:]]
    report("PASS" if r.returncode == 0 else "FAIL", "tests (clean HOME)", summary[0] + ("" if r.returncode == 0 else "  -> run: python run_tests.py --clean"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-tests", action="store_true")
    ap.add_argument("--require-popup-safe", action="store_true")
    ap.add_argument("--allow-dirty", action="store_true")
    a = ap.parse_args()
    check_git(a.allow_dirty)
    check_config()
    check_processes()
    check_gpu()
    check_popup_safe(a.require_popup_safe)
    if not a.skip_tests:
        check_tests()
    failed = rows.count("FAIL")
    print(f"\npreflight: {'FAILED (' + str(failed) + ')' if failed else 'OK'}" + (f", {rows.count('WARN')} warning(s)" if rows.count("WARN") else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
