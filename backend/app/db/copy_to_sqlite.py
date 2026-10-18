"""Copy an existing PostgreSQL database into a new SQLite file.

    uv run python -m app.db.copy_to_sqlite [--target PATH] [--dry-run] [--allow-active]

Stop the backend and the Telegram bot first. PostgreSQL is only read (one
read-only REPEATABLE READ transaction, so every table comes from the same
snapshot) and is never changed. The SQLite file is built under a temporary name
next to the target, filled, verified, and only then published, without
overwriting anything. If any step fails the temporary file is removed and the
target never appears. The tool does not change DATABASE_BACKEND or any other
setting: switching to SQLite is a separate, explicit step.

Nothing here prints row values (the database holds encrypted credentials);
only table names and counts.
"""

import argparse
import os
import secrets
import sqlite3
import subprocess
import sys
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from itertools import zip_longest
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Table, create_engine, func, select, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import OperationalError

from app.core.config import settings
from app.db import models  # noqa: F401  (registers the tables)
from app.db.base import Base
from app.db.engine import create_app_sync_engine

BACKEND_DIR = Path(__file__).parents[2]
BATCH_SIZE = 500

# Kept in step with task_execution_service.ACTIVE_STATUSES (a test checks it);
# not imported from there so this tool does not load the whole service layer.
ACTIVE_TASK_STATUSES = {"queued", "starting", "pending", "running", "waiting_approval"}


class MigrationRefused(RuntimeError):
    """A precondition or verification failed; nothing was published."""


# --------------------------------------------------------------------------
# Preflight


SIDE_FILE_SUFFIXES = ("-wal", "-shm", "-journal")


def describe_existing_file(path: Path) -> str:
    """What is at `path`, for the refusal message. The file is opened read-only
    and only table names and counts are looked at, never row values."""
    try:
        if path.stat().st_size == 0:
            return "an empty file"
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                )
            }
            if "task" in tables and "alembic_version" in tables:
                tasks = connection.execute("SELECT count(*) FROM task").fetchone()[0]
                return f"an Open Dots database that already holds {tasks} task(s)"
            return f"a SQLite database with {len(tables)} table(s) that is not an Open Dots database"
        finally:
            connection.close()
    except (sqlite3.OperationalError, OSError):
        # e.g. a WAL-mode database whose -shm file is gone, or a locked file
        return "a file that could not be inspected"
    except sqlite3.DatabaseError:
        return "not a SQLite database"


def target_problem(target: Path) -> str | None:
    """A file at the target (or a leftover SQLite side file) is never replaced."""
    side_files = [Path(str(target) + suffix) for suffix in SIDE_FILE_SUFFIXES]
    present_sides = [p for p in side_files if p.exists()]
    if not target.exists() and not present_sides:
        return None
    if target.exists():
        what = f"{target} already exists ({describe_existing_file(target)})."
    else:
        what = (
            f"{present_sides[0]} exists without a database file next to it (it belongs to a "
            "database that was moved or deleted)."
        )
    advice = (
        " This tool never overwrites a file. Choose another --target, or move or rename the "
        "existing one yourself."
    )
    if present_sides:
        names = ", ".join(p.name for p in present_sides)
        advice += (
            f" Move its side file(s) with it ({names}: SQLite's write-ahead-log files, which "
            "belong to that database). They normally disappear when the app is stopped, so "
            "stop the backend first if one is still running."
        )
    return what + advice


def alembic_head() -> str:
    return ScriptDirectory.from_config(Config(str(BACKEND_DIR / "alembic.ini"))).get_current_head()


def source_problems(source: Connection, head: str, allow_active: bool) -> list[str]:
    """Why this PostgreSQL database should not be copied right now (empty list
    means ready). Runs inside the snapshot transaction."""
    problems: list[str] = []
    has_version_table = source.execute(text("SELECT to_regclass('alembic_version')")).scalar()
    if not has_version_table:
        return ["The PostgreSQL database has no alembic_version table; run `alembic upgrade head` first."]
    versions = [row[0] for row in source.execute(text("SELECT version_num FROM alembic_version"))]
    if versions != [head]:
        problems.append(
            f"The PostgreSQL schema is at {versions or 'no revision'}, not the current head {head}; "
            "run `uv run alembic upgrade head` against PostgreSQL first."
        )
    if allow_active:
        return problems
    tasks = Base.metadata.tables["task"]
    asks = Base.metadata.tables["permission_ask"]
    active = source.execute(
        select(func.count()).select_from(tasks).where(tasks.c.status.in_(ACTIVE_TASK_STATUSES))
    ).scalar_one()
    pending = source.execute(
        select(func.count()).select_from(asks).where(asks.c.status == "pending")
    ).scalar_one()
    others = source.execute(
        text(
            "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
            "AND pid <> pg_backend_pid() AND backend_type = 'client backend'"
        )
    ).scalar_one()
    if active or pending:
        problems.append(
            f"{active} task(s) are still active and {pending} approval(s) are pending; a copy "
            "would miss what they write next. Stop the backend and bot and let them finish."
        )
    if others:
        problems.append(
            f"{others} other connection(s) are open on this database (is the backend or the "
            "Telegram bot still running?). Stop them, or pass --allow-active if you are sure."
        )
    return problems


# --------------------------------------------------------------------------
# Building, copying, verifying


def build_target_file(temp: Path) -> None:
    """Create the schema with the real migration entry point (frozen baseline,
    then every later migration), so the copy can never drift from what a new
    SQLite install gets."""
    environment = {
        **os.environ,
        "DATABASE_BACKEND": "sqlite",
        "SQLITE_PATH": str(temp),
        "PYTHONUTF8": "1",
    }
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_DIR, env=environment, capture_output=True, text=True, timeout=300,
    )
    if result.returncode != 0:
        raise MigrationRefused(f"Could not create the SQLite schema:\n{result.stderr[-1500:]}")


def _primary_key_order(table: Table):
    return list(table.primary_key.columns)


def _stream(connection: Connection, table: Table) -> Iterator[tuple]:
    statement = select(table).order_by(*_primary_key_order(table)).execution_options(yield_per=BATCH_SIZE)
    for row in connection.execute(statement):
        yield tuple(row)


def _normalise(row: tuple) -> tuple:
    return tuple(
        value.astimezone(UTC) if isinstance(value, datetime) and value.tzinfo else value
        for value in row
    )


def copy_tables(source: Connection, destination: Connection) -> dict[str, int]:
    """Copy every table in foreign-key order, keeping ids."""
    copied: dict[str, int] = {}
    for table in Base.metadata.sorted_tables:
        count = 0
        batch: list[dict] = []
        statement = select(table).order_by(*_primary_key_order(table)).execution_options(yield_per=BATCH_SIZE)
        for row in source.execute(statement):
            batch.append(dict(row._mapping))
            if len(batch) >= BATCH_SIZE:
                destination.execute(table.insert(), batch)
                count += len(batch)
                batch = []
        if batch:
            destination.execute(table.insert(), batch)
            count += len(batch)
        copied[table.name] = count
    return copied


def verify_copy(source: Connection, destination: Connection) -> list[str]:
    """Row counts, then every row of every table compared (counts of mismatches
    only; values are never printed)."""
    problems: list[str] = []
    for table in Base.metadata.sorted_tables:
        expected = source.execute(select(func.count()).select_from(table)).scalar_one()
        actual = destination.execute(select(func.count()).select_from(table)).scalar_one()
        if expected != actual:
            problems.append(f"{table.name}: {expected} rows in PostgreSQL, {actual} in SQLite")
            continue
        differing = sum(
            1
            for left, right in zip_longest(_stream(source, table), _stream(destination, table))
            if left is None or right is None or _normalise(left) != _normalise(right)
        )
        if differing:
            problems.append(f"{table.name}: {differing} row(s) differ between PostgreSQL and SQLite")
    return problems


def undecryptable_credentials(rows: Iterator[str]) -> int:
    """How many stored credentials cannot be decrypted with TOKEN_ENCRYPTION_KEYS."""
    try:
        from app.core.crypto import decrypt
    except Exception as exc:  # noqa: BLE001  (an invalid key fails at import)
        raise MigrationRefused(
            f"TOKEN_ENCRYPTION_KEYS is not usable ({type(exc).__name__}); cannot check credentials."
        ) from exc
    failures = 0
    for ciphertext in rows:
        try:
            decrypt(ciphertext)
        except Exception:  # noqa: BLE001
            failures += 1
    return failures


def _credential_rows(connection: Connection) -> Iterator[str]:
    table = Base.metadata.tables["user_credential"]
    for (ciphertext,) in connection.execute(select(table.c.ciphertext)):
        yield ciphertext


def finalize_sqlite(engine, temp: Path) -> None:
    """Fold the write-ahead log into the main file, leave WAL mode, and check
    the finished file with a fresh read-only connection."""
    engine.dispose()
    # A plain connection: the app's engine wraps every statement in a
    # transaction, and SQLite cannot leave WAL mode inside one.
    plain = sqlite3.connect(temp)
    try:
        plain.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
        plain.execute("PRAGMA journal_mode=DELETE").fetchall()
    finally:
        plain.close()
    for suffix in ("-wal", "-shm", "-journal"):
        if Path(str(temp) + suffix).exists():
            raise MigrationRefused(f"SQLite left a {suffix} file behind; refusing to publish.")
    check = sqlite3.connect(temp.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        if check.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise MigrationRefused("SQLite integrity_check failed on the new file.")
        if check.execute("PRAGMA foreign_key_check").fetchall():
            raise MigrationRefused("SQLite foreign_key_check failed on the new file.")
    finally:
        check.close()


def publish_no_overwrite(temp: Path, target: Path) -> None:
    """Move `temp` to `target`, failing with FileExistsError if `target` exists.

    A hard link fails atomically when the target exists on every OS, unlike
    rename (which replaces the target on Linux and macOS). On a filesystem
    without hard links it falls back to a check and a rename; that leaves a
    tiny window in which a file created at the target in between would be
    replaced on POSIX (Windows refuses)."""
    try:
        os.link(temp, target)
    except FileExistsError:
        raise
    except OSError:
        if target.exists():
            raise FileExistsError(str(target)) from None
        os.rename(temp, target)
        return
    os.unlink(temp)


def _remove_quietly(temp: Path) -> None:
    for suffix in ("", "-wal", "-shm", "-journal"):
        try:
            Path(str(temp) + suffix).unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


# --------------------------------------------------------------------------
# The whole operation


def copy_postgres_to_sqlite(
    source_url: str,
    target: Path,
    *,
    dry_run: bool = False,
    allow_active: bool = False,
    out: Callable[[str], None] = print,
) -> None:
    """Raises MigrationRefused when it cannot, or should not, continue."""
    target = Path(target).expanduser().absolute()
    problem = target_problem(target)
    if problem:
        raise MigrationRefused(problem)
    head = alembic_head()

    shown = make_url(source_url).render_as_string(hide_password=True)
    engine = create_engine(source_url)
    try:
        try:
            connection = engine.connect()
        except OperationalError as exc:
            raise MigrationRefused(f"Cannot connect to PostgreSQL at {shown} ({type(exc.orig).__name__}).") from exc
        with connection:
            # One read-only snapshot for the whole operation.
            source = connection.execution_options(
                isolation_level="REPEATABLE READ", postgresql_readonly=True
            )
            try:
                problems = source_problems(source, head, allow_active)
            except OperationalError as exc:
                raise MigrationRefused(f"Cannot read PostgreSQL at {shown} ({type(exc.orig).__name__}).") from exc
            if problems:
                raise MigrationRefused("\n".join(problems))

            counts = {
                table.name: source.execute(select(func.count()).select_from(table)).scalar_one()
                for table in Base.metadata.sorted_tables
            }
            failures = undecryptable_credentials(_credential_rows(source))
            if failures:
                raise MigrationRefused(
                    f"{failures} stored credential(s) cannot be decrypted with TOKEN_ENCRYPTION_KEYS; "
                    "use the key that was in use when they were saved."
                )

            if dry_run:
                out(f"Dry run: would copy from {shown} to {target}. Nothing was created.")
                for name, count in counts.items():
                    out(f"  {name}: {count} rows")
                return

            temp = target.with_name(f"{target.name}.migrating-{secrets.token_hex(4)}")
            lite = None
            try:
                build_target_file(temp)
                url = URL.create("sqlite", database=temp.as_posix())
                lite = create_app_sync_engine(url.render_as_string(hide_password=False))
                with lite.begin() as destination:
                    copied = copy_tables(source, destination)
                with lite.connect() as destination:
                    problems = verify_copy(source, destination)
                    if problems:
                        raise MigrationRefused("Verification failed:\n" + "\n".join(problems))
                    failures = undecryptable_credentials(_credential_rows(destination))
                if failures:
                    raise MigrationRefused(f"{failures} credential(s) cannot be decrypted after the copy.")
                finalize_sqlite(lite, temp)
                lite = None
                if target_problem(target):
                    raise MigrationRefused(f"{target} appeared while copying; not overwriting it.")
                try:
                    publish_no_overwrite(temp, target)
                except FileExistsError as exc:
                    raise MigrationRefused(f"{target} appeared while copying; not overwriting it.") from exc
            except BaseException:
                if lite is not None:
                    lite.dispose()
                _remove_quietly(temp)
                raise
            out(f"Copied {sum(copied.values())} rows into {target} and verified them:")
            for name, count in copied.items():
                out(f"  {name}: {count} rows")
    finally:
        engine.dispose()


NEXT_STEPS = """\
The copy is finished and verified. This tool did not change any setting, and
PostgreSQL was only read.

To start using SQLite, set in backend/.env:
  DATABASE_BACKEND=sqlite{path_line}
then start the app again. Switching back to PostgreSQL later restores the data
as it was when it was copied; anything written in SQLite after the switch is
not copied back."""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.db.copy_to_sqlite",
        description="Copy the PostgreSQL database into a new SQLite file (PostgreSQL is only read).",
    )
    parser.add_argument("--target", help=f"new SQLite file (default: {settings.SQLITE_FILE})")
    parser.add_argument("--dry-run", action="store_true", help="check and count, create nothing")
    parser.add_argument(
        "--allow-active", action="store_true",
        help="copy even if tasks look active or other connections are open (not recommended)",
    )
    parser.add_argument("--source-url", help="PostgreSQL URL (default: from the POSTGRES_* settings)")
    args = parser.parse_args(argv)

    target = Path(args.target).expanduser() if args.target else settings.SQLITE_FILE
    try:
        copy_postgres_to_sqlite(
            args.source_url or settings.POSTGRES_SYNC_URL,
            target,
            dry_run=args.dry_run,
            allow_active=args.allow_active,
        )
    except MigrationRefused as exc:
        print(f"Not copied: {exc}", file=sys.stderr)
        return 1
    if not args.dry_run:
        path_line = f"\n  SQLITE_PATH={target}" if args.target else ""
        print(NEXT_STEPS.format(path_line=path_line))
    return 0


if __name__ == "__main__":
    sys.exit(main())
