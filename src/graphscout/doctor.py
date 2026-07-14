"""Environment/capability check — `graphscout doctor`. Answers "why didn't
search rank anything" or "why does watch just poll" without reading source:
every optional codepath graphscout has a fallback for gets one line here,
present or not.
"""
import json
import shutil
import sqlite3
import sys
from pathlib import Path

from . import __version__, core


def _check_fts5() -> tuple:
    try:
        con = sqlite3.connect(":memory:")
        con.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        con.close()
        return True, "search() will use ranked bm25 matching"
    except sqlite3.OperationalError:
        return False, "search() falls back to substring scoring (no ranking)"


def _check_module(name: str, extra_hint: str) -> tuple:
    try:
        __import__(name)
        return True, ""
    except ImportError:
        return False, f"pip install {extra_hint}"


def _check_git() -> tuple:
    return shutil.which("git") is not None, "used for .gitignore-aware indexing, diff, hotspots"


def checks(directory: str = ".") -> dict:
    """The raw check results, JSON-able — `run()` below just formats these."""
    items = [
        {"name": "sqlite3 FTS5", "ok": (r := _check_fts5())[0], "note": r[1]},
        {"name": "git on PATH", "ok": (r := _check_git())[0], "note": r[1]},
        {"name": "mcp (server)", "ok": (r := _check_module("mcp", '"graphscout[mcp]"'))[0], "note": r[1]},
        {"name": "watchdog (instant watch)",
         "ok": (r := _check_module("watchdog", '"graphscout[watch]"'))[0], "note": r[1]},
        {"name": "tiktoken (exact tokens)",
         "ok": (r := _check_module("tiktoken", '"tiktoken"'))[0],
         "note": r[1] or "tokens() is exact (cl100k)"},
    ]
    try:
        import graphify  # noqa: F401
        from importlib.metadata import version as _v
        items.append({"name": "graphify (parser)", "ok": True, "note": f"graphifyy {_v('graphifyy')}"})
    except Exception as e:
        items.append({"name": "graphify (parser)", "ok": False, "note": f"import failed: {e}"})

    root = core.find_root(Path(directory).resolve())
    graph, idx = core.load(root)
    repo = {"root": str(root), "built": graph is not None}
    if graph is not None:
        repo.update(nodes=len(graph["nodes"]), edges=len(graph["edges"]),
                     built_at=idx.get("built"), failed=idx.get("failed") or [])

    n_roots = None
    rf = core.roots_file()
    if rf.exists():
        try:
            n_roots = len(json.loads(rf.read_text()))
        except Exception:
            pass

    return {"version": __version__, "python": sys.version.split()[0], "checks": items,
            "indexable_extensions": len(core.CODE_EXTS), "repo": repo,
            "cache_dir": str(core.cache_dir()), "registered_repos": n_roots}


def run(directory: str = ".") -> str:
    d = checks(directory)
    lines = [f"graphscout {d['version']}  (Python {d['python']})"]
    for c in d["checks"]:
        mark = "OK  " if c["ok"] else "MISS"
        lines.append(f"  [{mark}] {c['name']}" + (f" — {c['note']}" if c["note"] else ""))

    lines.append(f"\n{d['indexable_extensions']} indexable extensions "
                 "(graphify's live list + graphscout's floor)")

    repo = d["repo"]
    lines.append(f"\nrepo: {repo['root']}")
    if not repo["built"]:
        lines.append("  no graph built yet — run `graphscout build`")
    else:
        lines.append(f"  {repo['nodes']} nodes, {repo['edges']} edges, built {repo.get('built_at', '?')}")
        if repo["failed"]:
            lines.append(f"  {len(repo['failed'])} file(s) failed extraction last build: {repo['failed'][:5]}")

    if d["registered_repos"] is not None:
        lines.append(f"\n{d['registered_repos']} repo(s) registered under {d['cache_dir']}")
    return "\n".join(lines)
