"""Graph building, caching, and incremental refresh.

Graphs are stored per-repo under the cache dir (default ~/.cache/codegraph,
override with $CODEGRAPH_CACHE). Extraction is delegated to graphify
(tree-sitter AST parsing); this layer adds root discovery, mtime-based
incremental rebuilds, and root-relative path normalization.
"""
import hashlib
import json
import os
import sys
import time
from pathlib import Path

CODE_EXTS = {".py", ".js", ".ts", ".tsx", ".jsx", ".mjs", ".go", ".rs", ".java",
             ".rb", ".c", ".h", ".cpp", ".hpp", ".cs", ".php", ".swift", ".kt", ".sh"}
SKIP_DIRS = {".git", "node_modules", "venv", ".venv", "__pycache__", "dist", "build",
             ".next", "target", ".cache", "vendor", "site-packages", ".tox", "coverage"}
MAX_FILES = 5000
MAX_FILE_BYTES = 1_000_000


def cache_dir() -> Path:
    env = os.environ.get("CODEGRAPH_CACHE")
    return Path(env) if env else Path.home() / ".cache" / "codegraph"


def roots_file() -> Path:
    return cache_dir() / "roots.json"


def repo_key(root: Path) -> Path:
    return cache_dir() / hashlib.sha1(str(root).encode()).hexdigest()[:16]


def find_root(start: Path) -> Path:
    p = start if start.is_dir() else start.parent
    for q in [p, *p.parents]:
        if (q / ".git").exists():
            return q
    return p


def code_files(root: Path):
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for f in filenames:
            fp = Path(dirpath) / f
            if fp.suffix in CODE_EXTS and fp.stat().st_size < MAX_FILE_BYTES:
                out.append(fp)
    if len(out) > MAX_FILES:
        print(f"WARNING: {len(out)} code files; graphing first {MAX_FILES} "
              f"(largest dirs may be partial)", file=sys.stderr)
        out = out[:MAX_FILES]
    return out


def load(root: Path):
    d = repo_key(root)
    try:
        graph = json.loads((d / "graph.json").read_text())
        idx = json.loads((d / "index.json").read_text())
        return graph, idx
    except Exception:
        return None, None


def save(root: Path, graph, idx):
    d = repo_key(root)
    d.mkdir(parents=True, exist_ok=True)
    (d / "graph.json").write_text(json.dumps(graph))
    (d / "index.json").write_text(json.dumps(idx))
    rf = roots_file()
    try:
        roots = json.loads(rf.read_text()) if rf.exists() else {}
    except Exception:
        roots = {}
    roots[str(root)] = time.strftime("%Y-%m-%dT%H:%M:%S")
    rf.write_text(json.dumps(roots, indent=1))


def extract_files(paths, root):
    """Extract and normalize source_file to root-relative. graphify stores paths
    relative to the common ancestor of the batch (basename for single/same-dir
    batches), so we resolve via that ancestor. Unattributable ('' semantic) nodes
    are dropped — they'd break incremental dedup."""
    from graphify.extract import extract
    ok_nodes, ok_edges, failed = [], [], []
    resolved = [p.resolve() for p in paths]
    if not resolved:
        return ok_nodes, ok_edges, failed
    common = Path(os.path.commonpath([str(p) for p in resolved])) if len(resolved) > 1 else resolved[0].parent
    if common.is_file():
        common = common.parent

    def norm(sf):
        if not sf:
            return None
        try:
            return str((common / sf).resolve().relative_to(root))
        except ValueError:
            return None

    def collect(r):
        for n in r["nodes"]:
            sf = norm(n.get("source_file", ""))
            if sf:
                n["source_file"] = sf
                ok_nodes.append(n)
        for e in r["edges"]:
            sf = norm(e.get("source_file", ""))
            if sf:
                e["source_file"] = sf
                ok_edges.append(e)

    try:
        collect(extract(paths=resolved, parallel=len(resolved) > 4))
    except Exception:
        for p in resolved:
            common = p.parent
            try:
                collect(extract(paths=[p], parallel=False))
            except Exception:
                failed.append(str(p))
    return ok_nodes, ok_edges, failed


def build(root: Path, only_changed=False):
    files = code_files(root)
    graph, idx = load(root)
    mtimes = {str(f.relative_to(root)): f.stat().st_mtime for f in files}
    if only_changed and graph and idx:
        old = idx.get("mtimes", {})
        changed = [f for f in files if old.get(str(f.relative_to(root))) != f.stat().st_mtime]
        deleted = set(old) - set(mtimes)
        if not changed and not deleted:
            return graph, idx, 0
        drop = {str(f.relative_to(root)) for f in changed} | deleted
        nodes = [n for n in graph["nodes"] if n.get("source_file", "") not in drop]
        edges = [e for e in graph["edges"] if e.get("source_file", "") not in drop]
        new_n, new_e, failed = extract_files(changed, root)
        graph = {"nodes": nodes + new_n, "edges": edges + new_e}
        n_processed = len(changed)
    else:
        new_n, new_e, failed = extract_files(files, root)
        graph = {"nodes": new_n, "edges": new_e}
        n_processed = len(files)
    if failed:
        print(f"WARNING: {len(failed)} files failed extraction: {failed[:5]}", file=sys.stderr)
    idx = {"root": str(root), "mtimes": mtimes, "built": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "failed": failed}
    save(root, graph, idx)
    return graph, idx, n_processed


def ensure(root: Path):
    graph, idx, n = build(root, only_changed=True)
    if n:
        print(f"[codegraph] refreshed {n} file(s)", file=sys.stderr)
    return graph


def touch(target: Path, root: Path):
    """Re-extract one file into its repo's cached graph. No-op when the repo
    has no graph yet (hooks call this on every edit)."""
    g, idx = load(root)
    if not g:
        return
    f = str(target.relative_to(root))
    nodes = [n for n in g["nodes"] if n.get("source_file", "") != f]
    edges = [e for e in g["edges"] if e.get("source_file", "") != f]
    if target.exists() and target.suffix in CODE_EXTS:
        nn, ne, _ = extract_files([target], root)
        nodes += nn
        edges += ne
    if target.exists():
        idx["mtimes"][f] = target.stat().st_mtime
    else:
        idx["mtimes"].pop(f, None)
    save(root, {"nodes": nodes, "edges": edges}, idx)
