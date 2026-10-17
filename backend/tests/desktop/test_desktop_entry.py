"""The desktop backend as the launcher runs it: a real process, a real socket, the
launcher's message on stdin (D-012).

Each launch imports the app and runs the migrations, so these tests take seconds
each. They are the lifecycle tests: start with no .env, keep the key and the
data across launches, refuse what must be refused, stop when the launcher goes.
"""

import hashlib
import hmac
import itertools
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet

from app.db.sqlite_baseline import BASELINE_REVISION
from app.desktop import entry
from tests.db.sqlite_helpers import run_alembic

BACKEND_DIR = Path(__file__).parents[2]
TOKEN = "launch-" + "t" * 40
KEY = Fernet.generate_key().decode()
OTHER_KEY = Fernet.generate_key().decode()

# What a developer's shell or .env might say; none of it may reach a desktop run.
HOSTILE_ENV = {
    "DATABASE_BACKEND": "postgres",
    "POSTGRES_HOST": "unreachable.invalid",
    "SQLITE_PATH": "elsewhere.db",
    "TOKEN_ENCRYPTION_KEYS": "not-a-key",
    "FRONTEND_ORIGINS": "https://evil.example",
    "LOCAL_EXECUTION_ENABLED": "false",
    "BOAT_API_KEY": "from-the-environment",
}


_counter = itertools.count()


class Backend:
    def __init__(self, tmp_path: Path, data_dir: Path | None = None, **message):
        self.tmp = tmp_path
        self.data_dir = data_dir or tmp_path / "data"
        self.ui_dir = tmp_path / "ui"
        self.message = {
            "version": 1,
            "token": TOKEN,
            "encryption_key": KEY,
            "data_dir": str(self.data_dir),
            "ui_dir": str(self.ui_dir),
            "claude_cli_path": str(tmp_path / "no-such-claude"),
            **message,
        }
        self.message = {k: v for k, v in self.message.items() if v is not None}
        self.stdout: list[str] = []
        self.process: subprocess.Popen | None = None
        self._log_name = f"stderr-{next(_counter)}.log"

    @property
    def stderr_path(self) -> Path:
        return self.tmp / self._log_name

    def start(self, raw_line: bytes | None = None, close_stdin: bool = False) -> "Backend":
        (self.ui_dir).mkdir(exist_ok=True)
        if not (self.ui_dir / "index.html").exists():
            (self.ui_dir / "index.html").write_text("<h1>open dots</h1>", encoding="utf-8")
        work = self.tmp / "cwd"
        work.mkdir(exist_ok=True)
        (work / ".env").write_text("BOAT_API_KEY=from-dotenv\nDATABASE_BACKEND=postgres\n", encoding="utf-8")
        env = {**os.environ, **HOSTILE_ENV, "PYTHONUTF8": "1"}
        self.process = subprocess.Popen(
            [sys.executable, str(BACKEND_DIR / "desktop_main.py")],
            cwd=work, env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.stderr_path.open("wb"),
        )
        threading.Thread(target=self._pump, daemon=True).start()
        line = raw_line if raw_line is not None else (json.dumps(self.message) + "\n").encode()
        self.process.stdin.write(line)
        self.process.stdin.flush()
        if close_stdin:
            self.process.stdin.close()
        return self

    def _pump(self) -> None:
        for raw in self.process.stdout:
            self.stdout.append(raw.decode("utf-8", "replace").rstrip("\r\n"))

    def ready(self, timeout: float = 120) -> dict:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            for line in list(self.stdout):
                if line.startswith(entry.READY_PREFIX):
                    return json.loads(line[len(entry.READY_PREFIX):])
            if self.process.poll() is not None:
                break
            time.sleep(0.05)
        raise AssertionError(f"not ready (exit {self.process.poll()}):\n{self.log()}")

    def log(self) -> str:
        return self.stderr_path.read_text(encoding="utf-8", errors="replace") if self.stderr_path.exists() else ""

    def exit_code(self, timeout: float = 120) -> int:
        return self.process.wait(timeout=timeout)

    def stop(self, timeout: float = 40) -> int:
        """The way the launcher stops it: close stdin."""
        try:
            self.process.stdin.close()
        except OSError:
            pass
        try:
            return self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.process.kill()
            raise

    def kill(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.kill()
            self.process.wait(timeout=30)


@pytest.fixture
def launches():
    started: list[Backend] = []
    yield started
    for backend in started:
        backend.kill()


def launch(launches, tmp_path, **kwargs) -> Backend:
    backend = Backend(tmp_path, **kwargs)
    launches.append(backend)
    return backend


def get(port: int, path: str, token: str | None = TOKEN, **headers) -> httpx.Response:
    if token is not None:
        headers["x-open-dots-token"] = token
    return httpx.get(f"http://127.0.0.1:{port}{path}", headers=headers, timeout=60)


def tables(database: Path) -> set[str]:
    connection = sqlite3.connect(database)
    try:
        return {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        connection.close()


def test_a_first_launch_needs_no_env_and_serves_the_api_and_the_page_behind_the_token(launches, tmp_path):
    backend = launch(launches, tmp_path).start()
    ready = backend.ready()
    port = ready["port"]

    # The ready line is provably this backend's.
    assert ready["proof"] == hmac.new(TOKEN.encode(), f"ready:{port}".encode(), hashlib.sha256).hexdigest()
    assert 1024 < port < 65536

    # Nothing answers without the token, whatever the path.
    for path in ("/", "/api/v1/providers", "/api/v1/threads", "/api/v1/local/status", "/nope"):
        assert get(port, path, token=None).status_code == 401, path
    assert get(port, "/api/v1/providers", token="x" * 47).status_code == 401

    # With it: the API, and the built page from the same address.
    assert get(port, "/api/v1/providers").status_code == 200
    page = get(port, "/")
    assert (page.status_code, page.text) == (200, "<h1>open dots</h1>")

    # The profile, not the hostile environment, decided everything.
    status = get(port, "/api/v1/local/status").json()
    assert status["enabled"] is True and status["tools_enabled"] is True
    # The launcher's claude path does not exist: the message is the desktop's, not the .env one.
    assert status["available"] is False and "Claude CLI not found" in status["reason"]
    assert "Settings" in status["reason"] and "LOCAL_CLAUDE_CLI_PATH" not in status["reason"]
    assert get(port, "/api/v1/local/status", origin="https://evil.example").json()["reason"] == (
        "Local execution request has an untrusted Origin."
    )
    assert get(port, "/api/v1/local/status", origin=f"http://127.0.0.1:{port}").json()["reason"] != (
        "Local execution request has an untrusted Origin."
    )

    # SQLite in the data folder, migrated to the newest revision, nothing elsewhere.
    database = backend.data_dir / "open-dots.db"
    assert {"thread", "turn", "event", "approval", "alembic_version"} <= tables(database)

    # The launcher closing its end of stdin is how it stops us.
    assert backend.stop() == 0

    # A stop leaves the database file whole: the file alone, without its write-ahead
    # log, holds everything, so copying it as a backup loses nothing.
    wal = database.with_name(database.name + "-wal")
    assert not wal.exists() or wal.stat().st_size == 0
    alone = tmp_path / "copied-alone.db"
    alone.write_bytes(database.read_bytes())
    assert {"thread", "turn", "event", "approval"} <= tables(alone)
    assert "process" in backend.log()  # the log says which process served, for finding it


def test_the_token_and_the_key_are_never_written_to_the_log_or_the_stream(launches, tmp_path):
    backend = launch(launches, tmp_path).start()
    port = backend.ready()["port"]
    get(port, "/api/v1/threads")
    get(port, "/api/v1/threads", token=None)
    backend.stop()
    everything = backend.log() + "\n".join(backend.stdout)
    assert TOKEN not in everything and KEY not in everything


def test_each_launch_gets_its_own_port_from_the_operating_system(launches, tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    first = launch(launches, tmp_path / "a").start()
    second = launch(launches, tmp_path / "b").start()
    ports = {first.ready()["port"], second.ready()["port"]}
    assert len(ports) == 2
    assert first.stop() == 0 and second.stop() == 0


def test_a_second_launch_keeps_the_data_and_makes_no_backup_when_nothing_is_pending(launches, tmp_path):
    first = launch(launches, tmp_path).start()
    first.ready()
    assert first.stop() == 0
    database = first.data_dir / "open-dots.db"
    connection = sqlite3.connect(database)
    connection.execute("INSERT INTO user_credential (user_id, provider, file_path, ciphertext) VALUES (?,?,?,?)",
                       ("u", "github", "/x", Fernet(KEY.encode()).encrypt(b"secret").decode()))
    connection.commit()
    connection.close()

    second = launch(launches, tmp_path).start()
    second.ready()
    assert second.stop() == 0
    count = sqlite3.connect(database).execute("SELECT count(*) FROM user_credential").fetchone()[0]
    assert count == 1
    assert not (first.data_dir / "backups").exists()


def test_a_key_that_cannot_open_stored_credentials_stops_the_launch_and_changes_nothing(launches, tmp_path):
    first = launch(launches, tmp_path).start()
    first.ready()
    assert first.stop() == 0
    database = first.data_dir / "open-dots.db"
    ciphertext = Fernet(KEY.encode()).encrypt(b"secret").decode()
    connection = sqlite3.connect(database)
    connection.execute("INSERT INTO user_credential (user_id, provider, file_path, ciphertext) VALUES (?,?,?,?)",
                       ("u", "github", "/x", ciphertext))
    connection.commit()
    connection.close()
    revision_before = sqlite3.connect(database).execute("SELECT version_num FROM alembic_version").fetchone()

    wrong = launch(launches, tmp_path, encryption_key=OTHER_KEY).start()
    assert wrong.exit_code() == entry.EXIT_KEY_MISMATCH
    assert not [line for line in wrong.stdout if line.startswith(entry.READY_PREFIX)]
    assert "cannot be decrypted" in wrong.log()
    assert OTHER_KEY not in wrong.log() + "".join(wrong.stdout)

    connection = sqlite3.connect(database)
    assert connection.execute("SELECT ciphertext FROM user_credential").fetchall() == [(ciphertext,)]
    assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == revision_before
    connection.close()

    # And the right key still works afterwards.
    again = launch(launches, tmp_path).start()
    again.ready()
    assert again.stop() == 0


def test_a_database_with_a_migration_pending_is_copied_aside_first(launches, tmp_path):
    database = tmp_path / "data" / "open-dots.db"
    old = run_alembic(database, "upgrade", BASELINE_REVISION)
    assert old.returncode == 0, old.stderr
    ciphertext = Fernet(KEY.encode()).encrypt(b"secret").decode()
    connection = sqlite3.connect(database)
    connection.execute("INSERT INTO user_credential (user_id, provider, file_path, ciphertext) VALUES (?,?,?,?)",
                       ("u", "github", "/x", ciphertext))
    connection.commit()
    connection.close()
    assert "thread" not in tables(database)

    backend = launch(launches, tmp_path).start()
    backend.ready()
    assert backend.stop() == 0

    copies = list((tmp_path / "data" / "backups").glob("open-dots-*.db"))
    assert len(copies) == 1 and BASELINE_REVISION in copies[0].name
    old_copy = sqlite3.connect(copies[0])
    assert old_copy.execute("SELECT version_num FROM alembic_version").fetchone() == (BASELINE_REVISION,)
    assert old_copy.execute("SELECT ciphertext FROM user_credential").fetchall() == [(ciphertext,)]
    assert "thread" not in tables(copies[0])
    assert "thread" in tables(database)  # the real database moved on
    assert sqlite3.connect(database).execute("SELECT ciphertext FROM user_credential").fetchall() == [(ciphertext,)]

    # Up to date now: another launch makes no further copy.
    again = launch(launches, tmp_path).start()
    again.ready()
    assert again.stop() == 0
    assert len(list((tmp_path / "data" / "backups").glob("open-dots-*.db"))) == 1


@pytest.mark.parametrize("content", ["garbage", "tables-without-a-version"])
def test_a_file_the_app_did_not_make_is_refused_and_left_exactly_as_it_was(launches, tmp_path, content):
    database = tmp_path / "data" / "open-dots.db"
    database.parent.mkdir()
    if content == "garbage":
        database.write_bytes(b"this is not a database " * 40)
    else:
        connection = sqlite3.connect(database)
        connection.execute("CREATE TABLE somebody_elses (id INTEGER)")
        connection.commit()
        connection.close()
    before = hashlib.sha256(database.read_bytes()).hexdigest()

    backend = launch(launches, tmp_path).start()
    assert backend.exit_code() == entry.EXIT_DATABASE
    assert not [line for line in backend.stdout if line.startswith(entry.READY_PREFIX)]
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    assert not (tmp_path / "data" / "backups").exists()


def test_a_refused_message_stops_the_launch_before_anything_is_created(launches, tmp_path):
    backend = launch(launches, tmp_path).start(raw_line=b"this is not json\n")
    assert backend.exit_code() == entry.EXIT_HANDSHAKE
    assert backend.stdout == []
    assert not (tmp_path / "data").exists()


def test_a_launcher_that_goes_away_without_a_message_stops_the_launch(launches, tmp_path):
    backend = launch(launches, tmp_path).start(raw_line=b"", close_stdin=True)
    assert backend.exit_code() == entry.EXIT_HANDSHAKE
    assert not (tmp_path / "data").exists()


def test_missing_page_files_are_reported_and_nothing_is_served(launches, tmp_path):
    backend = launch(launches, tmp_path, ui_dir=str(tmp_path / "empty-ui"))
    (tmp_path / "empty-ui").mkdir()
    backend.start()
    assert backend.exit_code() == entry.EXIT_UI_MISSING
    assert "npm run build" in backend.log()
    assert not [line for line in backend.stdout if line.startswith(entry.READY_PREFIX)]


def test_a_launcher_that_dies_right_after_the_message_does_not_leave_a_backend_running(launches, tmp_path):
    backend = launch(launches, tmp_path).start(close_stdin=True)
    started = time.monotonic()
    code = backend.exit_code(timeout=120)
    assert code in (0, 1)  # stopped, before or after it was listening
    assert time.monotonic() - started < 120


def test_the_launcher_ending_while_the_backend_serves_stops_it(launches, tmp_path):
    """Hard kill of the launcher closes its end of the pipe, the same event."""
    backend = launch(launches, tmp_path).start()
    backend.ready()
    backend.process.stdin.close()
    assert backend.process.wait(timeout=40) == 0


def test_a_silent_launcher_is_given_up_on_with_the_handshake_exit_code():
    read_end, write_end = os.pipe()
    stream = os.fdopen(read_end, "rb")
    try:
        assert entry.main(stdin=stream, handshake_timeout=0.3) == entry.EXIT_HANDSHAKE
    finally:
        os.close(write_end)
        stream.close()


def test_the_server_stops_promptly_even_while_a_worker_thread_is_still_busy():
    """A Claude status check runs the CLI in a worker thread. `server.run` would wait
    for that thread before returning, for up to five minutes, and so would quitting."""
    import uvicorn
    from fastapi import FastAPI
    from fastapi.concurrency import run_in_threadpool

    app = FastAPI()

    @app.get("/slow")
    async def slow():
        await run_in_threadpool(time.sleep, 12)
        return {}

    sock = entry.bind_loopback()
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_config=None, timeout_graceful_shutdown=1))
    serving = threading.Thread(target=entry.serve, args=(server, sock), daemon=True)
    serving.start()
    end = time.monotonic() + 30
    while not server.started and time.monotonic() < end:
        time.sleep(0.05)
    assert server.started

    def request():
        try:
            httpx.get(f"http://127.0.0.1:{port}/slow", timeout=30)
        except httpx.HTTPError:
            pass  # the server went away mid-request, which is the point

    threading.Thread(target=request, daemon=True).start()
    time.sleep(0.7)  # the request is now inside its worker thread
    stopped_at = time.monotonic()
    server.should_exit = True
    serving.join(timeout=8)
    assert not serving.is_alive(), "the server did not stop while a worker thread was busy"
    assert time.monotonic() - stopped_at < 6



def test_a_second_backend_on_the_same_data_folder_is_refused_while_the_first_serves(launches, tmp_path):
    first = launch(launches, tmp_path).start()
    port = first.ready()["port"]
    second = launch(launches, tmp_path).start()
    assert second.exit_code() == entry.EXIT_DATABASE
    assert "already using the database" in second.log()
    assert f"process {first.process.pid}" in second.log()
    # The first one is untouched and still serves.
    assert get(port, "/api/v1/providers").status_code == 200
    assert first.stop() == 0
    # Once the first has stopped, the folder can be opened again.
    third = launch(launches, tmp_path).start()
    third.ready()
    assert third.stop() == 0
