"""Engine processes a hard kill left running, found and ended at the next start
(docs/RELIABILITY.md).

Every engine process (the `claude` CLI, `codex app-server`, an ACP agent, the
status probes) starts in a session and process group of its own, so the
engine's MCP servers and the commands it runs can be killed together. The flip
side: when the backend is killed with `kill -9` (or crashes), nothing kills
them, and they keep running.

So the backend writes one small record per engine process group it starts,
in ENGINE_PROCESS_DIR, and removes it when the process ends. Each record names
the process (pid, start time, the program it launched) and the backend that
started it (pid and start time). Each engine process also carries an
environment marker naming that backend, which its children inherit, including
the `docker exec` calls of a computer's MCP server, which run in sessions of
their own.

At startup, `sweep()` ends what is verifiably ours and orphaned:

- a record whose backend is still running (another backend on the same
  machine, on PostgreSQL) is left alone;
- otherwise its process group is killed only if the process at that pid
  started at the recorded time and runs the recorded program (or carries the
  marker). A pid the system has since given to another program has another
  start time, so it is never signalled;
- when the group's leader is already gone, only the remaining members that
  carry the dead backend's marker are killed;
- any other process carrying the marker of a backend that left records behind
  and is no longer running (verified by its pid and start time) is killed too.
  A backend that stopped cleanly removed its records, so what its engines
  deliberately left running in the background is not touched.

POSIX only; on Windows nothing is recorded and the sweep does nothing.
"""

import json
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.core.logging import get_logger

logger = get_logger(__name__)

# The environment variable naming the backend that started an engine:
# "<pid>-<start time token>".
MARKER_ENV = "OPEN_DOTS_ENGINE_OF"
_TOKEN_SAFE = re.compile(r"[^A-Za-z0-9]+")
_POSIX = sys.platform != "win32"
_PS_TIMEOUT = 10


@dataclass
class Proc:
    pid: int
    pgid: int
    zombie: bool
    started: str
    args: str = ""


@dataclass
class SweepReport:
    killed_groups: list[int] = field(default_factory=list)
    killed_pids: list[int] = field(default_factory=list)
    left_alone: list[int] = field(default_factory=list)
    records_removed: int = 0


def records_dir() -> Path:
    from app.core.config import settings

    if settings.ENGINE_PROCESS_DIR.strip():
        return Path(settings.ENGINE_PROCESS_DIR).expanduser()
    return settings.SQLITE_FILE.parent / "engine-processes"


# -- reading the process table -------------------------------------------------------


def _ps(args: list[str]) -> str | None:
    try:
        done = subprocess.run(
            ["ps", *args], capture_output=True, text=True, timeout=_PS_TIMEOUT,
            env={**os.environ, "LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout


def _linux_stat(pid: int) -> Proc | None:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    # The command name may hold spaces and parentheses; fields follow the last ')'.
    fields = stat[stat.rfind(")") + 2:].split()
    try:
        return Proc(pid=pid, pgid=int(fields[2]), zombie=fields[0] in ("Z", "X"), started=fields[19],
                    args=_linux_args(pid))
    except (IndexError, ValueError):
        return None


def _linux_args(pid: int) -> str:
    try:
        return " ".join(part for part in Path(f"/proc/{pid}/cmdline").read_bytes().decode(
            "utf-8", "replace").split("\0") if part)
    except OSError:
        return ""


def process_table() -> dict[int, Proc] | None:
    """Every process: pid, group, zombie, start time and command line. None
    when it cannot be read."""
    if not _POSIX:
        return None
    if sys.platform.startswith("linux") and Path("/proc/self/stat").exists():
        table = {}
        for name in os.listdir("/proc"):
            if name.isdigit():
                proc = _linux_stat(int(name))
                if proc is not None:
                    table[proc.pid] = proc
        return table
    # lstart is always five words in the C locale (Sun Oct 18 01:02:22 2026).
    out = _ps(["-A", "-ww", "-o", "pid=,pgid=,stat=,lstart=,args="])
    if out is None:
        return None
    table = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 8 or not parts[0].isdigit() or not parts[1].isdigit():
            continue
        table[int(parts[0])] = Proc(
            pid=int(parts[0]), pgid=int(parts[1]), zombie=parts[2].startswith("Z"),
            started=" ".join(parts[3:8]), args=" ".join(parts[8:]),
        )
    return table


def process_info(pid: int) -> Proc | None:
    if not _POSIX or pid <= 1:
        return None
    if sys.platform.startswith("linux") and Path("/proc/self/stat").exists():
        return _linux_stat(pid)
    out = _ps(["-ww", "-o", "pid=,pgid=,stat=,lstart=,args=", "-p", str(pid)])
    for line in (out or "").splitlines():
        parts = line.split()
        if len(parts) >= 8 and parts[0] == str(pid):
            return Proc(pid=pid, pgid=int(parts[1]), zombie=parts[2].startswith("Z"),
                        started=" ".join(parts[3:8]), args=" ".join(parts[8:]))
    return None


def process_markers() -> dict[int, str]:
    """pid -> marker value, for every process whose environment can be read
    and carries MARKER_ENV. (macOS hides the environment of its own system
    programs; engines, MCP servers and docker are not among them.)"""
    found: dict[int, str] = {}
    if not _POSIX:
        return found
    needle = f"{MARKER_ENV}="
    if sys.platform.startswith("linux") and Path("/proc/self/environ").exists():
        for name in os.listdir("/proc"):
            if not name.isdigit():
                continue
            try:
                environ = Path(f"/proc/{name}/environ").read_bytes().decode("utf-8", "replace")
            except OSError:
                continue
            for entry in environ.split("\0"):
                if entry.startswith(needle):
                    found[int(name)] = entry[len(needle):]
        return found
    # `ps -E` appends the environment to the command, space separated.
    out = _ps(["-A", "-E", "-ww", "-o", "pid=,command="]) or ""
    for line in out.splitlines():
        parts = line.split()
        if not parts or not parts[0].isdigit():
            continue
        for part in parts[1:]:
            if part.startswith(needle):
                found[int(parts[0])] = part[len(needle):]
    return found


def start_token(pid: int) -> str | None:
    proc = process_info(pid)
    return _TOKEN_SAFE.sub("", proc.started) if proc is not None and not proc.zombie else None


_owner: str | None = None


def owner_token() -> str:
    """This backend, as the marker names it: "<pid>-<start time token>"."""
    global _owner
    pid = os.getpid()
    if _owner is None or not _owner.startswith(f"{pid}-"):
        _owner = f"{pid}-{start_token(pid) or 'unknown'}"
    return _owner


def owner_alive(token: str, table: dict[int, Proc] | None = None) -> bool:
    """Whether the backend a marker names still runs: same pid, same start."""
    pid_text, _, started = token.partition("-")
    if not pid_text.isdigit() or not started or started == "unknown":
        return True  # cannot tell: never treat it as gone
    pid = int(pid_text)
    proc = table.get(pid) if table is not None else process_info(pid)
    return proc is not None and not proc.zombie and _TOKEN_SAFE.sub("", proc.started) == started


def mark(env: dict[str, str]) -> dict[str, str]:
    """`env` for an engine process: with this backend's marker added."""
    if _POSIX:
        env[MARKER_ENV] = owner_token()
    return env


# -- records ---------------------------------------------------------------------------


def record(pid: int | None, program: str) -> None:
    """Note that this backend started engine process group `pid` (the
    process leads its own session). Never raises."""
    if not _POSIX or not pid:
        return
    try:
        proc = process_info(pid)
        if proc is None:
            return  # already gone
        folder = records_dir()
        folder.mkdir(parents=True, exist_ok=True)
        entry = {
            "pid": pid,
            "started": proc.started,
            "program": Path(program).name,
            "owner": owner_token(),
            "recorded_at": int(time.time()),
        }
        tmp = folder / f".{pid}.tmp"
        tmp.write_text(json.dumps(entry), encoding="utf-8")
        os.replace(tmp, folder / f"{pid}.json")
    except Exception:  # noqa: BLE001  (bookkeeping must never fail a turn)
        logger.debug("could not record an engine process", pid=pid)


def forget(pid: int | None) -> None:
    """The engine process `pid` ended. Never raises."""
    if not _POSIX or not pid:
        return
    try:
        path = records_dir() / f"{pid}.json"
        entry = json.loads(path.read_text(encoding="utf-8"))
        if entry.get("owner") == owner_token():
            path.unlink(missing_ok=True)
    except (OSError, ValueError):
        pass


def _records() -> list[tuple[Path, dict]]:
    folder = records_dir()
    try:
        paths = sorted(folder.glob("*.json"))
    except OSError:
        return []
    out = []
    for path in paths:
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            entry = None
        if isinstance(entry, dict) and isinstance(entry.get("pid"), int):
            out.append((path, entry))
        else:
            path.unlink(missing_ok=True)
    return out


def _runs_program(proc: Proc, program: str) -> bool:
    return bool(program) and program in proc.args


def _kill(target: int, group: bool) -> bool:
    try:
        if group:
            os.killpg(target, signal.SIGKILL)
        else:
            os.kill(target, signal.SIGKILL)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


def sweep() -> SweepReport:
    """End engine processes left by a backend that is no longer running
    (see the module docstring for the checks). Never raises."""
    report = SweepReport()
    if not _POSIX:
        return report
    try:
        table = process_table()
        if table is None:
            return report
        markers = process_markers()
        me = os.getpid()
        own_group = os.getpgid(0)
        done: set[int] = set()
        # Backends that ended without removing their records: crashed or killed.
        crashed: set[str] = set()
        for path, entry in _records():
            pid, owner = entry["pid"], str(entry.get("owner", ""))
            if owner == owner_token() or owner_alive(owner, table):
                report.left_alone.append(pid)
                continue
            crashed.add(owner)
            leader = table.get(pid)
            if leader is not None and not leader.zombie:
                ours = leader.started == entry.get("started") and (
                    _runs_program(leader, str(entry.get("program", ""))) or markers.get(pid) == owner
                )
                if ours and leader.pgid == pid and pid not in (me, own_group):
                    if _kill(pid, group=True):
                        report.killed_groups.append(pid)
                        done.update(p for p, proc in table.items() if proc.pgid == pid)
                elif not ours:
                    # The pid now belongs to another program: never signalled.
                    report.left_alone.append(pid)
            else:
                # The leader is gone; a group id is not reused while any member
                # remains, but only members that carry the dead backend's
                # marker are ended.
                for member, proc in table.items():
                    if proc.pgid == pid and not proc.zombie and markers.get(member) == owner and member != me:
                        if _kill(member, group=False):
                            report.killed_pids.append(member)
                            done.add(member)
            path.unlink(missing_ok=True)
            report.records_removed += 1
        # Processes outside any recorded group (a computer's `docker exec`
        # runs in a session of its own) that carry the marker of a backend
        # that crashed. A backend that stopped cleanly removed its records, so
        # what its engines deliberately left running in the background stays.
        for pid, owner in markers.items():
            if pid in done or pid == me or owner not in crashed:
                continue
            proc = table.get(pid)
            if proc is None or proc.zombie:
                continue
            if _kill(pid, group=False):
                report.killed_pids.append(pid)
    except Exception:  # noqa: BLE001
        logger.exception("could not sweep engine processes left by an earlier run")
    if report.killed_groups or report.killed_pids:
        logger.warning(
            "ended engine processes a previous backend left running",
            groups=report.killed_groups, pids=report.killed_pids,
        )
    return report
