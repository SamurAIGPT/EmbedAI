"""Which database the app uses, and the guarantee that an existing setup is
never silently moved to SQLite: PostgreSQL stays the default, SQLite is chosen
only by an explicit DATABASE_BACKEND=sqlite."""

from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy.engine import make_url

from app.core.config import Settings

REQUIRED = dict(
    BOAT_API_KEY="x", TOKEN_ENCRYPTION_KEYS="x", HOOK_TOKEN="x",
    PERMISSION_HOOK_BASE_URL="http://127.0.0.1:1",
)


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in (
        "DATABASE_BACKEND", "SQLITE_PATH", "POSTGRES_USER", "POSTGRES_PASSWORD",
        "POSTGRES_DB", "POSTGRES_HOST", "POSTGRES_PORT",
    ):
        monkeypatch.delenv(name, raising=False)


def build(env_file: Path | None = None, **overrides) -> Settings:
    return Settings(_env_file=env_file, **REQUIRED, **overrides)


def test_nothing_configured_is_still_postgres_with_the_docker_compose_defaults():
    """A setup with no POSTGRES_* lines at all relies on these defaults."""
    settings = build()
    assert settings.DATABASE_KIND == "postgres"
    url = make_url(settings.ASYNC_DATABASE_URL)
    assert url.drivername == "postgresql+asyncpg"
    assert (url.host, url.port, url.database) == ("localhost", 5432, "vadoo")
    assert make_url(settings.SYNC_DATABASE_URL).drivername == "postgresql+psycopg2"


def test_an_existing_env_file_keeps_postgres(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "POSTGRES_USER=vadoo\nPOSTGRES_PASSWORD=vadoo\nPOSTGRES_DB=vadoo\n"
        "POSTGRES_HOST=localhost\nPOSTGRES_PORT=5433\n",
        encoding="utf-8",
    )
    settings = build(env_file=env_file)
    assert settings.DATABASE_KIND == "postgres"
    assert make_url(settings.ASYNC_DATABASE_URL).port == 5433


def test_an_env_file_without_any_database_setting_keeps_postgres(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("FRONTEND_ORIGINS=http://localhost:3000\n", encoding="utf-8")
    assert build(env_file=env_file).DATABASE_KIND == "postgres"


def test_a_sqlite_path_alone_does_not_switch_database(monkeypatch):
    monkeypatch.setenv("SQLITE_PATH", "/tmp/whatever.db")
    assert build().DATABASE_KIND == "postgres"


def test_sqlite_is_chosen_explicitly():
    settings = build(DATABASE_BACKEND="sqlite")
    assert settings.DATABASE_KIND == "sqlite"
    assert make_url(settings.ASYNC_DATABASE_URL).drivername == "sqlite+aiosqlite"
    assert make_url(settings.SYNC_DATABASE_URL).drivername == "sqlite"


def test_explicit_sqlite_wins_over_postgres_values(monkeypatch):
    monkeypatch.setenv("POSTGRES_HOST", "db.internal")
    assert build(DATABASE_BACKEND="sqlite").DATABASE_KIND == "sqlite"


def test_explicit_postgres_is_postgres():
    assert build(DATABASE_BACKEND="postgres").DATABASE_KIND == "postgres"


def test_backend_names_are_normalised():
    assert build(DATABASE_BACKEND=" PostgreSQL ").DATABASE_KIND == "postgres"
    assert build(DATABASE_BACKEND=" SQLite ").DATABASE_KIND == "sqlite"


def test_unknown_backend_is_rejected():
    with pytest.raises(ValidationError):
        build(DATABASE_BACKEND="mysql")


def test_default_sqlite_file_is_in_the_users_open_dots_folder():
    assert build().SQLITE_FILE == Path.home() / ".open-dots" / "open-dots.db"


@pytest.mark.parametrize("name", ["with space", "unicode-é", "a#b%c"])
def test_sqlite_path_round_trips(tmp_path, name):
    target = tmp_path / name / "data.db"
    settings = build(DATABASE_BACKEND="sqlite", SQLITE_PATH=str(target))
    for url in (settings.ASYNC_DATABASE_URL, settings.SYNC_DATABASE_URL):
        assert Path(make_url(url).database) == target


def test_tilde_in_the_path_is_expanded():
    path = build(SQLITE_PATH="~/somewhere/x.db").SQLITE_FILE
    assert path == Path.home() / "somewhere" / "x.db"
