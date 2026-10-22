#!/usr/bin/env bash
# Structured progress watcher for a live research_output run -- polls the run's own
# _run_state.json (ground truth: completion_check_attempts, fetched_urls, final artifact) instead
# of grepping the CLI's ANSI-colored narration log, which only ever shows text a check happened to
# print, not the recorded problem/attempt-count state itself.
#
# Usage: scripts/watch_run.sh [run_dir] [pid]
#   run_dir defaults to the most recently modified research_output/*/
#   pid, if given, ends the watch when that process exits (the only reliable "run is actually
#   done" signal -- final_report.md existing is NOT: it persists across retries and gets
#   rewritten/re-quarantined in place by GROUNDING_CHECKS dispatches, so its mere presence was
#   confirmed live to fire a false "CONVERGED" while the run was still 2 attempts from its budget).
set -euo pipefail
run_dir="${1:-}"
watch_pid="${2:-}"
if [ -z "$run_dir" ]; then
    run_dir=$(ls -dt research_output/*/ 2>/dev/null | head -1)
fi
if [ -z "$run_dir" ] || [ ! -f "$run_dir/_run_state.json" ]; then
    echo "No run found (pass a research_output/<run>/ dir explicitly)." >&2
    exit 1
fi
state="$run_dir/_run_state.json"
echo "Watching $state"$([ -n "$watch_pid" ] && echo " (ends when pid $watch_pid exits)")

last_count=0
while true; do
    if [ -f "$state" ]; then
        new=$(jq -r --argjson skip "$last_count" \
            '.completion_check_attempts[$skip:][] | "attempt \(.attempt): \(.problem)"' \
            "$state" 2>/dev/null || true)
        if [ -n "$new" ]; then
            echo "$new"
            last_count=$(jq '.completion_check_attempts | length' "$state")
        fi
    fi
    if [ -n "$watch_pid" ] && ! kill -0 "$watch_pid" 2>/dev/null; then
        final="none"
        [ -f "$run_dir/final_report.md" ] && final="final_report.md present"
        echo "PROCESS EXITED ($final, fetched_urls=$(jq '.fetched_urls | length' "$state" 2>/dev/null || echo '?'))"
        break
    fi
    sleep 5
done
