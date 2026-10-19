"""The trusted-proxy rules against a real `python -m app.server` process.

A client that connects from 127.0.0.2 is a loopback address to the application but is not one of
the two addresses Uvicorn trusts by default (127.0.0.1 and ::1), which makes it a stand-in for a
proxy that is not trusted, or, when listed in TRUSTED_PROXIES, for one that is. Linux only: it
answers on the whole 127.0.0.0/8 range, macOS and Windows do not.

What each case watches is what the server decides from the forwarded headers: whether the sign-in
cookie is marked Secure (the scheme) and who a failed sign-in is counted against (the client).
"""

import os
import subprocess
import sys
import time
from contextlib import contextmanager

import httpx
import pytest

from app.auth import limiter
from app.core.config import settings
from tests.conftest import BACKEND_DIR, using_sqlite
from tests.db.sqlite_helpers import run_alembic
from tests.server.test_server_entry import free_port

pytestmark = [
    pytest.mark.skipif(not sys.platform.startswith("linux"), reason="needs 127.0.0.2 to be a local address"),
    pytest.mark.skipif(not using_sqlite(), reason="uses its own SQLite file"),
]

HOST = "dots.example.com"
PASSPHRASE = "a long passphrase for the proxy tests"
UNTRUSTED_PEER = "127.0.0.2"


@contextmanager
def running_server(tmp_path, **extra):
    """A server on a scratch database with the owner passphrase set; yields (port, log reader)."""
    pages = tmp_path / "ui"
    pages.mkdir()
    (pages / "index.html").write_text("<html></html>")
    database = tmp_path / "server.db"
    assert run_alembic(database, "upgrade", "head").returncode == 0
    port = free_port()
    environment = {
        **{k: v for k, v in os.environ.items() if k not in ("TRUSTED_PROXIES", "FORWARDED_ALLOW_IPS")},
        "DATABASE_BACKEND": "sqlite",
        "SQLITE_PATH": str(database),
        "TOKEN_ENCRYPTION_KEYS": settings.TOKEN_ENCRYPTION_KEYS,
        "DECISION_LOG_DIR": str(tmp_path / "decisions"),
        "ATTACHMENTS_DIR": str(tmp_path / "attachments"),
        "ENGINE_PROCESS_DIR": str(tmp_path / "engines"),
        "LOCAL_WORKSPACE_ROOT": str(tmp_path / "workspace"),
        "OPEN_DOTS_UI_DIR": str(pages),
        "OPEN_DOTS_PORT": str(port),
        "OPEN_DOTS_PUBLIC_URL": f"https://{HOST}",
        "LOOPBACK_TRUST": "off",
        "PYTHONUTF8": "1",
        **extra,
    }
    shell = subprocess.run([sys.executable, "-m", "app.auth.cli", "set-passphrase", "--stdin"], cwd=BACKEND_DIR, env=environment,
                           input=PASSPHRASE + "\n", capture_output=True, text=True, timeout=60)
    assert shell.returncode == 0, shell.stderr
    log_path = tmp_path / "server.log"
    log = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen([sys.executable, "-m", "app.server"], cwd=BACKEND_DIR, env=environment, stdout=log, stderr=subprocess.STDOUT)

    def read_log() -> str:
        log.flush()
        return log_path.read_text(encoding="utf-8", errors="replace")

    try:
        deadline = time.monotonic() + 40
        while True:
            if process.poll() is not None:
                pytest.fail(f"the server exited early ({process.returncode}):\n{read_log()[-3000:]}")
            try:
                if httpx.get(f"http://127.0.0.1:{port}/api/v1/health", timeout=2).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if time.monotonic() > deadline:
                pytest.fail(f"the server did not come up in 40 seconds:\n{read_log()[-3000:]}")
            time.sleep(0.3)
        yield port, read_log
    finally:
        process.terminate()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        log.close()


def visitor(port: int, *, from_address: str, claims: str, https: bool = True):
    """One sign-in attempt, as a proxy at `from_address` would forward a visitor it calls `claims`."""
    def attempt(passphrase: str) -> httpx.Response:
        headers = {"Host": HOST, "X-Forwarded-For": claims}
        if https:
            headers["X-Forwarded-Proto"] = "https"
        transport = httpx.HTTPTransport(local_address=from_address)
        with httpx.Client(transport=transport, base_url=f"http://127.0.0.1:{port}", timeout=15) as client:
            return client.post("/api/v1/auth/login", json={"passphrase": passphrase}, headers=headers)

    return attempt


def fail_until_locked(attempt) -> None:
    for _ in range(limiter.login_lockout.max_failures):
        assert attempt("wrong passphrase").status_code == 401


def test_by_default_only_loopback_is_believed(tmp_path):
    with running_server(tmp_path) as (port, _log):
        # 127.0.0.1 is a trusted proxy: its forwarded scheme and visitor are believed
        trusted = visitor(port, from_address="127.0.0.1", claims="198.51.100.1")
        assert "Secure" in trusted(PASSPHRASE).headers["set-cookie"]
        fail_until_locked(trusted)
        assert trusted(PASSPHRASE).status_code == 429
        assert visitor(port, from_address="127.0.0.1", claims="198.51.100.2")(PASSPHRASE).status_code == 200  # another visitor

        # 127.0.0.2 is not on the list: the same headers change nothing
        stranger = visitor(port, from_address=UNTRUSTED_PEER, claims="198.51.100.3")
        assert "Secure" not in stranger(PASSPHRASE).headers["set-cookie"]
        fail_until_locked(stranger)
        other_claim = visitor(port, from_address=UNTRUSTED_PEER, claims="198.51.100.4")
        assert other_claim(PASSPHRASE).status_code == 429  # one socket, one bucket, whatever it claims


def test_a_listed_proxy_is_believed(tmp_path):
    with running_server(tmp_path, TRUSTED_PROXIES=UNTRUSTED_PEER) as (port, _log):
        listed = visitor(port, from_address=UNTRUSTED_PEER, claims="198.51.100.3")
        assert "Secure" in listed(PASSPHRASE).headers["set-cookie"]
        fail_until_locked(listed)
        assert listed(PASSPHRASE).status_code == 429
        assert visitor(port, from_address=UNTRUSTED_PEER, claims="198.51.100.4")(PASSPHRASE).status_code == 200


def test_a_client_supplied_first_hop_does_not_choose_the_bucket(tmp_path):
    with running_server(tmp_path, TRUSTED_PROXIES=UNTRUSTED_PEER) as (port, _log):
        fail_until_locked(visitor(port, from_address=UNTRUSTED_PEER, claims="198.51.100.9"))
        # the proxy appended the real visitor (198.51.100.9) after whatever the visitor itself sent
        spoofed = visitor(port, from_address=UNTRUSTED_PEER, claims="198.51.100.77, 198.51.100.9")
        assert spoofed(PASSPHRASE).status_code == 429


def test_the_environment_cannot_widen_the_list(tmp_path):
    with running_server(tmp_path, FORWARDED_ALLOW_IPS="*") as (port, read_log):
        stranger = visitor(port, from_address=UNTRUSTED_PEER, claims="198.51.100.3")
        assert "Secure" not in stranger(PASSPHRASE).headers["set-cookie"]
        fail_until_locked(stranger)
        assert visitor(port, from_address=UNTRUSTED_PEER, claims="198.51.100.4")(PASSPHRASE).status_code == 429
        assert "FORWARDED_ALLOW_IPS" in read_log() and "ignored" in read_log()


def test_a_bad_list_stops_the_real_server(tmp_path):
    (tmp_path / "index.html").write_text("<html></html>")
    environment = {**os.environ, "OPEN_DOTS_UI_DIR": str(tmp_path), "TRUSTED_PROXIES": "*", "PYTHONUTF8": "1"}
    result = subprocess.run([sys.executable, "-m", "app.server"], cwd=BACKEND_DIR, env=environment,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 2 and "TRUSTED_PROXIES" in result.stderr
