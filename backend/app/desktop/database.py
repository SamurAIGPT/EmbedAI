"""The desktop launch's database work, before the server listens (D-012 decision 7).

Order matters and every step can refuse without having changed the file:
1. judge the file (an unrecognised file is refused and left exactly as it was);
2. check that the encryption key opens what is stored (the credential table exists
   from the first revision on, so this needs no migration);
3. if a migration is pending, copy the file aside first;
4. migrate.
"""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import select

from app.core import crypto
from app.db.engine import create_app_sync_engine
from app.db.models.user_credential import UserCredential
from app.db.sqlite_baseline import SqliteNotReady, sqlite_file_state
from app.desktop import profile

BACKUPS_KEPT = 5
BACKUP_PREFIX = "open-dots-"
ALEMBIC_DIR = Path(__file__).resolve().parents[2] / "alembic"


class DatabaseRefused(Exception):
    """The database file is not one this app made, or could not be made ready."""


class KeyMismatch(Exception):
    """Stored credentials exist that the encryption key cannot open."""

    def __init__(self, unreadable: int) -> None:
        super().__init__(
            f"{unreadable} stored credential(s) cannot be decrypted with the encryption key "
            "this launch was given. Nothing was changed."
        )
        self.unreadable = unreadable


def _alembic_config() -> Config:
    config = Config()  # no ini file: Alembic must not reconfigure the app's logging
    config.set_main_option("script_location", str(ALEMBIC_DIR))
    return config


def _stored_revision(database_file: Path) -> str | None:
    connection = sqlite3.connect(database_file)
    try:
        row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        return row[0] if row else None
    finally:
        connection.close()


def backup_before_migration(database_file: Path, backup_dir: Path, revision: str) -> Path:
    """Copy the database with SQLite's own backup API (a consistent copy even if
    the file is in WAL mode), under a temporary name first so a half-written
    backup is never mistaken for a good one."""
    backup_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    final = backup_dir / f"{BACKUP_PREFIX}{revision}-{stamp}.db"
    partial = final.with_suffix(".db.part")
    source = sqlite3.connect(database_file)
    try:
        target = sqlite3.connect(partial)
        try:
            source.backup(target)
        finally:
            target.close()
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    finally:
        source.close()
    partial.replace(final)
    prune_backups(backup_dir)
    return final


def prune_backups(backup_dir: Path, keep: int = BACKUPS_KEPT) -> None:
    """Keep the newest `keep` backups. Only files this module names are touched."""
    for stale in backup_dir.glob(f"{BACKUP_PREFIX}*.db.part"):
        stale.unlink(missing_ok=True)  # a backup that was interrupted
    found = sorted(backup_dir.glob(f"{BACKUP_PREFIX}*.db"), key=lambda path: path.name, reverse=True)
    for old in found[keep:]:
        old.unlink(missing_ok=True)


def checkpoint(database_file: Path) -> bool:
    """Fold the write-ahead log into the database file, so the file on its own holds
    everything (a person copying it as a backup gets all of it). Best effort: the
    app is already stopping, and a busy database is simply left for next time."""
    try:
        connection = sqlite3.connect(database_file, timeout=5)
        try:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
        finally:
            connection.close()
    except sqlite3.Error:
        return False
    return True


def check_key() -> None:
    """Every stored credential must open with the current key. A lost or replaced
    key is reported here instead of being discovered when a token is first used,
    and a new key is never made to cover for it."""
    engine = create_app_sync_engine()
    unreadable = 0
    try:
        with engine.connect() as connection:
            for (ciphertext,) in connection.execute(select(UserCredential.ciphertext)):
                try:
                    crypto.decrypt(ciphertext)
                except Exception:  # noqa: BLE001 - any failure to decrypt counts
                    unreadable += 1
    finally:
        engine.dispose()
    if unreadable:
        raise KeyMismatch(unreadable)


def prepare(desktop: profile.DesktopProfile) -> Path | None:
    """Get the database ready. Returns the backup that was made, if one was."""
    database_file = desktop.database_file
    desktop.data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)

    try:
        state = sqlite_file_state(database_file)
    except SqliteNotReady as exc:
        raise DatabaseRefused(str(exc)) from None

    config = _alembic_config()
    backup = None
    if state == "ready":
        check_key()
        stored = _stored_revision(database_file)
        if stored != ScriptDirectory.from_config(config).get_current_head():
            try:
                backup = backup_before_migration(database_file, desktop.backup_dir, stored or "unknown")
            except (OSError, sqlite3.Error) as exc:
                raise DatabaseRefused(
                    f"Could not make a backup before updating the database ({exc}). "
                    "The database was not changed."
                ) from None

    try:
        command.upgrade(config, "head")
    except Exception as exc:  # noqa: BLE001 - reported, with the backup's location if there is one
        where = f" The copy made before the update is {backup}." if backup else ""
        raise DatabaseRefused(f"Updating the database failed ({type(exc).__name__}: {exc}).{where}") from None

    return backup
