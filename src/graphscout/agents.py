"""Wire the graphscout MCP server into agent CLIs, and remove it again.

Each agent is either:
  - "cli": has its own `<tool> mcp add/remove <name> -- <command> [args]`
    subcommand, which we shell out to (most robust — we never guess at that
    tool's own config file format or schema).
  - "json": no MCP-management subcommand, so we edit its documented MCP
    config file directly (mcpServers.<name> = {command, args}).

Only agents whose registration mechanism was verified against the real CLI
(or a stable, documented config schema) are listed here — silently guessing
at an unverified format would ship a broken integration, which is worse than
not shipping one.
"""
import json
import shutil
import subprocess
from pathlib import Path

SERVER_NAME = "graphscout"
SERVER_CMD = ["graphscout", "mcp"]

AGENTS = {
    "claude-code": {
        "kind": "cli",
        "binary": "claude",
        "add": lambda b: [b, "mcp", "add", SERVER_NAME, "-s", "user", "--", *SERVER_CMD],
        "remove": lambda b: [b, "mcp", "remove", SERVER_NAME, "-s", "user"],
    },
    "codex": {
        "kind": "cli",
        "binary": "codex",
        "add": lambda b: [b, "mcp", "add", SERVER_NAME, "--", *SERVER_CMD],
        "remove": lambda b: [b, "mcp", "remove", SERVER_NAME],
    },
    "gemini": {
        "kind": "cli",
        "binary": "gemini",
        "add": lambda b: [b, "mcp", "add", "-s", "user", SERVER_NAME, *SERVER_CMD],
        "remove": lambda b: [b, "mcp", "remove", "-s", "user", SERVER_NAME],
    },
    "cursor": {
        "kind": "json",
        "path": Path.home() / ".cursor" / "mcp.json",
    },
}


def detect() -> dict:
    """Which configured agents are actually present on this machine."""
    out = {}
    for name, spec in AGENTS.items():
        if spec["kind"] == "cli":
            out[name] = shutil.which(spec["binary"]) is not None
        else:
            # A json-config agent counts as "present" if its config dir's
            # parent exists (e.g. ~/.cursor) — the file itself may not yet.
            out[name] = spec["path"].parent.exists()
    return out


def _run(argv):
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=20)
        return r.returncode == 0, (r.stdout + r.stderr).strip()
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)


def _json_install(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        cfg = json.loads(path.read_text()) if path.exists() else {}
    except json.JSONDecodeError:
        return f"skip {path} — not valid JSON, edit it by hand"
    cfg.setdefault("mcpServers", {})[SERVER_NAME] = {"command": SERVER_CMD[0], "args": SERVER_CMD[1:]}
    path.write_text(json.dumps(cfg, indent=2) + "\n")
    return f"wired {path}"


def _json_uninstall(path: Path):
    if not path.exists():
        return f"skip {path} — not present"
    try:
        cfg = json.loads(path.read_text())
    except json.JSONDecodeError:
        return f"skip {path} — not valid JSON, edit it by hand"
    removed = cfg.get("mcpServers", {}).pop(SERVER_NAME, None) is not None
    path.write_text(json.dumps(cfg, indent=2) + "\n")
    return f"removed from {path}" if removed else f"not present in {path}"


def install(names=None) -> list:
    present = detect()
    targets = names or [n for n, ok in present.items() if ok]
    log = []
    for name in targets:
        spec = AGENTS.get(name)
        if not spec:
            log.append(f"unknown agent: {name}")
            continue
        if not present.get(name) and not names:
            continue  # auto mode: skip agents that aren't installed
        if spec["kind"] == "cli":
            b = shutil.which(spec["binary"])
            if not b:
                log.append(f"skip {name} — `{spec['binary']}` not on PATH")
                continue
            _run(spec["remove"](b))  # idempotent: clear any stale entry first
            ok, msg = _run(spec["add"](b))
            log.append(f"{'wired' if ok else 'FAILED'} {name}" + (f" — {msg}" if not ok else ""))
        else:
            log.append(_json_install(spec["path"]))
    return log


def uninstall(names=None) -> list:
    targets = names or list(AGENTS)
    log = []
    for name in targets:
        spec = AGENTS.get(name)
        if not spec:
            log.append(f"unknown agent: {name}")
            continue
        if spec["kind"] == "cli":
            b = shutil.which(spec["binary"])
            if not b:
                log.append(f"skip {name} — `{spec['binary']}` not on PATH")
                continue
            ok, msg = _run(spec["remove"](b))
            log.append(f"{'removed' if ok else 'not present'} {name}")
        else:
            log.append(_json_uninstall(spec["path"]))
    return log
