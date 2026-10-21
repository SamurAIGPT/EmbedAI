#!/usr/bin/env python3
"""The Docker image and compose.yaml, built and run for real.

scripts/smoke-server.py and smoke-server-chat.py check the server process. This one checks the
packaging around it: what is (and is not) inside the image, that compose starts it healthy on a
fresh volume, that the published port reaches the pages and the sign-in, that no address is
believed as a proxy unless it is named, that data survives `down` and `up`, that an older
database is upgraded on start, that a backup runs inside the container, and that a stop signal ends
it cleanly. Nothing outside a scratch folder, a scratch compose project and its images is touched.

    cd backend && uv run python ../scripts/smoke-docker.py [--skip-build] [--skip-engine-build] [--keep]

Needs Docker with Compose. The engine-build check installs a small public npm package, so it needs
network access; skip it with --skip-engine-build. Exit code 0 when every check passed.
"""

import argparse
import json
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from cryptography.fernet import Fernet

REPO = Path(__file__).resolve().parents[1]
PROJECT = "open-dots-smoke"
IMAGE = "open-dots:smoke"
ENGINE_IMAGE = "open-dots:smoke-engine"
HOST = "dots.example.com"
PASSPHRASE = "a long passphrase for the docker smoke"
STOP_BUDGET_SECONDS = 25
CHECKS: list[tuple[str, float]] = []


def check(name: str):
    def wrap(fn):
        def run(*args, **kwargs):
            started = time.monotonic()
            try:
                result = fn(*args, **kwargs)
            except BaseException:
                print(f"FAIL  {name}", flush=True)
                raise
            seconds = time.monotonic() - started
            CHECKS.append((name, seconds))
            print(f"PASS  {name}  ({seconds:.1f}s)", flush=True)
            return result

        return run

    return wrap


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def run(command: list[str], *, check_exit: bool = True, timeout: int = 900, **kwargs) -> subprocess.CompletedProcess:
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, cwd=REPO, **kwargs)
    if check_exit and result.returncode != 0:
        raise AssertionError(f"{' '.join(command)} -> {result.returncode}\n{result.stdout[-1500:]}\n{result.stderr[-1500:]}")
    return result


class Stack:
    """The compose project under test."""

    def __init__(self, scratch: Path) -> None:
        self.port = free_port()
        self.env_file = scratch / "open-dots.env"
        self.env_file.write_text(
            "\n".join([
                f"TOKEN_ENCRYPTION_KEYS={Fernet.generate_key().decode()}",
                "LOCAL_EXECUTION_ENABLED=true",
                "LOOPBACK_TRUST=off",
                f"OPEN_DOTS_PUBLIC_URL=https://{HOST}",
                "",
            ]),
            encoding="utf-8",
        )
        self.environment = {
            "OPEN_DOTS_ENV_FILE": str(self.env_file),
            "OPEN_DOTS_PORT": str(self.port),
            "OPEN_DOTS_IMAGE": IMAGE,
        }

    def compose(self, *args: str, check_exit: bool = True, timeout: int = 300) -> subprocess.CompletedProcess:
        import os

        return run(["docker", "compose", "-p", PROJECT, "-f", "compose.yaml", *args], check_exit=check_exit,
                   timeout=timeout, env={**os.environ, **self.environment})

    def container(self) -> str:
        return self.compose("ps", "-q", "open-dots").stdout.strip()

    def state(self) -> dict:
        return json.loads(run(["docker", "inspect", self.container()]).stdout)[0]["State"]

    def wait_healthy(self, timeout: float = 120) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            container = self.container()
            if container:
                health = json.loads(run(["docker", "inspect", container]).stdout)[0]["State"]
                if health.get("Status") == "exited":
                    raise AssertionError(f"the container exited ({health.get('ExitCode')}):\n{self.logs()[-2500:]}")
                if (health.get("Health") or {}).get("Status") == "healthy":
                    return
            time.sleep(2)
        raise AssertionError(f"the container did not become healthy in {timeout:.0f} s:\n{self.logs()[-2500:]}")

    def logs(self) -> str:
        result = self.compose("logs", "--no-color", "open-dots", check_exit=False)
        return result.stdout + result.stderr

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def visitor(self, claims: str, passphrase: str) -> httpx.Response:
        """A sign-in as a proxy would forward it: the visitor it calls `claims`, over HTTPS."""
        headers = {"Host": HOST, "X-Forwarded-For": claims, "X-Forwarded-Proto": "https"}
        return httpx.post(self.url("/api/v1/auth/login"), json={"passphrase": passphrase}, headers=headers, timeout=20)

    def exec(self, *command: str, stdin: str | None = None) -> subprocess.CompletedProcess:
        return self.compose("exec", "-T", "open-dots", *command, check_exit=False) if stdin is None else run(
            ["docker", "exec", "-i", self.container(), *command], check_exit=False, input=stdin)


def image_shell(image: str, script: str) -> str:
    return run(["docker", "run", "--rm", "--entrypoint", "sh", image, "-c", script]).stdout


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--skip-build", action="store_true", help=f"use the existing image {IMAGE}")
    parser.add_argument("--skip-engine-build", action="store_true", help="skip the build that installs a sample engine package (needs network)")
    parser.add_argument("--keep", action="store_true", help="leave the scratch project, volume and images in place")
    args = parser.parse_args()

    if run(["docker", "version", "--format", "{{.Server.Version}}"], check_exit=False).returncode != 0:
        print("Docker is not available.", file=sys.stderr)
        return 2

    scratch = Path(tempfile.mkdtemp(prefix="open-dots-smoke-docker-"))
    stack = Stack(scratch)
    try:
        if not args.skip_build:
            check("the image builds from the repository")(lambda: run(["docker", "build", "-t", IMAGE, "."], timeout=1800))()

        @check("the image runs as a non-root user and holds no settings file, private notes, tests or engine")
        def image_contents():
            report = image_shell(IMAGE, """
                echo "uid=$(id -u)"
                echo "home=$HOME"
                find / -xdev \\( -name '.env' -o -name '.env.*' -o -name '.internal' -o -name 'open-dots.env' \\) \\
                     -not -path '/proc/*' 2>/dev/null | sed 's/^/leak=/'
                find /app \\( -name tests -o -name '.pytest_cache' -o -name '.ruff_cache' -o -name '*.md' \\) | sed 's/^/stray=/'
                for tool in node npm uv claude codex; do command -v $tool >/dev/null 2>&1 && echo "tool=$tool"; done
                ls -ld /data | awk '{print "data_owner=" $3}'
                echo "engines_bin=$(ls -A /opt/engines/bin | wc -l)"
            """)
            lines = dict(line.split("=", 1) for line in report.splitlines() if "=" in line and not line.startswith(("leak=", "stray=", "tool=")))
            assert lines["uid"] == "10001" and lines["home"] == "/data" and lines["data_owner"] == "opendots", report
            for prefix in ("leak=", "stray=", "tool="):
                assert not [line for line in report.splitlines() if line.startswith(prefix)], report
            assert lines["engines_bin"] == "0", report

        image_contents()

        @check("without a key the container stops at once and says what is missing")
        def needs_a_key():
            result = run(["docker", "run", "--rm", IMAGE], check_exit=False, timeout=180)
            assert result.returncode != 0, result.stdout
            assert "TOKEN_ENCRYPTION_KEYS" in result.stdout + result.stderr, result.stdout[-800:] + result.stderr[-800:]

        needs_a_key()

        if not args.skip_engine_build:
            @check("an engine package is installed only when named at build time, and brings Node with it")
            def engine_build():
                run(["docker", "build", "--build-arg", "ENGINES=semver@7.6.3", "-t", ENGINE_IMAGE, "."], timeout=1800)
                out = image_shell(ENGINE_IMAGE, "semver 7.6.3; node --version | cut -c1-2; ls /data | wc -l; ls -A /data/.npm /root 2>/dev/null | wc -l")
                assert out.splitlines()[0] == "7.6.3" and out.splitlines()[1] == "v2", out
                assert "semver" not in image_shell(IMAGE, "command -v semver || echo absent"), "the default image has the sample package"
                assert out.splitlines()[2] == "0", "the build left something on the volume path"

            engine_build()

        @check("compose starts it healthy on a fresh volume, with the database created")
        def starts():
            stack.compose("up", "-d", "--no-build")
            stack.wait_healthy()
            assert re.search(r"Database ready at revision \w+", stack.logs()), stack.logs()[-1500:]

        starts()

        @check("the pages, health and a closed API answer on the published port, which is on the loopback only")
        def published():
            assert httpx.get(stack.url("/api/v1/health"), timeout=10).status_code == 200
            assert httpx.get(stack.url("/login/"), headers={"Host": HOST}, timeout=10).status_code == 200
            assert httpx.get(stack.url("/api/v1/agents"), headers={"Host": HOST}, timeout=10).status_code == 401
            mapping = stack.compose("port", "open-dots", "8000").stdout.strip()
            assert mapping.startswith("127.0.0.1:"), f"the port is published on {mapping}"

        published()

        @check("by default nothing is believed: forwarded HTTPS from the host side is ignored (cookie not Secure, one shared lockout)")
        def untrusted_by_default():
            done = stack.exec("python", "-m", "app.auth.cli", "set-passphrase", "--stdin", stdin=PASSPHRASE + "\n")
            assert done.returncode == 0, done.stdout + done.stderr
            first = stack.visitor("198.51.100.1", PASSPHRASE)
            assert first.status_code == 200 and "Secure" not in first.headers["set-cookie"], (first.status_code, first.headers.get("set-cookie"))
            for claim in ("198.51.100.2", "198.51.100.3", "198.51.100.4", "198.51.100.5", "198.51.100.6"):
                assert stack.visitor(claim, "wrong passphrase").status_code == 401
            assert stack.visitor("198.51.100.7", PASSPHRASE).status_code == 429  # one socket, one bucket, whatever it claims

        untrusted_by_default()

        @check("naming the gateway address in OPEN_DOTS_TRUSTED_PROXIES makes a host-side proxy believed, with a lockout per visitor")
        def gateway_opt_in():
            gateway = json.loads(run(["docker", "network", "inspect", f"{PROJECT}_default"]).stdout)[0]["IPAM"]["Config"][0]["Gateway"]
            assert gateway == "172.31.250.1", gateway  # the address compose.yaml documents
            stack.environment["OPEN_DOTS_TRUSTED_PROXIES"] = gateway
            stack.compose("up", "-d", "--no-build")
            stack.wait_healthy()
            first = stack.visitor("198.51.100.11", PASSPHRASE)
            assert first.status_code == 200 and "Secure" in first.headers["set-cookie"], (first.status_code, first.headers.get("set-cookie"))
            for _ in range(5):
                assert stack.visitor("198.51.100.12", "wrong passphrase").status_code == 401
            assert stack.visitor("198.51.100.12", PASSPHRASE).status_code == 429
            assert stack.visitor("198.51.100.13", PASSPHRASE).status_code == 200  # another visitor of the same proxy

        gateway_opt_in()

        @check("doctor inside the container finds everything ready except an engine, which the image does not carry")
        def doctor():
            result = stack.exec("python", "-m", "app.server", "doctor", "--json")
            checks = json.loads(result.stdout)
            failed = {item["name"] for item in checks if item["status"] == "fail"}
            assert failed == {"Engines"}, [item for item in checks if item["status"] in ("fail", "warn")]
            by_name = {item["name"]: item for item in checks}
            for name in ("Database", "Pages", "Encryption key", "Local execution", "Database folder", "Workspace folder"):
                assert by_name[name]["status"] == "ok", by_name[name]

        doctor()

        @check("a backup runs inside the container and lands on the volume")
        def backup():
            result = stack.exec("python", "-m", "app.server", "backup")
            assert result.returncode == 0 and "Backup written to /data/backups/" in result.stdout, result.stdout + result.stderr
            listing = stack.exec("ls", "/data/backups").stdout
            assert "open-dots-backup-" in listing, listing

        backup()

        @check("data survives down and up: the owner and the sign-in are still there")
        def survives():
            stack.compose("down")
            stack.compose("up", "-d", "--no-build")
            stack.wait_healthy()
            assert stack.visitor("198.51.100.9", PASSPHRASE).status_code == 200
            again = httpx.post(stack.url("/api/v1/auth/setup"), json={"passphrase": "another long passphrase"}, headers={"Host": HOST}, timeout=10)
            assert again.status_code in (403, 409), again.status_code  # already configured, or not from this machine

        survives()

        @check("an older database is upgraded on start, with the SQLite file copied aside first")
        def upgrades():
            stack.compose("stop", "-t", "30")
            run(["docker", "run", "--rm", "--env-file", str(stack.env_file), "-e", "TRUSTED_PROXIES=",
                 "-v", f"{PROJECT}_data:/data", "--entrypoint", "python", IMAGE,
                 "-m", "alembic", "-c", "/app/backend/alembic.ini", "downgrade", "-1"])
            stack.compose("up", "-d", "--no-build")
            stack.wait_healthy()
            logs = stack.logs()
            assert "Copied the database to" in logs and re.search(r"Database ready at revision \w+", logs), logs[-1500:]
            assert stack.visitor("198.51.100.10", PASSPHRASE).status_code == 200

        upgrades()

        @check("a stop signal ends it cleanly within the grace period")
        def stops():
            started = time.monotonic()
            stack.compose("stop", "-t", "30")
            took = time.monotonic() - started
            container = run(["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={PROJECT}"]).stdout.split()[0]
            state = json.loads(run(["docker", "inspect", container]).stdout)[0]["State"]
            # 143 is SIGTERM: Uvicorn finishes its shutdown and then re-raises the signal, so a clean stop
            # by `docker stop` reads as 143. 137 would mean it was killed after the grace period.
            assert state["ExitCode"] in (0, 143), state
            assert took < STOP_BUDGET_SECONDS, f"stopping took {took:.1f} s"
            assert "Application shutdown complete" in stack.logs(), stack.logs()[-1500:]

        stops()
    except BaseException as error:
        print(f"\nSmoke test failed: {type(error).__name__}: {error}", file=sys.stderr)
        args.keep = True
        raise
    finally:
        if not args.keep:
            stack.compose("down", "-v", "--remove-orphans", check_exit=False)
            run(["docker", "rmi", "-f", ENGINE_IMAGE], check_exit=False)
            if not args.skip_build:
                run(["docker", "rmi", "-f", IMAGE], check_exit=False)
        else:
            print(f"left in place: compose project {PROJECT}, images {IMAGE}, scratch {scratch}")
        shutil.rmtree(scratch, ignore_errors=True) if not args.keep else None
    total = sum(seconds for _, seconds in CHECKS)
    print(f"\nall {len(CHECKS)} checks passed in {total:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
