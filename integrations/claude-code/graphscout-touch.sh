#!/usr/bin/env bash
# Claude Code PostToolUse hook (Edit|Write, async): keep code graphs fresh —
# re-extract the edited file into its repo's graphscout cache.
# No-op when the repo has no graph yet, so it's safe to install globally.
set -uo pipefail
FP=$(jq -r '.tool_input.file_path // .tool_response.filePath // empty')
[[ -z "$FP" || ! -f "$FP" ]] && exit 0
case "${FP##*.}" in
  py|js|jsx|mjs|ts|tsx|vue|svelte|astro|go|rs|zig|java|groovy|kt|kts|scala|\
  c|h|cpp|cc|cxx|hpp|cs|rb|php|swift|m|mm|lua|dart|ex|exs|jl|r|v|sv|sh|bash|ps1|tf) ;;
  *) exit 0 ;;
esac
exec graphscout touch "$FP" 2>/dev/null
