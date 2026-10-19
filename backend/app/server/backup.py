"""`python -m app.server backup` and `restore`: one archive of what the server keeps.

What is in an archive, each under its own top-level folder:

    database/open-dots.db   a consistent copy of the SQLite database (SQLite's own backup API)
    attachments/            files attached to messages
    decisions/              the decision log
    memory-state/           the notes journal and the proposed-notes queue
    workspace/              the agents' notes and the folders conversations work in
    manifest.json           what was copied, the schema revision, and a SHA-256 of every file

What is NOT in it, on purpose: the encryption key (TOKEN_ENCRYPTION_KEYS; an archive plus its
key would be everything an attacker needs), the engines' own sign-ins (`~/.claude`, `~/.codex`),
folders outside the workspace root that an agent was pointed at, and PostgreSQL (use pg_dump).
The archive holds your conversations and notes in clear and your stored credentials encrypted:
keep it as private as the server.

A backup never stops the server; the database copy is consistent, other files are copied as
they are. A restore refuses to touch anything that exists and refuses while a server runs:
it only fills an empty data folder, and checks the archive before it changes anything.
"""

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import tarfile
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

FORMAT = 1
MANIFEST = "manifest.json"
DATABASE_NAME = "open-dots.db"
PARTS = ("database", "attachments", "decisions", "memory-state", "workspace")
ARCHIVE_PREFIX = "open-dots-backup-"
WORKSPACE_TABLES = ("agent", "computer", "thread")  # tables with a workspace_path column
LEASE_SUFFIXES = (".lease", ".lock")  # the data-folder lease sits beside the database; it is not data


class BackupError(Exception):
    """Nothing was changed; the message says why and what to do."""


class KeyProblem(BackupError):
    """Stored credentials cannot be opened with the current encryption key."""


@dataclass
class PartReport:
    files: int = 0
    bytes: int = 0


@dataclass
class BackupReport:
    archive: Path
    parts: dict[str, PartReport]
    skipped: list[str] = field(default_factory=list)
    server_running: bool = False


@dataclass
class RestoreReport:
    parts: dict[str, PartReport]
    revision: str | None
    older_than_code: bool
    moved_from: str | None = None  # the workspace root the backup was made under, when it differs from this one
    rewritten: int = 0  # database rows whose workspace path was moved to the new root
    outside: list[str] = field(default_factory=list)  # workspace paths that were outside the old root: kept as they were


# -- where things are ------------------------------------------------------------------------------

def locations() -> dict[str, Path]:
    """Where this installation keeps each part (the same helpers the server uses)."""
    from app.core import app_settings
    from app.core.config import settings
    from app.services import attachments, decision_log, memory_journal
    from app.services.workspaces import canonical_workspace

    if settings.DATABASE_KIND != "sqlite":
        raise BackupError("Backups cover a SQLite installation. For PostgreSQL use pg_dump for the database, "
                          "and copy the attachments, decision log and workspace folders yourself.")
    return {
        "database": settings.SQLITE_FILE,
        "attachments": attachments.root_dir(),
        "decisions": decision_log.log_dir(),
        "memory-state": memory_journal.state_root(),
        "workspace": canonical_workspace(app_settings.get("LOCAL_WORKSPACE_ROOT")),
    }


def _inside(path: Path, folder: Path) -> bool:
    try:
        path.resolve().relative_to(folder.resolve())
        return True
    except ValueError:
        return False


def _server_running(database: Path) -> bool:
    from app.core import data_lease

    try:
        lease = data_lease.acquire(database)
    except data_lease.DataDirInUse:
        return True
    lease.release()
    return False


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _snapshot_database(database: Path, target: Path) -> str | None:
    """A consistent copy, checked; returns its schema revision."""
    source = sqlite3.connect(database)
    try:
        copy = sqlite3.connect(target)
        try:
            source.backup(copy)
        finally:
            copy.close()
    finally:
        source.close()
    return _check_database(target)


def _check_database(path: Path) -> str | None:
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise BackupError("The database copy did not pass SQLite's integrity check.")
        try:
            row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        except sqlite3.Error:
            raise BackupError("This is not an Open Dots database (it has no schema version).") from None
        return row[0] if row else None
    except sqlite3.DatabaseError as exc:
        raise BackupError(f"The database file cannot be read ({exc}).") from None
    finally:
        connection.close()


# -- backup --------------------------------------------------------------------------------------------

def _tar_filter(info: tarfile.TarInfo, executable: bool = False) -> tarfile.TarInfo:
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    info.mode = (0o700 if (executable or info.isdir()) else 0o600)
    return info


def default_output_dir(database: Path) -> Path:
    return database.parent / "backups"


def create_backup(out_dir: Path | None = None, *, skip_workspaces: bool = False) -> BackupReport:
    from app.server.database import newest_revision

    where = locations()
    database = where["database"]
    if not database.is_file():
        raise BackupError(f"There is no database at {database}, so there is nothing to back up.")
    out_dir = (out_dir or default_output_dir(database)).expanduser().resolve()
    for name, folder in where.items():
        if name != "database" and _inside(out_dir, folder):
            raise BackupError(f"The output folder {out_dir} is inside the {name} folder it would copy. Choose another folder.")
    out_dir.mkdir(mode=0o700, parents=True, exist_ok=True)

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    archive = out_dir / f"{ARCHIVE_PREFIX}{stamp}.tar.gz"
    if archive.exists():
        raise BackupError(f"{archive} already exists; wait a second and run it again.")
    running = _server_running(database)
    parts = {name: PartReport() for name in PARTS}
    skipped: list[str] = []
    hashes: dict[str, str] = {}

    work = Path(tempfile.mkdtemp(prefix=".backup-", dir=out_dir))
    partial = out_dir / (archive.name + ".part")
    try:
        copy = work / DATABASE_NAME
        revision = _snapshot_database(database, copy)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        with os.fdopen(os.open(partial, flags, 0o600), "wb") as raw, tarfile.open(fileobj=raw, mode="w:gz", dereference=True) as tar:
            def add_file(path: Path, arcname: str, part: str) -> None:
                executable = bool(path.stat().st_mode & stat.S_IXUSR)
                info = _tar_filter(tar.gettarinfo(str(path), arcname), executable)
                with path.open("rb") as handle:
                    reader = _HashingReader(handle)
                    try:
                        tar.addfile(info, reader)
                    except OSError:
                        raise BackupError(f"{arcname} changed while it was being copied. Stop the server and run the backup again.") from None
                hashes[arcname] = reader.hexdigest()
                parts[part].files += 1
                parts[part].bytes += info.size

            add_file(copy, f"database/{DATABASE_NAME}", "database")
            for name in PARTS[1:]:
                if skip_workspaces and name == "workspace":
                    continue
                folder = where[name]
                if not folder.is_dir():
                    continue
                for current, directories, files in os.walk(folder, followlinks=False):
                    base = Path(current)
                    kept = []
                    for directory in sorted(directories):
                        if (base / directory).is_symlink():
                            skipped.append(f"{name}/{(base / directory).relative_to(folder).as_posix()} (a link)")
                        else:
                            kept.append(directory)
                    directories[:] = kept
                    relative = base.relative_to(folder)
                    arc_dir = "/".join([name, *relative.parts])
                    info = _tar_filter(tar.gettarinfo(str(base), arc_dir))
                    tar.addfile(info)
                    for file_name in sorted(files):
                        path = base / file_name
                        label = f"{name}/{path.relative_to(folder).as_posix()}"
                        if path.is_symlink() or not path.is_file():
                            skipped.append(f"{label} (not a regular file)")
                            continue
                        try:
                            add_file(path, label, name)
                        except OSError as exc:
                            skipped.append(f"{label} ({exc.strerror or 'unreadable'})")
            manifest = {
                "format": FORMAT,
                "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "database_revision": revision,
                "code_revision": newest_revision(),
                "workspaces_included": not skip_workspaces,
                "roots": {"workspace": str(where["workspace"])},
                "parts": {name: {"files": r.files, "bytes": r.bytes} for name, r in parts.items()},
                "files": hashes,
            }
            payload = json.dumps(manifest, indent=1).encode("utf-8")
            info = tarfile.TarInfo(MANIFEST)
            info.size = len(payload)
            info.mtime = int(datetime.now(UTC).timestamp())
            tar.addfile(_tar_filter(info), fileobj=_Bytes(payload))
        os.replace(partial, archive)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return BackupReport(archive=archive, parts=parts, skipped=skipped, server_running=running)


class _HashingReader:
    """Hashes exactly what the archive receives, so the checksum is of the bytes stored."""

    def __init__(self, handle) -> None:
        self._handle, self._digest = handle, hashlib.sha256()

    def read(self, n: int = -1) -> bytes:
        block = self._handle.read(n)
        self._digest.update(block)
        return block

    def hexdigest(self) -> str:
        return self._digest.hexdigest()


class _Bytes:
    def __init__(self, data: bytes) -> None:
        self._data, self._at = data, 0

    def read(self, n: int = -1) -> bytes:
        end = len(self._data) if n is None or n < 0 else self._at + n
        chunk, self._at = self._data[self._at:end], min(end, len(self._data))
        return chunk


# -- restore -----------------------------------------------------------------------------------------------

def _clean_member(name: str) -> tuple[str, ...]:
    """The path parts of an archive member, or an error for anything that could escape."""
    if not name or name.startswith(("/", "\\")) or ":" in name.split("/")[0] or "\\" in name:
        raise BackupError(f"The archive has an unsafe path ({name!r}); it was not restored.")
    parts = tuple(part for part in name.split("/") if part not in ("", "."))
    if not parts or any(part == ".." for part in parts):
        raise BackupError(f"The archive has an unsafe path ({name!r}); it was not restored.")
    return parts


def _targets_are_free(where: dict[str, Path]) -> None:
    taken = []
    for name, path in where.items():
        if name == "database":
            if path.exists() or Path(str(path) + "-wal").exists() or Path(str(path) + "-shm").exists():
                taken.append(f"the database file {path}")
        elif path.exists() and any(path.iterdir()) if path.is_dir() else path.exists():
            taken.append(f"the {name} folder {path}")
    if taken:
        raise BackupError("A restore only fills an empty data folder, and these already hold data: "
                          + "; ".join(taken) + ". Move them aside (or point the settings at new, empty places) and run it again.")


def _read_manifest(tar: tarfile.TarFile) -> dict:
    try:
        member = tar.getmember(MANIFEST)
    except KeyError:
        raise BackupError("This is not an Open Dots backup (it has no manifest).") from None
    handle = tar.extractfile(member)
    try:
        manifest = json.loads(handle.read().decode("utf-8")) if handle else None
    except (ValueError, UnicodeDecodeError):
        manifest = None
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT or not isinstance(manifest.get("files"), dict):
        raise BackupError("This backup's manifest is not one this version understands.")
    return manifest


def _stage(tar: tarfile.TarFile, manifest: dict, staging: Path) -> dict[str, PartReport]:
    """Unpack into `staging`, checking every member and every hash; nothing is trusted from the archive's own modes."""
    parts = {name: PartReport() for name in PARTS}
    expected = dict(manifest["files"])
    seen: set[str] = set()
    for member in tar.getmembers():
        if member.name == MANIFEST:
            continue
        pieces = _clean_member(member.name)
        top = pieces[0]
        if top not in PARTS:
            raise BackupError(f"The archive holds something unexpected ({member.name!r}); it was not restored.")
        destination = staging.joinpath(*pieces)
        if member.isdir():
            destination.mkdir(mode=0o700, parents=True, exist_ok=True)
            continue
        if not member.isreg():
            raise BackupError(f"The archive holds a link or special file ({member.name!r}); it was not restored.")
        key = "/".join(pieces)
        if key not in expected:
            raise BackupError(f"{member.name!r} is not in the archive's manifest; it was not restored.")
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        digest = hashlib.sha256()
        source = tar.extractfile(member)
        with open(destination, "wb") as out:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
                out.write(block)
        if digest.hexdigest() != expected[key]:
            raise BackupError(f"{member.name!r} does not match its checksum: the archive is damaged or was changed. Nothing was restored.")
        os.chmod(destination, 0o700 if member.mode & stat.S_IXUSR else 0o600)
        seen.add(key)
        parts[top].files += 1
        parts[top].bytes += member.size
    missing = sorted(set(expected) - seen)
    if missing:
        raise BackupError(f"The archive is missing {len(missing)} file(s) its manifest lists (for example {missing[0]!r}). Nothing was restored.")
    if f"database/{DATABASE_NAME}" not in seen:
        raise BackupError("The archive has no database. Nothing was restored.")
    return parts


def _flavor(path: str):
    from pathlib import PurePosixPath, PureWindowsPath

    return PureWindowsPath if ("\\" in path or (len(path) > 1 and path[1] == ":")) else PurePosixPath


def _relocate_workspace_paths(database: Path, old_root: str, new_root: Path) -> tuple[int, list[str]]:
    """Point the stored workspace paths at `new_root`: a path inside `old_root` keeps its place under the new
    root; any other path is left exactly as it was (it was never in the backup) and is returned for the report."""
    flavor = _flavor(old_root)
    old = flavor(old_root)
    changed, outside = 0, []
    connection = sqlite3.connect(database)
    try:
        for table in WORKSPACE_TABLES:
            try:
                rows = connection.execute(f"SELECT id, workspace_path FROM {table} WHERE workspace_path IS NOT NULL").fetchall()
            except sqlite3.Error:
                continue  # an older schema without the table or column
            for row_id, value in rows:
                try:
                    relative = flavor(value).relative_to(old)
                except ValueError:
                    outside.append(value)
                    continue
                connection.execute(f"UPDATE {table} SET workspace_path = ? WHERE id = ?", (str(Path(new_root, *relative.parts)), row_id))
                changed += 1
        connection.commit()
    finally:
        connection.close()
    return changed, sorted(set(outside))


def _check_key(database: Path) -> None:
    from app.core import crypto

    connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        try:
            rows = connection.execute("SELECT ciphertext FROM user_credential").fetchall()
        except sqlite3.Error:
            return
    finally:
        connection.close()
    unreadable = 0
    for (ciphertext,) in rows:
        try:
            crypto.decrypt(ciphertext)
        except Exception:  # noqa: BLE001 - any failure to decrypt counts
            unreadable += 1
    if unreadable:
        raise KeyProblem(f"{unreadable} stored credential(s) in the backup cannot be decrypted with the current "
                         "TOKEN_ENCRYPTION_KEYS. Use the key the backup was made with. Nothing was restored.")


def restore_backup(archive: Path) -> RestoreReport:
    from alembic.script import ScriptDirectory

    from app.core import data_lease
    from app.core.config import settings
    from app.server.database import _config, newest_revision

    where = locations()
    archive = Path(archive).expanduser()
    if not archive.is_file():
        raise BackupError(f"There is no backup file at {archive}.")
    _targets_are_free(where)
    try:
        lease = data_lease.acquire(where["database"])  # a running server holds it; this also stops one starting meanwhile
    except data_lease.DataDirInUse as exc:
        raise BackupError(f"The server is running, and a restore must not happen under it. Stop it first. ({exc})") from None
    staging: Path | None = None
    placed: list[tuple[Path, Path]] = []
    try:
        try:
            tar = tarfile.open(archive, "r:gz")
        except (tarfile.TarError, OSError) as exc:
            raise BackupError(f"{archive} is not a readable backup archive ({exc}).") from None
        with tar:
            try:
                manifest = _read_manifest(tar)
                staging = Path(tempfile.mkdtemp(prefix=".restore-", dir=where["database"].parent))
                os.chmod(staging, 0o700)
                parts = _stage(tar, manifest, staging)
            except (tarfile.TarError, EOFError, OSError) as exc:
                raise BackupError(f"The archive could not be read all the way through ({exc}). Nothing was restored.") from None

        staged_db = staging / "database" / DATABASE_NAME
        revision = _check_database(staged_db)
        known = {script.revision for script in ScriptDirectory.from_config(_config()).walk_revisions()}
        if revision is not None and revision not in known:
            raise BackupError("The backup was made by a newer version of Open Dots (its database revision is unknown here). "
                              "Update the code first. Nothing was restored.")
        _check_key(staged_db)

        old_root = (manifest.get("roots") or {}).get("workspace")
        moved_from, rewritten, outside = None, 0, []
        if isinstance(old_root, str) and old_root and old_root != str(where["workspace"]):
            moved_from = old_root
            rewritten, outside = _relocate_workspace_paths(staged_db, old_root, where["workspace"])

        moves = [(staged_db, where["database"])]
        for name in PARTS[1:]:
            source = staging / name
            if source.is_dir():
                moves.append((source, where[name]))
        for source, target in moves:
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if target.is_dir():  # empty (checked above); a move onto it needs it gone
                target.rmdir()
            shutil.move(str(source), str(target))
            placed.append((source, target))
        os.chmod(where["database"], 0o600)
        placed.clear()
        return RestoreReport(parts=parts, revision=revision, older_than_code=revision != newest_revision(),
                             moved_from=moved_from, rewritten=rewritten, outside=outside)
    except BaseException:
        for source, target in reversed(placed):  # put back what was already moved
            try:
                shutil.move(str(target), str(source))
            except OSError:
                pass
        raise
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
        lease.release()
