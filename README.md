# graphscout

**Cached, incremental code-graph maps so AI agents query code structure instead of reading whole files.**

Agents burn most of their tokens reading source files to answer structural questions — "where is this defined?", "who calls this?", "what does this file import?". `graphscout` answers those questions from a cached tree-sitter AST graph in milliseconds, so the agent reads only the exact line ranges it needs.

```
$ graphscout sym cli_fallback
CLIFallback  [class]  agent/cli_fallback.py:24-210
run_cascade  [function]  agent/cli_fallback.py:96-158

$ graphscout callers run_cascade
handle_turn  [function]  agent/loop.py:311-360
retry_turn   [function]  agent/loop.py:402-431
```

One `build` per repo; after that, every query auto-refreshes only the files that changed since the last call (mtime-based). No forced background process, no database, no API keys — a JSON cache under `~/.cache/graphscout`. Want it always fresh with zero per-query overhead instead? Run `graphscout watch` — see [Auto-sync](#auto-sync-optional).

> Formerly published as `codegraph-kit` (repo `codegraph`) — renamed to avoid confusion with the unrelated, much larger [colbymchenry/codegraph](https://github.com/colbymchenry/codegraph) project. Same tool, same cache format (`$CODEGRAPH_CACHE` still works as a fallback env var).

## Install

```bash
pip install graphscout          # CLI
pip install "graphscout[mcp]"   # CLI + MCP server
pip install "graphscout[watch]" # CLI + instant filesystem-event auto-sync
```

Python ≥ 3.10. Parsing is done by [graphify](https://pypi.org/project/graphifyy/) (tree-sitter), which extracts real defs/calls/imports for Python, JavaScript, TypeScript/TSX, Java, Groovy, C, C++, Ruby, C#, Kotlin, Scala, PHP, Lua, and Swift, and walks 40+ other extensions (Go, Rust, Vue, Svelte, Astro, Dart, Elixir, Terraform, and more) for outline/import-level structure.

## Commands

| Command | What it answers |
|---|---|
| `graphscout build [dir]` | full graph build (run once per repo) |
| `graphscout map [dir]` | repo overview: size, per-directory breakdown, top hub symbols |
| `graphscout file <path>` | outline of one file: definitions + line ranges |
| `graphscout sym <name>` | where is this symbol defined? |
| `graphscout callers <name>` | who calls it? |
| `graphscout callees <name>` | what does it call? |
| `graphscout deps <path>` | what does this file import? |
| `graphscout ensure [dir]` | incremental refresh (queries do this automatically) |
| `graphscout watch [dir]` | block, keeping the graph in sync as files change |
| `graphscout touch <path>` | re-extract one file (for editor/agent hooks) |
| `graphscout agent` | print an instruction snippet for your agent's context file |
| `graphscout install [agent...]` | wire the MCP server into detected agents |
| `graphscout uninstall [agent...]` | remove it again |
| `graphscout mcp` | run as an MCP server (stdio) |

## Integrate with any agent

`graphscout` is plain CLI-over-stdout, so **any agent that can run shell commands can use it** — Claude Code, Codex CLI, Cursor, Aider, OpenHands, Goose, custom agents. Two steps:

**1. Tell the agent the graph exists.** Append the ready-made snippet to your agent's context file:

```bash
graphscout agent >> AGENTS.md      # or CLAUDE.md, .cursorrules, .github/copilot-instructions.md
```

**2. (Optional) Keep the graph fresh on every edit.** For Claude Code, install the bundled PostToolUse hook so each `Edit`/`Write` re-extracts just that file:

```bash
cp integrations/claude-code/graphscout-touch.sh ~/.claude/hooks/
chmod +x ~/.claude/hooks/graphscout-touch.sh
# then merge integrations/claude-code/settings-snippet.json into ~/.claude/settings.json
```

Even without a hook, queries stay correct: every query runs an mtime check first and re-extracts anything stale.

### MCP, wired automatically

```bash
pip install "graphscout[mcp]"
graphscout install          # auto-detects and wires every agent found on PATH
graphscout install cursor   # or target specific agents: claude-code, codex, gemini, cursor
graphscout uninstall        # reverse it
```

`install` shells out to each agent's own `mcp add` command where one exists (Claude Code, Codex CLI, Gemini CLI — verified against their real CLIs, not guessed), and edits `~/.cursor/mcp.json` directly for Cursor, which has no such subcommand. It's idempotent — safe to re-run.

Tools exposed: `build_graph`, `graph_map`, `file_outline`, `find_symbol`, `callers`, `callees`, `file_deps` — same output as the CLI.

### Auto-sync (optional)

```bash
graphscout watch          # blocks, keeps the graph in sync as you/your agent edit files
```

Uses [watchdog](https://pypi.org/project/watchdog/) for instant, low-CPU filesystem events when installed (`pip install "graphscout[watch]"`); falls back to a ~1.5s mtime poll otherwise. This is the always-fresh alternative to the per-edit `touch` hook above — run one or the other, not both. Skip both and every query still self-heals via its own mtime check; `watch` just removes that per-query overhead.

## Why not just let the agent read files?

Reading a 1,500-line file to find one function costs ~15k tokens; `graphscout file` returns the outline in ~200 tokens, and the agent then reads only the 40-line range it needs. On large repos the difference compounds — structural questions (symbol lookup, call tracing, import mapping) stop costing file-reads entirely.

Honest limitations, printed in the output when they apply:

- **Dynamic dispatch isn't captured** — call edges come from static AST analysis; `getattr`-style calls need grep.
- **Unsupported/exotic languages** fall back to "read it directly".
- Caps: 5,000 files per repo, 1 MB per file (warned, not silent).

## How it works

1. `build` walks the repo (skipping `node_modules`, `venv`, `dist`, …), runs tree-sitter extraction via graphify, normalizes all paths root-relative, and writes `graph.json` + an mtime index to `~/.cache/graphscout/<repo-hash>/`.
2. Every query calls `ensure` first: files whose mtime changed are re-extracted and spliced into the graph; deleted files are dropped. Typical refresh is a handful of files, so queries stay fast. `watch` does the same refresh on a timer/event loop instead of per-query.
3. Output is deliberately plain text with `file:line` locations — clickable in most agent UIs and trivially parseable.

Set `GRAPHSCOUT_CACHE` to relocate the cache (useful in CI and sandboxes).

## License

MIT
