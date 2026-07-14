"""Query functions over a built graph. Each returns a plain string ready to
show to a human or an agent — the CLI prints it, the MCP server returns it."""
from collections import Counter
from pathlib import Path

from . import analysis, core, routes as routes_mod


def loc(n):
    return f"{n.get('source_file', '?')}:{n.get('source_location', '?')}"


def fmt_node(n):
    return f"{n.get('label', n['id'])}  [{n.get('type', n.get('_origin', '?'))}]  {loc(n)}"


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


def q_orphans(root: Path, g, limit: int = 60) -> str:
    """Dead-code candidates: symbols with no in-repo caller. Two filters on
    top of analysis.orphans' graph-level pass, both needed in practice:

    1. Decorated defs (the line above their `def`/`class` starts with `@`)
       are dropped — decorators are the most common "invoked by a framework,
       not app code" signal (routes, CLI commands, pytest fixtures).
    2. A plain-text cross-check for the symbol's bare name elsewhere in the
       indexed source. graphify's Python `calls` edges only cover bare-name
       calls reached via `from mod import name`; the equally common
       `import mod; mod.name()` idiom (this very codebase's style — `core.
       ensure(...)`, `queries.q_map(...)`) resolves an `imports` edge on the
       module but never a `calls` edge on the function, which would make
       nearly every qualified-call codebase look like it's full of dead
       code. Re-scanning text is a blunter instrument than a graph edge, but
       it catches exactly the case the graph misses."""
    candidates = analysis.orphans(g)
    spans = analysis.node_spans(g)
    corpus = None
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
        if corpus is None:
            corpus = analysis.name_occurrence_counts(root, g)
        name = n.get("label", "").rstrip("()").rsplit(".", 1)[-1]
        if corpus.get(name, 0) > 1:
            continue
        kept.append(n)
    if not kept:
        return ("no orphan candidates found — every symbol has at least one in-repo caller "
                "(or looked like an entrypoint/test/decorated handler and was excluded)")
    lines = [f"{len(kept)} dead-code candidate(s) (no in-repo caller — verify before deleting, "
             "see `graphscout orphans` caveats):"]
    lines += [f"  {fmt_node(n)}" for n in kept[:limit]]
    if len(kept) > limit:
        lines.append(f"  ... {len(kept) - limit} more (narrow with a smaller repo or raise --limit)")
    return "\n".join(lines)


def q_hotspots(root: Path, g, limit: int = 20, max_commits: int = 2000) -> str:
    """Refactor-priority ranking: files that are both frequently changed
    (git log churn) and structurally central (graph degree) — see
    analysis.hotspots for why neither signal alone is a good proxy."""
    churn = analysis.churn_counts(root, max_commits=max_commits)
    if not churn:
        return "(no churn signal — not a git repo, or git isn't on PATH)"
    ranked = analysis.hotspots(g, churn, limit=limit)
    if not ranked:
        return ("no hotspots — no file has both git history and graph connectivity "
                "(a fresh repo, or the graph and churn signals don't overlap)")
    lines = [f"top {len(ranked)} hotspot(s) — churn x connectivity, over the last "
             f"{max_commits} commit(s):",
             f"{'score':>7}  {'commits':>7}  {'degree':>6}  file"]
    lines += [f"{score:>7}  {c:>7}  {d:>6}  {f}" for score, c, d, f in ranked]
    return "\n".join(lines)


def q_routes(root: Path, g) -> str:
    """Framework route/endpoint detection across the indexed file set (Flask,
    FastAPI, Django, Express, Gin, NestJS, Spring, ASP.NET, Actix, Rails,
    Laravel) — a fast heuristic scan, not a graphify capability."""
    files = [str(f.relative_to(root)) for f in core.code_files(root)]
    found = routes_mod.detect_routes(root, files)
    return routes_mod.format_routes(found)


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


def q_rdeps(root: Path, g, target: Path) -> str:
    """Reverse file imports: who depends on this file (the transpose of `deps`)."""
    f = str(target.resolve().relative_to(root))
    rev = analysis.reverse_file_deps(g)
    importers = rev.get(f, [])
    if not importers:
        return f"(no in-repo file imports {f} via a resolved edge)"
    lines = [f"{len(importers)} file(s) import {f}:"]
    lines += [f"  {imp}" for imp in importers]
    return "\n".join(lines)


def q_tree(root: Path, g, query: str, depth: int = 4, limit: int = 5) -> str:
    """Recursive nested call tree from the top matches for query — read top-down
    to follow what a symbol executes, deeper than one-hop `callees`."""
    seeds = analysis.search(g, query, limit=limit)
    if not seeds:
        return f"no symbol matching '{query}' — try `search` first"
    byid = {n["id"]: n for n in g["nodes"]}
    out = []
    for seed in seeds:
        tree = analysis.call_tree(g, seed["id"], depth=depth)
        label = seed.get("label", seed["id"])

        def walk(node, indent):
            n = byid.get(node["id"])
            head = fmt_node(n) if n else node["id"]
            if node.get("cycle"):
                out.append(f"{indent}{head}  (cycle — already shown)")
                return
            out.append(f"{indent}{head}")
            for c in node.get("children", []):
                walk(c, indent + "  ")

        out.append(f"## {label}")
        walk(tree, "")
        if tree.get("truncated"):
            out.append("  (truncated — node cap reached; narrow the query or lower --depth)")
        out.append("")
    return "\n".join(out).rstrip()


def q_cycles(root: Path, g) -> str:
    """File-level import cycles, one representative path per strongly-connected
    component. A structural smell — not every cycle is a runtime bug, but none
    of them show up in a linear file read."""
    cycles = analysis.import_cycles(g)
    if not cycles:
        return "no import cycles detected (over resolved import edges)"
    lines = [f"{len(cycles)} import cycle(s) detected:"]
    for c in cycles:
        if len(c) == 1:
            lines.append(f"  self-import: {c[0]}")
        else:
            lines.append("  " + " -> ".join(c + [c[0]]))
    return "\n".join(lines)


def q_entrypoints(root: Path, g, limit: int = 100) -> str:
    """Likely externally-invoked symbols (CLI commands, main/handler entry
    points, framework-decorated callables) — the repo's invokable surface in
    one call. Complements `routes` (HTTP) and the inverse of `orphans`."""
    found = analysis.entrypoints(g, root=root, limit=limit)
    if not found:
        return ("no entrypoint candidates — no known entrypoint names "
                "(main/run/handler/app/...) or framework decorators detected")
    lines = [f"{len(found)} entrypoint candidate(s):"]
    for n, reason in found:
        lines.append(f"  [{reason:14s}] {fmt_node(n)}")
    return "\n".join(lines)


def q_viz(root: Path, g, query: str = None, fmt: str = "mermaid",
          limit: int = 60, depth: int = 2, kind: str = "calls") -> str:
    """Emit a graph diagram an agent (or human) can render: the call-graph
    subgraph around a symbol's blast radius when `query` is given, otherwise the
    top hubs by degree. Mermaid (renders inline in GitHub/Notion/most agent UIs)
    by default; `--format=dot` for Graphviz. `--kind=imports` switches to the
    file-level import graph (module dependencies instead of symbol calls).
    Bounded so a hub can't swamp the diagram — a map to navigate by, not every edge."""
    if kind == "imports":
        return _viz_imports(g, fmt, limit)
    byid = {n["id"]: n for n in g["nodes"]}
    call_edges = [e for e in g["edges"] if e.get("relation") == "calls"]

    if query:
        seeds = analysis.search(g, query, limit=5)
        if not seeds:
            return f"no symbol matching '{query}' — nothing to visualize"
        radius = analysis.blast_radius(g, [n["id"] for n in seeds], depth=depth, max_nodes=limit)
        node_ids = radius["nodes"]
        title = f"blast radius of '{query}' (depth {depth}): {len(node_ids)} symbols"
    else:
        deg = Counter()
        for e in call_edges:
            deg[e["source"]] += 1
            deg[e["target"]] += 1
        node_ids = {nid for nid, _ in deg.most_common(limit)}
        title = f"top {len(node_ids)} call-graph hubs"

    node_ids = {nid for nid in node_ids if nid in byid}
    edges = [(e["source"], e["target"]) for e in call_edges
             if e["source"] in node_ids and e["target"] in node_ids]

    def label_of(nid):
        n = byid.get(nid)
        if not n:
            return nid
        lab = n.get("label", nid).rstrip("()")
        return lab.rsplit(".", 1)[-1]

    if fmt == "dot":
        out = [f'// {title}', 'digraph graphscout {', '  rankdir=LR;',
               '  node [shape=box, style=rounded, fontname="Helvetica"];']
        for nid in node_ids:
            out.append(f'  "{nid}" [label="{label_of(nid)}"];')
        for s, t in edges:
            out.append(f'  "{s}" -> "{t}";')
        out.append("}")
        return "\n".join(out)

    # mermaid flowchart
    out = [f"---", f"title: {title}", f"---", "flowchart LR"]
    # sanitize ids: mermaid node ids can't contain certain chars
    safe = {}
    for nid in node_ids:
        safe[nid] = "n" + str(abs(hash(nid)) % 10_000_000)
    seen_pairs = set()
    for nid in node_ids:
        out.append(f'  {safe[nid]}["{label_of(nid).replace(chr(34), chr(39))}"]')
    for s, t in edges:
        pair = (s, t)
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        out.append(f"  {safe[s]} --> {safe[t]}")
    return "\n".join(out)


def _viz_imports(g, fmt: str, limit: int) -> str:
    """File-level import graph (module dependencies) as dot/mermaid. Complementary
    to the call-graph viz: calls show runtime flow within a file cluster, imports
    show which modules depend on which — the shape `cycles`/`affected`/`rdeps`
    all operate on, drawn out. Bounded by `limit` files (highest-degree first)."""
    deps = analysis.file_import_graph(g)
    pairs, files = [], set()
    for src, targets in deps.items():
        for t in targets:
            pairs.append((src, t))
            files.add(src)
            files.add(t)
    if not pairs:
        return "no resolved file-level import edges to visualize"
    deg = Counter()
    for s, t in pairs:
        deg[s] += 1
        deg[t] += 1
    if len(files) > limit:
        keep = {f for f, _ in deg.most_common(limit)}
        pairs = [(s, t) for s, t in pairs if s in keep and t in keep]
        files = keep
    title = f"file-level import graph: {len(files)} files, {len(pairs)} edges"

    def lab(f):
        return f.rsplit("/", 1)[-1]

    if fmt == "dot":
        out = [f"// {title}", "digraph graphscout_imports {", "  rankdir=LR;",
               '  node [shape=box, style=rounded, fontname="Helvetica"];']
        for f in files:
            out.append(f'  "{f}" [label="{lab(f)}"];')
        for s, t in pairs:
            out.append(f'  "{s}" -> "{t}";')
        out.append("}")
        return "\n".join(out)

    out = ["---", f"title: {title}", "---", "flowchart LR"]
    safe = {f: "n" + str(abs(hash(f)) % 10_000_000) for f in files}
    for f in files:
        out.append(f'  {safe[f]}["{lab(f).replace(chr(34), chr(39))}"]')
    seen = set()
    for s, t in pairs:
        if (s, t) in seen:
            continue
        seen.add((s, t))
        out.append(f"  {safe[s]} --> {safe[t]}")
    return "\n".join(out)


def q_metrics(root: Path, g, query: str = None, limit: int = 20) -> str:
    """Per-symbol complexity cards: fan-in (load-bearing — how many sites call
    it), fan-out (calls the most — a complexity proxy), reference/type edges in
    & out, and inferred body lines. With a `query`, one symbol's card; without,
    the repo ranked two ways — top fan-out (likely-too-big functions) and top
    fan-in (hubs many callers depend on). All graph-derived signals neither
    codegraph surfaces."""
    m = analysis.metrics(g, root=root, query=query, limit=limit)
    if m["mode"] == "symbol":
        if not m["cards"]:
            return f"no symbol matching '{query}'"
        out = [f"metrics for '{query}' ({len(m['cards'])} match(es)):"]
        for c in m["cards"]:
            n = c["node"]
            out.append(f"  {fmt_node(n)}")
            out.append(f"    fan-in {c['fan_in']}  fan-out {c['fan_out']}  "
                       f"refs in/out {c['refs_in']}/{c['refs_out']}  "
                       f"~{c['lines']} lines  degree {c['degree']}")
        return "\n".join(out)
    out = [f"complexity metrics over {m['symbols']} symbol(s):"]
    if m["top_fanout"]:
        out.append("top fan-out (calls the most — complexity proxy):")
        for c in m["top_fanout"]:
            out.append(f"  out {c['fan_out']:3d}  in {c['fan_in']:3d}   {fmt_node(c['node'])}")
    if m["top_fanin"]:
        out.append("top fan-in (most callers — load-bearing hubs):")
        for c in m["top_fanin"]:
            out.append(f"  in {c['fan_in']:3d}  out {c['fan_out']:3d}   {fmt_node(c['node'])}")
    if not m["top_fanout"] and not m["top_fanin"]:
        out.append("  (no call edges recorded — graphify found no bare-name calls to resolve)")
    return "\n".join(out)


def q_dupes(root: Path, g, min_lines: int = 4, limit: int = 20) -> str:
    """Copy-paste / near-identical function bodies. Bodies are normalized
    (comments, whitespace, case stripped) so formatter-only and re-commented
    copies still cluster; identifier renaming is NOT normalized, so true
    type-2 clones won't merge — a deliberate precision/recall trade. A call graph
    can't see this at all (two identical helpers have no edge between them)."""
    clusters = analysis.duplicate_clusters(g, root, min_lines=min_lines, limit=limit)
    if not clusters:
        return (f"no duplicate clusters found (min_lines={min_lines}) — no two symbol "
                f"bodies normalized to the same signature")
    out = [f"{len(clusters)} duplicate cluster(s) (normalized bodies shared by >=2 symbols):"]
    for cl in clusters:
        members = [f"{n.get('label', n['id'])}@{n.get('source_file', '?')}:L{s}-{e}"
                   for n, s, e in cl]
        out.append(f"  [{len(cl)}]  " + "  |  ".join(members))
    return "\n".join(out)


def q_recent(root: Path, g, commits: int = 20, limit: int = 40) -> str:
    """Symbols in files touched by the last N commits — 'what's been moving
    lately' for orienting on a repo. Lighter than `diff` (two explicit refs):
    this is the churn-window view. Reuses churn_counts over a bounded commit
    window, not full history."""
    rows = analysis.recent_symbols(root, g, commits=commits, limit=limit)
    if not rows:
        return "(no recent-change signal — not a git repo, or no commits touch indexed files)"
    out = [f"symbols in files touched by the last {commits} commit(s):"]
    for cnt, _f, n in rows:
        out.append(f"  {cnt:3d}x  {fmt_node(n)}")
    return "\n".join(out)


def q_why(root: Path, g, from_name: str, to_name: str) -> str:
    """Shortest call chain from one symbol to another over resolved call edges —
    the two-symbol 'why does A depend on B' question that `impact` (undirected
    blast radius from one seed) doesn't directly answer. Reports the hop-by-hop
    path, or that they're unreachable."""
    srcs = analysis.search(g, from_name, limit=1)
    dsts = analysis.search(g, to_name, limit=1)
    if not srcs or not dsts:
        return (f"could not resolve both '{from_name}' and '{to_name}' to symbols — "
                "use `search` to confirm exact names")
    fid, tid = srcs[0]["id"], dsts[0]["id"]
    path = analysis.shortest_call_path(g, fid, tid)
    if path is None:
        return (f"'{from_name}' cannot reach '{to_name}' over resolved call edges "
                "(dynamic dispatch isn't captured — fall back to grep)")
    byid = {n["id"]: n for n in g["nodes"]}
    labels = [fmt_node(byid[nid]) if nid in byid else nid for nid in path]
    out = [f"call path '{from_name}' -> '{to_name}' ({len(path) - 1} hop(s)):"]
    out.append("  " + " -> ".join(labels))
    return "\n".join(out)


def q_tokens(root: Path, g, name: str) -> str:
    """Token cost of one symbol's body — 'is it worth reading the whole thing, or
    take the outline first?' The single most on-brand metric for a tool whose
    purpose is to stop agents reading whole files. tiktoken (cl100k) when
    installed, else a chars/4 heuristic within ~15% for typical code."""
    seeds = analysis.search(g, name, limit=1)
    if not seeds:
        return f"no symbol matching '{name}'"
    n = seeds[0]
    spans = analysis.node_spans(g)
    start, nxt = spans.get(n["id"], (None, None))
    snip = analysis.read_snippet(root, n["source_file"], start, nxt, max_lines=400) if start else None
    if not snip:
        return f"could not read body of '{n.get('label', n['id'])}'"
    body, s, e = snip
    est = analysis.estimate_tokens(body)
    return (f"'{n.get('label', n['id'])}' [{loc(n)}] lines {s}-{e}: "
            f"~{est['tokens']} tokens ({est['method']}), {est['chars']} chars")
