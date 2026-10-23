"""The PostgreSQL Alembic head, the models and the SQLite baseline describe the
same schema (constraints, indexes and defaults included), apart from the known
dialect differences in tests/db/schema_description.py.

Needs a reachable PostgreSQL server where the configured user may create
databases; the test builds two throwaway databases and drops them afterwards.
It is skipped for a SQLite test run or when no server is reachable."""

import os
import subprocess
import sys
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.core.config import settings
from app.db import models  # noqa: F401
from app.db.base import Base
from app.db.engine import create_app_sync_engine
from tests.db.schema_description import comparable, describe
from tests.db.sqlite_helpers import BACKEND_DIR, run_alembic

pytestmark = pytest.mark.skipif(
    settings.DATABASE_KIND != "postgres", reason="compares against PostgreSQL"
)


@pytest.fixture(scope="module")
def postgres_databases():
    base = make_url(settings.SYNC_DATABASE_URL)
    admin = create_engine(base, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        admin.dispose()
        pytest.skip(f"no PostgreSQL server reachable: {type(exc).__name__}")
    suffix = uuid4().hex[:10]
    names = {"alembic": f"od_equiv_alembic_{suffix}", "models": f"od_equiv_models_{suffix}"}
    try:
        with admin.connect() as connection:
            for name in names.values():
                connection.execute(text(f'CREATE DATABASE "{name}"'))
    except Exception as exc:  # noqa: BLE001
        admin.dispose()
        pytest.skip(f"cannot create a scratch database: {type(exc).__name__}")
    try:
        yield {key: base.set(database=name) for key, name in names.items()}
    finally:
        with admin.connect() as connection:
            for name in names.values():
                connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture(scope="module")
def schemas(postgres_databases):
    # 1. what the migration chain builds, in its own process like a user would run it
    alembic_url = postgres_databases["alembic"]
    environment = {
        **os.environ,
        "DATABASE_BACKEND": "postgres",
        "POSTGRES_DB": alembic_url.database,
        "PYTHONUTF8": "1",
    }
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_DIR, env=environment, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr

    # 2. what the models describe
    models_engine = create_engine(postgres_databases["models"])
    Base.metadata.create_all(models_engine)

    alembic_engine = create_engine(alembic_url)
    try:
        yield describe(alembic_engine), describe(models_engine)
    finally:
        alembic_engine.dispose()
        models_engine.dispose()


def test_postgres_alembic_head_equals_the_models(schemas):
    from_alembic, from_models = schemas
    assert set(from_alembic) == set(Base.metadata.tables)
    for table in sorted(from_models):
        assert from_alembic[table] == from_models[table], f"table {table} differs"


def test_sqlite_baseline_matches_postgres_alembic_head(schemas, tmp_path):
    from_alembic, _ = schemas
    database = tmp_path / "baseline.db"
    result = run_alembic(database, "upgrade", "head")
    assert result.returncode == 0, result.stderr
    engine = create_app_sync_engine(f"sqlite:///{database.as_posix()}")
    try:
        sqlite_schema = describe(engine)
    finally:
        engine.dispose()

    postgres_view, sqlite_view = comparable(from_alembic), comparable(sqlite_schema)
    assert set(postgres_view) == set(sqlite_view)
    for table in sorted(postgres_view):
        assert sqlite_view[table] == postgres_view[table], f"table {table} differs between the databases"


def test_named_agent_columns_are_the_same_on_both_databases(schemas, tmp_path):
    """Spelled out so a later change to the comparison cannot hide them: the
    agent table and `thread.agent_id` exist, with the same type, nullability
    and default, on PostgreSQL and on SQLite."""
    from_alembic, from_models = schemas
    database = tmp_path / "agents.db"
    assert run_alembic(database, "upgrade", "head").returncode == 0
    engine = create_app_sync_engine(f"sqlite:///{database.as_posix()}")
    try:
        sqlite_view = comparable(describe(engine))
    finally:
        engine.dispose()
    postgres_view = comparable(from_alembic)

    def column(view, table, name):
        return next(c for c in view[table]["columns"] if c[0] == name)

    assert column(postgres_view, "thread", "agent_id") == column(sqlite_view, "thread", "agent_id")
    assert column(sqlite_view, "thread", "agent_id") == ("agent_id", "integer", True, None)
    assert postgres_view["agent"] == sqlite_view["agent"]
    assert comparable(from_models)["agent"] == postgres_view["agent"]
    assert column(sqlite_view, "agent", "memory_enabled") == ("memory_enabled", "boolean", False, "true")
    assert column(sqlite_view, "agent", "recall_enabled") == ("recall_enabled", "boolean", False, "true")
    assert column(sqlite_view, "agent", "memory_upkeep") == ("memory_upkeep", "boolean", False, "true")
    assert column(sqlite_view, "agent", "approval_mode") == ("approval_mode", "text", False, "'ask'")
    assert column(sqlite_view, "agent", "fallback_provider") == ("fallback_provider", "text", True, None)
    assert column(sqlite_view, "agent", "fallback_model") == ("fallback_model", "text", True, None)
    assert postgres_view["turn"] == sqlite_view["turn"]
    assert column(sqlite_view, "turn", "engine_phase") == ("engine_phase", "text", True, None)
