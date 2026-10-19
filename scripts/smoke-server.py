#!/usr/bin/env python3
"""End-to-end smoke test of the server run mode (`python -m app.server`).

Walks through what a person does when they put Open Dots on a server behind a
reverse proxy, against a real process and a scratch SQLite database: migrate, set
the owner passphrase, start the server, load the pages, sign in as a visitor
arriving through a proxy (by hand-written forwarded headers), pair a phone, and
check that a second server on the same data is refused, that stopping the server
with SIGTERM ends it, and that a restart keeps everything.

It tests this application, not a particular proxy: any reverse proxy that sends the
same headers behaves the same.

Nothing outside the scratch folder is touched; no network, no engine.

    cd backend && uv run python ../scripts/smoke-server.py --ui-dir ../frontend/out [--keep]
    cd backend && uv run python ../scripts/smoke-server.py --stand-in-ui     # no web build needed

Exit code 0 when every check passed. Runs in CI (.github/workflows/ci.yml).
"""

import argparse
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
# The throwaway key CI uses (a valid Fernet key; protects nothing).
TEST_KEY = "3n3b2YkQd0xVv8m1c1m0nq1l4Z3xk9q0n7xv7y5q8wE="
PASSPHRASE = "a long smoke test passphrase"
PUBLIC_HOST = "dots.example.com"
PUBLIC_URL = f"https://{PUBLIC_HOST}"
PROXY = {"Host": PUBLIC_HOST, "X-Forwarded-For": "203.0.113.9", "X-Forwarded-Proto": "https", "Origin": PUBLIC_URL}

CHECKS: list[tuple[str, float]] = []


def check(name: str):
    def wrap(fn):
        def run(*args, **kwargs):
            started = time.monotonic()
            try:
                result = fn(*args, **kwargs)
            except BaseException:
                print(f"FAIL  {name}")
                raise
            elapsed = time.monotonic() - started
            CHECKS.append((name, elapsed))
            print(f"ok    {name} ({elapsed:.1f}s)")
            return result

        return run

    return wrap


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def make_stand_in_ui(folder: Path) -> Path:
    """Just enough pages for the checks, for a run with no web build."""
    for page in ("", "login", "pair", "chat", "settings"):
        target = folder / page
        target.mkdir(parents=True, exist_ok=True)
        (target / "index.html").write_text(f"<html><title>{page or 'home'}</title></html>")
    (folder / "404.html").write_text("<html>missing</html>")
    return folder


class Server:
    def __init__(self, root: Path, ui_dir: Path) -> None:
        self.root = root
        self.ui_dir = ui_dir
        self.database = root / "data" / "open-dots.db"
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.process: subprocess.Popen | None = None
        self.log_path = root / f"server-{self.port}.log"

    def env(self, **extra: str) -> dict:
        home = self.root / "home"
        home.mkdir(exist_ok=True)
        keep = {k: os.environ[k] for k in ("PATH", "SYSTEMROOT", "TMPDIR", "LANG") if k in os.environ}
        return {
            **keep,
            "HOME": str(home),
            "USERPROFILE": str(home),  # where Windows looks for the home folder
            "PYTHONPATH": str(BACKEND_DIR),
            "PYTHONUTF8": "1",
            "DATABASE_BACKEND": "sqlite",
            "SQLITE_PATH": str(self.database),
            "TOKEN_ENCRYPTION_KEYS": TEST_KEY,
            "BOAT_API_KEY": "",
            "TELEGRAM_BOT_TOKEN": "",
            "DECISION_LOG_DIR": str(self.root / "decisions"),
            "ATTACHMENTS_DIR": str(self.root / "attachments"),
            "ENGINE_PROCESS_DIR": str(self.root / "engines"),
            "LOCAL_WORKSPACE_ROOT": str(self.root / "work"),
            "OPEN_DOTS_UI_DIR": str(self.ui_dir),
            "OPEN_DOTS_PORT": str(self.port),
            "OPEN_DOTS_PUBLIC_URL": PUBLIC_URL,
            "LOCAL_EXECUTION_ENABLED": "true",  # a server install sets this in its settings file; the agents API needs it
            **extra,
        }

    def run(self, *args: str, stdin: str | None = None, **env: str) -> subprocess.CompletedProcess:
        # A scratch working directory, so no .env in the checkout is read.
        return subprocess.run(
            [sys.executable, *args], cwd=self.root, env=self.env(**env), input=stdin,
            capture_output=True, text=True, timeout=120,
        )

    def start(self, **env: str) -> None:
        log = open(self.log_path, "ab")
        self.process = subprocess.Popen(
            [sys.executable, "-m", "app.server"], cwd=self.root, env=self.env(**env),
            stdout=log, stderr=subprocess.STDOUT,
        )
        log.close()
        self.client = httpx.Client(base_url=self.url, timeout=10)

    def log_text(self) -> str:
        return self.log_path.read_text(encoding="utf-8", errors="replace") if self.log_path.exists() else ""

    def wait_up(self, timeout: float = 60.0) -> None:
        end = time.monotonic() + timeout
        while True:
            assert self.process and self.process.poll() is None, f"the server exited early:\n{self.log_text()}"
            try:
                if self.client.get("/api/v1/health").status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            assert time.monotonic() < end, f"the server did not come up:\n{self.log_text()}"
            time.sleep(0.3)

    def stop(self) -> int | None:
        """SIGTERM where there is one; returns the exit code."""
        process = self.process
        if process is None or process.poll() is not None:
            return process.returncode if process else None
        if sys.platform == "win32":
            process.terminate()
        else:
            process.send_signal(signal.SIGTERM)
        try:
            return process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise AssertionError(f"the server ignored SIGTERM for 20 seconds:\n{self.log_text()}")

    def close(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.kill()
            self.process.wait()
        if getattr(self, "client", None):
            self.client.close()


def signed_in(server: Server) -> tuple[dict, str]:
    """Sign in the way a browser behind a proxy does; returns the cookie header and CSRF token."""
    response = server.client.post("/api/v1/auth/login", json={"passphrase": PASSPHRASE}, headers=PROXY)
    assert response.status_code == 200, response.text
    cookie = response.headers["set-cookie"].split(";")[0]
    return {**PROXY, "Cookie": cookie}, response.json()["csrf_token"]


@check("the server refuses to start on a database that was never migrated")
def refuses_unmigrated(server: Server) -> None:
    result = server.run("-m", "app.server")
    assert result.returncode == 4, (result.returncode, result.stderr)
    assert "python -m app.server migrate" in result.stderr
    assert not server.database.exists(), "looking at the database must not create it"


@check("migrate creates the database, and running it again changes nothing")
def migrates(server: Server) -> None:
    first = server.run("-m", "app.server", "migrate")
    assert first.returncode == 0 and "Database ready" in first.stdout, first.stderr
    again = server.run("-m", "app.server", "migrate")
    assert again.returncode == 0 and "already up to date" in again.stdout, again.stderr


@check("the owner passphrase is set from the server's own shell")
def sets_passphrase(server: Server) -> None:
    result = server.run("-m", "app.auth.cli", "set-passphrase", "--stdin", stdin=PASSPHRASE + "\n")
    assert result.returncode == 0, result.stderr


@check("the server will not listen beyond this machine without being told to")
def refuses_public_bind(server: Server) -> None:
    result = server.run("-m", "app.server", OPEN_DOTS_HOST="0.0.0.0")
    assert result.returncode == 2 and "OPEN_DOTS_ALLOW_PUBLIC_BIND" in result.stderr, (result.returncode, result.stderr)


@check("the pages load without signing in; a missing page is a 404")
def pages_load(server: Server) -> None:
    for path in ("/", "/login/", "/pair/", "/chat/", "/settings/"):
        response = server.client.get(path, headers=PROXY)
        assert response.status_code == 200 and "text/html" in response.headers["content-type"], (path, response.status_code)
    assert server.client.get("/no-such-page/", headers=PROXY).status_code == 404
    page = server.client.get("/login/", headers=PROXY)
    assert page.headers["x-frame-options"] == "DENY" and "frame-ancestors 'none'" in page.headers["content-security-policy"]
    redirect = server.client.get("/login", headers=PROXY, follow_redirects=False)
    assert redirect.status_code == 307 and redirect.headers["location"] == "/login/", redirect.headers.get("location")


@check("the API still asks for sign-in, and health does not")
def api_is_closed(server: Server) -> None:
    assert server.client.get("/api/v1/health", headers=PROXY).json() == {"status": "ok"}
    for path in ("/api/v1/agents", "/api/v1/threads", "/api/v1/settings", "/docs", "/openapi.json"):
        refused = server.client.get(path, headers=PROXY)
        assert refused.status_code == 401, (path, refused.status_code)
    assert server.client.post("/login/", json={}, headers=PROXY).status_code == 401


@check("a host name the server does not answer to is refused")
def refuses_strange_host(server: Server) -> None:
    refused = server.client.get("/api/v1/health", headers={"Host": "evil.example.net"})
    assert refused.status_code == 421, refused.status_code
    assert "evil.example.net" not in server.log_text()


@check("signing in through a proxy works: wrong passphrase refused, right one sets a Secure cookie")
def signs_in(server: Server) -> None:
    wrong = server.client.post("/api/v1/auth/login", json={"passphrase": "not the passphrase"}, headers=PROXY)
    assert wrong.status_code == 401 and wrong.json()["detail"]["error_code"] == "wrong_passphrase"
    right = server.client.post("/api/v1/auth/login", json={"passphrase": PASSPHRASE}, headers=PROXY)
    assert right.status_code == 200, right.text
    cookie = right.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=lax" in cookie, cookie


@check("a signed-in owner can use the API; a write without the CSRF token is refused")
def uses_api(server: Server) -> int:
    headers, csrf = signed_in(server)
    assert server.client.get("/api/v1/agents", headers=headers).status_code == 200
    no_csrf = server.client.post("/api/v1/agents", json={"name": "smoke"}, headers=headers)
    assert no_csrf.status_code == 403 and no_csrf.json()["detail"]["error_code"] == "csrf_failed"
    made = server.client.post("/api/v1/agents", json={"name": "smoke"}, headers={**headers, "X-CSRF-Token": csrf})
    assert made.status_code in (200, 201), made.text
    return made.json()["id"]


@check("a phone pairs with a one-time code and gets chat only")
def pairs_phone(server: Server) -> None:
    issued = server.run("-m", "app.auth.cli", "pairing-code", "--scope", "chat", "--label", "Smoke phone")
    assert issued.returncode == 0, issued.stderr
    assert f"open:         {PUBLIC_URL}/pair#code=" in issued.stdout, issued.stdout
    code = next(line.split()[-1] for line in issued.stdout.splitlines() if line.startswith("pairing code:"))
    paired = server.client.post("/api/v1/auth/pair", json={"code": code, "label": "phone"}, headers=PROXY)
    assert paired.status_code == 200, paired.text
    bearer = {**PROXY, "Authorization": f"Bearer {paired.json()['token']}"}
    assert server.client.get("/api/v1/threads", headers=bearer).status_code == 200
    assert server.client.get("/api/v1/settings", headers=bearer).status_code == 403
    again = server.client.post("/api/v1/auth/pair", json={"code": code, "label": "phone"}, headers=PROXY)
    assert again.status_code in (400, 401, 403, 404, 410), "a pairing code must work only once"


@check("a proxy that omits forwarded headers is reported once in the log; one that sends them is not")
def warns_about_proxy(server: Server) -> None:
    server.client.get("/login/", headers={"Host": PUBLIC_HOST})
    server.client.get("/login/", headers={"Host": PUBLIC_HOST})
    text = server.log_text()
    assert text.count("without X-Forwarded-For") == 1 and text.count("without X-Forwarded-Proto") == 1, text[-1500:]
    # Nothing else was reported: local visits and visits that carry the headers (every earlier check) add nothing.
    assert text.count("arrived without") == 2, text[-1500:]


@check("a second server on the same data is refused")
def second_server_refused(server: Server) -> None:
    other = subprocess.Popen(
        [sys.executable, "-m", "app.server"], cwd=server.root, env=server.env(OPEN_DOTS_PORT=str(free_port())),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        output, _ = other.communicate(timeout=60)
    except subprocess.TimeoutExpired:
        other.kill()
        raise AssertionError("the second server did not exit")
    assert other.returncode != 0 and "already using the database" in output, (other.returncode, output[-800:])


@check("SIGTERM stops the server promptly and without an error")
def stops_on_sigterm(server: Server) -> None:
    code = server.stop()
    # Cleanly (0), or by the signal itself where the platform reports it that way.
    assert code in (0, -signal.SIGTERM, 128 + signal.SIGTERM) or sys.platform == "win32", f"exit code {code}"
    assert "Traceback" not in server.log_text(), server.log_text()[-1500:]


@check("after a restart the owner, the agent and the pairing are all still there")
def survives_restart(server: Server, agent_id: int) -> None:
    server.start()
    server.wait_up()
    headers, _ = signed_in(server)  # the passphrase survived
    agents = server.client.get("/api/v1/agents", headers=headers).json()
    assert any(agent["id"] == agent_id for agent in agents), agents
    devices = server.client.get("/api/v1/auth/devices", headers=headers).json()
    assert any(device["label"] == "phone" for device in devices), devices
    assert server.stop() is not None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ui-dir", help="the folder made by scripts/build-ui.js (frontend/out)")
    parser.add_argument("--stand-in-ui", action="store_true", help="use a few stand-in pages instead of a web build")
    parser.add_argument("--keep", action="store_true", help="keep the scratch folder and print where it is")
    args = parser.parse_args()
    if not args.ui_dir and not args.stand_in_ui:
        parser.error("give --ui-dir, or --stand-in-ui for a run with no web build")

    root = Path(tempfile.mkdtemp(prefix="open-dots-smoke-server-"))
    ui_dir = make_stand_in_ui(root / "ui") if args.stand_in_ui else Path(args.ui_dir).resolve()
    if not (ui_dir / "login" / "index.html").is_file():
        print(f"{ui_dir} has no login page; build the pages first (node scripts/build-ui.js).", file=sys.stderr)
        return 2
    server = Server(root / "run", ui_dir)
    server.root.mkdir(parents=True)
    started = time.monotonic()
    try:
        refuses_unmigrated(server)
        migrates(server)
        sets_passphrase(server)
        refuses_public_bind(server)
        server.start()
        server.wait_up()
        pages_load(server)
        api_is_closed(server)
        refuses_strange_host(server)
        signs_in(server)
        agent_id = uses_api(server)
        pairs_phone(server)
        warns_about_proxy(server)
        second_server_refused(server)
        stops_on_sigterm(server)
        survives_restart(server, agent_id)
    except BaseException as error:
        print(f"\nSmoke test failed: {type(error).__name__}: {error}", file=sys.stderr)
        print(f"Scratch folder kept at {root}", file=sys.stderr)
        server.close()
        return 1
    server.close()
    print(f"\nAll {len(CHECKS)} checks passed in {time.monotonic() - started:.0f}s.")
    if args.keep:
        print(f"Scratch folder kept at {root}")
    else:
        shutil.rmtree(root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
