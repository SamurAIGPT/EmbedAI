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

    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8')

    @property
    def FRONTEND_ORIGIN_LIST(self) -> list[str]:
        return [origin.strip() for origin in self.FRONTEND_ORIGINS.split(",") if origin.strip()]

    @property
    def ASYNC_DATABASE_URL(self) -> str:
        return self._database_url("postgresql+asyncpg")

    @property
    def SYNC_DATABASE_URL(self) -> str:
        return self._database_url("postgresql+psycopg2")

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
