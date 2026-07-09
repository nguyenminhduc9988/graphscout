# codegraph

**Cached, incremental code-graph maps so AI agents query code structure instead of reading whole files.**

Agents burn most of their tokens reading source files to answer structural questions — "where is this defined?", "who calls this?", "what does this file import?". `codegraph` answers those questions from a cached tree-sitter AST graph in milliseconds, so the agent reads only the exact line ranges it needs.

```
$ codegraph sym cli_fallback
CLIFallback  [class]  agent/cli_fallback.py:24-210
run_cascade  [function]  agent/cli_fallback.py:96-158

$ codegraph callers run_cascade
handle_turn  [function]  agent/loop.py:311-360
retry_turn   [function]  agent/loop.py:402-431
```

One `build` per repo; after that, every query auto-refreshes only the files that changed since the last call (mtime-based). No daemon, no database, no API keys — a JSON cache under `~/.cache/codegraph`.

## Install

```bash
pip install codegraph-kit          # CLI
pip install "codegraph-kit[mcp]"   # CLI + MCP server
```

Python ≥ 3.10. Parsing is done by [graphify](https://pypi.org/project/graphifyy/) (tree-sitter), which ships wheels for Python, JavaScript, TypeScript, Go, Rust, Java, Ruby, C, C++, C#, PHP, Swift, Kotlin, shell, and more.

## Commands

| Command | What it answers |
|---|---|
| `codegraph build [dir]` | full graph build (run once per repo) |
| `codegraph map [dir]` | repo overview: size, per-directory breakdown, top hub symbols |
| `codegraph file <path>` | outline of one file: definitions + line ranges |
| `codegraph sym <name>` | where is this symbol defined? |
| `codegraph callers <name>` | who calls it? |
| `codegraph callees <name>` | what does it call? |
| `codegraph deps <path>` | what does this file import? |
| `codegraph ensure [dir]` | incremental refresh (queries do this automatically) |
| `codegraph touch <path>` | re-extract one file (for editor/agent hooks) |
| `codegraph agent` | print an instruction snippet for your agent's context file |
| `codegraph mcp` | run as an MCP server (stdio) |

## Integrate with any agent

`codegraph` is plain CLI-over-stdout, so **any agent that can run shell commands can use it** — Claude Code, Codex CLI, Cursor, Aider, OpenHands, Goose, custom agents. Two steps:

**1. Tell the agent the graph exists.** Append the ready-made snippet to your agent's context file:

```bash
codegraph agent >> AGENTS.md      # or CLAUDE.md, .cursorrules, .github/copilot-instructions.md
```

**2. (Optional) Keep the graph fresh on every edit.** For Claude Code, install the bundled PostToolUse hook so each `Edit`/`Write` re-extracts just that file:

```bash
cp integrations/claude-code/codegraph-touch.sh ~/.claude/hooks/
chmod +x ~/.claude/hooks/codegraph-touch.sh
# then merge integrations/claude-code/settings-snippet.json into ~/.claude/settings.json
```

Even without a hook, queries stay correct: every query runs an mtime check first and re-extracts anything stale.

### MCP (for agents that don't shell out)

```bash
pip install "codegraph-kit[mcp]"
```

Register `codegraph mcp` as a stdio server. For Claude Code:

```bash
claude mcp add codegraph -- codegraph mcp
```

Tools exposed: `build_graph`, `graph_map`, `file_outline`, `find_symbol`, `callers`, `callees`, `file_deps` — same output as the CLI.

## Why not just let the agent read files?

Reading a 1,500-line file to find one function costs ~15k tokens; `codegraph file` returns the outline in ~200 tokens, and the agent then reads only the 40-line range it needs. On large repos the difference compounds — structural questions (symbol lookup, call tracing, import mapping) stop costing file-reads entirely.

Honest limitations, printed in the output when they apply:

- **Dynamic dispatch isn't captured** — call edges come from static AST analysis; `getattr`-style calls need grep.
- **Unsupported/exotic languages** fall back to "read it directly".
- Caps: 5,000 files per repo, 1 MB per file (warned, not silent).

## How it works

1. `build` walks the repo (skipping `node_modules`, `venv`, `dist`, …), runs tree-sitter extraction via graphify, normalizes all paths root-relative, and writes `graph.json` + an mtime index to `~/.cache/codegraph/<repo-hash>/`.
2. Every query calls `ensure` first: files whose mtime changed are re-extracted and spliced into the graph; deleted files are dropped. Typical refresh is a handful of files, so queries stay fast.
3. Output is deliberately plain text with `file:line` locations — clickable in most agent UIs and trivially parseable.

Set `CODEGRAPH_CACHE` to relocate the cache (useful in CI and sandboxes).

## License

MIT
