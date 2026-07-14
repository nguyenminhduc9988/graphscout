"""MCP server exposing graphscout queries as tools, for agents that speak MCP
instead of shelling out. Requires the `mcp` extra: pip install graphscout[mcp]

Run:  graphscout mcp        (stdio transport)
"""
from pathlib import Path

from . import core, queries

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as e:  # pragma: no cover
    raise SystemExit(
        "MCP support needs the optional dependency: pip install graphscout[mcp]"
    ) from e

server = FastMCP(
    "graphscout",
    instructions=(
        "Cached tree-sitter code graphs. For almost any structural question — "
        "'how does X work', locating a symbol, tracing a call — call `explore` "
        "first: it returns the matching symbols' verbatim source, their "
        "callers/callees, and the blast radius of changing them in one call. "
        "Treat the returned source as already read; don't re-verify with grep. "
        "Reach for the narrower tools (search/callers/callees/impact/file_outline) "
        "only when you need just that one slice."
    ),
)


def _root(directory: str) -> Path:
    return core.find_root(Path(directory).resolve())


@server.tool()
def explore(query: str, directory: str = ".", limit: int = 5, depth: int = 2) -> str:
    """Answer almost any structural question in one call: verbatim source of the
    top-matching symbols, their callers/callees, and the multi-hop blast radius
    of changing them. Start here before file_outline/search/callers/callees."""
    root = _root(directory)
    return queries.q_explore(root, core.ensure(root), query, limit=limit, depth=depth)


@server.tool()
def search(query: str, directory: str = ".", limit: int = 20) -> str:
    """Ranked full-text symbol search (ranked multi-term, prefix-matched) —
    broader and better-ranked than a plain substring match."""
    root = _root(directory)
    return queries.q_search(root, core.ensure(root), query, limit=limit)


@server.tool()
def impact(name: str, directory: str = ".", depth: int = 3) -> str:
    """Multi-hop blast radius of a symbol: every symbol and file reachable via
    call edges within `depth` hops, in either direction — bigger picture than
    one-hop callers/callees before making a change."""
    root = _root(directory)
    return queries.q_impact(root, core.ensure(root), name, depth=depth)


@server.tool()
def affected(files: list, directory: str = ".", depth: int = 8) -> str:
    """Test files transitively affected by a set of changed files, traced via
    resolved import edges — point this at a diff before running the full suite."""
    root = _root(directory)
    return queries.q_affected(root, core.ensure(root), files, depth=depth)


@server.tool()
def graph_map(directory: str) -> str:
    """Repo overview: node/edge counts, per-directory breakdown, top hub symbols."""
    root = _root(directory)
    return queries.q_map(root, core.ensure(root))


@server.tool()
def file_outline(path: str) -> str:
    """Outline of one source file: definitions with line ranges."""
    target = Path(path).resolve()
    root = core.find_root(target)
    return queries.q_file(root, core.ensure(root), target)


@server.tool()
def find_symbol(name: str, directory: str = ".") -> str:
    """Locate a symbol by substring -> file:lines."""
    root = _root(directory)
    return queries.q_sym(root, core.ensure(root), name)


@server.tool()
def callers(name: str, directory: str = ".") -> str:
    """Who calls this symbol (matched by substring)."""
    root = _root(directory)
    return queries.q_calls(root, core.ensure(root), name, "callers")


@server.tool()
def callees(name: str, directory: str = ".") -> str:
    """What this symbol calls (matched by substring)."""
    root = _root(directory)
    return queries.q_calls(root, core.ensure(root), name, "callees")


@server.tool()
def file_deps(path: str) -> str:
    """Imports of a source file."""
    target = Path(path).resolve()
    root = core.find_root(target)
    return queries.q_deps(root, core.ensure(root), target)


@server.tool()
def symbol_diff(ref1: str, ref2: str = "", directory: str = ".") -> str:
    """Symbol-level diff between two git refs (or a ref and the working tree,
    when ref2 is left blank): which functions/classes were added, removed, or
    had their body change — a code-review-shaped view a line diff can't give,
    since a signature-only rename shows as one changed symbol, not a hunk."""
    from . import diffing
    root = _root(directory)
    results = diffing.diff_symbols(root, ref1, ref2 or None)
    return diffing.format_diff(results, ref1, ref2 or None)


@server.tool()
def routes(directory: str = ".") -> str:
    """Detect API routes/endpoints (Flask, FastAPI, Django, Express, Gin, NestJS,
    Spring, ASP.NET, Actix, Rails, Laravel) — the repo's API surface in one call."""
    root = _root(directory)
    return queries.q_routes(root, core.ensure(root))


@server.tool()
def orphans(directory: str = ".") -> str:
    """Dead-code candidates: symbols with no in-repo caller. Heuristic —
    entrypoints, tests, decorated handlers (routes/CLI/fixtures) and dynamic
    dispatch are excluded up front, but always verify before deleting."""
    root = _root(directory)
    return queries.q_orphans(root, core.ensure(root))


@server.tool()
def hotspots(directory: str = ".", limit: int = 20) -> str:
    """Refactor-priority ranking: files that are both frequently changed
    (git log churn) and structurally central (graph connectivity) — where a
    change is likely to matter most and have the widest blast radius."""
    root = _root(directory)
    return queries.q_hotspots(root, core.ensure(root), limit=limit)


@server.tool()
def call_tree(name: str, directory: str = ".", depth: int = 4) -> str:
    """Recursive nested call tree from a symbol: what it calls, what those call,
    etc. (depth-N) — read top-down to follow execution. Deeper and more readable
    than one-hop `callees` when you're tracing a flow."""
    root = _root(directory)
    return queries.q_tree(root, core.ensure(root), name, depth=depth)


@server.tool()
def reverse_deps(path: str, directory: str = ".") -> str:
    """Reverse file imports: every in-repo file that imports the given file —
    'what breaks if I change or delete this?' for non-test code. (`affected` is
    the test-only specialization of this.)"""
    root = _root(directory)
    target = Path(path).resolve()
    return queries.q_rdeps(root, core.ensure(root), target)


@server.tool()
def import_cycles(directory: str = ".") -> str:
    """File-level import cycles (A imports B imports A) over resolved import
    edges — one representative path per cycle. A structural smell; not every
    cycle is a runtime bug, but none show up in a linear file read."""
    root = _root(directory)
    return queries.q_cycles(root, core.ensure(root))


@server.tool()
def entrypoints(directory: str = ".") -> str:
    """Likely externally-invoked surface: CLI commands, main/handler entry
    points, and framework-decorated callables — what a runtime, framework, or
    out-of-repo caller reaches directly. Complements `routes` (HTTP only) and is
    the inverse of `orphans` (unintentionally dead)."""
    root = _root(directory)
    return queries.q_entrypoints(root, core.ensure(root))


@server.tool()
def visualize(name: str = "", directory: str = ".", fmt: str = "mermaid",
              limit: int = 60, depth: int = 2, kind: str = "calls") -> str:
    """Render the call graph as Mermaid (default, renders inline in GitHub/Notion)
    or Graphviz DOT (fmt='dot'). With a `name`, draws that symbol's blast-radius
    subgraph; without it, the top call-graph hubs. `kind='imports'` switches to
    the file-level module dependency graph. Bounded so a hub can't swamp the
    diagram — a navigation map, not every edge."""
    root = _root(directory)
    return queries.q_viz(root, core.ensure(root), query=name or None, fmt=fmt,
                         limit=limit, depth=depth, kind=kind)


@server.tool()
def metrics(name: str = "", directory: str = ".", limit: int = 20) -> str:
    """Per-symbol complexity: fan-in (load-bearing — how many sites call it),
    fan-out (calls the most — a complexity proxy), reference/type edges, and
    inferred body lines. With `name`, that symbol's card; without, the repo
    ranked two ways — top fan-out (likely-too-big functions) and top fan-in
    (hubs many callers depend on). Graph signals a call list alone can't give."""
    root = _root(directory)
    return queries.q_metrics(root, core.ensure(root), query=name or None, limit=limit)


@server.tool()
def duplicates(directory: str = ".", min_lines: int = 4) -> str:
    """Copy-paste / near-identical function bodies. Bodies are normalized
    (comments, whitespace, case stripped) so formatter-only and re-commented
    copies still cluster; identifier renaming is NOT normalized, so true
    type-2 clones won't merge. A call graph can't see this at all — two
    identical helpers have no edge between them."""
    root = _root(directory)
    return queries.q_dupes(root, core.ensure(root), min_lines=min_lines)


@server.tool()
def recent(directory: str = ".", commits: int = 20) -> str:
    """Symbols in files touched by the last N commits — 'what's been moving
    lately' for orienting on a repo. Lighter than `symbol_diff` (two explicit
    refs): this is the churn-window view over the last `commits` changes."""
    root = _root(directory)
    return queries.q_recent(root, core.ensure(root), commits=commits)


@server.tool()
def why(from_name: str, to_name: str, directory: str = ".") -> str:
    """Shortest call chain from one symbol to another over resolved call edges —
    the two-symbol 'why does A depend on B' question that `impact` (undirected
    blast radius from one seed) doesn't directly answer. Reports the hop-by-hop
    path, or that they're unreachable."""
    root = _root(directory)
    return queries.q_why(root, core.ensure(root), from_name, to_name)


@server.tool()
def token_cost(name: str, directory: str = ".") -> str:
    """Token cost of one symbol's body — 'is it worth reading the whole thing,
    or take the outline first?' tiktoken (cl100k) when installed, else a chars/4
    heuristic within ~15% for typical code."""
    root = _root(directory)
    return queries.q_tokens(root, core.ensure(root), name)


@server.tool()
def list_roots() -> str:
    """Every repo with a cached graph, newest-registered first, with its
    current node/edge counts — check this before `build_graph` to avoid
    redundantly rebuilding a repo already indexed."""
    roots = core.registered_roots()
    if not roots:
        return "no repos registered yet — run build_graph on one"
    lines = []
    for r in roots:
        state = f"{r['nodes']} nodes, {r['edges']} edges" if r["built"] else "UNBUILT (cache missing)"
        lines.append(f"{r['registered_at']}  {r['root']}  ({state})")
    return "\n".join(lines)


@server.tool()
def doctor(directory: str = ".") -> str:
    """Environment/capability check: FTS5 ranked search, git, the mcp/watchdog
    optional extras, live indexable-extension count, and this repo's graph
    health (built? stale? any extraction failures?)."""
    from . import doctor as doctor_mod
    return doctor_mod.run(directory)


@server.tool()
def build_graph(directory: str) -> str:
    """Full (re)build of a repo's graph. Run once per repo; queries refresh incrementally."""
    root = _root(directory)
    g, _idx, n = core.build(root)
    return f"built {root}: {n} files -> {len(g['nodes'])} nodes, {len(g['edges'])} edges"


def run():
    server.run()
