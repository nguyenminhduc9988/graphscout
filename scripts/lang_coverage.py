"""Measure real cross-file def/call resolution per language, instead of
hand-copying a language count into the README. For each language, writes a
two-file sample (a definition file + a caller file importing/qualifying it)
into a scratch dir, builds a graphscout graph over it, and checks whether a
`calls` edge links the caller to the callee across the file boundary — the
same bar colbymchenry/codegraph's "34 languages with full extraction" claim
implies. Run: `python scripts/lang_coverage.py` (needs the repo's own venv).
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from graphscout import core  # noqa: E402

# name -> {defs: {relpath: contents}, caller: relpath, callee_label_substr}
SAMPLES = {
    "python": {
        "a.py": 'def helper(x):\n    return x + 1\n',
        "main.py": 'from a import helper\n\n\ndef run():\n    return helper(2)\n',
    },
    "javascript": {
        "a.js": 'function helper(x) { return x + 1; }\nmodule.exports = { helper };\n',
        "main.js": 'const { helper } = require("./a");\nfunction run() { return helper(2); }\n',
    },
    "typescript": {
        "a.ts": 'export function helper(x: number): number { return x + 1; }\n',
        "main.ts": 'import { helper } from "./a";\nfunction run() { return helper(2); }\n',
    },
    "go": {
        "a.go": 'package main\nfunc Helper(x int) int { return x + 1 }\n',
        "main.go": 'package main\nfunc run() int { return Helper(2) }\n',
    },
    "rust": {
        "a.rs": 'pub fn helper(x: i32) -> i32 { x + 1 }\n',
        "main.rs": 'use crate::a::helper;\nfn run() -> i32 { helper(2) }\n',
    },
    "java": {
        "A.java": 'class A {\n    static int helper(int x) { return x + 1; }\n}\n',
        "Main.java": 'class Main {\n    int run() { return A.helper(2); }\n}\n',
    },
    "csharp": {
        "A.cs": 'class A {\n    public static int Helper(int x) { return x + 1; }\n}\n',
        "Main.cs": 'class Program {\n    int Run() { return A.Helper(2); }\n}\n',
    },
    "ruby": {
        "a.rb": 'def helper(x)\n  x + 1\nend\n',
        "main.rb": 'require_relative "a"\ndef run\n  helper(2)\nend\n',
    },
    "php": {
        "a.php": '<?php\nfunction helper($x) { return $x + 1; }\n',
        "main.php": '<?php\nrequire_once "a.php";\nfunction run() { return helper(2); }\n',
    },
    "c": {
        "a.h": 'int helper(int x);\n',
        "a.c": '#include "a.h"\nint helper(int x) { return x + 1; }\n',
        "main.c": '#include "a.h"\nint run() { return helper(2); }\n',
    },
    "cpp": {
        "a.hpp": 'int helper(int x);\n',
        "a.cpp": '#include "a.hpp"\nint helper(int x) { return x + 1; }\n',
        "main.cpp": '#include "a.hpp"\nint run() { return helper(2); }\n',
    },
    "kotlin": {
        "A.kt": 'fun helper(x: Int): Int { return x + 1 }\n',
        "Main.kt": 'fun run(): Int { return helper(2) }\n',
    },
    "swift": {
        "A.swift": 'func helper(_ x: Int) -> Int { return x + 1 }\n',
        "Main.swift": 'func run() -> Int { return helper(2) }\n',
    },
    "dart": {
        "a.dart": 'int helper(int x) { return x + 1; }\n',
        "main.dart": 'import "a.dart";\nint run() { return helper(2); }\n',
    },
    "scala": {
        "A.scala": 'object A {\n  def helper(x: Int): Int = x + 1\n}\n',
        "Main.scala": 'object Main {\n  def run(): Int = A.helper(2)\n}\n',
    },
    "lua": {
        "a.lua": 'function helper(x)\n  return x + 1\nend\nreturn { helper = helper }\n',
        "main.lua": 'local a = require("a")\nfunction run()\n  return a.helper(2)\nend\n',
    },
    "elixir": {
        "a.ex": 'defmodule A do\n  def helper(x), do: x + 1\nend\n',
        "main.ex": 'defmodule Main do\n  def run, do: A.helper(2)\nend\n',
    },
    "groovy": {
        "A.groovy": 'class A {\n    static int helper(int x) { return x + 1 }\n}\n',
        "Main.groovy": 'class Main {\n    int run() { return A.helper(2) }\n}\n',
    },
    "vue": {
        "a.js": 'export function helper(x) { return x + 1; }\n',
        "Main.vue": '<script>\nimport { helper } from "./a";\n'
                    'export default { methods: { run() { return helper(2); } } };\n</script>\n',
    },
    "powershell": {
        "a.ps1": 'function Helper($x) { return $x + 1 }\n',
        "main.ps1": '. ./a.ps1\nfunction Run { return Helper 2 }\n',
    },
}

# Languages verified (by hand, same method as above) to produce zero `calls`
# edges even within a single file with this graphify version — not a sample
# mistake, a real current gap. Listed so the README's language count is
# honest about what's *not* covered yet, not just what is.
KNOWN_GAPS_NO_CALL_RESOLUTION = ("dart", "scala", "lua", "elixir")


def check(name: str, files: dict) -> tuple:
    tmp = Path(tempfile.mkdtemp(prefix=f"gs_lang_{name}_"))
    # graphify's own AST cache is keyed by file content and written to a path
    # relative to the process CWD by default (`graphify-out/`), not the repo
    # being built — so two builds from the same CWD with byte-identical
    # sample content (plausible here: several languages share the trivial
    # "helper(x) -> x+1" body) would silently share cache entries and leak
    # stale node ids from one build into another. Point it inside this test's
    # own scratch dir so every language gets an isolated cache.
    env_bak = os.environ.get("GRAPHIFY_OUT")
    os.environ["GRAPHIFY_OUT"] = str(tmp / ".graphify-out")
    try:
        (tmp / ".git").mkdir()
        for rel, content in files.items():
            (tmp / rel).write_text(content)
        _g, _idx, n = core.build(tmp)
        graph, _idx = core.load(tmp)
        byid = {node["id"]: node for node in graph["nodes"]}
        calls = [e for e in graph["edges"] if e.get("relation") == "calls"]
        cross_file = [
            e for e in calls
            if byid.get(e["source"], {}).get("source_file")
            and byid.get(e["target"], {}).get("source_file")
            and byid[e["source"]]["source_file"] != byid[e["target"]]["source_file"]
        ]
        return n, len(graph["nodes"]), len(calls), len(cross_file)
    finally:
        if env_bak is None:
            os.environ.pop("GRAPHIFY_OUT", None)
        else:
            os.environ["GRAPHIFY_OUT"] = env_bak
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    print(f"{'language':<12} {'files':>5} {'nodes':>6} {'calls':>6} {'cross-file calls':>17}")
    full, partial, none = [], [], []
    for name, files in SAMPLES.items():
        n_files, n_nodes, n_calls, n_cross = check(name, files)
        tag = "FULL" if n_cross else ("SAME-FILE ONLY" if n_calls else "NO CALLS")
        (full if n_cross else (partial if n_calls else none)).append(name)
        print(f"{name:<12} {n_files:>5} {n_nodes:>6} {n_calls:>6} {n_cross:>17}   {tag}")
    print(f"\n{len(full)}/{len(SAMPLES)} sampled languages resolve cross-file calls: {', '.join(sorted(full))}")
    if partial:
        print(f"same-file only: {', '.join(sorted(partial))}")
    if none:
        print(f"no call edges at all: {', '.join(sorted(none))}")


if __name__ == "__main__":
    main()
