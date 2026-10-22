"""Start the real backend as its own process, for tests that need real signals,
real ports and real child processes (docs/RELIABILITY.md).

Every backend gets its own scratch folder: a fresh SQLite database migrated
with the real Alembic entry point, its own workspace, decision log, HOME and a
fake `claude` executable (tests/e2e/fake_claude.py). It always runs on SQLite,
whatever database the rest of the suite uses, so it never touches shared rows.
"""

import json
import os
import signal
import socket
import sqlite3
import stat
import subprocess
import sys
import time
from pathlib import Path

import httpx

BACKEND_DIR = Path(__file__).parents[2]
FAKE_CLAUDE = Path(__file__).with_name("fake_claude.py")
# The throwaway key CI uses (a valid Fernet key; protects nothing).
TEST_KEY = "3n3b2YkQd0xVv8m1c1m0nq1l4Z3xk9q0n7xv7y5q8wE="


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # A zombie (exited, not yet reaped by its parent) counts as gone.
    try:
        out = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, timeout=10)
        return bool(out.stdout.strip()) and not out.stdout.strip().startswith("Z")
    except (OSError, subprocess.SubprocessError):
        return True


def wait_for(check, timeout: float = 30.0, interval: float = 0.1, what: str = "a condition"):
    end = time.monotonic() + timeout
    while True:
        value = check()
        if value:
            return value
        if time.monotonic() > end:
            raise AssertionError(f"timed out waiting for {what}")
        time.sleep(interval)


class Backend:
    def __init__(self, root: Path, *, env: dict | None = None, graceful_seconds: float | None = None,
                 database: Path | None = None) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.database = database or root / "data" / "open-dots.db"
        self.plan = root / "cli" / "plan.json"
        self.plan.parent.mkdir(parents=True, exist_ok=True)
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.log_path = root / f"backend-{self.port}.log"
        self.graceful_seconds = graceful_seconds
        self.extra_env = env or {}
        self.process: subprocess.Popen | None = None
        self.claude = self._make_claude()
        self.script({"type": "chat"})

    # -- the fake CLI

    def _make_claude(self) -> Path:
        bin_dir = self.root / "bin"
        bin_dir.mkdir(exist_ok=True)
        exe = bin_dir / "claude"
        exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE_CLAUDE}" "$@"\n', encoding="utf-8")
        exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return exe

    def script(self, *steps: dict) -> None:
        """What the fake CLI plays: invocation N plays steps[N] (the last repeats)."""
        self.plan.write_text(json.dumps({"steps": list(steps)}), encoding="utf-8")
        (self.plan.parent / "count").unlink(missing_ok=True)

    def cli_log(self, event: str | None = None) -> list[dict]:
        path = self.plan.parent / "log.jsonl"
        if not path.exists():
            return []
        entries = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        return [e for e in entries if event is None or e["event"] == event]

    # -- the process

    def env(self) -> dict:
        home = self.root / "home"
        home.mkdir(exist_ok=True)
        keep = {k: os.environ[k] for k in ("PATH", "SYSTEMROOT", "TMPDIR", "LANG") if k in os.environ}
        return {
            **keep,
            "HOME": str(home),
            "PYTHONPATH": str(BACKEND_DIR),
            "PYTHONUTF8": "1",
            "DATABASE_BACKEND": "sqlite",
            "SQLITE_PATH": str(self.database),
            "TOKEN_ENCRYPTION_KEYS": TEST_KEY,
            "BOAT_API_KEY": "",
            "TELEGRAM_BOT_TOKEN": "",
            "LOCAL_EXECUTION_ENABLED": "true",
            "LOCAL_TOOLS_ENABLED": "true",
            "LOCAL_CLAUDE_CLI_PATH": str(self.claude),
            "LOCAL_CODEX_CLI_PATH": str(self.root / "bin" / "no-codex"),
            # The server allow-lists child-process env vars. Expose the fake
            # CLI's FAKE_* configuration to subprocesses in E2E runs, just as
            # tests that run the adapters in-process do in conftest.py.
            "OPEN_DOTS_ENGINE_ENV": "FAKE_*",
            "LOCAL_WORKSPACE_ROOT": str(self.root / "work"),
            "DECISION_LOG_DIR": str(self.root / "decisions"),
            "ATTACHMENTS_DIR": str(self.root / "attachments"),
            "FAKE_CLAUDE_PLAN": str(self.plan),
            "THREAD_HEARTBEAT_SECONDS": "1",
            "THREAD_LEASE_SECONDS": "6",
            "ROUTINE_POLL_SECONDS": "0.5",
            "LOOPBACK_TRUST": "off",
            "FRONTEND_ORIGINS": "http://localhost:3000",
            **self.extra_env,
        }

    def migrate(self) -> None:
        # Alembic in its own process with this backend's settings, the way a
        # user runs it (and without importing the app's settings here, so the
        # smoke script needs no environment of its own).
        self.database.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=BACKEND_DIR, env=self.env(), capture_output=True, text=True, timeout=180,
        )
        assert result.returncode == 0, result.stderr

    def start(self, *, wait: bool = True, migrate: bool = True) -> "Backend":
        if migrate:
            self.migrate()
        args = [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(self.port)]
        if self.graceful_seconds is not None:
            args += ["--timeout-graceful-shutdown", str(self.graceful_seconds)]
        log = self.log_path.open("ab")
        self.process = subprocess.Popen(args, cwd=self.root, env=self.env(), stdout=log, stderr=subprocess.STDOUT)
        log.close()
        if wait:
            self.wait_ready()
        return self

    def wait_ready(self, timeout: float = 60.0) -> None:
        def up():
            if self.process.poll() is not None:
                raise AssertionError(f"backend exited ({self.process.returncode}):\n{self.log()}")
            try:
                return httpx.get(f"{self.url}/api/v1/health", timeout=2).status_code == 200
            except httpx.HTTPError:
                return False

        wait_for(up, timeout, what="the backend to answer")

    def log(self) -> str:
        return self.log_path.read_text(encoding="utf-8", errors="replace") if self.log_path.exists() else ""

    def signal(self, sig=signal.SIGTERM) -> None:
        self.process.send_signal(sig)

    def wait_exit(self, timeout: float = 30.0) -> int:
        return self.process.wait(timeout=timeout)

    def stop(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.send_signal(signal.SIGTERM)
            try:
                self.process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=10)
        # Anything the fake CLI left behind (only if a test failed half-way).
        for entry in self.cli_log("hanging"):
            for key in ("pid", "child_pid"):
                pid = entry.get(key)
                if isinstance(pid, int) and alive(pid):
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except OSError:
                        pass

    # -- reading what it stored

    def query(self, sql: str, *params) -> list[tuple]:
        connection = sqlite3.connect(self.database, timeout=10)
        try:
            return connection.execute(sql, params).fetchall()
        finally:
            connection.close()

    # -- HTTP

    def client(self, **kwargs) -> httpx.Client:
        return httpx.Client(base_url=self.url, timeout=30, **kwargs)


class Owner:
    """A signed-in owner: sets the passphrase on first use, then keeps the
    session cookie and sends the CSRF header on writes, like the web page."""

    PASSPHRASE = "correct horse battery staple, for tests"

    def __init__(self, backend: Backend) -> None:
        self.http = backend.client()
        self.csrf: str | None = None

    def setup(self) -> dict:
        response = self.http.post("/api/v1/auth/setup", json={"passphrase": self.PASSPHRASE})
        assert response.status_code == 200, response.text
        self.csrf = response.json().get("csrf_token")
        return response.json()

    def login(self) -> dict:
        response = self.http.post("/api/v1/auth/login", json={"passphrase": self.PASSPHRASE})
        assert response.status_code == 200, response.text
        self.csrf = response.json().get("csrf_token")
        return response.json()

    def _headers(self) -> dict:
        return {"x-csrf-token": self.csrf} if self.csrf else {}

    def get(self, path: str, **kw):
        return self.http.get(path, **kw)

    def post(self, path: str, json=None, **kw):
        return self.http.post(path, json=json, headers=self._headers(), **kw)

    def patch(self, path: str, json=None, **kw):
        return self.http.patch(path, json=json, headers=self._headers(), **kw)

    def put(self, path: str, json=None, **kw):
        return self.http.put(path, json=json, headers=self._headers(), **kw)

    def delete(self, path: str, **kw):
        return self.http.delete(path, headers=self._headers(), **kw)

    def close(self) -> None:
        self.http.close()
