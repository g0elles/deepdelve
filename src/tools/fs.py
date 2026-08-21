from typing import Dict, List
import os
import re
import contextvars
from agent_framework import tool
from tools.core import with_quota, _get_tool_rule, TOOL_ERROR_PREFIX

# --- WORKSPACE FILE SYSTEM ---
_IN_MEMORY_FS: Dict[str, str] = {}
session_dir_ctx = contextvars.ContextVar('session_dir', default="")

def _get_workspace_type() -> str:
    import config
    return config.get_workspace_type()

def _get_workspace_dir() -> str:
    import config
    return config.get_workspace_dir()

def _get_safe_path(filename: str) -> str:
    # Safely allow subdirectories while blocking traversal hacks. Drive-letter names are matched
    # by regex, not os.path.splitdrive — splitdrive only recognizes drives when running ON
    # Windows, so the same guard silently stopped rejecting "C:\evil" after the Linux migration
    # (caught by this suite's own verdict row going red cross-platform).
    if (".." in filename or filename.startswith("/") or filename.startswith("\\")
            or re.match(r'^[A-Za-z]:', filename)):  # "C:\evil" / "C:evil" / UNC
        return ""

    session_dir = session_dir_ctx.get()
    if session_dir:
        filename = os.path.join(session_dir, filename)

    if _get_workspace_type() == "disk":
        # os.path.join silently discards the base for drive-qualified ("C:\evil") and
        # drive-relative ("C:evil") names on Windows — containment check catches those.
        base = os.path.abspath(_get_workspace_dir())
        target = os.path.abspath(os.path.join(base, filename))
        try:
            if os.path.commonpath([base, target]) != base:
                return ""
        except ValueError:  # different drives
            return ""
        return target
    return filename

def get_workspace_files() -> List[str]:
    """Helper for TUI to list files agnostic of storage backend.

    Returns bare filenames (without session prefix) so agents can pass them
    directly to read_workspace_file/grep_workspace_file. The session prefix
    is transparently added by _get_safe_path inside those functions.
    """
    session_dir = session_dir_ctx.get()

    if _get_workspace_type() == "disk":
        d = _get_workspace_dir()
        if session_dir:
            d = os.path.join(d, session_dir)
        if not os.path.isdir(d): return []
        res = []
        for root, _, files in os.walk(d):
            for f in files:
                rel = os.path.relpath(os.path.join(root, f), d)
                res.append(rel.replace("\\", "/"))
        return res

    if session_dir:
        prefix = session_dir + "/"
        return [k[len(prefix):] for k in _IN_MEMORY_FS.keys() if k.startswith(prefix)]
    return list(_IN_MEMORY_FS.keys())

def get_workspace_file_content(filename: str) -> str | None:
    """Helper for TUI to read a file agnostic of storage backend."""
    path = _get_safe_path(filename)
    if not path: return None
    if _get_workspace_type() == "disk":
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return f.read()
            except Exception:
                return None
        return None
    return _IN_MEMORY_FS.get(path)

def _clean_filename_for_match(name: str) -> str:
    """Normalize a filename for fuzzy comparison: bare basename, no extension, '.' treated the
    same as '_' (a model garbling 'arxiv.org' vs the real 'arxiv_org' slug is exactly the kind of
    near-miss this exists to catch), lowercased, trailing '?' stripped."""
    name = name.rsplit("/", 1)[-1]
    name = re.sub(r'\.(md|pdf|txt)$', '', name, flags=re.IGNORECASE)
    return name.replace(".", "_").replace("?", "").lower()

def resolve_fuzzy_filename(filename: str) -> str | None:
    """When an exact filename lookup misses, try to recover the real file the caller almost
    certainly meant. Confirmed live (2026-07-12): a sub-agent handed a filename second-hand
    (not the one it fetched itself) reconstructs it from memory and gets it wrong — trailing
    '?' garbage, truncation, '.' instead of '_', even outright typos ('nixtaverse_nixta' for the
    real 'nixtlaverse_nixtla...', missing an 'l') — burning a full turn AND a quota unit per
    failed guess (measured on one live run: 16% of read/grep_workspace_file calls failed
    "not found", 7/12 of those visibly garbled with a literal '?'). Uses difflib's
    SequenceMatcher rather than a plain common-prefix check — a real early-string typo like the
    'nixtla' example above shares almost no prefix with the correct name at all, but is still an
    obvious match to a human (and should be to this). Deliberately conservative, matching this
    project's "keep false positives rare" posture elsewhere (utils/grounding.py): only resolves
    when exactly one real file scores above a high similarity threshold, and when there are
    multiple candidates, only if the best is clearly ahead of the runner-up. Returns None (no
    auto-resolve) on an ambiguous or no-match request — the caller's normal "not found" error
    still fires."""
    real_files = get_workspace_files()
    if not real_files:
        return None

    cleaned_request = _clean_filename_for_match(filename)
    if len(cleaned_request) < 6:  # too short to fuzzy-match safely
        return None

    import difflib
    scored = []
    for real in real_files:
        cleaned_real = _clean_filename_for_match(real)
        # Real filenames carry a trailing content hash the model never sees (e.g.
        # '..._a1b2c3d4') — comparing against the FULL real name would dilute the ratio with a
        # suffix the request was never going to match. Compare against a same-length-ish prefix
        # of the real name instead when the real name is longer.
        compare_real = cleaned_real[:len(cleaned_request) + 4] if len(cleaned_real) > len(cleaned_request) else cleaned_real
        ratio = difflib.SequenceMatcher(None, cleaned_request, compare_real).ratio()
        if ratio >= 0.72:
            scored.append((ratio, real))

    if not scored:
        return None
    scored.sort(key=lambda x: x[0], reverse=True)
    if len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.1:
        return scored[0][1]
    return None

def _analyzer_read_over_cap(current_count: int, cap: int) -> bool:
    """True if a research-role dispatch (DocumentAnalyzer/DataAnalyzer in practice — see
    task_read_grep_count_ctx's own docstring for why) has already made `cap` combined
    read_workspace_file/grep_workspace_file calls THIS dispatch and one more would push it over.
    Mirrors tools.web._specialist_fetch_over_cap's exact shape (hard reject, don't truncate).
    Confirmed live 2026-08-01 (RESEARCH.md Sec.16): one Analyzer dispatch burned 34-40 of the
    shared, whole-run grep_workspace_file quota chasing a single hard document, starving every
    OTHER Analyzer dispatched afterward in the same run of the ability to search/read their own,
    unrelated, perfectly readable sources at all."""
    return current_count >= cap


def _check_analyzer_read_cap(tool_name: str) -> str | None:
    """Shared reject-before-execution check for both read_workspace_file and grep_workspace_file
    -- see _analyzer_read_over_cap's own docstring. Returns an error string if over cap (caller
    must return it immediately, without doing the read/grep), else increments the counter and
    returns None. Skipped entirely (returns None) when task_read_grep_count_ctx isn't active for
    this dispatch (None default, or explicitly disabled for Builder/FindingsWriter/PeerReviewer at
    the orchestrator.py reset site) -- same "if ctx.get() is not None" safety pattern
    tools.web._specialist_fetch_over_cap's own call site already uses, so tests/dispatches outside
    a tracked context are never affected.

    Deliberately does NOT claim "no quota was consumed on this rejected call" the way
    fetch_url_to_workspace's own sibling cap message does (checked directly, 2026-08-01): both
    @with_quota's wrapper (tools/core.py's check_quota) increments the tool's GLOBAL `used` count
    UNCONDITIONALLY on every under-limit call, before the wrapped function body -- where this check
    lives -- ever runs. A global quota unit IS spent by the time this rejects the call; only the
    real read/grep work is skipped. Worded to reflect that."""
    from utils.run_state import task_read_grep_count_ctx
    counter = task_read_grep_count_ctx.get()
    if counter is None:
        return None
    from config import cfg
    cap = cfg.get("settings", {}).get("analyzer_read_cap", 8)
    if _analyzer_read_over_cap(counter[0], cap):
        return (
            f"Error: {tool_name} call rejected — this dispatch has already made {counter[0]} "
            f"read/grep call(s) (cap: {cap} per dispatch). Stop searching this document: "
            f"synthesize and return your findings now with what you have, or note that this "
            f"source didn't yield the information you needed."
        )
    counter[0] += 1
    return None


@tool
@with_quota
def read_workspace_file(filename: str, start_line: int = 1, end_line: int = -1) -> str:
    """Read a stored text file. Use start_line and end_line bounds to read large files safely. Both bounds are 1-indexed."""
    cap_error = _check_analyzer_read_cap("read_workspace_file")
    if cap_error:
        return cap_error
    try:
        content = get_workspace_file_content(filename)
        if content is None:
            resolved = resolve_fuzzy_filename(filename)
            content = get_workspace_file_content(resolved) if resolved else None
            if content is None:
                return f"Error: '{filename}' not found."
            filename = resolved

        lines = content.splitlines()
        total = len(lines)

        max_lines = _get_tool_rule("read_workspace_file", "max_lines", 300)

        if end_line == -1: end_line = total

        start = max(1, start_line)
        end = min(total, end_line)

        if (end - start + 1) > max_lines:
            return f"Error: Requested {end - start + 1} lines, but your quota restricts you to {max_lines} lines per read. Use grep_workspace_file or chunked bounds."

        chunk = "\n".join(lines[start - 1:end])
        return f"--- {filename} [Lines {start}-{end} of {total}] ---\n{chunk}"
    except Exception as e:
        return f"Error: {type(e).__name__}: {e}"

@tool
@with_quota
def write_workspace_file(filename: str, content: str) -> str:
    """Save content to your workspace."""
    try:
        path = _get_safe_path(filename)
        if not path: return f"Error: Invalid filename '{filename}'."
        if _get_workspace_type() == "disk":
            parent_dir = os.path.dirname(path)
            if parent_dir:
                os.makedirs(parent_dir, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            return f"Wrote '{filename}' to disk."
        else:
            _IN_MEMORY_FS[path] = content
            return f"Wrote '{filename}' to memory."
    except Exception as e:
        return f"Error: {type(e).__name__}: {e}"

@tool
@with_quota
def edit_workspace_file(filename: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
    """Replace an exact substring within an existing workspace file, without rewriting the rest
    of it. Prefer this over write_workspace_file for a small, targeted fix (e.g. dropping one bad
    citation, correcting one figure) -- it can't accidentally drop or alter anything else in the
    file the way a full regeneration can. old_string must appear EXACTLY ONCE in the file unless
    replace_all is true (in which case every occurrence is replaced); use write_workspace_file
    instead if you're rewriting most of the file's content."""
    try:
        content = get_workspace_file_content(filename)
        if content is None:
            resolved = resolve_fuzzy_filename(filename)
            content = get_workspace_file_content(resolved) if resolved else None
            if content is None:
                return f"Error: '{filename}' not found."
            filename = resolved

        count = content.count(old_string)
        if count == 0:
            return f"Error: old_string not found in '{filename}' -- it must match the file's exact existing text (no paraphrasing)."
        if count > 1 and not replace_all:
            return f"Error: old_string appears {count} times in '{filename}' -- make it more specific (include surrounding context) or pass replace_all=true if you really mean to replace all of them."

        new_content = content.replace(old_string, new_string) if replace_all else content.replace(old_string, new_string, 1)
        path = _get_safe_path(filename)
        if not path:
            return f"Error: Invalid filename '{filename}'."
        if _get_workspace_type() == "disk":
            with open(path, "w", encoding="utf-8") as f:
                f.write(new_content)
        else:
            _IN_MEMORY_FS[path] = new_content
        return f"Edited '{filename}' ({count} replacement{'s' if count != 1 else ''})."
    except Exception as e:
        return f"Error: {type(e).__name__}: {e}"

@tool
@with_quota
def list_workspace_files() -> str:
    """List all files in your workspace, showing line and character counts."""
    files = get_workspace_files()
    if not files: return "Workspace empty."
    res = []
    for k in sorted(files):
        content = get_workspace_file_content(k) or ""
        res.append(f"{k} (Lines: {len(content.splitlines())}, Chars: {len(content)})")
    return "\n".join(res)

@tool
@with_quota
def grep_workspace_file(filename: str, pattern: str, context_lines: int = 2) -> str:
    """Search for a REQUIRED regex `pattern` within a file, returning matching lines with
    surrounding context. `pattern` has no default — you must already know what text/term you're
    looking for. If you just want to check whether a file exists or see its raw content, use
    `read_workspace_file` instead; this tool is for finding a specific string inside a file
    already known to exist, not for browsing or existence-checking."""
    cap_error = _check_analyzer_read_cap("grep_workspace_file")
    if cap_error:
        return cap_error
    try:
        content = get_workspace_file_content(filename)
        resolved_note = ""
        if content is None:
            resolved = resolve_fuzzy_filename(filename)
            content = get_workspace_file_content(resolved) if resolved else None
            if content is None:
                return f"Error: '{filename}' not found."
            # Unlike read_workspace_file, the result body never mentions the filename — the model
            # needs an explicit note or it won't learn the corrected name for later calls.
            resolved_note = f"(Note: '{filename}' not found — searched '{resolved}' instead, the closest real match.)\n"
            filename = resolved

        lines = content.splitlines()
        max_matches = _get_tool_rule("grep_workspace_file", "max_matches", 10)

        compiled = re.compile(pattern, re.IGNORECASE)
        matches = []
        for i, line in enumerate(lines):
            if compiled.search(line):
                matches.append(i)
                if len(matches) >= max_matches:
                    break

        if not matches: return f"{resolved_note}No matches found for '{pattern}'."

        out = [resolved_note] if resolved_note else []
        for match_idx in matches:
            start = max(0, match_idx - context_lines)
            end = min(len(lines), match_idx + context_lines + 1)
            out.append(f"--- Match near line {match_idx + 1} ---")
            for j in range(start, end):
                prefix = "> " if j == match_idx else "  "
                out.append(f"{j + 1:04d}{prefix}{lines[j]}")

        return "\n".join(out)
    except Exception as e:
        return f"{TOOL_ERROR_PREFIX}Grep failed: {type(e).__name__}: {e}"

@tool
@with_quota
def remove_workspace_file(filename: str) -> str:
    """A destructive action that mandates human oversight. Deletes a file."""
    try:
        path = _get_safe_path(filename)
        if not path: return f"Error: Invalid filename '{filename}'."
        if _get_workspace_type() == "disk":
            if os.path.exists(path):
                os.remove(path)
                return f"Deleted: {filename}"
        else:
            if path in _IN_MEMORY_FS:
                del _IN_MEMORY_FS[path]
                return f"Deleted: {filename}"
        return f"Error: '{filename}' not found."
    except Exception as e:
        return f"Error: {type(e).__name__}: {e}"
