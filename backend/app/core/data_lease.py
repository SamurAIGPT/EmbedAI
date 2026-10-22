"""One backend per SQLite database (docs/RELIABILITY.md).

Two backends on one database file would both run the routine scheduler, both
sweep each other's turns as stale and both write the same workspace folders.
So the backend holds an exclusive operating-system lock on a small file next to
the database for as long as it runs, and a second backend that finds the lock
taken refuses to start, naming the process that holds it.

The lock is advisory and owned by the kernel (`flock` on macOS and Linux,
`LockFileEx` through msvcrt on Windows): it ends when the holding process ends,
however it ends, so a crash or `kill -9` never leaves a stale lock behind and
there is nothing to clean up by hand. The file itself stays; what it contains
(pid, host, start time) is only for the refusal message, never for deciding.

Not covered: two machines sharing a database over a network file system whose
locks are unreliable (SQLite itself warns against that), and PostgreSQL, where
several backends on one server are allowed and turn ownership is enforced by
leases in the database (docs/ARCHITECTURE.md).
"""

import json
import os
import socket
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

LEASE_SUFFIX = ".backend-lease"


class DataDirInUse(Exception):
    """Another running backend holds this database's lease."""

    def __init__(self, database: Path, holder: dict | None) -> None:
        self.database = database
        self.holder = holder or {}
        who = ""
        pid = self.holder.get("pid")
        if isinstance(pid, int):
            host = self.holder.get("host")
            who = f" (process {pid}" + (f" on {host}" if isinstance(host, str) and host else "") + ")"
        super().__init__(
            f"Another Open Duet backend{who} is already using the database {database}. "
            "Stop it first, or point this one at a different database (SQLITE_PATH). "
            "Two backends on one database would run every routine twice."
        )


@dataclass
class DataDirLease:
    database: Path
    path: Path
    _fd: int | None
    _holders: int = field(default=1)

    @property
    def held(self) -> bool:
        return self._fd is not None

    def release(self) -> None:
        """Give the lease up (the last release in this process unlocks)."""
        if self._fd is None:
            return
        self._holders -= 1
        if self._holders > 0:
            return
        _held.pop(self.path, None)
        fd, self._fd = self._fd, None
        try:
            _unlock(fd)
        finally:
            os.close(fd)


# Leases this process holds, by lease file: acquiring again in the same process
# (the desktop entry, then the app's startup) shares the one lock.
_held: dict[Path, DataDirLease] = {}


def lease_path(database: Path) -> Path:
    return database.with_name(database.name + LEASE_SUFFIX)


def _try_lock(fd: int) -> bool:
    if sys.platform == "win32":
        import msvcrt

        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    import fcntl

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False


def _unlock(fd: int) -> None:
    try:
        if sys.platform == "win32":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass


def _read_holder(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "null")
    except (OSError, ValueError):
        return None  # Windows keeps a locked byte unreadable; the message just names no pid
    return data if isinstance(data, dict) else None


def acquire(database: Path) -> DataDirLease:
    """Take the lease of `database` (a SQLite file, which need not exist yet).
    Raises DataDirInUse when another process holds it."""
    database = Path(database).expanduser().resolve()
    path = lease_path(database)
    existing = _held.get(path)
    if existing is not None and existing.held:
        existing._holders += 1
        return existing
    database.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        if not _try_lock(fd):
            raise DataDirInUse(database, _read_holder(path))
        record = {
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "started_at": int(time.time()),
            "database": str(database),
        }
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, (json.dumps(record) + "\n").encode("utf-8"))
    except BaseException:
        os.close(fd)
        raise
    lease = DataDirLease(database=database, path=path, _fd=fd)
    _held[path] = lease
    return lease


def acquire_for_settings(settings) -> DataDirLease | None:
    """The lease for the configured database: SQLite only (see the module
    docstring for PostgreSQL)."""
    if settings.DATABASE_KIND != "sqlite":
        return None
    return acquire(settings.SQLITE_FILE)
