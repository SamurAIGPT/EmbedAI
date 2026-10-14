from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    BOAT_BASE_URL : str = "https://boat.dev/api/v1"
    BOAT_API_KEY : str
    # Boat bills sandbox creation to the caller's personal account unless a
    # team/org is explicitly attached to the request.
    BOAT_ORG_ID : str = ""

    # Opt-in web search and page fetching for Claude tasks, via HTTP MCP.
    PARALLEL_SEARCH_ENABLED: bool = False

    # Which database to use: "sqlite" or "postgres". Empty means PostgreSQL, as
    # it always has, so no existing setup changes database unless this is set.
    # (Deliberately not inferred from the POSTGRES_* values: they all have
    # defaults that match docker-compose, so a setup with none of them in .env
    # is still a PostgreSQL setup.)
    DATABASE_BACKEND: str = ""
    # SQLite database file. Empty means ~/.open-dots/open-dots.db.
    SQLITE_PATH: str = ""

    POSTGRES_USER: str = "vadoo"
    POSTGRES_PASSWORD: str = "vadoo"
    POSTGRES_DB: str = "vadoo"
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432

    TOKEN_ENCRYPTION_KEYS: str

    TELEGRAM_BOT_TOKEN: str = ""
    # Shown to web UI users as a clickable t.me/<username> link when linking
    # their Telegram account. Purely cosmetic — the link flow works without it.
    TELEGRAM_BOT_USERNAME: str = ""

    # Origin(s) allowed to call this API from a browser (the frontend's dev
    # server / deployed URL). Comma-separated.
    FRONTEND_ORIGINS: str = "http://localhost:3000"
    
    # A secret token that the webhook will require to be present in the request header. This is to prevent unauthorized requests from the public internet.
    HOOK_TOKEN: str

    # Public URL the sandbox can reach to call back into this backend for
    # permission checks (an ngrok tunnel in dev, the real deployed URL in
    # production). No trailing slash.
    PERMISSION_HOOK_BASE_URL: str

    # Run Claude through the user's own locally installed, signed-in CLI
    # instead of a Boat sandbox. Off by default so existing deployments are
    # unaffected; see app/services/local_access_policy.py for who may use it.
    LOCAL_EXECUTION_ENABLED: bool = False
    # Host header values accepted for local execution requests (comma-separated).
    LOCAL_ALLOWED_HOSTS: str = "localhost,127.0.0.1,[::1]"
    # Full path to the real `claude` executable. Leave empty to auto-detect.
    LOCAL_CLAUDE_CLI_PATH: str = ""
    # Working directory for local runs. Empty means ~/.open-dots/local.
    LOCAL_WORKSPACE_ROOT: str = ""
    LOCAL_TASK_TIMEOUT_SECONDS: int = 300
    LOCAL_MAX_OUTPUT_BYTES: int = 1_000_000

    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8')

    @property
    def FRONTEND_ORIGIN_LIST(self) -> list[str]:
        return [origin.strip() for origin in self.FRONTEND_ORIGINS.split(",") if origin.strip()]

    @property
    def LOCAL_ALLOWED_HOST_LIST(self) -> list[str]:
        return [host.strip().lower() for host in self.LOCAL_ALLOWED_HOSTS.split(",") if host.strip()]

    @field_validator("DATABASE_BACKEND")
    @classmethod
    def _check_database_backend(cls, value: str) -> str:
        value = value.strip().lower()
        if value == "postgresql":
            value = "postgres"
        if value not in ("", "sqlite", "postgres"):
            raise ValueError('DATABASE_BACKEND must be "sqlite", "postgres" or empty')
        return value

    @property
    def DATABASE_KIND(self) -> str:
        """"sqlite" only when DATABASE_BACKEND says so; PostgreSQL otherwise."""
        return "sqlite" if self.DATABASE_BACKEND == "sqlite" else "postgres"

    @property
    def SQLITE_FILE(self) -> Path:
        if self.SQLITE_PATH.strip():
            return Path(self.SQLITE_PATH).expanduser()
        return Path.home() / ".open-dots" / "open-dots.db"

    @property
    def ASYNC_DATABASE_URL(self) -> str:
        if self.DATABASE_KIND == "sqlite":
            return self._sqlite_url("sqlite+aiosqlite")
        return self._database_url("postgresql+asyncpg")

    @property
    def SYNC_DATABASE_URL(self) -> str:
        if self.DATABASE_KIND == "sqlite":
            return self._sqlite_url("sqlite")
        return self._database_url("postgresql+psycopg2")

    def _sqlite_url(self, driver: str) -> str:
        # as_posix: forward slashes work for SQLite on every OS.
        return URL.create(driver, database=self.SQLITE_FILE.as_posix()).render_as_string(
            hide_password=False
        )

    def _database_url(self, driver: str) -> str:
        return URL.create(
            driver,
            username=self.POSTGRES_USER,
            password=self.POSTGRES_PASSWORD,
            host=self.POSTGRES_HOST,
            port=self.POSTGRES_PORT,
            database=self.POSTGRES_DB,
        ).render_as_string(hide_password=False)


settings = Settings()  # type: ignore
