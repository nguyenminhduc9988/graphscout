"""Graph analysis beyond one-hop lookups: full-text search, multi-hop blast
radius, and import-based test-impact tracing. Pure functions over the
in-memory graph dict produced by core.build/core.ensure — no extra caching
layer, so results are always as fresh as the graph passed in."""
import fnmatch
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

DEFAULT_TEST_GLOBS = (
    "test_*.py", "*_test.py", "*Test.*", "*Tests.*", "*.test.*", "*.spec.*",
    "*_spec.rb", "*_test.go", "*_test.exs",
)
LANG_BY_EXT = {
    ".py": "python", ".js": "javascript", ".jsx": "jsx", ".ts": "typescript",
    ".tsx": "tsx", ".go": "go", ".rs": "rust", ".java": "java", ".kt": "kotlin",
    ".rb": "ruby", ".php": "php", ".c": "c", ".h": "c", ".cpp": "cpp",
    ".cs": "csharp", ".swift": "swift", ".sh": "bash", ".lua": "lua",
}


def lang_for(source_file: str) -> str:
    return LANG_BY_EXT.get(Path(source_file).suffix, "")


def node_spans(g) -> dict:
    """id -> (start_line, next_start_line_in_same_file_or_None). graphify only
    records a start line per node, not a range, so the end of a snippet is
    inferred as the line before the next node's start in the same file."""
    by_file = defaultdict(list)
    for n in g["nodes"]:
        m = re.match(r"L(\d+)", n.get("source_location", "") or "")
        if m:
            by_file[n.get("source_file", "")].append((int(m.group(1)), n["id"]))
    spans = {}
    for items in by_file.values():
        items.sort()
        for i, (line, nid) in enumerate(items):
            spans[nid] = (line, items[i + 1][0] if i + 1 < len(items) else None)
    return spans


def read_snippet(root: Path, source_file: str, start: int, next_start: int = None,
                  max_lines: int = 60):
    """Verbatim source lines [start, end] for one node, root-relative path.
    Returns (body, start, end) or None if the file can't be read."""
    try:
        lines = (root / source_file).read_text(errors="replace").splitlines()
    except OSError:
        return None
    if start - 1 >= len(lines):
        return None
    end = min(next_start - 1 if next_start else start + max_lines - 1,
               start + max_lines - 1, len(lines))
    return "\n".join(lines[start - 1:end]), start, end


def search(g, query: str, limit: int = 30, include_docs: bool = False) -> list:
    """Full-text symbol search. Uses SQLite FTS5 (bm25-ranked, prefix-matched,
    multi-term) when available; falls back to substring scoring otherwise —
    same call signature either way, so callers never branch on which ran.
    Docstring/comment nodes (file_type 'rationale') are excluded by default —
    they're prose, not addressable symbols, and drown out real hits by sheer
    term frequency; pass include_docs=True to search them too."""
    terms = re.findall(r"\w+", query)
    if not terms:
        return []
    nodes = g["nodes"] if include_docs else [n for n in g["nodes"] if n.get("file_type") != "rationale"]
    byid = {n["id"]: n for n in nodes}
    try:
        con = sqlite3.connect(":memory:")
        con.execute("CREATE VIRTUAL TABLE fts USING fts5(id UNINDEXED, label, file, kind)")
        con.executemany(
            "INSERT INTO fts (id, label, file, kind) VALUES (?,?,?,?)",
            [(n["id"], n.get("label", ""), n.get("source_file", ""),
              (n.get("metadata") or {}).get("kind", "")) for n in nodes],
        )
        match_q = " OR ".join(f'"{t}"*' for t in terms)
        rows = con.execute(
            "SELECT id FROM fts WHERE fts MATCH ? ORDER BY bm25(fts) LIMIT ?",
            (match_q, limit),
        ).fetchall()
        con.close()
        hits = [byid[r[0]] for r in rows if r[0] in byid]
        if hits:
            return hits
    except sqlite3.OperationalError:
        pass  # FTS5 not compiled into this Python's sqlite3 — fall through

    ql = query.lower()
    scored = []
    for n in nodes:
        label = n.get("label", "").lower()
        if all(t.lower() in label or t.lower() in n["id"].lower() for t in terms):
            score = 0 if label == ql else (1 if label.startswith(ql) else 2)
            scored.append((score, n))
    scored.sort(key=lambda x: x[0])
    return [n for _, n in scored[:limit]]


def blast_radius(g, seed_ids, depth: int = 2, max_nodes: int = 400) -> dict:
    """Multi-hop reachable set over call edges (both directions — who this
    affects if changed, and what it depends on), unlike a single callers/
    callees hop. Bounded by depth and max_nodes so it can't runaway on a hub."""
    adj = defaultdict(set)
    for e in g["edges"]:
        if e.get("relation") == "calls":
            adj[e["source"]].add(e["target"])
            adj[e["target"]].add(e["source"])
    byid = {n["id"]: n for n in g["nodes"]}
    seen = set(seed_ids)
    frontier = set(seed_ids)
    for _ in range(depth):
        nxt = set()
        for nid in frontier:
            nxt |= adj.get(nid, set())
        nxt -= seen
        if not nxt:
            break
        seen |= nxt
        frontier = nxt
        if len(seen) > max_nodes:
            break
    files = {byid[nid]["source_file"] for nid in seen if nid in byid and byid[nid].get("source_file")}
    return {"nodes": seen, "files": files, "truncated": len(seen) > max_nodes}


def file_import_graph(g) -> dict:
    """file -> set of files it imports, restricted to imports graphify could
    resolve to another in-repo node (stdlib/third-party imports have no local
    target and are dropped — they can't feed a test-impact trace anyway)."""
    byid = {n["id"]: n for n in g["nodes"]}
    deps = defaultdict(set)
    for e in g["edges"]:
        if e.get("relation") not in ("imports", "imports_from"):
            continue
        tgt = byid.get(e["target"])
        src_file = e.get("source_file")
        if tgt and src_file and tgt.get("source_file") and tgt["source_file"] != src_file:
            deps[src_file].add(tgt["source_file"])
    return deps


def affected(g, changed_files, depth: int = 8, test_globs=None) -> list:
    """Test files transitively depending on any changed_files, via resolved
    import edges. changed_files are root-relative paths (as stored on nodes)."""
    reverse = defaultdict(set)
    for importer, targets in file_import_graph(g).items():
        for t in targets:
            reverse[t].add(importer)
    seen = set(changed_files)
    frontier = set(changed_files)
    for _ in range(depth):
        nxt = set()
        for f in frontier:
            nxt |= reverse.get(f, set())
        nxt -= seen
        if not nxt:
            break
        seen |= nxt
        frontier = nxt
    patterns = test_globs or DEFAULT_TEST_GLOBS
    return sorted(
        f for f in seen
        if any(fnmatch.fnmatch(Path(f).name, p) or fnmatch.fnmatch(f, p) for p in patterns)
    )
