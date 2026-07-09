"""codegraph — cached, incremental code-graph maps so agents query structure
instead of reading whole files. Backed by graphify (tree-sitter AST extraction).

  codegraph build [dir]          full graph build (registers repo root)
  codegraph ensure [dir]         incremental refresh (only changed files re-extracted)
  codegraph map [dir]            compact overview: counts, per-dir breakdown, top hubs
  codegraph file <path>          outline of one file (defs + line ranges -> sliced reads)
  codegraph sym <name> [dir]     locate symbol by substring -> file + lines
  codegraph callers <name> [dir] who calls it
  codegraph callees <name> [dir] what it calls
  codegraph deps <path>          imports of a file
  codegraph touch <path>         re-extract one file into its repo's cache (hook use)
  codegraph agent                print an instruction snippet for AGENTS.md / CLAUDE.md
  codegraph mcp                  run as an MCP server (requires `pip install codegraph-kit[mcp]`)
  codegraph --version            print version

Queries auto-run `ensure` first, so the graph tracks the working tree.
"""
import sys
from pathlib import Path

from . import __version__, core, queries

AGENT_SNIPPET = """\
## Code navigation — graph first, read only what you need

This repo has `codegraph` (cached tree-sitter code graphs). Before reading
source files, query the graph and then read ONLY the located line ranges:

- `codegraph map` — repo overview: size, per-directory breakdown, top hub symbols
- `codegraph file <path>` — outline of a file (definitions + line ranges)
- `codegraph sym <name>` — locate a symbol -> file:lines
- `codegraph callers <name>` / `codegraph callees <name>` — trace call edges
- `codegraph deps <path>` — what a file imports

Run `codegraph build` once per repo; afterwards every query refreshes changed
files automatically. Fall back to reading whole files only when the graph
can't answer (unsupported language, dynamic dispatch, subtle logic).
"""


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if not args or args[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd = args[0]
    tail = args[1:]

    if cmd in ("--version", "-V", "version"):
        print(f"codegraph {__version__}")
        return 0
    if cmd == "agent":
        print(AGENT_SNIPPET)
        return 0
    if cmd == "mcp":
        from .mcp_server import run  # lazy: needs the [mcp] extra
        run()
        return 0

    target = None
    if cmd in ("build", "ensure", "map", "hubs"):
        root = core.find_root(Path(tail[0]).resolve() if tail else Path.cwd())
    elif cmd in ("file", "deps", "touch"):
        if not tail:
            print(f"usage: codegraph {cmd} <path>", file=sys.stderr)
            return 2
        target = Path(tail[0]).resolve()
        root = core.find_root(target)
    elif cmd in ("sym", "callers", "callees"):
        if not tail:
            print(f"usage: codegraph {cmd} <name> [dir]", file=sys.stderr)
            return 2
        root = core.find_root(Path(tail[1]).resolve() if len(tail) > 1 else Path.cwd())
    else:
        print(f"unknown command: {cmd}")
        print(__doc__)
        return 2

    if cmd == "build":
        g, idx, n = core.build(root)
        print(f"built {root}: {n} files -> {len(g['nodes'])} nodes, {len(g['edges'])} edges")
        return 0
    if cmd == "touch":
        core.touch(target, root)
        return 0

    g = core.ensure(root)

    if cmd == "ensure":
        print(f"{root}: {len(g['nodes'])} nodes, {len(g['edges'])} edges (fresh)")
    elif cmd in ("map", "hubs"):
        print(queries.q_map(root, g))
    elif cmd == "file":
        print(queries.q_file(root, g, target))
    elif cmd == "sym":
        print(queries.q_sym(root, g, tail[0]))
    elif cmd in ("callers", "callees"):
        print(queries.q_calls(root, g, tail[0], cmd))
    elif cmd == "deps":
        print(queries.q_deps(root, g, target))
    return 0


if __name__ == "__main__":
    sys.exit(main())
