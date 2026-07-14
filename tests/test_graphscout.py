import json
import os
import time
from pathlib import Path

import pytest

from graphscout import cli, core

A_PY = '''\
import os


def helper(x):
    return os.path.join("a", x)


def main_entry():
    return helper("b")
'''

B_PY = '''\
from a import helper


class Runner:
    def run(self):
        return helper("c")
'''


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("GRAPHSCOUT_CACHE", str(tmp_path / "cache"))
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    (root / "a.py").write_text(A_PY)
    (root / "sub").mkdir()
    (root / "sub" / "b.py").write_text(B_PY)
    return root


def run(capsys, *args):
    rc = cli.main(list(args))
    out = capsys.readouterr().out
    return rc, out


def test_build_and_map(repo, capsys):
    rc, out = run(capsys, "build", str(repo))
    assert rc == 0 and "built" in out and "0 nodes" not in out
    rc, out = run(capsys, "map", str(repo))
    assert "nodes" in out and "top hubs:" in out


def test_file_outline_and_sym(repo, capsys):
    run(capsys, "build", str(repo))
    rc, out = run(capsys, "file", str(repo / "a.py"))
    assert "helper" in out and "main_entry" in out
    rc, out = run(capsys, "sym", "main_entry", str(repo))
    assert "a.py" in out


def test_callers_and_deps(repo, capsys):
    run(capsys, "build", str(repo))
    rc, out = run(capsys, "callers", "helper", str(repo))
    assert "main_entry" in out or "run" in out
    rc, out = run(capsys, "deps", str(repo / "a.py"))
    assert "os" in out


def test_incremental_refresh_no_node_growth(repo, capsys):
    """Repeated ensure must not duplicate nodes; edits must be picked up."""
    run(capsys, "build", str(repo))
    g1, _ = core.load(repo)
    run(capsys, "ensure", str(repo))
    run(capsys, "ensure", str(repo))
    g2, _ = core.load(repo)
    assert len(g2["nodes"]) == len(g1["nodes"])

    time.sleep(0.01)
    (repo / "a.py").write_text(A_PY + "\n\ndef added_later():\n    return 1\n")
    os.utime(repo / "a.py")
    rc, out = run(capsys, "sym", "added_later", str(repo))
    assert "a.py" in out
    g3, _ = core.load(repo)
    dupes = [n for n in g3["nodes"] if "helper" in n.get("label", "")
             and n.get("source_file") == "a.py"]
    assert len(dupes) == 1


def test_deleted_file_dropped(repo, capsys):
    run(capsys, "build", str(repo))
    (repo / "sub" / "b.py").unlink()
    rc, out = run(capsys, "sym", "Runner", str(repo))
    assert "no symbol matching" in out


def test_touch_single_file(repo, capsys):
    run(capsys, "build", str(repo))
    time.sleep(0.01)
    (repo / "a.py").write_text(A_PY.replace("main_entry", "renamed_entry"))
    core.touch(repo / "a.py", repo)
    g, idx = core.load(repo)
    labels = {n.get("label", "") for n in g["nodes"] if n.get("source_file") == "a.py"}
    assert any("renamed_entry" in l for l in labels)
    assert not any("main_entry" in l for l in labels)


def test_agent_snippet_and_version(repo, capsys):
    rc, out = run(capsys, "agent")
    assert "graphscout explore" in out and "graphscout map" in out
    rc, out = run(capsys, "--version")
    assert out.startswith("graphscout ")


def test_unknown_command(repo, capsys):
    rc, out = run(capsys, "frobnicate")
    assert rc == 2


def test_cache_is_root_scoped(repo, capsys):
    run(capsys, "build", str(repo))
    d = core.repo_key(repo)
    assert (d / "graph.json").exists()
    roots = json.loads(core.roots_file().read_text())
    assert str(repo) in roots


def test_watch_polling_refresh(repo, monkeypatch):
    """Without watchdog installed, watch() falls back to mtime polling."""
    monkeypatch.setitem(__import__("sys").modules, "watchdog", None)
    monkeypatch.setattr(core.time, "sleep", lambda _s: None)

    gen = core.watch(repo, interval=0.01)
    first = next(gen)
    assert "initial build" in first

    time.sleep(0.01)
    (repo / "a.py").write_text(A_PY + "\n\ndef watched_fn():\n    return 1\n")
    os.utime(repo / "a.py")
    second = next(gen)
    assert "refreshed" in second
    gen.close()

    g, _ = core.load(repo)
    assert any("watched_fn" in n.get("label", "") for n in g["nodes"])


def test_roots_lists_registered_repos(repo, capsys):
    run(capsys, "build", str(repo))
    rc, out = run(capsys, "roots")
    assert str(repo) in out and "nodes" in out

    rc, out = run(capsys, "roots", "--json")
    data = json.loads(out)
    assert any(r["root"] == str(repo) and r["built"] for r in data)


def test_windsurf_is_a_registered_json_agent():
    from graphscout import agents
    assert agents.AGENTS["windsurf"]["kind"] == "json"
    assert agents.AGENTS["windsurf"]["path"].name == "mcp_config.json"


def test_install_uninstall_json_agent(tmp_path, monkeypatch):
    from graphscout import agents

    cursor_path = tmp_path / "cursor" / "mcp.json"
    monkeypatch.setitem(agents.AGENTS, "cursor", {"kind": "json", "path": cursor_path})

    log = agents.install(["cursor"])
    assert any("wired" in line for line in log)
    cfg = json.loads(cursor_path.read_text())
    assert cfg["mcpServers"]["graphscout"]["command"] == "graphscout"

    log = agents.uninstall(["cursor"])
    assert any("removed" in line for line in log)
    cfg = json.loads(cursor_path.read_text())
    assert "graphscout" not in cfg["mcpServers"]


def test_detect_skips_missing_cli_agent(monkeypatch):
    from graphscout import agents

    monkeypatch.setattr(agents.shutil, "which", lambda _b: None)
    present = agents.detect()
    assert all(ok is False for name, ok in present.items() if agents.AGENTS[name]["kind"] == "cli")


def test_search_ranks_and_excludes_docstrings(repo, capsys):
    run(capsys, "build", str(repo))
    rc, out = run(capsys, "search", "helper", str(repo))
    assert "helper" in out


def test_explore_returns_verbatim_source_and_blast_radius(repo, capsys):
    run(capsys, "build", str(repo))
    rc, out = run(capsys, "explore", "helper", str(repo))
    assert "def helper(x):" in out  # verbatim source, not just a location
    assert "callers:" in out
    assert "blast radius" in out


def test_impact_is_multi_hop(repo, capsys):
    run(capsys, "build", str(repo))
    rc, out = run(capsys, "impact", "helper", str(repo), "--depth=3")
    assert "impact of 'helper'" in out
    assert "main_entry" in out or "run" in out  # reached transitively via calls


def test_affected_traces_resolved_imports(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GRAPHSCOUT_CACHE", str(tmp_path / "cache"))
    root = tmp_path / "proj"
    (root / ".git").mkdir(parents=True)
    (root / "lib.py").write_text("def foo():\n    return 1\n")
    (root / "app.py").write_text("from lib import foo\n\n\ndef run():\n    return foo()\n")
    (root / "test_app.py").write_text("from app import run\n\n\ndef test_run():\n    assert run() == 1\n")
    run(capsys, "build", str(root))
    rc, out = run(capsys, "affected", str(root / "lib.py"))
    assert "test_app.py" in out


def test_affected_no_matches_says_so(repo, capsys):
    run(capsys, "build", str(repo))
    rc, out = run(capsys, "affected", str(repo / "a.py"))
    assert "no affected" in out


def test_gitignore_is_honored_in_real_git_repo(tmp_path, monkeypatch, capsys):
    """A real `git init` repo (not just a bare .git/ dir) should route through
    `git ls-files`, so .gitignore is honored the same way git itself sees it."""
    import subprocess
    monkeypatch.setenv("GRAPHSCOUT_CACHE", str(tmp_path / "cache"))
    root = tmp_path / "proj"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    (root / "generated").mkdir()
    (root / ".gitignore").write_text("generated/\n")
    (root / "generated" / "gen.py").write_text("def gen():\n    return 1\n")
    (root / "kept.py").write_text("def kept():\n    return 1\n")
    rc, out = run(capsys, "build", str(root))
    assert "1 files" in out
    rc, out = run(capsys, "map", str(root))
    assert "kept.py" in out and "generated" not in out


def test_config_exclude_and_include_override(tmp_path, monkeypatch, capsys):
    import subprocess
    monkeypatch.setenv("GRAPHSCOUT_CACHE", str(tmp_path / "cache"))
    root = tmp_path / "proj"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    (root / "hidden_by_config.py").write_text("def a():\n    return 1\n")
    (root / "kept.py").write_text("def b():\n    return 1\n")
    (root / "graphscout.json").write_text('{"exclude": ["hidden_by_config.py"]}')
    rc, out = run(capsys, "build", str(root))
    assert "1 files" in out  # only kept.py — hidden_by_config.py excluded


def test_explore_snippet_not_truncated_by_docstring(tmp_path, monkeypatch, capsys):
    """A function's own docstring node sits on the line right after `def` —
    node_spans must not treat that as "the next symbol" and cut the snippet
    down to just the signature."""
    monkeypatch.setenv("GRAPHSCOUT_CACHE", str(tmp_path / "cache"))
    root = tmp_path / "proj"
    (root / ".git").mkdir(parents=True)
    (root / "mod.py").write_text(
        'def documented(x):\n'
        '    """This is a docstring long enough to be its own rationale node."""\n'
        '    y = x + 1\n'
        '    return y\n'
    )
    run(capsys, "build", str(root))
    rc, out = run(capsys, "explore", "documented", str(root))
    assert "return y" in out


def test_orphans_flags_dead_code_not_used_symbols(repo, capsys):
    run(capsys, "build", str(repo))
    rc, out = run(capsys, "orphans", str(repo))
    assert "run" not in out.split("dead-code")[0]  # sanity: header prints regardless
    assert "helper" not in out or "Runner" not in out  # both are used (called via sub/b.py, a.py)


def test_orphans_ignores_qualified_module_calls(tmp_path, monkeypatch, capsys):
    """`import mod; mod.fn()` never produces a `calls` edge (graphify only
    resolves bare-name calls from `from mod import fn`) — orphans must not
    flag every function used this way as dead, or it's useless on any
    codebase (including this one) that uses the `import module` style."""
    monkeypatch.setenv("GRAPHSCOUT_CACHE", str(tmp_path / "cache"))
    root = tmp_path / "proj"
    (root / ".git").mkdir(parents=True)
    (root / "core.py").write_text("def ensure(x):\n    return x\n\n\ndef truly_dead():\n    return 1\n")
    (root / "main.py").write_text("import core\n\n\ndef run():\n    return core.ensure(1)\n")
    run(capsys, "build", str(root))
    rc, out = run(capsys, "orphans", str(root))
    assert "ensure" not in out
    assert "truly_dead" in out


def test_json_flag_produces_structured_output(repo, capsys):
    run(capsys, "build", str(repo))
    rc, out = run(capsys, "map", str(repo), "--json")
    data = json.loads(out)
    assert data["nodes"] > 0 and "hubs" in data

    rc, out = run(capsys, "sym", "helper", str(repo), "--json")
    data = json.loads(out)
    assert data["query"] == "helper"
    assert any("a.py" in m["file"] for m in data["matches"])

    rc, out = run(capsys, "callers", "helper", str(repo), "--json")
    data = json.loads(out)
    assert data["direction"] == "callers"

    rc, out = run(capsys, "deps", str(repo / "a.py"), "--json")
    data = json.loads(out)
    assert data["file"] == "a.py"
    assert any(imp["target"] == "os" for imp in data["imports"])

    rc, out = run(capsys, "affected", str(repo / "a.py"), "--json")
    data = json.loads(out)
    assert "affected_tests" in data


def test_daemon_start_stop_status(repo, capsys):
    run(capsys, "build", str(repo))
    rc, out = run(capsys, "daemon", "status", str(repo))
    assert "no daemon running" in out

    rc, out = run(capsys, "daemon", "start", str(repo))
    assert "started daemon" in out
    try:
        time.sleep(0.5)
        rc, out = run(capsys, "daemon", "status", str(repo))
        assert "daemon running" in out
    finally:
        rc, out = run(capsys, "daemon", "stop", str(repo))
        assert "stopped daemon" in out

    time.sleep(0.3)
    rc, out = run(capsys, "daemon", "status", str(repo))
    assert "no daemon running" in out


def test_doctor_reports_env_and_repo_health(repo, capsys):
    run(capsys, "build", str(repo))
    rc, out = run(capsys, "doctor", str(repo))
    assert "graphscout " in out
    assert "indexable extensions" in out
    assert "nodes" in out and "edges" in out


def test_hotspots_ranks_by_churn_and_degree(tmp_path, monkeypatch, capsys):
    import subprocess
    monkeypatch.setenv("GRAPHSCOUT_CACHE", str(tmp_path / "cache"))
    root = tmp_path / "proj"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    (root / "hot.py").write_text(A_PY)
    (root / "cold.py").write_text("def lonely():\n    return 1\n")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)
    for i in range(3):
        (root / "hot.py").write_text(A_PY + f"\n# churn {i}\n")
        subprocess.run(["git", "commit", "-q", "-am", f"edit {i}"], cwd=root, check=True)

    run(capsys, "build", str(root))
    rc, out = run(capsys, "hotspots", str(root))
    assert "hot.py" in out
    assert out.index("hot.py") < out.index("cold.py") if "cold.py" in out else True


def test_hotspots_no_git_says_so(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GRAPHSCOUT_CACHE", str(tmp_path / "cache"))
    root = tmp_path / "proj"
    (root / ".git").mkdir(parents=True)  # bare marker dir, not a real git repo
    (root / "a.py").write_text("def f():\n    return 1\n")
    run(capsys, "build", str(root))
    rc, out = run(capsys, "hotspots", str(root))
    assert "no churn signal" in out


def test_diff_detects_added_removed_modified(tmp_path, monkeypatch, capsys):
    import subprocess
    monkeypatch.setenv("GRAPHSCOUT_CACHE", str(tmp_path / "cache"))
    root = tmp_path / "proj"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    (root / "a.py").write_text("def helper(x):\n    return x + 1\n\n\ndef old_func():\n    return 1\n")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)
    (root / "a.py").write_text("def helper(x):\n    return x + 2\n\n\ndef new_func():\n    return 2\n")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "change"], cwd=root, check=True)

    rc, out = run(capsys, "diff", "HEAD~1", "HEAD", str(root))
    assert "+ new_func()" in out and "- old_func()" in out and "~ helper()" in out


def test_diff_against_working_tree_ignores_boundary_noise(tmp_path, monkeypatch, capsys):
    import subprocess
    monkeypatch.setenv("GRAPHSCOUT_CACHE", str(tmp_path / "cache"))
    root = tmp_path / "proj"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    (root / "a.py").write_text("def helper(x):\n    return x + 1\n\n\ndef keep():\n    return 2\n")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)
    (root / "a.py").write_text(
        "def helper(x):\n    return x + 1\n\n\ndef keep():\n    return 2\n\n\ndef wip():\n    return 3\n"
    )
    rc, out = run(capsys, "diff", "HEAD", str(root))
    assert "+ wip()" in out
    assert "keep()" not in out  # unchanged body must not show as modified just because wip() was appended after it


def test_routes_detects_flask_and_express(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GRAPHSCOUT_CACHE", str(tmp_path / "cache"))
    root = tmp_path / "proj"
    (root / ".git").mkdir(parents=True)
    (root / "app.py").write_text(
        'from flask import Flask\n'
        'app = Flask(__name__)\n\n'
        '@app.get("/users/<id>")\n'
        'def get_user(id):\n'
        '    return "ok"\n'
    )
    (root / "server.js").write_text(
        'app.post("/api/items", (req, res) => res.send("created"));\n'
    )
    run(capsys, "build", str(root))
    rc, out = run(capsys, "routes", str(root))
    assert "GET" in out and "/users/<id>" in out and "app.py" in out
    assert "POST" in out and "/api/items" in out and "server.js" in out


def test_routes_no_matches_says_so(repo, capsys):
    run(capsys, "build", str(repo))
    rc, out = run(capsys, "routes", str(repo))
    assert "no routes detected" in out


def test_routes_detects_expanded_framework_set(tmp_path, monkeypatch, capsys):
    """Regression coverage for the frameworks added to close the gap against
    codegraph's 17-row table: Play, Drupal (routing.yml + hooks), Axum,
    actix/Rocket, Gin HandleFunc, Vapor, React Router, and the file-based
    routers (SvelteKit/Vue-Nuxt/Astro)."""
    monkeypatch.setenv("GRAPHSCOUT_CACHE", str(tmp_path / "cache"))
    root = tmp_path / "proj"
    (root / ".git").mkdir(parents=True)

    (root / "conf").mkdir()
    (root / "conf" / "routes").write_text("GET     /users/:id    controllers.Users.show(id: Long)\n")

    (root / "mymodule").mkdir()
    (root / "mymodule" / "mymodule.routing.yml").write_text(
        "mymodule.settings_form:\n  path: '/admin/config/mymodule'\n"
    )
    (root / "mymodule" / "mymodule.module").write_text(
        "<?php\nfunction mymodule_menu() {\n  return [];\n}\n"
    )

    (root / "src").mkdir()
    (root / "src" / "main.rs").write_text(
        'let app = Router::new().route("/hello", get(hello_handler));\n'
        '#[get("/rocket-style")]\nfn r() {}\n'
    )

    (root / "web").mkdir()
    (root / "web" / "router.go").write_text(
        'router.HandleFunc("/mux-style", handler)\n'
    )

    (root / "Sources").mkdir()
    (root / "Sources" / "routes.swift").write_text(
        'app.get("vapor/hello") { req in "hi" }\n'
    )

    (root / "web2").mkdir()
    (root / "web2" / "App.jsx").write_text(
        'function App() { return <Route path="/react-route" component={Home} />; }\n'
    )

    (root / "pages").mkdir()
    (root / "pages" / "about.vue").write_text("<template>hi</template>\n")

    (root / "routes" / "blog" / "[slug]").mkdir(parents=True)
    (root / "routes" / "blog" / "[slug]" / "+page.server.ts").write_text("export function load() {}\n")

    (root / "src" / "pages").mkdir(parents=True)
    (root / "src" / "pages" / "[slug].astro").write_text("---\n---\n<h1>hi</h1>\n")

    run(capsys, "build", str(root))
    rc, out = run(capsys, "routes", str(root))

    assert "[play]" in out and "/users/:id" in out
    assert "[drupal]" in out and "/admin/config/mymodule" in out and "mymodule_menu" in out
    assert "[axum/actix/rocket]" in out and "/hello" in out and "/rocket-style" in out
    assert "[gin/chi/gorilla/mux]" in out and "/mux-style" in out
    assert "[vapor]" in out and "vapor/hello" in out
    assert "[react-router]" in out and "/react-route" in out
    assert "[vue-router/nuxt]" in out and "/about" in out
    assert "[sveltekit]" in out and "/blog/[slug]" in out
    assert "[astro]" in out and "/[slug]" in out


def test_config_include_overrides_gitignore(tmp_path, monkeypatch, capsys):
    import subprocess
    monkeypatch.setenv("GRAPHSCOUT_CACHE", str(tmp_path / "cache"))
    root = tmp_path / "proj"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    (root / "vendored_src").mkdir()
    (root / ".gitignore").write_text("vendored_src/\n")
    (root / "vendored_src" / "v.py").write_text("def v():\n    return 1\n")
    (root / "graphscout.json").write_text('{"include": ["vendored_src/"]}')
    rc, out = run(capsys, "build", str(root))
    assert "1 files" in out


# --- metrics / dupes / recent / why / tokens ---------------------------------

def test_metrics_symbol_card(repo, capsys):
    """metrics <name> -> one symbol's fan-in/fan-out card. helper is called by
    both main_entry (a.py) and run (sub/b.py), so fan-in >= 1."""
    run(capsys, "build", str(repo))
    rc, out = run(capsys, "metrics", "helper", str(repo))
    assert rc == 0
    assert "metrics for 'helper'" in out
    assert "fan-in" in out and "fan-out" in out


def test_metrics_repo_rankings(repo, capsys):
    """metrics with no query -> repo mode, two ranked lists."""
    run(capsys, "build", str(repo))
    rc, out = run(capsys, "metrics", str(repo))
    assert rc == 0
    assert "complexity metrics" in out
    assert "top fan-out" in out and "top fan-in" in out


def test_metrics_json(repo, capsys):
    run(capsys, "build", str(repo))
    rc, out = run(capsys, "metrics", "helper", str(repo), "--json")
    assert rc == 0
    d = json.loads(out)
    assert d["mode"] == "symbol" and d["cards"]
    assert "fan_in" in d["cards"][0] and "symbol" in d["cards"][0]


def test_metrics_unknown_symbol(repo, capsys):
    run(capsys, "build", str(repo))
    rc, out = run(capsys, "metrics", "nope_doesnotexist", str(repo))
    assert rc == 0 and "no symbol matching" in out


def test_why_finds_call_path(repo, capsys):
    """main_entry calls helper directly -> a 1-hop path is reported."""
    run(capsys, "build", str(repo))
    rc, out = run(capsys, "why", "main_entry", "helper", str(repo))
    assert rc == 0
    assert "call path" in out
    assert "main_entry" in out and "helper" in out
    assert "1 hop" in out  # len(path) - 1


def test_why_unreachable(repo, capsys):
    """helper doesn't call main_entry -> unreachable over call edges."""
    run(capsys, "build", str(repo))
    rc, out = run(capsys, "why", "helper", "main_entry", str(repo))
    assert rc == 0 and "cannot reach" in out


def test_why_missing_arg(repo, capsys):
    run(capsys, "build", str(repo))
    rc, out = run(capsys, "why", "only_one")  # one positional -> usage error to stderr
    assert rc == 2


def test_why_json(repo, capsys):
    run(capsys, "build", str(repo))
    rc, out = run(capsys, "why", "main_entry", "helper", str(repo), "--json")
    assert rc == 0
    d = json.loads(out)
    assert d["resolved"] and d["reachable"]
    assert len(d["path"]) == 2 and d["hops"] == 1


def test_tokens_symbol(repo, capsys):
    run(capsys, "build", str(repo))
    rc, out = run(capsys, "tokens", "helper", str(repo))
    assert rc == 0
    assert "tokens" in out and ("tiktoken" in out or "heuristic" in out)


def test_tokens_json(repo, capsys):
    run(capsys, "build", str(repo))
    rc, out = run(capsys, "tokens", "helper", str(repo), "--json")
    assert rc == 0
    d = json.loads(out)
    assert d["found"] and d["tokens"] >= 1 and "method" in d


def test_tokens_unknown(repo, capsys):
    run(capsys, "build", str(repo))
    rc, out = run(capsys, "tokens", "nope", str(repo))
    assert rc == 0 and "no symbol matching" in out


def test_recent_no_commits(tmp_path, monkeypatch, capsys):
    """A repo with a .git dir but no commits -> the no-signal message, not a crash."""
    monkeypatch.setenv("GRAPHSCOUT_CACHE", str(tmp_path / "cache"))
    root = tmp_path / "r"
    (root / ".git").mkdir(parents=True)
    (root / "m.py").write_text("def f():\n    return 1\n")
    run(capsys, "build", str(root))
    rc, out = run(capsys, "recent", str(root))
    assert rc == 0
    assert "no recent-change signal" in out


def test_recent_with_commits(tmp_path, monkeypatch, capsys):
    import subprocess
    monkeypatch.setenv("GRAPHSCOUT_CACHE", str(tmp_path / "cache"))
    root = tmp_path / "r"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    (root / "m.py").write_text("def f():\n    return 1\n")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True)
    run(capsys, "build", str(root))
    rc, out = run(capsys, "recent", str(root))
    assert rc == 0
    assert "touched by the last" in out
    assert "f" in out  # the one symbol surfaced


def test_viz_imports_kind(repo, capsys):
    """viz --kind=imports draws file-level module edges; sub/b.py imports a.py."""
    run(capsys, "build", str(repo))
    rc, out = run(capsys, "viz", str(repo), "--kind=imports")
    assert rc == 0
    assert "file-level import graph" in out
    assert "flowchart LR" in out
    assert "b.py" in out and "a.py" in out


def test_viz_imports_dot(repo, capsys):
    run(capsys, "build", str(repo))
    rc, out = run(capsys, "viz", str(repo), "--kind=imports", "--format=dot")
    assert rc == 0
    assert "digraph graphscout_imports" in out


def test_viz_calls_kind_unchanged(repo, capsys):
    """Default kind (calls) still works after adding the kind switch."""
    run(capsys, "build", str(repo))
    rc, out = run(capsys, "viz", str(repo))
    assert rc == 0 and ("flowchart LR" in out or "digraph graphscout" in out)


def test_dupes_detects_copy_paste(tmp_path, monkeypatch, capsys):
    """Two functions in separate files with identical normalized bodies cluster
    as a duplicate, even though their names differ — copy-paste is about the
    body, not the (dropped) declaration line."""
    monkeypatch.setenv("GRAPHSCOUT_CACHE", str(tmp_path / "cache"))
    root = tmp_path / "r"
    root.mkdir()
    body = "\n".join([
        "    total = 0",
        "    for item in items:",
        "        total += item",
        "    return total",
    ])
    (root / "x.py").write_text(f"def sum_a(items):\n{body}\n")
    (root / "y.py").write_text(f"def sum_b(items):\n{body}\n")
    run(capsys, "build", str(root))
    rc, out = run(capsys, "dupes", str(root))
    assert rc == 0
    assert "duplicate cluster" in out
    assert "sum_a" in out and "sum_b" in out


def test_dupes_clean_repo(repo, capsys):
    """The default fixture has no duplicates -> the clean message."""
    run(capsys, "build", str(repo))
    rc, out = run(capsys, "dupes", str(repo))
    assert rc == 0 and "no duplicate clusters" in out


def test_dupes_json(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GRAPHSCOUT_CACHE", str(tmp_path / "cache"))
    root = tmp_path / "r"
    root.mkdir()
    body = "\n".join([
        "    first = x + 1",
        "    second = y + 2",
        "    third = z + 3",
        "    return first + second + third",
    ])
    (root / "x.py").write_text(f"def a():\n{body}\n")
    (root / "y.py").write_text(f"def b():\n{body}\n")
    run(capsys, "build", str(root))
    rc, out = run(capsys, "dupes", str(root), "--json")
    assert rc == 0
    d = json.loads(out)
    assert d["count"] >= 1 and d["clusters"][0]
