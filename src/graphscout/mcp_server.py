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
def build_graph(directory: str) -> str:
    """Full (re)build of a repo's graph. Run once per repo; queries refresh incrementally."""
    root = _root(directory)
    g, _idx, n = core.build(root)
    return f"built {root}: {n} files -> {len(g['nodes'])} nodes, {len(g['edges'])} edges"


def run():
    server.run()
