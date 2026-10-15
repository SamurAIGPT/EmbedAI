"""The desktop profile decides every setting of a desktop launch; everywhere else
the settings behave exactly as they always did (D-012 decision 4)."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy.engine import make_url

from app.core.config import Settings
from app.desktop import profile

BACKEND_DIR = Path(__file__).parents[2]
KEY = "k" * 43 + "="


def make_profile(tmp_path: Path, **overrides) -> profile.DesktopProfile:
    values = dict(data_dir=tmp_path / "data", origin="http://127.0.0.1:51234", encryption_key=KEY)
    return profile.DesktopProfile(**{**values, **overrides})


@pytest.fixture
def desktop(monkeypatch, tmp_path):
    monkeypatch.setattr(profile, "_current", make_profile(tmp_path))
    return profile.current()


def test_without_the_profile_the_settings_sources_are_pydantics_own_order():
    """Source, headless and test runs: init arguments, environment, .env, secrets."""
    assert not profile.active()
    sources = ("init", "env", "dotenv", "secrets")
    assert Settings.settings_customise_sources(Settings, *sources) == sources


def test_without_the_profile_the_environment_and_dotenv_still_apply(monkeypatch, tmp_path):
    dotenv = tmp_path / ".env"
    dotenv.write_text("BOAT_ORG_ID=from-dotenv\nPOSTGRES_DB=from-dotenv\n", encoding="utf-8")
    monkeypatch.setenv("POSTGRES_DB", "from-environment")
    settings = Settings(_env_file=dotenv, TOKEN_ENCRYPTION_KEYS=KEY)
    assert settings.POSTGRES_DB == "from-environment"  # the environment beats .env
    assert settings.BOAT_ORG_ID == "from-dotenv"


def test_with_the_profile_only_the_profile_and_the_defaults_apply(monkeypatch, tmp_path, desktop):
    # Everything a developer's shell or .env might say that could change a desktop run.
    dotenv = tmp_path / ".env"
    dotenv.write_text("BOAT_API_KEY=from-dotenv\nTELEGRAM_BOT_TOKEN=from-dotenv\n", encoding="utf-8")
    for name, value in {
        "DATABASE_BACKEND": "postgres",
        "SQLITE_PATH": str(tmp_path / "elsewhere.db"),
        "LOCAL_EXECUTION_ENABLED": "false",
        "LOCAL_TOOLS_ENABLED": "false",
        "LOCAL_WORKSPACE_ROOT": str(tmp_path / "elsewhere"),
        "LOCAL_CLAUDE_CLI_PATH": "/not/claude",
        "FRONTEND_ORIGINS": "https://evil.example",
        "TOKEN_ENCRYPTION_KEYS": "from-the-environment",
        "BOAT_API_KEY": "from-the-environment",
    }.items():
        monkeypatch.setenv(name, value)

    settings = Settings(_env_file=dotenv, **profile.settings_overrides())

    assert settings.DATABASE_KIND == "sqlite"
    assert settings.SQLITE_FILE == desktop.data_dir / "open-dots.db"
    assert make_url(settings.ASYNC_DATABASE_URL).drivername == "sqlite+aiosqlite"
    assert settings.LOCAL_EXECUTION_ENABLED is True
    assert settings.LOCAL_TOOLS_ENABLED is True
    assert Path(settings.LOCAL_WORKSPACE_ROOT) == desktop.data_dir / "local"
    assert settings.LOCAL_CLAUDE_CLI_PATH == ""
    assert settings.FRONTEND_ORIGIN_LIST == ["http://127.0.0.1:51234"]
    assert settings.TOKEN_ENCRYPTION_KEYS == KEY
    # Optional providers are not part of the desktop profile, whatever else says.
    assert settings.BOAT_API_KEY == ""
    assert settings.BOAT_ENABLED is False
    assert settings.TELEGRAM_BOT_TOKEN == ""


def test_the_launchers_claude_path_is_the_one_profile_input_beyond_the_defaults(tmp_path):
    chosen = make_profile(tmp_path, claude_cli_path="C:/tools/claude.exe")
    assert chosen.settings()["LOCAL_CLAUDE_CLI_PATH"] == "C:/tools/claude.exe"


def test_the_profile_never_shows_the_key_when_printed(tmp_path):
    assert KEY not in repr(make_profile(tmp_path))
    assert KEY not in str(make_profile(tmp_path))


def test_the_profile_can_be_activated_only_once(monkeypatch, tmp_path):
    monkeypatch.setattr(profile, "_current", None)
    profile.activate(make_profile(tmp_path))
    with pytest.raises(RuntimeError, match="already active"):
        profile.activate(make_profile(tmp_path))


def test_no_setting_is_left_to_the_environment_in_a_fresh_desktop_process(tmp_path):
    """The real thing: a new interpreter, a hostile environment, a .env file in
    the working directory. Only the profile's values come out."""
    data = tmp_path / "data"
    work = tmp_path / "cwd"
    work.mkdir()
    (work / ".env").write_text("BOAT_API_KEY=from-dotenv\nDATABASE_BACKEND=postgres\n", encoding="utf-8")
    script = (
        "from pathlib import Path\n"
        "from app.desktop import profile\n"
        f"profile.activate(profile.DesktopProfile(data_dir=Path({str(data)!r}), "
        f"origin='http://127.0.0.1:9', encryption_key={KEY!r}))\n"
        "from app.core.config import settings\n"
        "print(settings.DATABASE_KIND, settings.BOAT_API_KEY == '', settings.SQLITE_FILE)\n"
    )
    env = {
        **os.environ,
        "PYTHONPATH": str(BACKEND_DIR),
        "BOAT_API_KEY": "from-the-environment",
        "DATABASE_BACKEND": "postgres",
        "TOKEN_ENCRYPTION_KEYS": "not-a-key",
    }
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=work, env=env, capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.split(maxsplit=2)[:2] == ["sqlite", "True"]
    assert result.stdout.strip().endswith(str(data / "open-dots.db"))


def test_the_claude_not_found_message_names_what_each_kind_of_install_actually_reads(monkeypatch, tmp_path):
    """A source install points at .env's LOCAL_CLAUDE_CLI_PATH; the desktop app has no
    .env, so it names the launcher variable instead."""
    from app.core.config import settings
    from app.services import local_claude_runner

    monkeypatch.setattr(settings, "LOCAL_CLAUDE_CLI_PATH", str(tmp_path / "no-such-claude"))

    assert not profile.active()
    source = local_claude_runner._status_blocking().error
    assert "LOCAL_CLAUDE_CLI_PATH" in source and "OPEN_DOTS_CLAUDE_PATH" not in source

    monkeypatch.setattr(profile, "_current", make_profile(tmp_path))
    desktop = local_claude_runner._status_blocking().error
    assert "OPEN_DOTS_CLAUDE_PATH" in desktop and "LOCAL_CLAUDE_CLI_PATH" not in desktop
    assert "Check again" in desktop

