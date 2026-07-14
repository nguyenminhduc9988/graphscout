"""Graph analysis beyond one-hop lookups: full-text search, multi-hop blast
radius, and import-based test-impact tracing. Pure functions over the
in-memory graph dict produced by core.build/core.ensure — no extra caching
layer, so results are always as fresh as the graph passed in."""
import fnmatch
import re
import shutil
import sqlite3
import subprocess
from collections import Counter, defaultdict
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


def _is_file_root(n) -> bool:
    """True for the synthetic node graphify emits to represent an entire
    source file (id == the file's module stem, label == its basename, or
    metadata.kind == "file" where graphify tags it explicitly). It sits at
    the same L1 as the file's first real symbol, so if left in the boundary
    calculation it can sort ahead of that symbol and collapse its span to
    zero lines — this node spans the whole file, not up to "the next node",
    so it must never act as a boundary."""
    if (n.get("metadata") or {}).get("kind") == "file":
        return True
    return n.get("label", "") == Path(n.get("source_file", "")).name


def _is_synthetic_entrypoint(n) -> bool:
    """True for synthetic "run the whole file" nodes graphify emits for
    script-like languages (bash's `_entrypoint` kind today; matched by
    suffix in case more show up) — these are invoked by executing the file,
    never by an in-repo call edge, so they'd otherwise always look dead."""
    kind = (n.get("metadata") or {}).get("kind", "")
    return kind.endswith("_entrypoint")


def node_spans(g) -> dict:
    """id -> (start_line, next_start_line_in_same_file_or_None). graphify only
    records a start line per node, not a range, so the end of a snippet is
    inferred as the line before the next node's start in the same file.
    Docstring/comment ('rationale') nodes and the whole-file root node are
    excluded from the boundary calculation — graphify places a function's
    docstring node on the very next line after its def (which would otherwise
    truncate the function's own snippet to just its signature), and the file
    root node shares L1 with the file's first real symbol (which would
    otherwise collapse that symbol's span to zero lines)."""
    by_file = defaultdict(list)
    for n in g["nodes"]:
        if n.get("file_type") == "rationale" or _is_file_root(n):
            continue
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


_ENTRYPOINT_NAMES = {"main", "__main__", "run", "handler", "lambda_handler", "index", "setup", "teardown"}

_CALL_SHAPE_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")


def name_occurrence_counts(root, g) -> dict:
    """name -> number of `name(`-shaped occurrences across every file the
    graph knows about (its own definition included, so a genuinely unused
    symbol has a count of 1). Used to backstop orphan detection against call
    styles graphify's edges don't capture (see q_orphans) — a text scan, not
    a semantic one, so it can't tell a real call from a coincidental same-
    named local function in an unrelated file; that's fine here, since the
    cost of a false "still used" is just an over-cautious dead-code list.
    One regex pass per file (not one per candidate name) so this stays cheap
    even on a repo with thousands of symbols."""
    names = {n.get("label", "").rstrip("()").rsplit(".", 1)[-1]
             for n in g["nodes"] if n.get("file_type") != "rationale"}
    names.discard("")
    counts = Counter()
    files = {n.get("source_file", "") for n in g["nodes"] if n.get("source_file")}
    for f in files:
        try:
            text = (root / f).read_text(errors="replace")
        except OSError:
            continue
        counts.update(m for m in _CALL_SHAPE_RE.findall(text) if m in names)
    return counts


def orphans(g, test_globs=None) -> list:
    """Symbols with zero incoming `calls`/`references` edges from anywhere
    else in the repo — dead-code CANDIDATES, not certainties. A static call
    graph can't see: dynamic dispatch (`getattr`-style calls), reflection,
    framework-invoked handlers (routes, CLI commands, signal/event handlers),
    or anything called from outside this repo (a published library's public
    API). Common entrypoint names, dunder methods, and symbols defined in
    test files are excluded up front since they're *expected* to have no
    in-repo caller; `queries.q_orphans` filters decorated defs on top of this
    (decorators are the most common "invoked by a framework" signal)."""
    incoming = {e["target"] for e in g["edges"] if e.get("relation") in ("calls", "references")}
    patterns = test_globs or DEFAULT_TEST_GLOBS
    out = []
    for n in g["nodes"]:
        if (n.get("file_type") == "rationale" or n["id"] in incoming
                or _is_file_root(n) or _is_synthetic_entrypoint(n)):
            continue
        label = n.get("label", "")
        name = label.rstrip("()").rsplit(".", 1)[-1]
        if name in _ENTRYPOINT_NAMES or (name.startswith("__") and name.endswith("__")):
            continue
        sf = n.get("source_file", "")
        if any(fnmatch.fnmatch(Path(sf).name, p) for p in patterns):
            continue
        if name.startswith("test_") or name.endswith("_test") or name.startswith("Test"):
            continue
        out.append(n)
    return out


def churn_counts(root: Path, max_commits: int = 2000) -> Counter:
    """file -> number of commits that touched it, over the last max_commits
    (not full history, so this stays fast on old/huge repos and reflects
    recent activity rather than a decade-old refactor). Empty Counter (never
    an error) when git or a repo isn't available — callers treat that as
    "no churn signal", not a failure."""
    if not shutil.which("git") or not (root / ".git").exists():
        return Counter()
    try:
        r = subprocess.run(
            ["git", "-C", str(root), "log", f"-{max_commits}", "--name-only", "--pretty=format:"],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return Counter()
    if r.returncode != 0:
        return Counter()
    return Counter(line.strip() for line in r.stdout.splitlines() if line.strip())


def hotspots(g, churn, limit: int = 20) -> list:
    """Rank files by churn x structural connectivity — the classic "hotspot"
    proxy for refactor priority (Tornhill's *Your Code as a Crime Scene*):
    change frequency alone flags files that are merely active (a config file
    edited every release), and connectivity alone flags files that are
    merely big; the product flags files that are both busy AND load-bearing,
    which is where a change is most likely to have a wide, risky blast
    radius. Returns (score, churn, degree, file) tuples, highest score
    first; a file needs a nonzero count on both axes to be listed at all."""
    node_deg = Counter()
    for e in g["edges"]:
        node_deg[e["source"]] += 1
        node_deg[e["target"]] += 1
    file_deg = Counter()
    for n in g["nodes"]:
        sf = n.get("source_file")
        if sf:
            file_deg[sf] += node_deg.get(n["id"], 0)
    scored = []
    for f in set(churn) | set(file_deg):
        c, d = churn.get(f, 0), file_deg.get(f, 0)
        if c and d:
            scored.append((c * d, c, d, f))
    scored.sort(reverse=True)
    return scored[:limit]


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


def reverse_file_deps(g) -> dict:
    """file -> sorted list of files that import it (the transpose of
    file_import_graph). Answers 'what else breaks if I change/delete this file?'
    for non-test code too — `affected` is the test-only specialization of this,
    reached by walking reverse edges out to test files."""
    rev = defaultdict(set)
    for importer, targets in file_import_graph(g).items():
        for t in targets:
            rev[t].add(importer)
    return {f: sorted(s) for f, s in rev.items()}


def call_tree(g, seed_id: str, depth: int = 4, max_nodes: int = 200) -> dict:
    """Nested callees of seed: what it calls, what those call, etc. — a
    depth-bounded DFS over `calls` edges (source->target) that the caller
    formats into the indented tree an agent reads top-down to follow execution.
    Each node is expanded at most once (global visited set) so a diamond
    (a->{b,c}->d) doesn't duplicate d, and a back-edge (a->b->a) is marked
    `cycle` instead of looping; a global node cap stops a hub from exploding
    the tree. Returns {'id', 'children': [...], 'truncated'}."""
    callees = defaultdict(list)
    for e in g["edges"]:
        if e.get("relation") == "calls":
            callees[e["source"]].append(e["target"])
    seen = set()
    truncated = False

    def build(nid, d):
        nonlocal truncated
        seen.add(nid)
        children = []
        if d > 0:
            for tgt in callees.get(nid, []):
                if tgt in seen:
                    children.append({"id": tgt, "cycle": True})
                elif len(seen) >= max_nodes:
                    truncated = True
                else:
                    children.append(build(tgt, d - 1))
        return {"id": nid, "children": children}

    tree = build(seed_id, depth)
    tree["truncated"] = truncated
    return tree


def import_cycles(g, max_cycles: int = 50) -> list:
    """File-level import cycles (A imports B imports ... imports A) over the
    resolved import graph. Returns one representative cycle per strongly-
    connected component (the member files, rotated to start at the
    lexicographically smallest so the same cycle isn't reported twice), plus
    self-imports. Iterative Tarjan (no recursion, so deep chains can't blow the
    stack); a static import graph can't see runtime/deferred-import cycles and a
    cycle here isn't always a bug, but it's a structural smell worth surfacing."""
    deps = file_import_graph(g)
    files = set(deps) | {t for targets in deps.values() for t in targets}

    # --- iterative Tarjan SCC ---
    index = lowlink = {}
    on_stack = set()
    stack = []
    sccs = []
    counter = 0
    for start in files:
        if start in index:
            continue
        work = [(start, iter(sorted(deps.get(start, ()))))]
        index[start] = lowlink[start] = counter
        counter += 1
        stack.append(start)
        on_stack.add(start)
        while work:
            v, it = work[-1]
            advanced = False
            for w in it:
                if w not in index:
                    index[w] = lowlink[w] = counter
                    counter += 1
                    stack.append(w)
                    on_stack.add(w)
                    work.append((w, iter(sorted(deps.get(w, ())))))
                    advanced = True
                    break
                elif w in on_stack:
                    lowlink[v] = min(lowlink[v], index[w])
            if advanced:
                continue
            work.pop()
            if lowlink[v] == index[v]:
                comp = []
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    comp.append(w)
                    if w == v:
                        break
                sccs.append(comp)
            if work:
                parent = work[-1][0]
                lowlink[parent] = min(lowlink[parent], lowlink[v])

    # --- one representative cycle per nontrivial SCC ---
    out = []
    for comp in sccs:
        compset = set(comp)
        if len(comp) == 1:
            v = comp[0]
            if v in deps.get(v, ()):  # genuine self-import
                out.append([v])
            continue
        # DFS within the SCC for a cycle returning to its start node
        start = min(comp)
        path = [start]
        visited = {start}

        def dfs(node):
            for nxt in sorted(n for n in deps.get(node, ()) if n in compset):
                if nxt == start and len(path) > 1:
                    return True
                if nxt not in visited:
                    visited.add(nxt)
                    path.append(nxt)
                    if dfs(nxt):
                        return True
                    path.pop()
            return False

        if dfs(start):
            rotated = list(path)
            out.append(rotated)
        if len(out) >= max_cycles:
            break
    return out


# Names that, by convention, mark a symbol as the thing an external caller
# (a runtime, a framework, an OS entry) invokes directly — never reached by an
# in-repo call edge, so they'd otherwise look like dead code. Broader than
# analysis.orphans' _ENTRYPOINT_NAMES: this is the public-surface list, not the
# "don't flag as orphan" list.
_ENTRYPOINT_NAMES_FULL = {
    "main", "__main__", "run", "app", "create_app", "application", "serve",
    "server", "start", "startup", "shutdown", "handler", "lambda_handler",
    "index", "invoke", "dispatch", "entrypoint", "cli", "manage", "wsgi",
    "asgi", "factory",
}
# Decorator substrings that signal "invoked by a framework, not by app code":
# CLI frameworks, schedulers, message/event brokers, signal handlers.
_FRAMEWORK_DECORATOR_RE = re.compile(
    r"@(?:\w+\.)*(?:click|typer|fire|app|cli|command|group|argument|option|"
    r"route|router|get|post|put|delete|patch|task|shared_task|celery|cron|"
    r"schedule|signal|receiver|listener|subscriber|event|hook|fixture|step|"
    r"bot|command_handler|EventHandler)\b"
)


def entrypoints(g, root: Path = None, limit: int = 100) -> list:
    """The repo's likely externally-invoked surface — symbols a runtime,
    framework, CLI, or out-of-repo caller reaches directly (so they have no
    in-repo `calls` edge and would otherwise look dead). Complementary to
    `routes` (HTTP only) and the inverse of `orphans` (unintentionally dead):
    `entrypoints` is *intentionally* external. Two signals, unioned:

    1. A known entrypoint name (main, run, handler, app, create_app, ...).
    2. A decorator whose name marks a framework invocation (click/typer/celery/
       signal/event/route/fixture/...), detected by peeking at the line above
       the def — the same trick q_orphans uses, in the opposite direction.

    Returns nodes tagged with a 'reason' ('name' | 'decorator' | 'name+decorator')
    and a file:line each. A heuristic net, not a guarantee — verify before
    treating the list as exhaustive."""
    incoming = {e["target"] for e in g["edges"] if e.get("relation") in ("calls", "references")}
    spans = node_spans(g)
    out = []
    for n in g["nodes"]:
        if n.get("file_type") == "rationale" or _is_file_root(n):
            continue
        label = n.get("label", "")
        name = label.rstrip("()").rsplit(".", 1)[-1]
        by_name = name in _ENTRYPOINT_NAMES_FULL
        by_dec = False
        if not by_name:  # name match is cheap and authoritative; only peek otherwise
            start = spans.get(n["id"], (None, None))[0]
            sf = n.get("source_file", "")
            if start and start > 1 and root is not None:
                try:
                    prev = (root / sf).read_text(errors="replace").splitlines()[start - 2]
                    by_dec = bool(_FRAMEWORK_DECORATOR_RE.search(prev))
                except (OSError, IndexError):
                    pass
        if by_name or by_dec:
            reason = "name+decorator" if (by_name and by_dec) else ("name" if by_name else "decorator")
            out.append((n, reason))
    # stable, predictable order: kind, then file, then line
    out.sort(key=lambda t: (t[1], t[0].get("source_file", ""), t[0].get("source_location", "")))
    return out[:limit]


# ---------------------------------------------------------------------------
# Complexity / metrics — fan-in, fan-out, approx LOC, type coupling. Real graph
# signals graphify gives us for free but neither codegraph surfaces: how many
# callers a symbol has (load-bearing), how many callees (complexity proxy), and
# how big its body is (inferred line span). `name` resolves to one card; no name
# ranks the whole repo so an agent can spot god-functions and hubs at a glance.
# ---------------------------------------------------------------------------

def call_fan_counts(g) -> tuple:
    """(incoming-calls Counter, outgoing-calls Counter) over `calls` edges.
    fan-in = how many sites call it (load-bearing); fan-out = how many distinct
    callees it has (a complexity proxy)."""
    inn, outt = Counter(), Counter()
    for e in g["edges"]:
        if e.get("relation") == "calls":
            outt[e["source"]] += 1
            inn[e["target"]] += 1
    return inn, outt


def approx_lines(spans, node) -> int:
    """Body size from the inferred (start, next_start) span — graphify records
    no end line, so this is 'up to the next sibling symbol', an over-estimate
    by half a blank line at worst. 0 when we can't place the node on a line."""
    start, nxt = spans.get(node["id"], (None, None))
    if not start:
        return 0
    return (nxt - start) if nxt else 0


def symbol_card(g, node, spans=None) -> dict:
    """One symbol's metric card: fan-in, fan-out, reference/type edges in & out,
    approx body lines, total degree, file:line. All graph-derived, so it costs
    nothing an agent couldn't compute — but pre-computed and shaped for a glance."""
    inn, outt = call_fan_counts(g)
    spans = spans if spans is not None else node_spans(g)
    refs_in = sum(1 for e in g["edges"]
                  if e.get("relation") == "references" and e["target"] == node["id"])
    refs_out = sum(1 for e in g["edges"]
                   if e.get("relation") == "references" and e["source"] == node["id"])
    fin, fout = inn.get(node["id"], 0), outt.get(node["id"], 0)
    return {
        "node": node, "fan_in": fin, "fan_out": fout,
        "refs_in": refs_in, "refs_out": refs_out,
        "degree": fin + fout, "lines": approx_lines(spans, node),
    }


def metrics(g, root: Path = None, query: str = None, limit: int = 20) -> dict:
    """If `query` is given, resolve it to symbols and return each one's card
    (most often a single symbol). Otherwise rank the repo's symbols two ways:
    top fan-out (complexity — likely-too-big functions) and top fan-in
    (load-bearing hubs many callers depend on). File-root, rationale, and
    synthetic entrypoint nodes are excluded so the rankings are real symbols."""
    inn, outt = call_fan_counts(g)
    spans = node_spans(g)
    byid = {n["id"]: n for n in g["nodes"]}

    def real(n):
        return (n.get("file_type") != "rationale" and not _is_file_root(n)
                and not _is_synthetic_entrypoint(n))

    if query:
        seeds = search(g, query, limit=5)
        return {"mode": "symbol", "query": query,
                "cards": [symbol_card(g, s, spans) for s in seeds]}

    real_nodes = [n for n in g["nodes"] if real(n)]
    by_fanout = sorted(real_nodes, key=lambda n: outt.get(n["id"], 0), reverse=True)
    by_fanin = sorted(real_nodes, key=lambda n: inn.get(n["id"], 0), reverse=True)
    return {
        "mode": "repo", "symbols": len(real_nodes),
        "top_fanout": [symbol_card(g, n, spans) for n in by_fanout[:limit]
                       if outt.get(n["id"], 0) > 0],
        "top_fanin": [symbol_card(g, n, spans) for n in by_fanin[:limit]
                      if inn.get(n["id"], 0) > 0],
    }


# ---------------------------------------------------------------------------
# Duplicate / near-identical function bodies — copy-paste detection that a call
# graph can't see (two identical helpers have no edge between them). Bodies are
# normalized (comments + whitespace + case stripped) so formatter-only and
# re-commented copies still cluster; identifier renaming is NOT normalized, so a
# true type-2 clone won't merge — a deliberate precision/recall trade (the tool
# is a "where might I dedupe" map, not a clone oracle). Trivial bodies (<min_lines
# or a tiny normalized signature) are skipped to avoid noise.
# ---------------------------------------------------------------------------

_TRAILING_COMMENT_RE = re.compile(r"(#|//).*$")
_WS_RE = re.compile(r"\s+")


def _normalize_body(text: str) -> str:
    """Strip trailing line comments, blank lines, and case/whitespace so two
    functions that differ only in formatting/comments collapse to one key."""
    kept = []
    for line in text.splitlines():
        line = _TRAILING_COMMENT_RE.sub("", line).strip()
        if line:
            kept.append(line)
    return _WS_RE.sub(" ", " ".join(kept)).strip().lower()


def duplicate_clusters(g, root: Path, min_lines: int = 4, min_chars: int = 40,
                       limit: int = 20) -> list:
    """normalized-body-signature -> [(node, start, end)] for bodies shared by
    >=2 symbols. The declaration line (`def NAME(...)` / `class ...`) is dropped
    before normalizing, so two functions with identical bodies but different
    names DO cluster — that's the copy-paste case. Identifier renaming inside the
    body is NOT normalized, so true type-2 clones won't merge (deliberate
    precision/recall trade). Returns clusters biggest-first, bounded by `limit`."""
    spans = node_spans(g)
    groups = defaultdict(list)
    for n in g["nodes"]:
        if (n.get("file_type") == "rationale" or _is_file_root(n)
                or _is_synthetic_entrypoint(n)):
            continue
        start, nxt = spans.get(n["id"], (None, None))
        if not start:
            continue
        snip = read_snippet(root, n.get("source_file", ""), start, nxt, max_lines=200)
        if not snip:
            continue
        body, s, e = snip
        if e - s + 1 < min_lines:
            continue
        # drop the declaration line so the function's own name doesn't defeat
        # matching — two identical bodies with different names are the clone.
        lines = body.splitlines()
        if len(lines) > 1:
            body = "\n".join(lines[1:])
        sig = _normalize_body(body)
        if len(sig) < min_chars:
            continue
        groups[sig].append((n, s, e))
    clusters = [v for v in groups.values() if len(v) >= 2]
    clusters.sort(key=lambda c: (-len(c), -len(c[0][0].get("label", ""))))
    return clusters[:limit]


# ---------------------------------------------------------------------------
# Recently-changed symbols — git-aware "what's been moving lately". `diff`
# compares two explicit refs; `recent` is the lighter "over the last N commits,
# which symbols live in files that churned" view an agent reaches for when
# orienting on a repo. Reuses churn_counts (last-N commit window, not full
# history) and maps touched files back to their symbols via source_file.
# ---------------------------------------------------------------------------

def recent_symbols(root: Path, g, commits: int = 20, limit: int = 40) -> list:
    if not shutil.which("git") or not (root / ".git").exists():
        return []
    churn = churn_counts(root, max_commits=commits)
    if not churn:
        return []
    indexed = {n.get("source_file") for n in g["nodes"] if n.get("source_file")}
    by_file = defaultdict(list)
    for n in g["nodes"]:
        sf = n.get("source_file")
        if sf and n.get("file_type") != "rationale" and not _is_file_root(n):
            by_file[sf].append(n)
    rows = []
    for f, cnt in churn.items():
        if f not in indexed:
            continue
        for n in by_file.get(f, []):
            rows.append((cnt, f, n))
    rows.sort(key=lambda r: (-r[0], r[1], r[2].get("source_location", "")))
    return rows[:limit]


# ---------------------------------------------------------------------------
# Shortest path / reachability — "how does A reach B" over directed call edges.
# A focused counterpart to `impact` (undirected blast radius from one seed): this
# answers the two-symbol question "why does X depend on Y" with the actual call
# chain, or reports unreachable. BFS over source->target `calls` edges; bounded
# visit set so a hub can't make it wander.
# ---------------------------------------------------------------------------

def shortest_call_path(g, from_id: str, to_id: str, max_nodes: int = 5000) -> list:
    """id list from from_id to to_id (inclusive) over `calls` edges, or None."""
    from collections import deque
    adj = defaultdict(set)
    for e in g["edges"]:
        if e.get("relation") == "calls":
            adj[e["source"]].add(e["target"])
    if from_id == to_id:
        return [from_id]
    prev = {from_id: None}
    q = deque([from_id])
    while q and len(prev) < max_nodes:
        cur = q.popleft()
        for nxt in adj.get(cur, ()):
            if nxt in prev:
                continue
            prev[nxt] = cur
            if nxt == to_id:
                path, c = [], to_id
                while c is not None:
                    path.append(c)
                    c = prev[c]
                return list(reversed(path))
            q.append(nxt)
    return None


# ---------------------------------------------------------------------------
# Token-cost estimation — "is it worth reading this whole symbol, or should I
# take the outline first?" The single most on-brand metric for a tool whose
# purpose is to stop agents reading whole files. Uses tiktoken (cl100k) when the
# user has it; otherwise a chars/4 heuristic that's within ~15% for typical code.
# ---------------------------------------------------------------------------

def estimate_tokens(text: str) -> dict:
    chars = len(text)
    lines = text.count("\n") + (1 if text and not text.endswith("\n") else 0)
    tok_heuristic = max(1, chars // 4)
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        exact = len(enc.encode(text))
        return {"tokens": exact, "method": "tiktoken(cl100k)",
                "chars": chars, "lines": lines}
    except Exception:
        return {"tokens": tok_heuristic, "method": "heuristic(chars/4)",
                "chars": chars, "lines": lines}
