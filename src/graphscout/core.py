"""Graph building, caching, and incremental refresh.

Graphs are stored per-repo under the cache dir (default ~/.cache/graphscout,
override with $GRAPHSCOUT_CACHE). Extraction is delegated to graphify
(tree-sitter AST parsing); this layer adds root discovery, mtime-based
incremental rebuilds, and root-relative path normalization.
"""
import fnmatch
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

# Every extension graphify can walk into a real language extractor (defs, calls,
# imports) — not just files it can list. Kept as a static set rather than
# importing graphify.detect at module scope, since that's an internal API.
CODE_EXTS = {
    ".py", ".js", ".jsx", ".mjs", ".ts", ".tsx", ".ejs", ".ets", ".vue", ".svelte", ".astro",
    ".go", ".rs", ".zig", ".java", ".groovy", ".kt", ".kts", ".scala",
    ".c", ".h", ".cpp", ".cc", ".cxx", ".hpp", ".cs", ".razor", ".cshtml",
    ".rb", ".php", ".swift", ".m", ".mm", ".lua", ".luau", ".dart",
    ".ex", ".exs", ".jl", ".r", ".v", ".sv", ".svh",
    ".pas", ".pp", ".dpr", ".dpk", ".lpr", ".lpk", ".dfm", ".lfm",
    ".sh", ".bash", ".ps1", ".hcl", ".tf", ".tfvars",
    ".f", ".f90", ".f95", ".f03", ".f08",
}
SKIP_DIRS = {".git", "node_modules", "venv", ".venv", "__pycache__", "dist", "build",
             ".next", "target", ".cache", "vendor", "site-packages", ".tox", "coverage"}
MAX_FILES = 5000
MAX_FILE_BYTES = 1_000_000


def cache_dir() -> Path:
    env = os.environ.get("GRAPHSCOUT_CACHE") or os.environ.get("CODEGRAPH_CACHE")
    return Path(env) if env else Path.home() / ".cache" / "graphscout"


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


def _load_config(root: Path) -> dict:
    """Optional graphscout.json (codegraph.json also accepted, for projects
    already carrying one): {"exclude": [...], "include": [...], "extensions":
    {".ext": "lang"}} — gitignore-style glob patterns, root-relative."""
    for name in ("graphscout.json", "codegraph.json"):
        p = root / name
        if p.exists():
            try:
                return json.loads(p.read_text())
            except (json.JSONDecodeError, OSError):
                print(f"WARNING: {p} is not valid JSON; ignoring", file=sys.stderr)
    return {}


def _glob_match(patterns, relpath: str) -> bool:
    posix = relpath.replace(os.sep, "/")
    name = posix.rsplit("/", 1)[-1]
    for p in patterns:
        pp = p.rstrip("/")
        if fnmatch.fnmatch(posix, p) or fnmatch.fnmatch(name, p) or posix.startswith(pp + "/"):
            return True
    return False


def _hits_skip_dirs(relpath: str) -> bool:
    return any(part in SKIP_DIRS or part.startswith(".") for part in Path(relpath).parts[:-1])


def _git_tracked(root: Path):
    """Root-relative paths git would show as tracked or untracked-but-not-
    ignored — i.e. everything .gitignore (nested files included, plus the
    global excludesfile) says to keep. None if this isn't a usable git repo,
    so callers fall back to a plain walk."""
    if not shutil.which("git") or not (root / ".git").exists():
        return None
    try:
        r = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            capture_output=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return [p for p in r.stdout.decode(errors="replace").split("\0") if p]


def _walk_all(root: Path):
    """Every file under root, pruning only the hard-coded SKIP_DIRS/dotdirs —
    ignores .gitignore entirely. Used for the non-git fallback and to let
    `include` patterns pull gitignored paths back in."""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for f in filenames:
            out.append(str((Path(dirpath) / f).relative_to(root)))
    return out


def code_files(root: Path):
    cfg = _load_config(root)
    exts = CODE_EXTS | {"." + k.lstrip(".") for k in (cfg.get("extensions") or {})}
    excludes, includes = cfg.get("exclude") or [], cfg.get("include") or []

    tracked = _git_tracked(root)
    base = tracked if tracked is not None else _walk_all(root)
    base = [p for p in base if not _hits_skip_dirs(p)]

    if includes:  # explicit opt-in overrides .gitignore, never the hard skip list
        forced = [p for p in _walk_all(root) if _glob_match(includes, p)]
        base = list(dict.fromkeys(base + forced))

    if excludes:  # wins over everything, including `include`
        base = [p for p in base if not _glob_match(excludes, p)]

    out = []
    for rel in base:
        fp = root / rel
        try:
            if fp.suffix in exts and fp.stat().st_size < MAX_FILE_BYTES:
                out.append(fp)
        except OSError:
            continue
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
        print(f"[graphscout] refreshed {n} file(s)", file=sys.stderr)
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


def watch(root: Path, interval: float = 1.5):
    """Block, keeping root's graph in sync as files change. Yields a status
    line each time it re-syncs (empty string on no-op polls). No hook or
    per-edit `touch` call needed while this runs — the opposite of `ensure`'s
    on-demand model. Uses watchdog for instant, low-CPU events when installed
    ($ pip install "graphscout[watch]"); falls back to mtime polling otherwise.
    """
    if not load(root)[0]:
        build(root)
        yield f"[graphscout] initial build of {root}"

    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer
    except ImportError:
        while True:
            time.sleep(interval)
            _g, _idx, n = build(root, only_changed=True)
            if n:
                yield f"[graphscout] refreshed {n} file(s)"
        return

    import queue
    q = queue.Queue()

    class Handler(FileSystemEventHandler):
        def on_any_event(self, event):
            if not event.is_directory and Path(event.src_path).suffix in CODE_EXTS:
                q.put(1)

    observer = Observer()
    observer.schedule(Handler(), str(root), recursive=True)
    observer.start()
    try:
        while True:
            q.get()
            time.sleep(0.3)  # debounce bursts (saves, formatters, git checkouts)
            while not q.empty():
                q.get_nowait()
            _g, _idx, n = build(root, only_changed=True)
            if n:
                yield f"[graphscout] refreshed {n} file(s)"
    finally:
        observer.stop()
        observer.join()
