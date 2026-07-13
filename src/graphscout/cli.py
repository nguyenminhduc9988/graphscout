"""graphscout — cached, incremental code-graph maps so agents query structure
instead of reading whole files. Backed by graphify (tree-sitter AST extraction).

  graphscout build [dir]          full graph build (registers repo root)
  graphscout ensure [dir]         incremental refresh (only changed files re-extracted)
  graphscout watch [dir]          block, keeping the graph in sync as files change
  graphscout explore <query> [dir] verbatim source + call edges + blast radius, one call
  graphscout map [dir]            compact overview: counts, per-dir breakdown, top hubs
  graphscout file <path>          outline of one file (defs + line ranges -> sliced reads)
  graphscout sym <name> [dir]     locate symbol by substring -> file + lines
  graphscout search <query> [dir] ranked full-text symbol search (FTS5, multi-term)
  graphscout callers <name> [dir] who calls it
  graphscout callees <name> [dir] what it calls
  graphscout impact <name> [dir]  multi-hop blast radius of changing a symbol
  graphscout deps <path>          imports of a file
  graphscout affected [files...]  test files transitively affected by changed files
  graphscout touch <path>         re-extract one file into its repo's cache (hook use)
  graphscout agent                print an instruction snippet for AGENTS.md / CLAUDE.md
  graphscout install [agent...]   wire the MCP server into detected agents (claude-code, codex, gemini, cursor)
  graphscout uninstall [agent...] remove it again
  graphscout mcp                  run as an MCP server (requires `pip install graphscout[mcp]`)
  graphscout --version            print version

Queries auto-run `ensure` first, so the graph tracks the working tree.
"""
import sys
from pathlib import Path

from . import __version__, core, queries

AGENT_SNIPPET = """\
## Code navigation — graph first, read only what you need

This repo has `graphscout` (cached tree-sitter code graphs). Before reading
source files, query the graph instead of grepping/reading file-by-file:

- `graphscout explore "<question or symbol>"` — start here: verbatim source of
  the matching symbols, their callers/callees, and the blast radius of
  changing them, all in one call
- `graphscout map` — repo overview: size, per-directory breakdown, top hub symbols
- `graphscout search <query>` — ranked full-text symbol search
- `graphscout file <path>` — outline of a file (definitions + line ranges)
- `graphscout impact <name>` — multi-hop blast radius before changing a symbol
- `graphscout affected <files...>` — which test files a change would affect
- `graphscout callers <name>` / `graphscout callees <name>` — one-hop call edges
- `graphscout deps <path>` — what a file imports

Run `graphscout build` once per repo; afterwards every query refreshes changed
files automatically. Fall back to reading whole files only when the graph
can't answer (unsupported language, dynamic dispatch, subtle logic).
"""


def _split_flags(tail):
    """--key=value -> flags dict, --stdin -> flags['stdin']='1', everything
    else stays positional. Keeps command parsing free of an argparse dependency."""
    flags, pos = {}, []
    for a in tail:
        if a == "--stdin":
            flags["stdin"] = "1"
        elif a.startswith("--") and "=" in a:
            k, v = a[2:].split("=", 1)
            flags[k] = v
        else:
            pos.append(a)
    return flags, pos


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if not args or args[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd = args[0]
    flags, pos = _split_flags(args[1:])

    if cmd in ("--version", "-V", "version"):
        print(f"graphscout {__version__}")
        return 0
    if cmd == "agent":
        print(AGENT_SNIPPET)
        return 0
    if cmd == "mcp":
        from .mcp_server import run  # lazy: needs the [mcp] extra
        run()
        return 0
    if cmd in ("install", "uninstall"):
        from . import agents
        fn = agents.install if cmd == "install" else agents.uninstall
        names = pos or None
        for line in fn(names):
            print(line)
        return 0
    if cmd == "affected":
        changed = list(pos)
        if flags.get("stdin"):
            changed += [l.strip() for l in sys.stdin if l.strip()]
        if not changed:
            print("usage: graphscout affected <file...> | git diff --name-only | graphscout affected --stdin",
                  file=sys.stderr)
            return 2
        root = core.find_root(Path(changed[0]).resolve() if Path(changed[0]).exists() else Path.cwd())
        g = core.ensure(root)
        print(queries.q_affected(root, g, changed, depth=int(flags.get("depth", 8)),
                                  test_glob=flags.get("test-glob")))
        return 0

    target = None
    if cmd in ("build", "ensure", "watch", "map", "hubs"):
        root = core.find_root(Path(pos[0]).resolve() if pos else Path.cwd())
    elif cmd in ("file", "deps", "touch"):
        if not pos:
            print(f"usage: graphscout {cmd} <path>", file=sys.stderr)
            return 2
        target = Path(pos[0]).resolve()
        root = core.find_root(target)
    elif cmd in ("sym", "callers", "callees", "explore", "search", "impact"):
        if not pos:
            print(f"usage: graphscout {cmd} <name> [dir]", file=sys.stderr)
            return 2
        root = core.find_root(Path(pos[1]).resolve() if len(pos) > 1 else Path.cwd())
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
    if cmd == "watch":
        print(f"[graphscout] watching {root} — Ctrl-C to stop", file=sys.stderr)
        try:
            for status in core.watch(root):
                if status:
                    print(status, file=sys.stderr)
        except KeyboardInterrupt:
            pass
        return 0

    g = core.ensure(root)

    if cmd == "ensure":
        print(f"{root}: {len(g['nodes'])} nodes, {len(g['edges'])} edges (fresh)")
    elif cmd in ("map", "hubs"):
        print(queries.q_map(root, g))
    elif cmd == "file":
        print(queries.q_file(root, g, target))
    elif cmd == "sym":
        print(queries.q_sym(root, g, pos[0]))
    elif cmd == "search":
        print(queries.q_search(root, g, pos[0], limit=int(flags.get("limit", 20))))
    elif cmd in ("callers", "callees"):
        print(queries.q_calls(root, g, pos[0], cmd))
    elif cmd == "impact":
        print(queries.q_impact(root, g, pos[0], depth=int(flags.get("depth", 3))))
    elif cmd == "explore":
        print(queries.q_explore(root, g, pos[0], limit=int(flags.get("limit", 5)),
                                 depth=int(flags.get("depth", 2))))
    elif cmd == "deps":
        print(queries.q_deps(root, g, target))
    return 0


if __name__ == "__main__":
    sys.exit(main())
