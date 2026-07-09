import json
import os
import time
from pathlib import Path

import pytest

from codegraph_kit import cli, core

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
    monkeypatch.setenv("CODEGRAPH_CACHE", str(tmp_path / "cache"))
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
    assert "codegraph map" in out and "codegraph sym" in out
    rc, out = run(capsys, "--version")
    assert out.startswith("codegraph ")


def test_unknown_command(repo, capsys):
    rc, out = run(capsys, "frobnicate")
    assert rc == 2


def test_cache_is_root_scoped(repo, capsys):
    run(capsys, "build", str(repo))
    d = core.repo_key(repo)
    assert (d / "graph.json").exists()
    roots = json.loads(core.roots_file().read_text())
    assert str(repo) in roots
