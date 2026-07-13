#!/usr/bin/env python3
"""Before/after benchmark for the `explore` consolidation (added in 0.3.0).

This is NOT a live-agent trial (no LLM is run) — it's a reproducible proxy
metric anyone can re-run offline: for a handful of symbol queries, compare
the OLD workflow (sym -> file -> callers -> callees as four separate calls,
none of which return verbatim source) against the NEW `explore` (one call,
verbatim source included). It measures call count and whether the response
is self-sufficient (has source) or would still need a follow-up Read.

Usage:
    python scripts/benchmark.py [repo_dir] [query ...]

With no queries given, it picks a handful of the graph's own top hub symbols.
"""
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from graphscout import core, queries  # noqa: E402


def old_path(root, g, query):
    calls = 0
    out = []
    out.append(queries.q_sym(root, g, query)); calls += 1
    hit_line = next((l for l in out[-1].splitlines() if query.lower() in l.lower()), None)
    if hit_line:
        path = hit_line.split()[-1].split(":")[0]
        out.append(queries.q_file(root, g, root / path)); calls += 1
    out.append(queries.q_calls(root, g, query, "callers")); calls += 1
    out.append(queries.q_calls(root, g, query, "callees")); calls += 1
    text = "\n".join(out)
    return calls, len(text), "def " in text or "class " in text  # has verbatim source?


def new_path(root, g, query):
    t0 = time.perf_counter()
    text = queries.q_explore(root, g, query, limit=5, depth=2)
    dt = time.perf_counter() - t0
    return 1, len(text), ("```" in text), dt


def default_queries(g, n=5):
    deg = Counter()
    for e in g["edges"]:
        if e.get("relation") == "calls":
            deg[e["source"]] += 1
            deg[e["target"]] += 1
    byid = {node["id"]: node for node in g["nodes"]}
    picks = []
    for nid, _ in deg.most_common(50):
        node = byid.get(nid)
        if node and node.get("file_type") != "rationale":
            label = node.get("label", "").rstrip("()")
            if label and label not in picks:
                picks.append(label)
        if len(picks) >= n:
            break
    return picks


def main():
    args = sys.argv[1:]
    root = core.find_root(Path(args[0]).resolve()) if args and Path(args[0]).exists() else core.find_root(Path.cwd())
    queries_arg = [a for a in args[1:]] or None
    g, _idx, _n = core.build(root)
    qs = queries_arg or default_queries(g)
    if not qs:
        print("no symbols found to benchmark"); return

    print(f"benchmarking {root} — {len(g['nodes'])} nodes, {len(g['edges'])} edges")
    print(f"{'query':<20} {'old calls':>10} {'old chars':>10} {'old src?':>9} "
          f"{'new calls':>10} {'new chars':>10} {'new src?':>9}")
    tot_old_calls = tot_new_calls = tot_old_chars = tot_new_chars = 0
    for q in qs:
        oc, ochars, osrc = old_path(root, g, q)
        nc, nchars, nsrc, _dt = new_path(root, g, q)
        tot_old_calls += oc; tot_new_calls += nc
        tot_old_chars += ochars; tot_new_chars += nchars
        print(f"{q:<20} {oc:>10} {ochars:>10} {str(osrc):>9} {nc:>10} {nchars:>10} {str(nsrc):>9}")

    n = len(qs)
    print(f"\ntotals over {n} queries: calls {tot_old_calls} -> {tot_new_calls} "
          f"({100 * (1 - tot_new_calls / tot_old_calls):.0f}% fewer), "
          f"payload {tot_old_chars} -> {tot_new_chars} chars")
    print("old path never returns verbatim source (a Read call would still follow); "
          "new path always does when a snippet is found.")


if __name__ == "__main__":
    main()
