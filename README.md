# graphscout

**Cached, incremental code-graph maps so AI agents query code structure instead of reading whole files.**

Agents burn most of their tokens reading source files to answer structural questions — "where is this defined?", "who calls this?", "what breaks if I change this?". `graphscout` answers those questions from a cached tree-sitter AST graph in milliseconds, and `explore` returns the matching symbols' **verbatim source** plus their call edges and blast radius in one call — so the agent usually doesn't need a follow-up `Read` at all.

```
$ graphscout explore run_cascade
## run_cascade()  [agent/cli_fallback.py:L96]
```python
def run_cascade(prompt, models):
    ...
```  (lines 96-158)
callers: handle_turn()  agent/loop.py:L311; retry_turn()  agent/loop.py:L402
callees: call_model()  agent/cli_fallback.py:L44

blast radius (depth 2): 9 symbols across 3 file(s)
  agent/cli_fallback.py, agent/loop.py, agent/retry.py
```

One `build` per repo; after that, every query auto-refreshes only the files that changed since the last call (mtime-based). No forced background process, no external database, no API keys — a JSON cache under `~/.cache/graphscout` (plus an in-memory SQLite FTS5 index built on demand for search). Want it always fresh with zero per-query overhead instead? Run `graphscout watch` — see [Auto-sync](#auto-sync-optional).

Run `python scripts/benchmark.py <repo>` for a reproducible, offline before/after: the old sym+file+callers+callees workflow vs. one `explore` call — [example run](#measured) below.

> Formerly published as `codegraph-kit` (repo `codegraph`) — renamed to avoid confusion with the unrelated, much larger [colbymchenry/codegraph](https://github.com/colbymchenry/codegraph) project. Same tool, same cache format (`$CODEGRAPH_CACHE` still works as a fallback env var). `graphscout` is a small, single-purpose CLI+MCP tool; it doesn't attempt colbymchenry/codegraph's scope (34 languages, cross-language iOS/RN bridging, 17-framework route detection) — see [Scope](#scope) for what it does and doesn't cover.

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
| `graphscout explore <query> [dir]` | **start here** — verbatim source + call edges + blast radius for the top-matching symbols, one call |
| `graphscout build [dir]` | full graph build (run once per repo) |
| `graphscout map [dir]` | repo overview: size, per-directory breakdown, top hub symbols |
| `graphscout file <path>` | outline of one file: definitions + line ranges |
| `graphscout sym <name>` | where is this symbol defined? (plain substring match) |
| `graphscout search <query> [dir]` | ranked full-text symbol search (FTS5, multi-term, prefix) |
| `graphscout callers <name>` | who calls it? (one hop) |
| `graphscout callees <name>` | what does it call? (one hop) |
| `graphscout impact <name> [dir]` | multi-hop blast radius before changing a symbol (`--depth`) |
| `graphscout deps <path>` | what does this file import? |
| `graphscout affected <file...>` | which test files transitively depend on these changed files (`--stdin`, `--depth`) |
| `graphscout ensure [dir]` | incremental refresh (queries do this automatically) |
| `graphscout watch [dir]` | block, keeping the graph in sync as files change |
| `graphscout touch <path>` | re-extract one file (for editor/agent hooks) |
| `graphscout agent` | print an instruction snippet for your agent's context file |
| `graphscout install [agent...]` | wire the MCP server into detected agents |
| `graphscout uninstall [agent...]` | remove it again |
| `graphscout mcp` | run as an MCP server (stdio) |

```bash
git diff --name-only | graphscout affected --stdin   # which tests does my diff touch?
graphscout impact CLIFallback --depth=4               # what breaks if I change this class?
```

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

Tools exposed: `explore` (lead with this), `search`, `impact`, `affected`, `build_graph`, `graph_map`, `file_outline`, `find_symbol`, `callers`, `callees`, `file_deps` — same output as the CLI. The server's `instructions` steer the agent to `explore` first, same rationale as codegraph's single-tool design: fewer mis-picks than a menu of narrow tools.

### Auto-sync (optional)

```bash
graphscout watch          # blocks, keeps the graph in sync as you/your agent edit files
```

Uses [watchdog](https://pypi.org/project/watchdog/) for instant, low-CPU filesystem events when installed (`pip install "graphscout[watch]"`); falls back to a ~1.5s mtime poll otherwise. This is the always-fresh alternative to the per-edit `touch` hook above — run one or the other, not both. Skip both and every query still self-heals via its own mtime check; `watch` just removes that per-query overhead.

## Why not just let the agent read files?

Reading a 1,500-line file to find one function costs ~15k tokens; `graphscout file` returns the outline in ~200 tokens, and the agent then reads only the 40-line range it needs. `explore` goes further: it returns that 40-line range *inline*, so most structural questions never trigger a `Read` at all.

### Measured

`python scripts/benchmark.py <repo>` compares the old sym+file+callers+callees workflow (4 calls, no verbatim source — a `Read` would still follow) against `explore` (1 call, source included), on real symbols picked from the target repo's own call graph. Not a live-agent trial — a reproducible, offline proxy anyone can re-run. Two real runs:

| Repo | Old calls → new | Old payload → new |
|---|---|---|
| this repo (119 nodes) | 20 → 5 (75% fewer) | 10,405 → 6,722 chars |
| [pallets/click](https://github.com/pallets/click) (1,803 nodes) | 20 → 5 (75% fewer) | 37,125 → 8,380 chars |

Call-count savings are structural (4 calls collapse to 1 regardless of repo size); payload savings vary with how verbose the old path's raw listings are versus one focused snippet.

Honest limitations, printed in the output when they apply:

- **Dynamic dispatch isn't captured** — call edges come from static AST analysis; `getattr`-style calls need grep.
- **Unsupported/exotic languages** fall back to "read it directly".
- **`affected` under-detects on multi-name absolute imports** — `from pkg import a, b` resolves to one edge on the package, not per-name, so a change to `a` won't always surface a test that only imports `b`. Relative imports (`from . import x`) and single-name absolute imports resolve fully.
- **No line ranges from the extractor** — graphify records a start line per symbol, not start+end; `explore`'s snippet end is inferred as "the line before the next symbol in the same file" (capped at 60 lines), which is usually right but can over- or under-include a trailing blank line or decorator.
- Caps: 5,000 files per repo, 1 MB per file (warned, not silent); blast-radius/impact traversal stops at 400 nodes (flagged as `(truncated)`).

## Scope

`graphscout` is a small (~1,000 line) single-purpose Python CLI + MCP server. It does not attempt [colbymchenry/codegraph](https://github.com/colbymchenry/codegraph)'s full scope — that's a funded, actively-developed product with 34 languages, measured cross-file coverage per language, 17-framework route detection, and iOS/React-Native/Expo cross-language bridging. If you need those, use it instead. `graphscout` covers the languages [graphify](https://pypi.org/project/graphifyy/) parses (Python, JS/TS, Java, C/C++, Ruby, C#, Kotlin, Scala, PHP, Lua, Swift for full def/call/import extraction; 40+ more for outline/import-level structure), and focuses on making each query self-sufficient for an agent — verbatim source, call edges, and blast radius in one call — rather than chasing feature-for-feature parity.

## How it works

1. `build` walks the repo (skipping `node_modules`, `venv`, `dist`, …), runs tree-sitter extraction via graphify, normalizes all paths root-relative, and writes `graph.json` + an mtime index to `~/.cache/graphscout/<repo-hash>/`.
2. Every query calls `ensure` first: files whose mtime changed are re-extracted and spliced into the graph; deleted files are dropped. Typical refresh is a handful of files, so queries stay fast. `watch` does the same refresh on a timer/event loop instead of per-query.
3. Output is deliberately plain text with `file:line` locations — clickable in most agent UIs and trivially parseable.

Set `GRAPHSCOUT_CACHE` to relocate the cache (useful in CI and sandboxes).

## License

MIT
