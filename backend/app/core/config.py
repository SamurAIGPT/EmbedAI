from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict
from sqlalchemy import URL

from app.core import public_url
from app.desktop import profile


class Settings(BaseSettings):
    BOAT_BASE_URL : str = "https://boat.dev/api/v1"
    # Boat is optional. Empty means no Boat sandboxes: the app starts without
    # it and anything that needs a sandbox answers "Boat is not configured".
    BOAT_API_KEY : str = ""
    # Boat bills sandbox creation to the caller's personal account unless a
    # team/org is explicitly attached to the request.
    BOAT_ORG_ID : str = ""
    # Optional trusted PEM CA bundle, also supported through backend/.env.
    SSL_CERT_FILE: str = ""

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
    # Folder for the decision log (what was asked, and how it was settled).
    # Empty means ~/.open-dots/decisions. The desktop app uses its data folder.
    DECISION_LOG_DIR: str = ""
    # Folder for files attached to messages. Empty means ~/.open-dots/attachments.
    # The desktop app uses its data folder.
    ATTACHMENTS_DIR: str = ""
    # Folder where the backend records the engine processes it starts, so the
    # next start can end any a hard kill left running (docs/RELIABILITY.md).
    # Empty means an `engine-processes` folder next to the SQLite file (by
    # default ~/.open-dots/engine-processes, also on PostgreSQL).
    ENGINE_PROCESS_DIR: str = ""
    # Decision log months older than this many days are deleted (1 to 3650).
    DECISION_LOG_RETENTION_DAYS: int = 180

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
    # Only used by Boat sandboxes; empty disables the permission hook endpoint.
    HOOK_TOKEN: str = ""

    # Public URL the sandbox can reach to call back into this backend for
    # permission checks (an ngrok tunnel in dev, the real deployed URL in
    # production). No trailing slash. Only needed with Boat.
    PERMISSION_HOOK_BASE_URL: str = ""

    # Run Claude through the user's own locally installed, signed-in CLI
    # instead of a Boat sandbox. Off by default so existing deployments are
    # unaffected; see app/services/local_access_policy.py for who may use it.
    LOCAL_EXECUTION_ENABLED: bool = False
    # Host header values accepted for local execution requests (comma-separated).
    LOCAL_ALLOWED_HOSTS: str = "localhost,127.0.0.1,[::1]"
    # Full path to the real `claude` executable. Leave empty to auto-detect.
    LOCAL_CLAUDE_CLI_PATH: str = ""
    # Full path to the `codex` executable for the Codex engine. Empty means
    # auto-detect on PATH.
    LOCAL_CODEX_CLI_PATH: str = ""
    # Working directory for local runs. Empty means ~/.open-dots/local.
    LOCAL_WORKSPACE_ROOT: str = ""
    TEAM_ENABLED: bool = True
    TEAM_LEAD_AUTO: bool = True
    TEAM_MAX_CREATED_PER_TURN: int = 4
    TEAM_MAX_AGENTS: int = 50
    TEAM_MAX_DEPTH: int = 3
    TEAM_MAX_CHILDREN_PER_TURN: int = 8
    TEAM_MAX_JOBS_PER_ROOT: int = 24
    TEAM_MAX_EXECUTIONS_PER_ROOT: int = 48
    TEAM_MAX_PARALLEL: int = 3
    TEAM_JOB_WAIT_SECONDS: int = 900
    TEAM_JOB_QUEUE_SECONDS: int = 600
    ROOM_CHAIN_DEPTH: int = 2
    LOCAL_TASK_TIMEOUT_SECONDS: int = 300
    LOCAL_MAX_OUTPUT_BYTES: int = 1_000_000
    # Stored as a desktop preference; headless installs do not run the wizard.
    DESKTOP_ONBOARDING_COMPLETED: bool = False
    # Let local Claude use tools (read/write files, run commands) in its
    # workspace. Every tool call the CLI would ask about waits for a human
    # Allow/Deny in the web UI. Off by default: runs stay chat-only.
    LOCAL_TOOLS_ENABLED: bool = False
    # An approval nobody answers within this time is denied.
    LOCAL_APPROVAL_TIMEOUT_SECONDS: int = 300

    # Owner authentication (docs/AUTH.md). Host names this server answers to,
    # comma-separated; a request with any other Host header is refused (DNS
    # rebinding). Add your public name when self-hosting, e.g.
    # "localhost,127.0.0.1,[::1],dots.example.com". A "*.example.com" entry
    # matches its subdomains.
    TRUSTED_HOSTS: str = "localhost,127.0.0.1,[::1]"
    # The address people use to reach a self-hosted server, e.g.
    # "https://dots.example.com" (scheme and host only, no path). Its host name
    # is answered to as if it were listed in TRUSTED_HOSTS, and pairing links
    # printed on the server use it. Empty for the desktop app and local use.
    OPEN_DOTS_PUBLIC_URL: str = ""
    # Whether a request from this machine needs no sign-in:
    #   off       every request signs in (default; safest)
    #   programs  loopback programs (curl, scripts) act as the owner; browsers
    #             still sign in. Agents running on this machine can then call
    #             the API as the owner too.
    #   all       loopback programs and browsers act as the owner (development)
    # Loopback trust never applies to a request with proxy headers.
    LOOPBACK_TRUST: str = "off"

    # Durable threads: a running turn refreshes its heartbeat this often, and a
    # turn whose heartbeat is older than the lease is marked interrupted (never
    # resumed or replayed). The lease must be several heartbeats long.
    THREAD_HEARTBEAT_SECONDS: int = 10
    THREAD_LEASE_SECONDS: int = 60
    # The turn watchdog: a turn whose engine does nothing (no event, no typing)
    # for this long, while not waiting on an approval, is stopped and fails with
    # `engine_stalled`. Longer than LOCAL_TASK_TIMEOUT_SECONDS by default, so it
    # only catches an engine whose own time limit did not end it. 0 turns it off.
    TURN_STALL_SECONDS: float = 600

    # Memory upkeep (docs/MEMORY.md): a conversation with an agent that has been
    # quiet for this many seconds (or has had 6 turns) is read once by a quick
    # model call that files the facts worth keeping. 0 switches the call off for
    # every agent.
    MEMORY_CAPTURE_QUIET_SECONDS: float = 120
    # Optional smaller model for those calls, per engine: "claude=haiku,openai_compat=llama3".
    # An engine not listed uses "haiku" on Claude, and the endpoint's own model elsewhere.
    # The call always goes to the engine the conversation already uses.
    MEMORY_HELPER_MODEL: str = ""
    # Compaction of a replayed conversation (docs/MEMORY.md): when on, an engine
    # with a quick call writes the summary of the folded part (redacted first, sent
    # only to the engine the conversation already uses); off keeps the excerpts.
    COMPACTION_MODEL_SUMMARY: bool = False
    # The nightly tidy-up runs once a day after this local hour (0-23) for each
    # agent with notes and upkeep on, never while it is working. 24 switches it off.
    MEMORY_TIDY_HOUR: int = 3

    # Routines (docs/ROUTINES.md): how often the scheduler looks for due and
    # finished runs, the largest webhook body accepted, and an optional
    # Telegram chat that also gets routine notifications (needs
    # TELEGRAM_BOT_TOKEN; empty means in-app notifications only).
    ROUTINE_POLL_SECONDS: float = 5.0
    ROUTINE_WEBHOOK_MAX_BYTES: int = 65_536
    ROUTINE_TELEGRAM_CHAT_ID: str = ""

    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8')

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # The desktop profile (app/desktop/profile.py) is the whole configuration of
        # a desktop launch: neither the environment nor a .env file may change it.
        # Everywhere else this is pydantic-settings' own order, unchanged.
        if profile.active():
            return (init_settings,)
        return (init_settings, env_settings, dotenv_settings, file_secret_settings)

    @property
    def FRONTEND_ORIGIN_LIST(self) -> list[str]:
        return [origin.strip() for origin in self.FRONTEND_ORIGINS.split(",") if origin.strip()]

    @property
    def BOAT_ENABLED(self) -> bool:
        return bool(self.BOAT_API_KEY.strip())

    @property
    def LOCAL_ALLOWED_HOST_LIST(self) -> list[str]:
        return [host.strip().lower() for host in self.LOCAL_ALLOWED_HOSTS.split(",") if host.strip()]

    @property
    def TRUSTED_HOST_LIST(self) -> list[str]:
        hosts = [host.strip().lower() for host in self.TRUSTED_HOSTS.split(",") if host.strip()]
        public = public_url.hostname(self.OPEN_DOTS_PUBLIC_URL)
        return hosts + [public] if public and public not in hosts else hosts

    @property
    def PUBLIC_ORIGIN(self) -> str:
        """The normalized public address, or "" when none is set."""
        return self.OPEN_DOTS_PUBLIC_URL

    @field_validator("OPEN_DOTS_PUBLIC_URL")
    @classmethod
    def _check_public_url(cls, value: str) -> str:
        return public_url.normalize(value)

    @field_validator("LOOPBACK_TRUST")
    @classmethod
    def _check_loopback_trust(cls, value: str) -> str:
        value = value.strip().lower() or "off"
        if value not in ("off", "programs", "all"):
            raise ValueError('LOOPBACK_TRUST must be "off", "programs" or "all"')
        return value

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
    def DATABASE_DESCRIPTION(self) -> str:
        """Which database is in use, for logs and messages (never the password)."""
        if self.DATABASE_KIND == "sqlite":
            return f"SQLite file {self.SQLITE_FILE}"
        return (
            f"PostgreSQL {self.POSTGRES_USER}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}"
            f"/{self.POSTGRES_DB}"
        )

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

    @property
    def POSTGRES_SYNC_URL(self) -> str:
        """The PostgreSQL URL from the POSTGRES_* values, whatever database is
        selected (the SQLite copy tool reads from it)."""
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


settings = Settings(**profile.settings_overrides())  # type: ignore
