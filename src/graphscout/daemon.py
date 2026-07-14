"""Background watch daemon: `graphscout daemon start|stop|status [dir]`.

`graphscout watch` (core.watch) already keeps a graph in sync, but it blocks
the foreground shell — fine for a dedicated terminal, useless for "just keep
every repo I touch fresh in the background" the way an OS file-watch daemon
does. This wraps the same watch loop in a detached subprocess with a pidfile,
so it survives the shell that started it and can be queried/stopped later.
Pure Python (`start_new_session` + a pidfile) — no bundled runtime, no OS
service manager integration required.
"""
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from . import core


def _pidfile(root: Path) -> Path:
    return core.repo_key(root) / "daemon.pid"


def _logfile(root: Path) -> Path:
    return core.repo_key(root) / "daemon.log"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    except OSError:
        return False
    return True


def status(root: Path) -> str:
    pf = _pidfile(root)
    if not pf.exists():
        return f"no daemon running for {root}"
    try:
        pid = int(pf.read_text().strip())
    except (ValueError, OSError):
        return f"stale/corrupt pidfile at {pf} — remove it and retry"
    if _alive(pid):
        return f"daemon running for {root}  (pid {pid}, log: {_logfile(root)})"
    pf.unlink(missing_ok=True)
    return f"no daemon running for {root} (stale pidfile removed)"


def start(root: Path) -> str:
    pf = _pidfile(root)
    if pf.exists():
        try:
            pid = int(pf.read_text().strip())
            if _alive(pid):
                return f"already running for {root} (pid {pid})"
        except (ValueError, OSError):
            pass
        pf.unlink(missing_ok=True)
    pf.parent.mkdir(parents=True, exist_ok=True)
    log = _logfile(root)
    with open(log, "ab") as logf:
        proc = subprocess.Popen(
            [sys.executable, "-m", "graphscout.cli", "watch", str(root)],
            stdout=logf, stderr=logf, stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    pf.write_text(str(proc.pid))
    time.sleep(0.3)  # give it a beat to fail fast (bad root, import error) before reporting success
    if not _alive(proc.pid):
        pf.unlink(missing_ok=True)
        tail = log.read_text(errors="replace")[-500:] if log.exists() else ""
        return f"daemon failed to start for {root} — log tail:\n{tail}"
    return f"started daemon for {root}  (pid {proc.pid}, log: {log})"


def stop(root: Path) -> str:
    pf = _pidfile(root)
    if not pf.exists():
        return f"no daemon running for {root}"
    try:
        pid = int(pf.read_text().strip())
    except (ValueError, OSError):
        pf.unlink(missing_ok=True)
        return f"stale/corrupt pidfile at {pf} — removed"
    if _alive(pid):
        os.kill(pid, signal.SIGTERM)
        for _ in range(20):  # ~2s grace period before declaring it stopped
            if not _alive(pid):
                break
            time.sleep(0.1)
    pf.unlink(missing_ok=True)
    return f"stopped daemon for {root} (pid {pid})"
