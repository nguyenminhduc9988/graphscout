"""graphscout — cached, incremental code-graph maps so agents query structure
instead of reading whole files. Backed by graphify (tree-sitter AST extraction).

  graphscout build [dir]          full graph build (registers repo root)
  graphscout ensure [dir]         incremental refresh (only changed files re-extracted)
  graphscout watch [dir]          block, keeping the graph in sync as files change
  graphscout daemon start|stop|status [dir]  same as watch, detached in the background
  graphscout explore <query> [dir] verbatim source + call edges + blast radius, one call
  graphscout map [dir]            compact overview: counts, per-dir breakdown, top hubs
  graphscout file <path>          outline of one file (defs + line ranges -> sliced reads)
  graphscout sym <name> [dir]     locate symbol by substring -> file + lines
  graphscout search <query> [dir] ranked full-text symbol search (FTS5, multi-term)
  graphscout callers <name> [dir] who calls it
  graphscout callees <name> [dir] what it calls
  graphscout tree <name> [dir]    recursive nested call tree (what it calls, depth-N)
  graphscout impact <name> [dir]  multi-hop blast radius of changing a symbol
  graphscout deps <path>          imports of a file
  graphscout rdeps <path>         reverse: who imports this file (what breaks if it changes)
  graphscout affected [files...]  test files transitively affected by changed files
  graphscout diff <ref> [ref2]    symbol-level diff: added/removed/modified defs, not lines
                                   (ref2 omitted -> compares against the working tree)
  graphscout routes [dir]         detect API routes/endpoints (Flask/FastAPI/Django/
                                   Express/Gin/NestJS/Spring/ASP.NET/Actix/Rails/Laravel)
  graphscout entrypoints [dir]    externally-invoked surface: CLI commands, main/handler,
                                   framework-decorated callables (complements routes)
  graphscout orphans [dir]        dead-code candidates: symbols with no in-repo caller
  graphscout cycles [dir]         file-level import cycles (A->B->A) over resolved imports
  graphscout hotspots [dir]       refactor priority: churn x graph connectivity (--commits)
  graphscout viz [name] [dir]     render the call graph (or a symbol's blast radius) as
                                   Mermaid or Graphviz DOT (--format=dot);
                                   --kind=imports for the file-level module graph
  graphscout metrics [query] [dir] per-symbol complexity: fan-in/fan-out/refs/lines
                                   (query omitted -> repo ranked: top fan-out & fan-in)
  graphscout dupes [dir]          copy-paste / near-identical function bodies (--min-lines)
  graphscout recent [dir]         symbols in files touched by the last N commits (--commits)
  graphscout why <a> <b> [dir]    shortest call chain from symbol a to symbol b
  graphscout tokens <name> [dir]  token cost of one symbol's body (tiktoken or chars/4)
  graphscout doctor [dir]         environment check: FTS5, git, mcp/watchdog extras, cache health
  graphscout touch <path>         re-extract one file into its repo's cache (hook use)
  graphscout agent                print an instruction snippet for AGENTS.md / CLAUDE.md
  graphscout install [agent...]   wire the MCP server into detected agents (claude-code, codex,
                                   gemini, cursor, windsurf)
  graphscout roots                list every repo with a cached graph, size, and freshness
  graphscout uninstall [agent...] remove it again
  graphscout mcp                  run as an MCP server (requires `pip install graphscout[mcp]`)
  graphscout --version            print version

Queries auto-run `ensure` first, so the graph tracks the working tree.

Append `--json` to map/sym/search/callers/callees/tree/impact/deps/rdeps/routes/
entrypoints/orphans/cycles/hotspots/viz/affected/diff/doctor/metrics/dupes/recent/
why/tokens for structured output.
"""
import json
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
- `graphscout tree <name>` — recursive nested call tree (follow execution depth-N)
- `graphscout affected <files...>` — which test files a change would affect
- `graphscout rdeps <path>` — reverse imports: what breaks if a file changes
- `graphscout diff <ref> [ref2]` — symbol-level added/removed/modified defs, not a line diff
- `graphscout routes` — detect this repo's API routes/endpoints
- `graphscout entrypoints` — externally-invoked surface (CLI/main/framework handlers)
- `graphscout orphans` — dead-code candidates (verify before deleting)
- `graphscout cycles` — file-level import cycles (A->B->A)
- `graphscout viz [name]` — render the call graph / blast radius as Mermaid or DOT
                                   (--kind=imports for the module dependency graph)
- `graphscout hotspots` — refactor priority (churn x connectivity)
- `graphscout metrics [query]` — per-symbol fan-in/fan-out/lines, or repo complexity rankings
- `graphscout dupes` — copy-paste / near-identical function bodies
- `graphscout recent` — symbols in files touched by the last N commits
- `graphscout why <a> <b>` — shortest call chain from symbol a to symbol b
- `graphscout tokens <name>` — token cost of a symbol's body (read it or just outline it?)
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
        elif a == "--json":
            flags["json"] = "1"
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
    if cmd == "roots":
        roots = core.registered_roots()
        if flags.get("json"):
            print(json.dumps(roots, indent=2))
        elif not roots:
            print("no repos registered yet — run `graphscout build` in one")
        else:
            for r in roots:
                state = f"{r['nodes']} nodes, {r['edges']} edges" if r["built"] else "UNBUILT (cache missing)"
                print(f"{r['registered_at']}  {r['root']}  ({state})")
        return 0
    if cmd == "doctor":
        from . import doctor
        directory = pos[0] if pos else "."
        if flags.get("json"):
            print(json.dumps(doctor.checks(directory), indent=2))
        else:
            print(doctor.run(directory))
        return 0
    if cmd == "daemon":
        from . import daemon
        if not pos or pos[0] not in ("start", "stop", "status"):
            print("usage: graphscout daemon <start|stop|status> [dir]", file=sys.stderr)
            return 2
        sub, rest = pos[0], pos[1:]
        root = core.find_root(Path(rest[0]).resolve() if rest else Path.cwd())
        print(getattr(daemon, sub)(root))
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
        if flags.get("json"):
            from . import jsonout
            print(json.dumps(jsonout.affected_data(root, g, changed, depth=int(flags.get("depth", 8)),
                                                     test_glob=flags.get("test-glob")), indent=2))
        else:
            print(queries.q_affected(root, g, changed, depth=int(flags.get("depth", 8)),
                                      test_glob=flags.get("test-glob")))
        return 0
    if cmd == "diff":
        from . import diffing
        refs = list(pos)
        dir_arg = refs.pop() if len(refs) > 1 and Path(refs[-1]).is_dir() else None
        if not refs:
            print("usage: graphscout diff <ref> [ref2] [dir]  (ref2 omitted -> working tree)",
                  file=sys.stderr)
            return 2
        root = core.find_root(Path(dir_arg).resolve() if dir_arg else Path.cwd())
        ref1, ref2 = refs[0], (refs[1] if len(refs) > 1 else None)
        try:
            results = diffing.diff_symbols(root, ref1, ref2)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        if flags.get("json"):
            from . import jsonout
            print(json.dumps(jsonout.diff_data(results, ref1, ref2), indent=2))
        else:
            print(diffing.format_diff(results, ref1, ref2))
        return 0

    target = None
    if cmd in ("build", "ensure", "watch", "map", "hubs", "routes", "orphans",
               "hotspots", "cycles", "entrypoints", "dupes", "recent"):
        root = core.find_root(Path(pos[0]).resolve() if pos else Path.cwd())
    elif cmd in ("file", "deps", "rdeps", "touch"):
        if not pos:
            print(f"usage: graphscout {cmd} <path>", file=sys.stderr)
            return 2
        target = Path(pos[0]).resolve()
        root = core.find_root(target)
    elif cmd in ("sym", "callers", "callees", "explore", "search", "impact", "tree", "tokens"):
        if not pos:
            print(f"usage: graphscout {cmd} <name> [dir]", file=sys.stderr)
            return 2
        root = core.find_root(Path(pos[1]).resolve() if len(pos) > 1 else Path.cwd())
    elif cmd == "viz":
        # viz [name] [dir] — name optional (omit -> top-hubs overview)
        if pos and Path(pos[-1]).is_dir():
            root = core.find_root(Path(pos[-1]).resolve())
            query = pos[0] if len(pos) > 1 else None
        elif pos:
            root = core.find_root(Path.cwd())
            query = pos[0]
        else:
            root = core.find_root(Path.cwd())
            query = None
    elif cmd == "metrics":
        # metrics [query] [dir] — query optional (omit -> repo rankings)
        if pos and Path(pos[-1]).is_dir():
            root = core.find_root(Path(pos[-1]).resolve())
            query = pos[0] if len(pos) > 1 else None
        elif pos:
            root = core.find_root(Path.cwd())
            query = pos[0]
        else:
            root = core.find_root(Path.cwd())
            query = None
    elif cmd == "why":
        # why <from_name> <to_name> [dir]
        if len(pos) < 2:
            print("usage: graphscout why <from_name> <to_name> [dir]", file=sys.stderr)
            return 2
        root = core.find_root(Path(pos[2]).resolve() if len(pos) > 2 else Path.cwd())
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
    as_json = bool(flags.get("json"))
    if as_json and cmd in ("map", "hubs", "sym", "search", "callers", "callees", "tree",
                            "impact", "deps", "rdeps", "routes", "entrypoints", "orphans",
                            "cycles", "hotspots", "viz", "metrics", "dupes", "recent",
                            "why", "tokens"):
        from . import jsonout
        if cmd in ("map", "hubs"):
            result = jsonout.map_data(root, g)
        elif cmd == "sym":
            result = jsonout.sym_data(g, pos[0])
        elif cmd == "search":
            result = jsonout.search_data(g, pos[0], limit=int(flags.get("limit", 20)))
        elif cmd in ("callers", "callees"):
            result = jsonout.calls_data(g, pos[0], cmd)
        elif cmd == "tree":
            result = jsonout.tree_data(g, pos[0], depth=int(flags.get("depth", 4)),
                                       limit=int(flags.get("limit", 5)))
        elif cmd == "impact":
            result = jsonout.impact_data(g, pos[0], depth=int(flags.get("depth", 3)))
        elif cmd == "deps":
            result = jsonout.deps_data(root, g, target)
        elif cmd == "rdeps":
            result = jsonout.rdeps_data(root, g, target)
        elif cmd == "routes":
            result = jsonout.routes_data(root, g)
        elif cmd == "entrypoints":
            result = jsonout.entrypoints_data(root, g, limit=int(flags.get("limit", 100)))
        elif cmd == "orphans":
            result = jsonout.orphans_data(root, g, limit=int(flags.get("limit", 60)))
        elif cmd == "cycles":
            result = jsonout.cycles_data(g)
        elif cmd == "viz":
            result = jsonout.viz_data(g, query=query, fmt=flags.get("format", "mermaid"),
                                      limit=int(flags.get("limit", 60)),
                                      depth=int(flags.get("depth", 2)),
                                      kind=flags.get("kind", "calls"))
        elif cmd == "metrics":
            result = jsonout.metrics_data(root, g, query=query, limit=int(flags.get("limit", 20)))
        elif cmd == "dupes":
            result = jsonout.dupes_data(root, g, min_lines=int(flags.get("min-lines", 4)),
                                         limit=int(flags.get("limit", 20)))
        elif cmd == "recent":
            result = jsonout.recent_data(root, g, commits=int(flags.get("commits", 20)),
                                          limit=int(flags.get("limit", 40)))
        elif cmd == "why":
            result = jsonout.why_data(root, g, pos[0], pos[1])
        elif cmd == "tokens":
            result = jsonout.tokens_data(root, g, pos[0])
        else:  # hotspots
            result = jsonout.hotspots_data(root, g, limit=int(flags.get("limit", 20)),
                                            max_commits=int(flags.get("commits", 2000)))
        print(json.dumps(result, indent=2))
        return 0

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
    elif cmd == "tree":
        print(queries.q_tree(root, g, pos[0], depth=int(flags.get("depth", 4)),
                             limit=int(flags.get("limit", 5))))
    elif cmd == "impact":
        print(queries.q_impact(root, g, pos[0], depth=int(flags.get("depth", 3))))
    elif cmd == "explore":
        print(queries.q_explore(root, g, pos[0], limit=int(flags.get("limit", 5)),
                                 depth=int(flags.get("depth", 2))))
    elif cmd == "deps":
        print(queries.q_deps(root, g, target))
    elif cmd == "rdeps":
        print(queries.q_rdeps(root, g, target))
    elif cmd == "routes":
        print(queries.q_routes(root, g))
    elif cmd == "entrypoints":
        print(queries.q_entrypoints(root, g, limit=int(flags.get("limit", 100))))
    elif cmd == "orphans":
        print(queries.q_orphans(root, g, limit=int(flags.get("limit", 60))))
    elif cmd == "cycles":
        print(queries.q_cycles(root, g))
    elif cmd == "viz":
        print(queries.q_viz(root, g, query=query, fmt=flags.get("format", "mermaid"),
                            limit=int(flags.get("limit", 60)),
                            depth=int(flags.get("depth", 2)),
                            kind=flags.get("kind", "calls")))
    elif cmd == "hotspots":
        print(queries.q_hotspots(root, g, limit=int(flags.get("limit", 20)),
                                  max_commits=int(flags.get("commits", 2000))))
    elif cmd == "metrics":
        print(queries.q_metrics(root, g, query=query, limit=int(flags.get("limit", 20))))
    elif cmd == "dupes":
        print(queries.q_dupes(root, g, min_lines=int(flags.get("min-lines", 4)),
                               limit=int(flags.get("limit", 20))))
    elif cmd == "recent":
        print(queries.q_recent(root, g, commits=int(flags.get("commits", 20)),
                                limit=int(flags.get("limit", 40))))
    elif cmd == "why":
        print(queries.q_why(root, g, pos[0], pos[1]))
    elif cmd == "tokens":
        print(queries.q_tokens(root, g, pos[0]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
