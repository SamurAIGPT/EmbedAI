from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.engine import make_url

from app.core.config import settings
from app.db.base import Base
from app.db.engine import create_app_sync_engine, is_sqlite, prepare_sqlite_file
from app.db.sqlite_baseline import BASELINE_REVISION, create_baseline, sqlite_file_state
from app.db.models import UserSandbox, UserCredential, Tasks, LinkCode, Schedule, PermissionRule,PermissionAsk # noqa: F401

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
target_metadata = Base.metadata

# Use the sync driver (psycopg2 for PostgreSQL, sqlite3 for SQLite) for
# migrations, regardless of what's configured in alembic.ini.
# ConfigParser uses percent signs for interpolation; preserve URL escapes.
config.set_main_option("sqlalchemy.url", settings.SYNC_DATABASE_URL.replace("%", "%%"))


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def _initialize_empty_sqlite(connection) -> None:
    """The PostgreSQL migration chain does not run on SQLite, so a new SQLite
    database starts from a frozen baseline stamped at BASELINE_REVISION and
    only later migrations are applied on top (see app/db/sqlite_baseline.py).
    All or nothing: a failure leaves the file empty."""
    with connection.begin():
        create_baseline(connection)
        MigrationContext.configure(connection).stamp(
            ScriptDirectory.from_config(config), BASELINE_REVISION
        )


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    url = config.get_main_option("sqlalchemy.url")
    sqlite_state = None
    if is_sqlite(url):
        prepare_sqlite_file(url)
        # Judge the file before any connection touches it; a database that is
        # refused (SqliteNotReady) must be left byte for byte as it was.
        sqlite_state = sqlite_file_state(make_url(url).database)
        connectable = create_app_sync_engine(url, poolclass=pool.NullPool)
    else:
        connectable = engine_from_config(
            config.get_section(config.config_ini_section, {}),
            prefix="sqlalchemy.",
            poolclass=pool.NullPool,
        )

    with connectable.connect() as connection:
        on_sqlite = connection.dialect.name == "sqlite"
        if sqlite_state == "new":
            _initialize_empty_sqlite(connection)
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # SQLite cannot alter constraints in place; batch mode rebuilds the table.
            render_as_batch=on_sqlite,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
