#!/usr/bin/env bash
# Claude Code PostToolUse hook (Edit|Write, async): keep code graphs fresh —
# re-extract the edited file into its repo's codegraph cache.
# No-op when the repo has no graph yet, so it's safe to install globally.
set -uo pipefail
FP=$(jq -r '.tool_input.file_path // .tool_response.filePath // empty')
[[ -z "$FP" || ! -f "$FP" ]] && exit 0
case "${FP##*.}" in
  py|js|ts|tsx|jsx|mjs|go|rs|java|rb|c|h|cpp|hpp|cs|php|swift|kt|sh) ;;
  *) exit 0 ;;
esac
exec codegraph touch "$FP" 2>/dev/null
