"""Query functions over a built graph. Each returns a plain string ready to
show to a human or an agent — the CLI prints it, the MCP server returns it."""
from collections import Counter
from pathlib import Path

from . import core


def loc(n):
    return f"{n.get('source_file', '?')}:{n.get('source_location', '?')}"


def fmt_node(n):
    return f"{n.get('label', n['id'])}  [{n.get('type', n.get('_origin', '?'))}]  {loc(n)}"


def fresh_graph(root: Path):
    return core.ensure(root)


def q_map(root: Path, g) -> str:
    nodes, edges = g["nodes"], g["edges"]
    deg = Counter()
    for e in edges:
        deg[e["source"]] += 1
        deg[e["target"]] += 1
    byid = {n["id"]: n for n in nodes}
    per_dir = Counter(n.get("source_file", "?").split("/")[0] for n in nodes)
    rels = Counter(e.get("relation", "?") for e in edges)
    lines = [f"{root} — {len(nodes)} nodes, {len(edges)} edges",
             f"per-dir: {dict(per_dir.most_common(12))}",
             f"relations: {dict(rels.most_common())}",
             "top hubs:"]
    for nid, d in deg.most_common(15):
        n = byid.get(nid)
        if n:
            lines.append(f"  {d:4d}  {fmt_node(n)}")
    return "\n".join(lines)


def q_file(root: Path, g, target: Path) -> str:
    f = str(target.resolve().relative_to(root))
    mine = [n for n in g["nodes"] if n.get("source_file", "") == f]
    if not mine:
        return f"(no graph entries for {f} — unsupported language or empty; read it directly)"
    return "\n".join(fmt_node(n) for n in sorted(mine, key=lambda x: x.get("source_location", "")))


def q_sym(root: Path, g, query: str, limit: int = 30) -> str:
    q = query.lower()
    hits = [n for n in g["nodes"] if q in n.get("label", "").lower() or q in n["id"].lower()]
    if not hits:
        return f"no symbol matching '{query}' — try grep"
    lines = [fmt_node(n) for n in hits[:limit]]
    if len(hits) > limit:
        lines.append(f"... {len(hits) - limit} more (narrow the query)")
    return "\n".join(lines)


def q_calls(root: Path, g, query: str, direction: str, limit: int = 40) -> str:
    """direction: 'callers' (who calls it) or 'callees' (what it calls)."""
    q = query.lower()
    byid = {n["id"]: n for n in g["nodes"]}
    key, other = ("target", "source") if direction == "callers" else ("source", "target")
    hits = [e for e in g["edges"] if e.get("relation") == "calls" and q in e[key].lower()]
    if not hits:
        return (f"no call edges matching '{query}' "
                "(dynamic dispatch isn't captured — fall back to grep)")
    lines = []
    for e in hits[:limit]:
        n = byid.get(e[other])
        lines.append(fmt_node(n) if n else
                     f"{e[other]}  ({e.get('source_file', '?')}:{e.get('source_location', '?')})")
    return "\n".join(lines)


def q_deps(root: Path, g, target: Path) -> str:
    f = str(target.resolve().relative_to(root))
    lines = [f"{e['relation']:13s} {e['target']}" for e in g["edges"]
             if e.get("source_file", "") == f and e.get("relation") in ("imports", "imports_from")]
    return "\n".join(lines) if lines else f"(no import edges recorded for {f})"
