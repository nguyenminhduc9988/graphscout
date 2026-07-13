<div align="center">

<img src="assets/hero.svg" alt="graphscout — query code structure, don't read whole files" width="900"/>

<br/>

[![PyPI](https://img.shields.io/pypi/v/graphscout.svg?color=7ee7c7&label=PyPI&logo=pypi&logoColor=white)](https://pypi.org/project/graphscout/)
[![Python](https://img.shields.io/pypi/pyversions/graphscout.svg?color=7ee7c7&logo=python&logoColor=white)](https://pypi.org/project/graphscout/)
[![CI](https://github.com/nguyenminhduc9988/graphscout/actions/workflows/ci.yml/badge.svg)](https://github.com/nguyenminhduc9988/graphscout/actions/workflows/ci.yml)
[![Downloads](https://img.shields.io/pypi/dm/graphscout.svg?color=a5b4fc&logo=pypi&logoColor=white)](https://pypi.org/project/graphscout/)
[![License: MIT](https://img.shields.io/badge/License-MIT-c4b5fd.svg)](LICENSE)
[![MCP](https://img.shields.io/badge/MCP-ready-7ee7c7.svg?logo=anthropic&logoColor=white)](#-mcp-wired-automatically)
[![Stars](https://img.shields.io/github/stars/nguyenminhduc9988/graphscout.svg?style=social)](https://github.com/nguyenminhduc9988/graphscout/stargazers)

[![Typing SVG](https://readme-typing-svg.demolab.com/?font=Fira+Code&weight=600&size=19&pause=1000&color=7EE7C7&center=true&vCenter=true&width=720&lines=Where+is+this+defined%3F+%E2%80%94+one+call.;Who+calls+this%3F+%E2%80%94+one+call.;What+breaks+if+I+change+it%3F+%E2%80%94+one+call.;Cached+~+incremental+~+zero+API+keys.)](https://github.com/nguyenminhduc9988/graphscout)

<br/>

<a href="#-install"><img src="https://img.shields.io/badge/⚡_Install-pip_install_graphscout-7ee7c7?style=for-the-badge&labelColor=0d1117" alt="Install"/></a>
&nbsp;
<a href="#-mcp-wired-automatically"><img src="https://img.shields.io/badge/🔌_Wire_MCP-graphscout_install-a5b4fc?style=for-the-badge&labelColor=0d1117" alt="Wire MCP"/></a>
&nbsp;
<a href="#-benchmark"><img src="https://img.shields.io/badge/📊_Benchmark-75%25_fewer_calls-c4b5fd?style=for-the-badge&labelColor=0d1117" alt="Benchmark"/></a>

</div>

---

<div align="center">
<img src="assets/demo.svg" alt="graphscout explore / search / impact demo — real, verified CLI output" width="900"/>
<br/>
<sub>▲ Real captured CLI output, not mockups — every line comes from an actual <code>graphscout</code> run on this repo.</sub>
</div>

<br/>

> **The problem.** AI agents burn most of their tokens *reading source files* to answer structural questions — *"where is this defined?"*, *"who calls this?"*, *"what breaks if I change it?"*
>
> **The fix.** `graphscout` answers those from a cached tree-sitter AST graph in milliseconds — and **`explore` returns the matching symbols' verbatim source plus their call edges and blast radius in one call**, so the agent usually doesn't need a follow-up `Read` at all.

<div align="center">
<img src="assets/stats.svg" alt="75% fewer tool calls · 1 call vs 4 · 21/21 tests passing" width="900"/>
<br/>
<sup>Measured, not asserted — see <a href="#-benchmark">Benchmark</a> for methodology and how to reproduce it yourself.</sup>
</div>

<br/>

One `build` per repo; after that, every query auto-refreshes only the files that changed since the last call (mtime-based). No forced background process, no external database, no API keys — a JSON cache under `~/.cache/graphscout`, plus an in-memory SQLite FTS5 index built on demand for search. Want it always fresh with zero per-query overhead instead? Run `graphscout watch`.

<sub>Formerly published as <code>codegraph-kit</code> (repo <code>codegraph</code>) — renamed to avoid confusion with the unrelated, much larger <a href="https://github.com/colbymchenry/codegraph">colbymchenry/codegraph</a>. Same cache format (<code>$CODEGRAPH_CACHE</code> still works). See <a href="#-honest-comparison-vs-colbymchenrycodegraph">Honest comparison</a> for exactly where each tool wins.</sub>

## 🎯 The one idea: blast radius in one call

The single most expensive question an agent asks is *"what breaks if I change this?"* — normally answered by reading file after file. `graphscout impact` walks the call graph both directions and hands back the **whole** affected set, ranked by depth:

<div align="center">
<img src="assets/blast.svg" alt="Animated blast-radius ripple — change one symbol, see everything it touches at depth 1 and depth 2" width="900"/>
</div>

```bash
graphscout impact parse_config --depth=2      # what breaks if I change this?
git diff --name-only | graphscout affected --stdin   # which tests does my diff touch?
```

No dynamic-dispatch guesswork dressed up as certainty — edges come from static AST analysis, and the honest limits are printed right in the output when they apply.

## 📦 Install

```bash
pip install graphscout          # CLI
pip install "graphscout[mcp]"   # CLI + MCP server
pip install "graphscout[watch]" # CLI + instant filesystem-event auto-sync
```

Python ≥ 3.10. Parsing is done by [graphify](https://pypi.org/project/graphifyy/) (tree-sitter).

<div align="center">
<img src="assets/langs.svg" alt="Language coverage — 13 with full def/call/import extraction, 40+ more with outline structure" width="900"/>
</div>

## ✨ What you get

<table>
<tr>
<td width="50%" valign="top">

### 🔎 `explore` — one call, not four
Verbatim source + callers/callees + multi-hop blast radius for the top-matching symbols. The shape an agent actually needs, instead of chaining `sym → file → callers → Read`.

</td>
<td width="50%" valign="top">

### 🧠 Ranked full-text search
In-memory SQLite FTS5 (bm25, prefix, multi-term) — not a plain substring scan — and docstring nodes are excluded so real symbols aren't drowned out by prose.

</td>
</tr>
<tr>
<td width="50%" valign="top">

### 💥 Multi-hop impact analysis
Bidirectional BFS over the call graph, depth-bounded — the *actual* blast radius of a change, not a one-hop callers/callees guess.

</td>
<td width="50%" valign="top">

### 🧪 Test-impact from a diff
`git diff --name-only \| graphscout affected --stdin` — which tests does this change touch, traced through resolved imports.

</td>
</tr>
<tr>
<td width="50%" valign="top">

### 🙈 `.gitignore`-aware indexing
Routes through `git ls-files --exclude-standard` — nested `.gitignore`s and the global excludes file honored exactly as git sees them. Hard-coded skips (`node_modules`, `dist`, …) apply regardless.

</td>
<td width="50%" valign="top">

### ⚡ Incremental, mtime-based cache
One `build` per repo; every query re-extracts only what changed. No database server, no daemon required — `graphscout watch` is opt-in.

</td>
</tr>
<tr>
<td width="50%" valign="top">

### ⚙️ `exclude` / `include` / `extensions`
Optional `graphscout.json` — force a vendored path back in, drop noisy generated code, or map a non-standard suffix onto a supported language.

</td>
<td width="50%" valign="top">

### 🔌 CLI *and* MCP
Same queries either way — shell out from any agent, or wire the MCP server into Claude Code, Codex, Gemini CLI, and Cursor with one command.

</td>
</tr>
</table>

## 🧭 Commands

| Command | What it answers |
|---|---|
| `graphscout explore <query> [dir]` | **start here** — verbatim source + call edges + blast radius, one call |
| `graphscout search <query> [dir]` | ranked full-text symbol search (FTS5, multi-term, prefix) |
| `graphscout impact <name> [dir]` | multi-hop blast radius before changing a symbol (`--depth`) |
| `graphscout affected <file...>` | test files transitively affected by changed files (`--stdin`, `--depth`) |
| `graphscout map [dir]` | repo overview: size, per-directory breakdown, top hub symbols |
| `graphscout file <path>` | outline of one file: definitions + line ranges |
| `graphscout sym <name>` | where is this symbol defined? (plain substring match) |
| `graphscout callers <name>` / `callees <name>` | who calls it / what does it call (one hop) |
| `graphscout deps <path>` | what does this file import? |
| `graphscout build [dir]` / `ensure [dir]` | full build (once per repo) / incremental refresh (automatic) |
| `graphscout watch [dir]` | block, keeping the graph in sync as files change |
| `graphscout touch <path>` | re-extract one file (for editor/agent hooks) |
| `graphscout agent` | print an instruction snippet for your agent's context file |
| `graphscout install [agent...]` / `uninstall` | wire (or remove) the MCP server for detected agents |
| `graphscout mcp` | run as an MCP server (stdio) |

## 🏗️ How it works

<div align="center">
<img src="assets/flow.svg" alt="build → tree-sitter extraction → graph.json cache → query, with data flowing through the pipeline" width="900"/>
</div>

<details>
<summary><b>📐 The same pipeline as a Mermaid diagram</b> (click to expand)</summary>

<br/>

```mermaid
flowchart LR
    A["build / ensure"] -->|"git ls-files --exclude-standard<br/>(honors .gitignore)"| B["tree-sitter extraction<br/>via graphify"]
    B --> C[("graph.json<br/>+ mtime index<br/>~/.cache/graphscout")]
    C --> D{"query"}
    D -->|explore| E["verbatim source<br/>+ call edges<br/>+ blast radius"]
    D -->|search| F["FTS5 ranked<br/>symbol search"]
    D -->|impact / affected| G["multi-hop BFS<br/>over call / import edges"]
    style E fill:#7ee7c7,color:#0d1117
    style F fill:#a5b4fc,color:#0d1117
    style G fill:#a5b4fc,color:#0d1117
```

</details>

Every query calls `ensure` first — files whose mtime changed are re-extracted and spliced into the graph; deleted files drop out. Typical refresh touches a handful of files, so queries stay fast. Output is deliberately plain text with `file:line` locations — clickable in most agent UIs, trivially parseable by the rest. Set `GRAPHSCOUT_CACHE` to relocate the cache (useful in CI and sandboxes).

## 🤖 Integrate with any agent

`graphscout` is plain CLI-over-stdout, so **any agent that can run shell commands can use it** — Claude Code, Codex CLI, Cursor, Aider, OpenHands, Goose, custom agents.

**1. Tell the agent the graph exists:**

```bash
graphscout agent >> AGENTS.md      # or CLAUDE.md, .cursorrules, .github/copilot-instructions.md
```

**2. (Optional) Keep the graph fresh on every edit.** For Claude Code, install the bundled PostToolUse hook so each `Edit`/`Write` re-extracts just that file:

```bash
cp integrations/claude-code/graphscout-touch.sh ~/.claude/hooks/
chmod +x ~/.claude/hooks/graphscout-touch.sh
# then merge integrations/claude-code/settings-snippet.json into ~/.claude/settings.json
```

Even without a hook, queries stay correct — every query runs an mtime check first and re-extracts anything stale.

### 🔌 MCP, wired automatically

```bash
pip install "graphscout[mcp]"
graphscout install          # auto-detects and wires every agent found on PATH
graphscout install cursor   # or target specific agents: claude-code, codex, gemini, cursor
graphscout uninstall        # reverse it
```

`install` shells out to each agent's own `mcp add` command where one exists (Claude Code, Codex CLI, Gemini CLI — verified against their real CLIs, not guessed), and edits `~/.cursor/mcp.json` directly for Cursor. Idempotent — safe to re-run.

Tools exposed: `explore` (lead with this), `search`, `impact`, `affected`, `build_graph`, `graph_map`, `file_outline`, `find_symbol`, `callers`, `callees`, `file_deps`. The server's `instructions` steer the agent to `explore` first — one strong tool beats a menu of narrow ones.

### 🔄 Auto-sync (optional)

```bash
graphscout watch          # blocks, keeps the graph in sync as you/your agent edit files
```

Uses [watchdog](https://pypi.org/project/watchdog/) for instant, low-CPU filesystem events when installed; falls back to a ~1.5s mtime poll otherwise. Alternative to the per-edit `touch` hook — run one or the other, not both.

## 🙈 Excludes, includes, custom extensions

Zero-config by default. Hard-coded skips (`.git`, `node_modules`, `venv`/`.venv`, `dist`, `build`, `target`, `vendor`, …) always apply, regardless of `.gitignore`. In a git repo, `.gitignore` is also honored via `git ls-files --exclude-standard` — nested `.gitignore`s and the global excludes file included, exactly as git itself sees them.

To go further, drop a `graphscout.json` at the repo root:

```json
{
  "exclude": ["static/", "**/generated/**"],
  "include": ["third_party/vendored_dep/"],
  "extensions": {".tpl": "php"}
}
```

`exclude` wins over everything, including `include`; `include` pulls a `.gitignore`d path back in but can't override the hard skip list. `extensions` maps a non-standard suffix onto a language graphify already parses.

## 📊 Benchmark

`python scripts/benchmark.py <repo>` compares the old `sym`+`file`+`callers`+`callees` workflow (4 calls, no verbatim source — a `Read` would still follow) against `explore` (1 call, source included), on real symbols picked from the target repo's own call graph. **Not a live-agent trial** — a reproducible, offline proxy anyone can re-run:

| Repo | Old calls → new | Old payload → new |
|---|---|---|
| this repo (119 nodes) | 20 → 5 (**75% fewer**) | 10,405 → 6,722 chars |
| [pallets/click](https://github.com/pallets/click) (1,803 nodes) | 20 → 5 (**75% fewer**) | 37,125 → 8,380 chars |

Call-count savings are structural (4 calls collapse to 1 regardless of repo size); payload savings vary with how verbose the old path's raw listings are versus one focused snippet.

<details>
<summary><b>⚠️ Honest limitations</b> (also printed in the output when they apply)</summary>

<br/>

- **Dynamic dispatch isn't captured** — call edges come from static AST analysis; `getattr`-style calls need grep.
- **Unsupported/exotic languages** fall back to "read it directly".
- **`affected` under-detects on multi-name absolute imports** — `from pkg import a, b` resolves to one edge on the package, not per-name. Relative imports and single-name absolute imports resolve fully.
- **No line ranges from the extractor** — graphify records a start line per symbol, not start+end; `explore`'s snippet end is inferred as "the line before the next symbol in the same file" (capped at 60 lines).
- **Caps:** 5,000 files per repo, 1 MB per file (warned, not silent); blast-radius/impact traversal stops at 400 nodes (flagged `(truncated)`).

</details>

## ⚖️ Honest comparison vs. colbymchenry/codegraph

[colbymchenry/codegraph](https://github.com/colbymchenry/codegraph) is a funded, actively-developed product — 59k+ stars, a Node/TypeScript codebase with bundled runtime, measured cross-file coverage per language, and real published agent benchmarks. `graphscout` is a ~1,100-line single-purpose Python tool. Here's where each one actually wins:

| | graphscout | colbymchenry/codegraph |
|---|:---:|:---:|
| Verbatim source + call edges + blast radius, one call | ✅ `explore` | ✅ `codegraph_explore` |
| Ranked full-text search (FTS5) | ✅ | ✅ |
| Multi-hop impact / blast radius | ✅ | ✅ |
| Test-impact from a diff | ✅ `affected` | ✅ `affected` |
| `.gitignore`-aware indexing | ✅ | ✅ |
| Languages with full def/call/import extraction | 13 | **34** |
| Framework route detection | ❌ | ✅ **17 frameworks** |
| Cross-language bridging (Swift↔ObjC, React Native, Expo) | ❌ | ✅ |
| Native OS file-watch daemon | optional (`watchdog`) | ✅ built-in |
| Runtime | pure Python (no bundled runtime) | bundled Node.js runtime |
| Telemetry | **none, ever** | opt-out, anonymized |
| Published agent benchmarks | offline call/payload proxy (above) | live Claude-Code trials across 7 repos |
| License | MIT | MIT |

If you need 34-language coverage, iOS/React-Native bridging, or framework route detection, use codegraph. If you want a small, auditable, telemetry-free tool that does one thing — make every structural query self-sufficient for an agent — that's what `graphscout` optimizes for.

## 📈 Star history

<div align="center">
<a href="https://www.star-history.com/#nguyenminhduc9988/graphscout&Date">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=nguyenminhduc9988/graphscout&type=Date&theme=dark" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=nguyenminhduc9988/graphscout&type=Date" />
   <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=nguyenminhduc9988/graphscout&type=Date" width="720"/>
 </picture>
</a>
</div>

---

<div align="center">

**If `graphscout` saved your agent a few thousand tokens, drop a ⭐ — it's the whole marketing budget.**

<br/>

<a href="https://github.com/nguyenminhduc9988/graphscout/stargazers"><img src="https://img.shields.io/badge/⭐_Star_this_repo-0d1117?style=for-the-badge&logo=github" alt="Star"/></a>
&nbsp;
<a href="https://pypi.org/project/graphscout/"><img src="https://img.shields.io/badge/📦_pip_install_graphscout-7ee7c7?style=for-the-badge&labelColor=0d1117" alt="pip install"/></a>
&nbsp;
<a href="https://github.com/nguyenminhduc9988/graphscout/issues"><img src="https://img.shields.io/badge/🐛_Report_an_issue-a5b4fc?style=for-the-badge&labelColor=0d1117" alt="Issues"/></a>

<br/><br/>

<sub>MIT © <a href="https://github.com/nguyenminhduc9988">Duc Nguyen</a> · Parsing by <a href="https://pypi.org/project/graphifyy/">graphify</a> (tree-sitter) · Built for agents, auditable by humans</sub>

</div>
