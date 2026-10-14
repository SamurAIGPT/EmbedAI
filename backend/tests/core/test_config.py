import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy.engine import make_url

from app.core.config import Settings


@pytest.mark.parametrize("password", ["owned@fixture", "owned:/#%fixture", "owned%fixture", "owned fixture", "owned-fixture"])
@pytest.mark.parametrize("property_name, driver", [("ASYNC_DATABASE_URL", "postgresql+asyncpg"), ("SYNC_DATABASE_URL", "postgresql+psycopg2")])
def test_database_password_round_trips(password: str, property_name: str, driver: str) -> None:
    config = Settings(POSTGRES_PASSWORD=password, DATABASE_BACKEND="postgres")
    parsed = make_url(getattr(config, property_name))
    assert parsed.drivername == driver
    assert parsed.password == password
    assert parsed.username == config.POSTGRES_USER
    assert parsed.host == config.POSTGRES_HOST
    assert parsed.port == config.POSTGRES_PORT
    assert parsed.database == config.POSTGRES_DB


@pytest.mark.parametrize("password", ["owned-fixture", "owned@:/#%fixture"])
def test_migration_command_accepts_reserved_password(password: str) -> None:
    environment = {**os.environ, "POSTGRES_PASSWORD": password, "DATABASE_BACKEND": "postgres"}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head", "--sql"],
        cwd=Path(__file__).parents[2], env=environment,
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert "CREATE TABLE" in result.stdout
