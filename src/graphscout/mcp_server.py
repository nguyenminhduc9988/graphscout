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
        "Cached tree-sitter code graphs. Query structure (outlines, symbols, "
        "call edges, imports) instead of reading whole files; then read only "
        "the located line ranges."
    ),
)


def _root(directory: str) -> Path:
    return core.find_root(Path(directory).resolve())


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
