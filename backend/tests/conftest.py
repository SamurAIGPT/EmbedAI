"""Test database selection.

The suite runs on whichever database the settings select: PostgreSQL when
POSTGRES_* values are set (the default for the existing setup), or SQLite with
`DATABASE_BACKEND=sqlite`. A SQLite run always uses a fresh temporary file,
never ~/.open-dots/open-dots.db, so the tests cannot touch a real database.

    uv run pytest                                   # PostgreSQL (from .env)
    DATABASE_BACKEND=sqlite uv run pytest           # SQLite, temporary file
"""

import tempfile
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings

BACKEND_DIR = Path(__file__).parents[1]

# This must happen before anything imports app.db.session, which creates the
# application's engine from the settings.
if settings.DATABASE_KIND == "sqlite":
    settings.SQLITE_PATH = str(Path(tempfile.mkdtemp(prefix="open-dots-test-")) / "test.db")

from app.db.engine import create_app_async_engine  # noqa: E402


def using_sqlite() -> bool:
    return settings.DATABASE_KIND == "sqlite"


@pytest.fixture(scope="session", autouse=True)
def sqlite_schema():
    """Create the schema of the temporary SQLite database with the real
    migration entry point, so every SQLite test run also exercises it."""
    if not using_sqlite():
        yield
        return
    from tests.db.sqlite_helpers import run_alembic

    result = run_alembic(Path(settings.SQLITE_PATH), "upgrade", "head")
    assert result.returncode == 0, result.stderr
    yield


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """A session bound to a transaction that's always rolled back, so tests
    never leave rows behind in the real database.

    Uses its own engine (rather than the app's shared one) because
    pytest-asyncio runs each test in its own event loop by default, and an
    asyncpg connection pool can't be reused across loops.
    """
    engine = create_app_async_engine()
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            session = AsyncSession(
                bind=connection,
                expire_on_commit=False,
                join_transaction_mode="create_savepoint",
            )
            try:
                yield session
            finally:
                await session.close()
                await transaction.rollback()
    finally:
        await engine.dispose()


@pytest.fixture(autouse=True)
def boat_settings(monkeypatch):
    """Boat is optional, so backend/.env may leave these empty (or hold a real
    key). Tests must not depend on that: they run against fake Boat servers and
    never need, or should see, a real key. A test that wants Boat unconfigured
    sets BOAT_API_KEY to "" itself."""
    monkeypatch.setattr(settings, "BOAT_API_KEY", "test-boat-key")
    monkeypatch.setattr(settings, "HOOK_TOKEN", "test-hook-token")
    monkeypatch.setattr(settings, "PERMISSION_HOOK_BASE_URL", "http://hook.test")


@pytest.fixture(autouse=True)
def decision_log_dir(monkeypatch, tmp_path):
    """Approvals write a decision log; no test may write to the real one."""
    folder = tmp_path / "decisions"
    monkeypatch.setattr(settings, "DECISION_LOG_DIR", str(folder))
    return folder


@pytest.fixture(autouse=True)
def attachments_dir(monkeypatch, tmp_path):
    """Attachments are written under a data folder; never the real one."""
    folder = tmp_path / "attachments"
    monkeypatch.setattr(settings, "ATTACHMENTS_DIR", str(folder))
    return folder


@pytest.fixture(autouse=True)
def engine_process_dir(monkeypatch, tmp_path):
    """Engine processes are recorded under a data folder; never the real one."""
    folder = tmp_path / "engine-processes"
    monkeypatch.setattr(settings, "ENGINE_PROCESS_DIR", str(folder))
    return folder


@pytest.fixture
def user_id() -> str:
    return "test-user-1"


@pytest.fixture
def other_user_id() -> str:
    return "test-user-2"
