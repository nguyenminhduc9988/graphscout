"""Symbol-level diff between two points in git history (or a ref and the
working tree) — "what functions/classes did this change actually add, remove,
or touch", not a line-based `git diff`. Extraction runs in-memory against
each ref's blob content directly (via `git show`), independent of the
on-disk incremental cache, so it works for any two refs regardless of what's
currently checked out.
"""
import subprocess
from pathlib import Path

from .analysis import _is_file_root, node_spans
from .core import CODE_EXTS


def _run(args, cwd=None, timeout=30):
    r = subprocess.run(args, cwd=cwd, capture_output=True, timeout=timeout)
    return r.returncode, r.stdout, r.stderr


def changed_files(root: Path, ref1: str, ref2: str = None) -> list:
    args = ["git", "-C", str(root), "diff", "--name-only", ref1] + ([ref2] if ref2 else [])
    rc, out, err = _run(args)
    if rc != 0:
        raise ValueError((err or out).decode(errors="replace").strip() or f"git diff failed for {ref1}")
    return [line for line in out.decode(errors="replace").splitlines() if line.strip()]


def _blob_at(root: Path, ref: str, relpath: str):
    """Bytes of relpath at ref, or None if it didn't exist there (added/deleted)."""
    rc, out, _err = _run(["git", "-C", str(root), "show", f"{ref}:{relpath}"])
    return out if rc == 0 else None


def _working_tree(root: Path, relpath: str):
    p = root / relpath
    try:
        return p.read_bytes()
    except OSError:
        return None


def _extract_bytes(content: bytes, suffix: str):
    """Extract nodes for one blob in isolation — a temp file with the right
    suffix so graphify's language detection matches, no repo context (import
    resolution across files isn't meaningful for a single historical blob
    anyway; this is about spotting def-level additions/removals/edits)."""
    import tempfile
    from graphify.extract import extract
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / f"blob{suffix}"
        p.write_bytes(content)
        try:
            r = extract(paths=[p], parallel=False)
        except Exception:
            return []
        return [n for n in r["nodes"] if n.get("file_type") != "rationale" and not _is_file_root(n)]


def _snippet(nodes, text: bytes, node, max_lines=60):
    """Verbatim body used for the added/removed/modified comparison. The
    inferred end line (the line before the *next* node's start — see
    node_spans) shifts whenever a neighboring symbol is added or removed
    nearby, even though this symbol's own body didn't change; trailing blank
    lines are stripped so that boundary noise doesn't register as a `~`."""
    spans = node_spans({"nodes": nodes})
    start, nxt = spans.get(node["id"], (None, None))
    if not start:
        return ""
    lines = text.decode(errors="replace").splitlines()
    if start - 1 >= len(lines):
        return ""
    end = min(nxt - 1 if nxt else start + max_lines - 1, start + max_lines - 1, len(lines))
    body = lines[start - 1:end]
    while body and not body[-1].strip():
        body.pop()
    return "\n".join(body)


def diff_symbols(root: Path, ref1: str, ref2: str = None, paths=None) -> list:
    """Per-file added/removed/modified top-level symbols between ref1 and
    (ref2 or the working tree). Returns a list of dicts, one per file that
    has at least one symbol-level change; files outside CODE_EXTS or where
    neither side parses to any symbols are skipped."""
    files = paths if paths is not None else changed_files(root, ref1, ref2)
    out = []
    for rel in files:
        if Path(rel).suffix not in CODE_EXTS:
            continue
        old_bytes = _blob_at(root, ref1, rel)
        new_bytes = _blob_at(root, ref2, rel) if ref2 else _working_tree(root, rel)
        old_nodes = _extract_bytes(old_bytes, Path(rel).suffix) if old_bytes is not None else []
        new_nodes = _extract_bytes(new_bytes, Path(rel).suffix) if new_bytes is not None else []
        old_by_label = {n["label"]: n for n in old_nodes if n.get("label")}
        new_by_label = {n["label"]: n for n in new_nodes if n.get("label")}
        added = sorted(set(new_by_label) - set(old_by_label))
        removed = sorted(set(old_by_label) - set(new_by_label))
        modified = []
        for label in sorted(set(old_by_label) & set(new_by_label)):
            old_snip = _snippet(old_nodes, old_bytes, old_by_label[label])
            new_snip = _snippet(new_nodes, new_bytes, new_by_label[label])
            if old_snip != new_snip:
                modified.append(label)
        if added or removed or modified:
            out.append({
                "file": rel, "added": added, "removed": removed, "modified": modified,
                "new_lines": {n["label"]: n.get("source_location", "?") for n in new_nodes},
                "old_lines": {n["label"]: n.get("source_location", "?") for n in old_nodes},
            })
    return out


def format_diff(results: list, ref1: str, ref2: str = None) -> str:
    label = f"{ref1}..{ref2}" if ref2 else f"{ref1}..working tree"
    if not results:
        return f"no symbol-level changes between {label} (in files graphscout indexes)"
    lines = [f"symbol-level diff {label}:"]
    for r in results:
        lines.append(f"\n{r['file']}")
        for sym in r["added"]:
            lines.append(f"  + {sym}  [{r['new_lines'].get(sym, '?')}]")
        for sym in r["removed"]:
            lines.append(f"  - {sym}  [{r['old_lines'].get(sym, '?')}]")
        for sym in r["modified"]:
            lines.append(f"  ~ {sym}  [{r['old_lines'].get(sym, '?')} -> {r['new_lines'].get(sym, '?')}]")
    added = sum(len(r["added"]) for r in results)
    removed = sum(len(r["removed"]) for r in results)
    modified = sum(len(r["modified"]) for r in results)
    lines.append(f"\n{added} added, {removed} removed, {modified} modified across {len(results)} file(s)")
    return "\n".join(lines)
