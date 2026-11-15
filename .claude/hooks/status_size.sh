#!/bin/sh
# SessionStart: flag session_status/CURRENT.md past the archive threshold (CLAUDE.md: > ~2,500 words).
f="$CLAUDE_PROJECT_DIR/session_status/CURRENT.md"
[ -f "$f" ] || exit 0
n=$(wc -w < "$f")
[ "$n" -gt 2500 ] && echo "session_status/CURRENT.md is $n words (> 2,500): archive to session_status/<date>.md and start a fresh CURRENT.md."
exit 0
