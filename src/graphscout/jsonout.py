"""Structured (JSON-able) variants of the query layer, for `--json` / the
`format="json"` MCP arg. Plain-text stays the default everywhere else — this
module exists for agents/scripts that want to parse a result instead of
scanning a formatted string, without duplicating the underlying analysis.
"""
from collections import Counter
from pathlib import Path

from . import analysis, core


def _node(n) -> dict:
    return {"label": n.get("label", n["id"]), "type": n.get("type", n.get("_origin", "?")),
            "file": n.get("source_file", "?"), "line": n.get("source_location", "?")}


def map_data(root: Path, g) -> dict:
    nodes, edges = g["nodes"], g["edges"]
    deg = Counter()
    for e in edges:
        deg[e["source"]] += 1
        deg[e["target"]] += 1
    byid = {n["id"]: n for n in nodes}
    per_dir = Counter(n.get("source_file", "?").split("/")[0] for n in nodes)
    rels = Counter(e.get("relation", "?") for e in edges)
    hubs = [{"degree": d, **_node(byid[nid])} for nid, d in deg.most_common(15) if nid in byid]
    return {"root": str(root), "nodes": len(nodes), "edges": len(edges),
            "per_dir": dict(per_dir.most_common(12)), "relations": dict(rels.most_common()),
            "hubs": hubs}


def sym_data(g, query: str, limit: int = 30) -> dict:
    q = query.lower()
    hits = [n for n in g["nodes"] if q in n.get("label", "").lower() or q in n["id"].lower()]
    return {"query": query, "matches": [_node(n) for n in hits[:limit]], "truncated": len(hits) > limit}


def calls_data(g, query: str, direction: str, limit: int = 40) -> dict:
    q = query.lower()
    byid = {n["id"]: n for n in g["nodes"]}
    key, other = ("target", "source") if direction == "callers" else ("source", "target")
    hits = [e for e in g["edges"] if e.get("relation") == "calls" and q in e[key].lower()]
    matches = [_node(byid[e[other]]) if e[other] in byid else
               {"label": e[other], "file": e.get("source_file", "?"), "line": e.get("source_location", "?")}
               for e in hits[:limit]]
    return {"query": query, "direction": direction, "matches": matches, "truncated": len(hits) > limit}


def deps_data(root: Path, g, target: Path) -> dict:
    f = str(target.resolve().relative_to(root))
    imports = [{"relation": e["relation"], "target": e["target"]} for e in g["edges"]
               if e.get("source_file", "") == f and e.get("relation") in ("imports", "imports_from")]
    return {"file": f, "imports": imports}


def search_data(g, query: str, limit: int = 20) -> dict:
    hits = analysis.search(g, query, limit)
    return {"query": query, "matches": [_node(n) for n in hits]}


def impact_data(g, query: str, depth: int = 3, limit: int = 40) -> dict:
    seeds = analysis.search(g, query, limit=5)
    if not seeds:
        return {"query": query, "found": False, "files": [], "symbols": [], "truncated": False}
    radius = analysis.blast_radius(g, [n["id"] for n in seeds], depth=depth)
    byid = {n["id"]: n for n in g["nodes"]}
    symbols = [_node(byid[nid]) for nid in radius["nodes"] if nid in byid][:limit]
    return {"query": query, "found": True, "depth": depth, "files": sorted(radius["files"]),
            "symbols": symbols, "truncated": radius["truncated"]}


def affected_data(root: Path, g, changed, depth: int = 8, test_glob: str = None) -> dict:
    changed_rel = []
    for c in changed:
        p = Path(c)
        p = p if p.is_absolute() else (root / p)
        try:
            changed_rel.append(str(p.resolve().relative_to(root)))
        except ValueError:
            changed_rel.append(c)
    globs = (test_glob,) if test_glob else None
    hits = analysis.affected(g, changed_rel, depth=depth, test_globs=globs)
    return {"changed": changed_rel, "affected_tests": hits}


def routes_data(root: Path, g) -> dict:
    from . import routes as routes_mod
    files = [str(f.relative_to(root)) for f in core.code_files(root)]
    found = routes_mod.detect_routes(root, files)
    return {"routes": [r._asdict() for r in found]}


def orphans_data(root: Path, g, limit: int = 60) -> dict:
    from . import queries
    text = queries.q_orphans(root, g, limit=limit)
    candidates = analysis.orphans(g)
    spans = analysis.node_spans(g)
    corpus = analysis.name_occurrence_counts(root, g)
    kept = []
    for n in candidates:
        start, _nxt = spans.get(n["id"], (None, None))
        if start and start > 1:
            try:
                lines = (root / n["source_file"]).read_text(errors="replace").splitlines()
                if lines[start - 2].strip().startswith(("@", "#[")):
                    continue
            except (OSError, IndexError):
                pass
        name = n.get("label", "").rstrip("()").rsplit(".", 1)[-1]
        if corpus.get(name, 0) > 1:
            continue
        kept.append(n)
    return {"candidates": [_node(n) for n in kept[:limit]], "truncated": len(kept) > limit}


def hotspots_data(root: Path, g, limit: int = 20, max_commits: int = 2000) -> dict:
    churn = analysis.churn_counts(root, max_commits=max_commits)
    ranked = analysis.hotspots(g, churn, limit=limit) if churn else []
    return {"has_churn_signal": bool(churn),
            "hotspots": [{"score": s, "commits": c, "degree": d, "file": f} for s, c, d, f in ranked]}


def diff_data(results: list, ref1: str, ref2: str = None) -> dict:
    return {"ref1": ref1, "ref2": ref2, "files": results}


def doctor_data(directory: str = ".") -> dict:
    from . import doctor as doctor_mod
    return doctor_mod.checks(directory)


def rdeps_data(root: Path, g, target: Path) -> dict:
    f = str(target.resolve().relative_to(root))
    rev = analysis.reverse_file_deps(g)
    return {"file": f, "importers": rev.get(f, [])}


def tree_data(g, query: str, depth: int = 4, limit: int = 5) -> dict:
    seeds = analysis.search(g, query, limit=limit)
    if not seeds:
        return {"query": query, "trees": []}
    byid = {n["id"]: n for n in g["nodes"]}

    def annotate(node):
        n = byid.get(node["id"])
        return {
            "label": n.get("label", node["id"]) if n else node["id"],
            "file": n.get("source_file", "?") if n else "?",
            "line": n.get("source_location", "?") if n else "?",
            "cycle": bool(node.get("cycle")),
            "children": [annotate(c) for c in node.get("children", [])],
        }

    trees = [{"seed": _node(s), "tree": annotate(analysis.call_tree(g, s["id"], depth=depth))}
             for s in seeds]
    return {"query": query, "depth": depth, "trees": trees}


def cycles_data(g) -> dict:
    cycles = analysis.import_cycles(g)
    return {"count": len(cycles), "cycles": cycles}


def entrypoints_data(root: Path, g, limit: int = 100) -> dict:
    found = analysis.entrypoints(g, root=root, limit=limit)
    return {"candidates": [{**_node(n), "reason": r} for n, r in found]}


def viz_data(g, query: str = None, fmt: str = "mermaid", limit: int = 60, depth: int = 2) -> dict:
    from . import queries
    text = queries.q_viz(Path("."), g, query=query, fmt=fmt, limit=limit, depth=depth)
    # q_viz builds the diagram string in both formats; return it plus the
    # resolved node/edge sets so a caller can re-style without rebuilding.
    byid = {n["id"]: n for n in g["nodes"]}
    call_edges = [e for e in g["edges"] if e.get("relation") == "calls"]
    if query:
        seeds = analysis.search(g, query, limit=5)
        radius = analysis.blast_radius(g, [n["id"] for n in seeds], depth=depth, max_nodes=limit)
        node_ids = [nid for nid in radius["nodes"] if nid in byid]
        scope = f"blast_radius:{query}"
    else:
        deg = Counter()
        for e in call_edges:
            deg[e["source"]] += 1
            deg[e["target"]] += 1
        node_ids = [nid for nid, _ in deg.most_common(limit) if nid in byid]
        scope = "top_hubs"
    idset = set(node_ids)
    edges = [{"source": s, "target": t} for s, t in
             ((e["source"], e["target"]) for e in call_edges
              if e["source"] in idset and e["target"] in idset)]
    return {"format": fmt, "scope": scope, "nodes": [_node(byid[nid]) for nid in node_ids],
            "edges": edges, "diagram": text}


def metrics_data(root: Path, g, query: str = None, limit: int = 20) -> dict:
    m = analysis.metrics(g, root=root, query=query, limit=limit)
    if m["mode"] == "symbol":
        return {"mode": "symbol", "query": query,
                "cards": [{**{k: v for k, v in c.items() if k != "node"},
                           "symbol": _node(c["node"])} for c in m["cards"]]}
    def rows(cards):
        return [{**{k: v for k, v in c.items() if k != "node"}, "symbol": _node(c["node"])}
                for c in cards]
    return {"mode": "repo", "symbols": m["symbols"],
            "top_fanout": rows(m["top_fanout"]), "top_fanin": rows(m["top_fanin"])}


def dupes_data(root: Path, g, min_lines: int = 4, limit: int = 20) -> dict:
    clusters = analysis.duplicate_clusters(g, root, min_lines=min_lines, limit=limit)
    return {"min_lines": min_lines, "count": len(clusters),
            "clusters": [[{**_node(n), "start": s, "end": e} for n, s, e in cl]
                         for cl in clusters]}


def recent_data(root: Path, g, commits: int = 20, limit: int = 40) -> dict:
    rows = analysis.recent_symbols(root, g, commits=commits, limit=limit)
    return {"commits": commits,
            "symbols": [{"commits": cnt, "file": f, **_node(n)} for cnt, f, n in rows]}


def why_data(root: Path, g, from_name: str, to_name: str) -> dict:
    srcs = analysis.search(g, from_name, limit=1)
    dsts = analysis.search(g, to_name, limit=1)
    if not srcs or not dsts:
        return {"from": from_name, "to": to_name, "resolved": False}
    path = analysis.shortest_call_path(g, srcs[0]["id"], dsts[0]["id"])
    byid = {n["id"]: n for n in g["nodes"]}
    return {"from": from_name, "to": to_name, "resolved": True,
            "reachable": path is not None,
            "hops": len(path) - 1 if path else 0,
            "path": [_node(byid[nid]) if nid in byid else {"id": nid} for nid in (path or [])]}


def tokens_data(root: Path, g, name: str) -> dict:
    seeds = analysis.search(g, name, limit=1)
    if not seeds:
        return {"query": name, "found": False}
    n = seeds[0]
    spans = analysis.node_spans(g)
    start, nxt = spans.get(n["id"], (None, None))
    snip = analysis.read_snippet(root, n["source_file"], start, nxt, max_lines=400) if start else None
    if not snip:
        return {"query": name, "found": True, "symbol": _node(n), "readable": False}
    body, s, e = snip
    est = analysis.estimate_tokens(body)
    return {"query": name, "found": True, "symbol": _node(n), "lines": [s, e], **est}
