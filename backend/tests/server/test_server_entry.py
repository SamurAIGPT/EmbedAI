"""The server run mode (app/server/entry.py): its settings, and one real process."""

import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest

from app.core.config import settings
from app.server.entry import EXIT_USAGE, ServerConfigError, main, read_options
from tests.conftest import BACKEND_DIR, using_sqlite
from tests.db.sqlite_helpers import run_alembic


@pytest.fixture
def ui(tmp_path):
    folder = tmp_path / "out"
    folder.mkdir()
    (folder / "index.html").write_text("<html>home</html>")
    (folder / "login").mkdir()
    (folder / "login" / "index.html").write_text("<html>login page</html>")
    return folder


def test_defaults_listen_on_loopback_port_8000(ui):
    options = read_options([], {"OPEN_DOTS_UI_DIR": str(ui)})
    assert (options.host, options.port, options.ui_dir) == ("127.0.0.1", 8000, ui.resolve())


def test_environment_and_flags_are_both_read_and_a_flag_wins(ui):
    env = {"OPEN_DOTS_UI_DIR": str(ui), "OPEN_DOTS_PORT": "9001", "OPEN_DOTS_HOST": "localhost"}
    assert read_options([], env).port == 9001
    assert read_options(["--port", "9002"], env).port == 9002


@pytest.mark.parametrize("port", ["abc", "0", "70000", "-1", ""])
def test_a_bad_port_says_what_to_change(ui, port):
    with pytest.raises(ServerConfigError, match="port"):
        read_options([], {"OPEN_DOTS_UI_DIR": str(ui), "OPEN_DOTS_PORT": port})


@pytest.mark.parametrize("host", ["0.0.0.0", "203.0.113.5", "dots.example.com", "::"])
def test_listening_beyond_this_machine_needs_an_explicit_opt_in(ui, host):
    with pytest.raises(ServerConfigError, match="OPEN_DOTS_ALLOW_PUBLIC_BIND"):
        read_options([], {"OPEN_DOTS_UI_DIR": str(ui), "OPEN_DOTS_HOST": host})
    allowed = {"OPEN_DOTS_UI_DIR": str(ui), "OPEN_DOTS_HOST": host, "OPEN_DOTS_ALLOW_PUBLIC_BIND": "1"}
    assert read_options([], allowed).host == host


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.2", "::1", "[::1]", "localhost", "LOCALHOST"])
def test_loopback_hosts_need_no_opt_in(ui, host):
    assert read_options([], {"OPEN_DOTS_UI_DIR": str(ui), "OPEN_DOTS_HOST": host}).host == host


def test_the_pages_folder_is_required_and_must_hold_a_build(tmp_path):
    with pytest.raises(ServerConfigError, match="OPEN_DOTS_UI_DIR"):
        read_options([], {})
    with pytest.raises(ServerConfigError, match="no index.html"):
        read_options([], {"OPEN_DOTS_UI_DIR": str(tmp_path)})


def test_main_reports_a_bad_setting_and_exits_without_starting(capsys, tmp_path):
    assert main([], {"OPEN_DOTS_UI_DIR": str(tmp_path)}) == EXIT_USAGE
    assert "no index.html" in capsys.readouterr().err


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def fetch(url: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()


@pytest.mark.skipif(not using_sqlite(), reason="the real-process check uses its own SQLite file")
def test_a_real_server_serves_pages_and_keeps_the_api_closed(ui, tmp_path):
    database = tmp_path / "server.db"
    assert run_alembic(database, "upgrade", "head").returncode == 0
    port = free_port()
    environment = {
        **os.environ,
        "DATABASE_BACKEND": "sqlite",
        "SQLITE_PATH": str(database),
        "TOKEN_ENCRYPTION_KEYS": settings.TOKEN_ENCRYPTION_KEYS,
        "DECISION_LOG_DIR": settings.DECISION_LOG_DIR,
        "ATTACHMENTS_DIR": str(tmp_path / "attachments"),
        "ENGINE_PROCESS_DIR": str(tmp_path / "engines"),
        "LOCAL_WORKSPACE_ROOT": str(tmp_path / "workspace"),
        "OPEN_DOTS_UI_DIR": str(ui),
        "OPEN_DOTS_PORT": str(port),
        "PYTHONUTF8": "1",
    }
    # The test must not depend on how the machine's .env sets local execution.
    environment.pop("LOCAL_EXECUTION_ENABLED", None)
    process = subprocess.Popen(
        [sys.executable, "-m", "app.server"], cwd=BACKEND_DIR, env=environment,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 40
        while True:
            if process.poll() is not None:
                pytest.fail(f"the server exited early ({process.returncode}):\n{process.stdout.read()}")
            try:
                status, body = fetch(f"{base}/api/v1/health")
                if status == 200:
                    break
            except OSError:
                pass
            if time.monotonic() > deadline:
                pytest.fail("the server did not come up in 40 seconds")
            time.sleep(0.3)

        assert fetch(f"{base}/login/") == (200, "<html>login page</html>")
        assert fetch(f"{base}/")[0] == 200
        status, body = fetch(f"{base}/api/v1/agents")
        assert status == 401 and "auth_required" in body
        assert fetch(f"{base}/nowhere/")[0] == 404
    finally:
        process.terminate()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        if process.stdout:
            process.stdout.close()
