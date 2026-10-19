"""Get the database ready before the server listens: `python -m app.server migrate`.

A server install updates by pulling a newer version and migrating, so this is the
step that must not lose data. It follows the order the desktop launch uses
(app/desktop/database.py), and every step before the last can refuse without
having changed anything:

1. SQLite: judge the file (a file this app did not make is refused untouched);
2. check that the encryption key opens the stored credentials;
3. if a migration is pending, copy the file aside first (SQLite's own backup API);
4. migrate to the newest revision.

PostgreSQL is migrated the same way but not copied: back it up with your own tools.
"""

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from app.core.config import settings
from app.db.engine import create_app_sync_engine
from app.db.sqlite_baseline import SqliteNotReady, sqlite_file_state
from app.desktop.database import (
    ALEMBIC_DIR,
    DatabaseRefused,
    KeyMismatch,
    backup_before_migration,
    check_key,
)

BACKUP_DIR_NAME = "backups"


@dataclass(frozen=True)
class MigrationReport:
    database: str  # what was migrated, for the message
    before: str | None  # the revision found (None for a new database)
    after: str  # the newest revision
    backup: Path | None  # the copy made first, if one was

    @property
    def changed(self) -> bool:
        return self.before != self.after


def _config() -> Config:
    config = Config()  # no ini file: Alembic must not reconfigure the app's logging
    config.set_main_option("script_location", str(ALEMBIC_DIR))
    return config


def _stored_revision(file: Path) -> str | None:
    connection = sqlite3.connect(file)
    try:
        row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        return row[0] if row else None
    finally:
        connection.close()


def newest_revision() -> str:
    return ScriptDirectory.from_config(_config()).get_current_head()


def schema_state() -> str:
    """"current", "behind" (an older revision, or none) or "new" (no database yet).
    Reads only: a missing SQLite file is not created by looking at it."""
    if settings.DATABASE_KIND == "sqlite":
        try:
            if sqlite_file_state(settings.SQLITE_FILE) == "new":
                return "new"
        except SqliteNotReady as exc:
            raise DatabaseRefused(str(exc)) from None
    engine = create_app_sync_engine()
    try:
        with engine.connect() as connection:
            revision = MigrationContext.configure(connection).get_current_revision()
    finally:
        engine.dispose()
    if revision is None:
        return "new"
    return "current" if revision == newest_revision() else "behind"


def prepare_database() -> MigrationReport:
    """Create or update the schema. Raises DatabaseRefused or KeyMismatch, having changed nothing."""
    config = _config()
    head = newest_revision()
    backup: Path | None = None
    before: str | None = None

    if settings.DATABASE_KIND == "sqlite":
        file = settings.SQLITE_FILE
        file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            state = sqlite_file_state(file)
        except SqliteNotReady as exc:
            raise DatabaseRefused(str(exc)) from None
        label = f"SQLite file {file}"
        if state == "ready":
            check_key()
            before = _stored_revision(file)
            if before != head:
                try:
                    backup = backup_before_migration(file, file.parent / BACKUP_DIR_NAME, before or "unknown")
                except (OSError, sqlite3.Error) as exc:
                    raise DatabaseRefused(
                        f"Could not make a backup before updating the database ({exc}). The database was not changed."
                    ) from None
    else:
        label = "the PostgreSQL database"

    try:
        command.upgrade(config, "head")
    except Exception as exc:  # noqa: BLE001 - reported, with the backup's location if there is one
        where = f" The copy made before the update is {backup}." if backup else ""
        raise DatabaseRefused(f"Updating the database failed ({type(exc).__name__}: {exc}).{where}") from None
    return MigrationReport(database=label, before=before, after=head, backup=backup)


__all__ = ["DatabaseRefused", "KeyMismatch", "MigrationReport", "newest_revision", "prepare_database", "schema_state"]
