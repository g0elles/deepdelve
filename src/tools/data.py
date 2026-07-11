from agent_framework import tool
from tools.core import with_quota, _get_tool_rule
from tools.fs import get_workspace_file_content


@tool
@with_quota
def extract_structured_data(filename: str) -> str:
    """Extract structured data (markdown tables and fenced code/JSON/CSV blocks) from a workspace
    file, each returned isolated with its starting line number. Finds structure generically
    instead of requiring you to already know a pattern to grep for — prefer this over
    grep_workspace_file for tabular or code-block data."""
    try:
        content = get_workspace_file_content(filename)
        if content is None:
            return f"Error: '{filename}' not found."

        lines = content.splitlines()
        blocks = []

        i = 0
        while i < len(lines):
            if lines[i].lstrip().startswith('|'):
                start = i
                while i < len(lines) and lines[i].lstrip().startswith('|'):
                    i += 1
                blocks.append(f"--- Table near line {start + 1} ---\n" + "\n".join(lines[start:i]))
            else:
                i += 1

        in_fence = False
        fence_start = 0
        fence_lang = ""
        fence_lines: list[str] = []
        for idx, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("```"):
                if not in_fence:
                    in_fence = True
                    fence_start = idx
                    fence_lang = stripped[3:].strip()
                    fence_lines = []
                else:
                    in_fence = False
                    blocks.append(f"--- Code block ({fence_lang or 'unknown'}) near line {fence_start + 1} ---\n" + "\n".join(fence_lines))
            elif in_fence:
                fence_lines.append(line)

        if not blocks:
            return f"No tables or fenced code/data blocks found in '{filename}' — this file may be pure prose. Use grep_workspace_file or read_workspace_file instead."

        max_blocks = _get_tool_rule("extract_structured_data", "max_blocks", 10)
        return "\n\n".join(blocks[:max_blocks])
    except Exception as e:
        import traceback
        return f"Error: {e}\n\nTraceback:\n{traceback.format_exc()}"
