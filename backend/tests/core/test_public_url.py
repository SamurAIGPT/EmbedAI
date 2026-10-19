"""OPEN_DOTS_PUBLIC_URL: one value for the address a self-hosted server is reached at
(app/core/public_url.py and its use in Settings and the pairing CLI)."""

import os
import subprocess
import sys

import pytest
from pydantic import ValidationError

from app.auth.request_info import host_is_trusted
from app.core import public_url
from app.core.config import Settings, settings
from tests.conftest import BACKEND_DIR, using_sqlite
from tests.db.sqlite_helpers import run_alembic


@pytest.mark.parametrize(
    "given, expected",
    [
        ("", ""),
        ("   ", ""),
        ("https://dots.example.com", "https://dots.example.com"),
        ("https://dots.example.com/", "https://dots.example.com"),
        ("  HTTPS://Dots.Example.COM  ", "https://dots.example.com"),
        ("http://203.0.113.5:8080", "http://203.0.113.5:8080"),
        ("https://dots.example.com:8443/", "https://dots.example.com:8443"),
        ("http://[::1]:8000", "http://[::1]:8000"),
    ],
)
def test_normalize_keeps_only_the_origin(given, expected):
    assert public_url.normalize(given) == expected


@pytest.mark.parametrize(
    "given",
    [
        "dots.example.com",  # no scheme
        "ftp://dots.example.com",
        "https://",
        "https://user:secret@dots.example.com",
        "https://dots.example.com/app",
        "https://dots.example.com/?a=1",
        "https://dots.example.com/#top",
        "https://dots.example.com:notaport",
    ],
)
def test_normalize_refuses_anything_that_is_not_an_origin(given):
    with pytest.raises(ValueError, match="OPEN_DOTS_PUBLIC_URL"):
        public_url.normalize(given)


def test_the_host_name_is_what_a_host_header_names():
    assert public_url.hostname("https://dots.example.com:8443") == "dots.example.com"
    assert public_url.hostname("http://[::1]:8000") == "[::1]"
    assert public_url.hostname("") == ""


def test_a_pairing_link_keeps_the_code_in_the_fragment():
    assert public_url.pairing_link("https://dots.example.com", "UQQ3-8HZK") == "https://dots.example.com/pair#code=UQQ3-8HZK"


def test_settings_answer_to_the_public_host_without_listing_it_twice():
    config = Settings(OPEN_DOTS_PUBLIC_URL="https://Dots.Example.com/", TRUSTED_HOSTS="localhost")
    assert config.PUBLIC_ORIGIN == "https://dots.example.com"
    assert config.TRUSTED_HOST_LIST == ["localhost", "dots.example.com"]
    listed = Settings(OPEN_DOTS_PUBLIC_URL="https://dots.example.com", TRUSTED_HOSTS="localhost,dots.example.com")
    assert listed.TRUSTED_HOST_LIST == ["localhost", "dots.example.com"]


def test_without_a_public_url_nothing_changes():
    config = Settings()
    assert config.PUBLIC_ORIGIN == ""
    assert config.TRUSTED_HOST_LIST == [h.strip().lower() for h in config.TRUSTED_HOSTS.split(",") if h.strip()]


def test_a_bad_public_url_stops_the_server_from_starting_and_says_why():
    with pytest.raises(ValidationError, match="OPEN_DOTS_PUBLIC_URL"):
        Settings(OPEN_DOTS_PUBLIC_URL="https://dots.example.com/app")


def test_the_host_check_accepts_the_public_name_and_still_refuses_others(monkeypatch):
    monkeypatch.setattr(settings, "OPEN_DOTS_PUBLIC_URL", "https://dots.example.com")
    monkeypatch.setattr(settings, "TRUSTED_HOSTS", "localhost,127.0.0.1,[::1]")
    assert host_is_trusted("dots.example.com")
    assert host_is_trusted("localhost")
    assert not host_is_trusted("evil.example.net")


@pytest.mark.skipif(not using_sqlite(), reason="the CLI check uses its own SQLite file")
def test_the_pairing_command_prints_a_full_link_only_when_the_address_is_set(tmp_path):
    database = tmp_path / "cli.db"
    assert run_alembic(database, "upgrade", "head").returncode == 0
    environment = {
        **os.environ,
        "DATABASE_BACKEND": "sqlite",
        "SQLITE_PATH": str(database),
        "TOKEN_ENCRYPTION_KEYS": settings.TOKEN_ENCRYPTION_KEYS,
        "DECISION_LOG_DIR": settings.DECISION_LOG_DIR,
        "PYTHONUTF8": "1",
    }

    def pairing_output(public: str) -> str:
        env = {**environment, "OPEN_DOTS_PUBLIC_URL": public}
        result = subprocess.run(
            [sys.executable, "-m", "app.auth.cli", "pairing-code", "--scope", "chat"],
            cwd=BACKEND_DIR, env=env, capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, result.stderr
        return result.stdout

    with_address = pairing_output("https://dots.example.com")
    code = next(line.split()[-1] for line in with_address.splitlines() if line.startswith("pairing code:"))
    assert f"open:         https://dots.example.com/pair#code={code}" in with_address
    assert "Open /pair on the web UI's address" not in with_address

    without = pairing_output("")
    assert "Open /pair on the web UI's address and type it." in without
    assert "https://" not in without
