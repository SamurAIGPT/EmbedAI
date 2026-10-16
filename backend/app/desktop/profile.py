"""The desktop profile: the settings a desktop launch runs with (D-012).

It is switched on by exactly one caller, `desktop_main.py`, after the launcher
has handed over its values. It is never switched on by an environment
variable, a `.env` value or a flag, so a server or headless install cannot end
up in it by accident, and nothing a user has exported can change a desktop run.

While it is on, `Settings` (app/core/config.py) reads these values and its own
defaults and nothing else. This module imports nothing from `app`, because the
settings object is built from it at import time.
"""

from dataclasses import dataclass, field
from pathlib import Path

DATABASE_FILE_NAME = "open-dots.db"
WORKSPACE_DIR_NAME = "local"


@dataclass(frozen=True)
class DesktopProfile:
    data_dir: Path
    origin: str  # the address the page is served from, e.g. http://127.0.0.1:51234
    # Never shown by repr(): a profile ends up in tracebacks and test output.
    encryption_key: str = field(repr=False)
    claude_cli_path: str = ""

    @property
    def database_file(self) -> Path:
        return self.data_dir / DATABASE_FILE_NAME

    @property
    def backup_dir(self) -> Path:
        return self.data_dir / "backups"

    def settings(self) -> dict[str, object]:
        """The values given to `Settings` as explicit arguments."""
        return {
            "DATABASE_BACKEND": "sqlite",
            "SQLITE_PATH": str(self.database_file),
            "TOKEN_ENCRYPTION_KEYS": self.encryption_key,
            # Local execution is the point of the desktop app. Tools are on
            # by default; calls the CLI asks about wait for an app approval.
            "LOCAL_EXECUTION_ENABLED": True,
            "LOCAL_TOOLS_ENABLED": True,
            "LOCAL_WORKSPACE_ROOT": str(self.data_dir / WORKSPACE_DIR_NAME),
            "LOCAL_CLAUDE_CLI_PATH": self.claude_cli_path,
            "FRONTEND_ORIGINS": self.origin,
        }


_current: DesktopProfile | None = None


def activate(profile: DesktopProfile) -> None:
    global _current
    if _current is not None:
        raise RuntimeError("The desktop profile is already active.")
    _current = profile


def current() -> DesktopProfile | None:
    return _current


def active() -> bool:
    return _current is not None


def settings_overrides() -> dict[str, object]:
    """Arguments for `Settings(...)`: empty unless the profile is active."""
    return _current.settings() if _current is not None else {}
