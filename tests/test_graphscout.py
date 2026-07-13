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
