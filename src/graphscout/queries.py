"""Query functions over a built graph. Each returns a plain string ready to
show to a human or an agent — the CLI prints it, the MCP server returns it."""
from collections import Counter
from pathlib import Path

from . import analysis, core


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


def q_search(root: Path, g, query: str, limit: int = 20) -> str:
    hits = analysis.search(g, query, limit)
    if not hits:
        return f"no matches for '{query}'"
    return "\n".join(fmt_node(n) for n in hits)


def q_impact(root: Path, g, query: str, depth: int = 3, limit: int = 40) -> str:
    seeds = analysis.search(g, query, limit=5)
    if not seeds:
        return f"no symbol matching '{query}' — try `search` first"
    radius = analysis.blast_radius(g, [n["id"] for n in seeds], depth=depth)
    byid = {n["id"]: n for n in g["nodes"]}
    lines = [f"impact of '{query}' at depth {depth}: {len(radius['nodes'])} symbols "
             f"across {len(radius['files'])} files"
             + (" (truncated)" if radius["truncated"] else "")]
    for f in sorted(radius["files"]):
        lines.append(f"  {f}")
    shown = [byid[nid] for nid in radius["nodes"] if nid in byid][:limit]
    if shown:
        lines.append("symbols:")
        lines += [f"  {fmt_node(n)}" for n in shown]
    return "\n".join(lines)


def q_explore(root: Path, g, query: str, limit: int = 5, depth: int = 2) -> str:
    """Consolidated query: verbatim source + call edges + blast radius for the
    top-matching symbols, in one call — the shape an agent usually needs
    instead of chaining sym -> file -> callers -> Read."""
    matches = analysis.search(g, query, limit=limit)
    if not matches:
        return (f"no symbol matching '{query}' — try `graphscout search {query}` "
                "with different terms, or grep")
    spans = analysis.node_spans(g)
    byid = {n["id"]: n for n in g["nodes"]}
    call_edges = [e for e in g["edges"] if e.get("relation") == "calls"]
    out = []
    for n in matches:
        out.append(f"## {n.get('label', n['id'])}  [{loc(n)}]")
        start, nxt = spans.get(n["id"], (None, None))
        snippet = analysis.read_snippet(root, n["source_file"], start, nxt) if start else None
        if snippet:
            body, s, e = snippet
            out.append(f"```{analysis.lang_for(n['source_file'])}\n{body}\n```  (lines {s}-{e})")
        callers = [byid[e["source"]] for e in call_edges
                   if e["target"] == n["id"] and e["source"] in byid]
        callees = [byid[e["target"]] for e in call_edges
                   if e["source"] == n["id"] and e["target"] in byid]
        if callers:
            out.append("callers: " + "; ".join(fmt_node(c) for c in callers[:8]))
        if callees:
            out.append("callees: " + "; ".join(fmt_node(c) for c in callees[:8]))
        out.append("")
    radius = analysis.blast_radius(g, [n["id"] for n in matches], depth=depth)
    out.append(f"blast radius (depth {depth}): {len(radius['nodes'])} symbols across "
               f"{len(radius['files'])} file(s)" + (" (truncated)" if radius["truncated"] else ""))
    if radius["files"]:
        out.append("  " + ", ".join(sorted(radius["files"])[:20]))
    return "\n".join(out)


def q_affected(root: Path, g, changed, depth: int = 8, test_glob: str = None) -> str:
    changed_rel = []
    for c in changed:
        p = Path(c)
        p = p if p.is_absolute() else (root / p)
        try:
            changed_rel.append(str(p.resolve().relative_to(root)))
        except ValueError:
            changed_rel.append(c)  # already root-relative or outside root; try as-is
    globs = (test_glob,) if test_glob else None
    hits = analysis.affected(g, changed_rel, depth=depth, test_globs=globs)
    return "\n".join(hits) if hits else "(no affected test files found via resolved imports)"
