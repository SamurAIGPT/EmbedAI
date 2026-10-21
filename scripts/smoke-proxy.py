#!/usr/bin/env python3
"""Open Dots behind a real reverse proxy: nginx and Caddy, in containers, with TLS.

The server relies on its proxy for a few things: HTTPS (so the sign-in cookie is Secure), the
visitor's address (so a lockout is per visitor), the Host name, streams that are not buffered,
uploads that fit, and /hooks/ that reaches the app. This starts the compose stack with each proxy
overlay in deploy/proxy/ and checks all of that through the proxy, verifying the certificate (never
skipping verification). It also measures the trust boundary instead of assuming it: which peers the
server believes, what a stray container on the same network, or on another network, can do with
forged X-Forwarded-* headers, and what trusting the whole network would have allowed.

    cd backend && uv run python ../scripts/smoke-proxy.py [--proxy nginx|caddy|both] [--skip-build] [--keep]

Needs Docker with Compose and the image from scripts/smoke-docker.py (built here unless --skip-build).
Exit code 0 when every check passed.
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from cryptography import x509
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

REPO = Path(__file__).resolve().parents[1]
PROJECT = "open-dots-proxy"
IMAGE = "open-dots:smoke"
DOMAIN = "dots.example.com"
PASSPHRASE = "a long passphrase for the proxy smoke"
PROXY_IP = "172.31.250.10"
SUBNET = "172.31.250.0/24"
CURL_IMAGE = "curlimages/curl"
CHECKS: list[tuple[str, float]] = []


def check(name: str):
    def wrap(fn):
        def run_check(*args, **kwargs):
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

        return run_check

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


def self_signed(directory: Path) -> Path:
    """A certificate for DOMAIN, valid for a day. Returns the certificate (also what clients trust)."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, DOMAIN)])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(DOMAIN)]), critical=False)
        .sign(key, hashes.SHA256())
    )
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "fullchain.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (directory / "privkey.pem").write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return directory / "fullchain.pem"


class Response:
    """What `curl -i` printed: the final response's status, headers and body (a 100 Continue is skipped)."""

    def __init__(self, raw: str) -> None:
        text = raw.replace("\r\n", "\n")
        while True:
            head, _, rest = text.partition("\n\n")
            lines = head.split("\n")
            self.status = int(lines[0].split()[1])
            if self.status != 100:
                break
            text = rest
        self.body = rest
        self.headers: dict[str, list[str]] = {}
        for line in lines[1:]:
            key, _, value = line.partition(":")
            self.headers.setdefault(key.strip().lower(), []).append(value.strip())

    def header(self, name: str) -> str:
        return ", ".join(self.headers.get(name.lower(), []))

    def json(self):
        return json.loads(self.body)

    @property
    def secure_cookie(self) -> bool:
        return any(part.strip().lower() == "secure" for value in self.headers.get("set-cookie", []) for part in value.split(";")[1:])


class Stack:
    def __init__(self, kind: str, scratch: Path) -> None:
        self.kind = kind
        self.scratch = scratch
        self.ports = {"app": free_port(), "http": free_port(), "https": free_port()}
        self.tls_dir = scratch / "tls"
        self.env_file = scratch / "open-dots.env"
        self.env_file.write_text("\n".join([
            f"TOKEN_ENCRYPTION_KEYS={Fernet.generate_key().decode()}",
            "LOCAL_EXECUTION_ENABLED=true",
            "LOOPBACK_TRUST=off",
            f"OPEN_DOTS_PUBLIC_URL=https://{DOMAIN}",
            "",
        ]), encoding="utf-8")
        self.ca: Path | None = None
        self.overrides: dict[str, str] = {}
        self.network = f"{PROJECT}_default"

    def environment(self) -> dict:
        env = {
            "OPEN_DOTS_ENV_FILE": str(self.env_file), "OPEN_DOTS_IMAGE": IMAGE, "OPEN_DOTS_DOMAIN": DOMAIN,
            "OPEN_DOTS_PORT": str(self.ports["app"]), "OPEN_DOTS_HTTP_PORT": str(self.ports["http"]),
            "OPEN_DOTS_HTTPS_PORT": str(self.ports["https"]), "OPEN_DOTS_HTTP_BIND": "127.0.0.1",
            "OPEN_DOTS_TLS_DIR": str(self.tls_dir),
        }
        if self.kind == "caddy":
            env["OPEN_DOTS_CADDY_TLS"] = "tls internal"
        env.update(self.overrides)
        return {**os.environ, **env}

    def compose(self, *args: str, check_exit: bool = True, timeout: int = 300) -> subprocess.CompletedProcess:
        return run(["docker", "compose", "-p", PROJECT, "-f", "compose.yaml", "-f", f"deploy/proxy/compose.{self.kind}.yaml", *args],
                   check_exit=check_exit, timeout=timeout, env=self.environment())

    def container(self, service: str) -> str:
        return self.compose("ps", "-q", service).stdout.strip()

    def wait_healthy(self, timeout: float = 120) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            container = self.container("open-dots")
            if container:
                state = json.loads(run(["docker", "inspect", container]).stdout)[0]["State"]
                if state.get("Status") == "exited":
                    raise AssertionError(f"the app exited ({state.get('ExitCode')}):\n{self.compose('logs', '--no-color', 'open-dots', check_exit=False).stdout[-2500:]}")
                if (state.get("Health") or {}).get("Status") == "healthy":
                    return
            time.sleep(2)
        raise AssertionError("the app did not become healthy")

    def app_logs(self) -> str:
        result = self.compose("logs", "--no-color", "open-dots", check_exit=False)
        return result.stdout + result.stderr

    def app_exec(self, *command: str, stdin: str | None = None) -> subprocess.CompletedProcess:
        return run(["docker", "exec", "-i", self.container("open-dots"), *command], check_exit=False, input=stdin)

    def app_address(self) -> str:
        info = json.loads(run(["docker", "inspect", self.container("open-dots")]).stdout)[0]
        return info["NetworkSettings"]["Networks"][self.network]["IPAddress"]

    # -- clients on the network ------------------------------------------------------------------

    def client(self, name: str, *, network: str | None = None) -> str:
        full = f"{PROJECT}-{name}"
        run(["docker", "rm", "-f", full], check_exit=False)
        run(["docker", "run", "-d", "--name", full, "--network", network or self.network, "--entrypoint", "sleep", CURL_IMAGE, "3600"])
        if self.ca is not None:
            run(["docker", "cp", str(self.ca), f"{full}:/tmp/ca.pem"])
        return full

    def request(self, client: str, method: str, url: str, *, body: dict | None = None, headers: dict | None = None,
                jar: str | None = None, extra: tuple[str, ...] = (), verify: bool = True) -> Response:
        command = ["docker", "exec", "-i", client, "curl", "-sS", "-i", "-m", "40", "-X", method]
        if verify and url.startswith("https://"):
            command += ["--cacert", "/tmp/ca.pem"]
        if jar:
            command += ["-b", jar, "-c", jar]
        for key, value in (headers or {}).items():
            command += ["-H", f"{key}: {value}"]
        if body is not None:
            command += ["-H", "content-type: application/json", "--data-binary", "@-"]
        command += [*extra, url]
        result = run(command, input=json.dumps(body) if body is not None else None, check_exit=False)
        if result.returncode != 0:
            raise AssertionError(f"curl {method} {url} failed ({result.returncode}): {result.stderr.strip()}")
        return Response(result.stdout)

    def public(self, path: str) -> str:
        return f"https://{DOMAIN}{path}"

    def login(self, client: str, passphrase: str, *, jar: str | None = None, headers: dict | None = None, base: str | None = None) -> Response:
        return self.request(client, "POST", (base or self.public("")) + "/api/v1/auth/login", body={"passphrase": passphrase},
                            jar=jar, headers=headers)


def prepare(stack: Stack, image_ready: bool) -> None:
    if not image_ready:
        run(["docker", "build", "-t", IMAGE, "."], timeout=1800)
    if stack.kind == "nginx":
        stack.ca = self_signed(stack.tls_dir)
    stack.compose("up", "-d", "--no-build")
    stack.wait_healthy()
    if stack.kind == "caddy":
        deadline = time.monotonic() + 90
        root = ""
        while time.monotonic() < deadline and "BEGIN CERTIFICATE" not in root:
            root = run(["docker", "exec", stack.container("proxy"), "cat", "/data/caddy/pki/authorities/local/root.crt"], check_exit=False).stdout
            time.sleep(2)
        assert "BEGIN CERTIFICATE" in root, "Caddy did not create its certificate authority"
        stack.ca = stack.scratch / "caddy-root.pem"
        stack.ca.write_text(root)
    done = stack.app_exec("python", "-m", "app.auth.cli", "set-passphrase", "--stdin", stdin=PASSPHRASE + "\n")
    assert done.returncode == 0, done.stdout + done.stderr


def matrix(kind: str, args) -> None:
    scratch = Path(tempfile.mkdtemp(prefix=f"open-dots-smoke-proxy-{kind}-"))
    stack = Stack(kind, scratch)
    names = [f"{kind}-c1", f"{kind}-c2", f"{kind}-c3", f"{kind}-rogue"]
    try:
        prepare(stack, image_ready=True)
        c1, c2, phone, rogue = (stack.client(name) for name in names)
        label = f"[{kind}]"

        @check(f"{label} HTTPS is served with a certificate the client verifies, and plain HTTP redirects to it")
        def tls():
            health = stack.request(c1, "GET", stack.public("/api/v1/health"))
            assert health.status == 200, health.body
            moved = stack.request(c1, "GET", f"http://{DOMAIN}/login/")
            assert moved.status in (301, 308) and moved.header("location").startswith(f"https://{DOMAIN}/"), (moved.status, moved.header("location"))
            # and a client that does not trust the certificate refuses, so the check above proves verification
            refused = run(["docker", "exec", c1, "curl", "-sS", "-m", "20", "-o", "/dev/null", "-w", "%{http_code}", stack.public("/api/v1/health")], check_exit=False)
            assert refused.returncode != 0, "a client without the CA accepted the certificate"

        tls()

        @check(f"{label} the pages load, the API is closed, and a name nobody serves is refused")
        def basics():
            assert stack.request(c1, "GET", stack.public("/login/")).status == 200
            assert stack.request(c1, "GET", stack.public("/api/v1/agents")).status == 401
            foreign = stack.request(c1, "GET", stack.public("/api/v1/health"), headers={"Host": "evil.example"})
            # The app refuses a Host it does not know (400/403); Caddy may refuse the mismatch itself (421) or answer an
            # empty page for a name it has no site for. What must never happen is the app's own answer being served.
            assert foreign.status in (400, 403, 421) or not foreign.body.strip(), (foreign.status, foreign.body[:200])
            assert '"status"' not in foreign.body, f"the app served a name nobody serves: {foreign.body[:200]}"

        basics()

        @check(f"{label} sign-in over HTTPS: Secure cookie, a lockout per visitor, and a forged X-Forwarded-For cannot pick the bucket")
        def sign_in():
            ok = stack.login(c1, PASSPHRASE, jar="/tmp/jar")
            assert ok.status == 200 and ok.secure_cookie, (ok.status, ok.headers.get("set-cookie"))
            stack.csrf = ok.json()["csrf_token"]
            for _ in range(5):
                assert stack.login(c2, "wrong passphrase").status == 401
            assert stack.login(c2, PASSPHRASE).status == 429
            forged = stack.login(c2, PASSPHRASE, headers={"X-Forwarded-For": "198.51.100.77"})
            assert forged.status == 429, "a client-supplied X-Forwarded-For chose its own lockout bucket"
            assert stack.login(c1, PASSPHRASE).status == 200  # another visitor of the same proxy is not locked out

        sign_in()

        def authed(method, path, body=None, extra=()):
            return stack.request(c1, method, stack.public(path), body=body, jar="/tmp/jar", headers={"x-csrf-token": stack.csrf}, extra=extra)

        @check(f"{label} the event stream reaches the client as it happens (replayed and live) and keepalives keep flowing")
        def stream():
            agent = authed("POST", "/api/v1/agents", {"name": f"{kind}-proxy-smoke"})
            assert agent.status in (200, 201), agent.body
            thread = authed("POST", "/api/v1/threads", {"agent_id": agent.json()["id"]})
            assert thread.status in (200, 201), thread.body
            tid = thread.json()["id"]
            assert authed("POST", f"/api/v1/threads/{tid}/session/reset").status == 200  # one event stored before the stream opens
            frames: list[tuple[float, str]] = []
            process = subprocess.Popen(
                # Asked the way a browser asks (Accept-Encoding: gzip): a proxy that compresses the stream holds it back.
                ["docker", "exec", c1, "curl", "-sSN", "--compressed", "-H", "Accept-Encoding: gzip", "--cacert", "/tmp/ca.pem",
                 "-b", "/tmp/jar", stack.public(f"/api/v1/threads/{tid}/events/stream")],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)

            def read():
                for line in process.stdout:
                    frames.append((time.monotonic(), line.rstrip("\n")))

            threading.Thread(target=read, daemon=True).start()
            try:
                opened = time.monotonic()

                def seen(text, after=0.0, within=8.0):
                    deadline = time.monotonic() + within
                    while time.monotonic() < deadline:
                        for moment, line in list(frames):
                            if text in line and moment >= after:
                                return moment
                        time.sleep(0.1)
                    return None

                replayed = seen("session.reset", after=opened - 1)
                assert replayed is not None and replayed - opened < 6, "the stored event was held back by the proxy"
                assert process.poll() is None, "the stream ended"
                before = time.monotonic()
                assert authed("POST", f"/api/v1/threads/{tid}/session/reset").status == 200
                second = None
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline and second is None:
                    resets = [m for m, line in frames if "session.reset" in line and m >= before]
                    second = resets[0] if resets else None
                    time.sleep(0.1)
                assert second is not None and second - before < 6, "a live event was held back by the proxy"
                alive = seen(": keepalive", after=opened, within=22)
                assert alive is not None, "no keepalive reached the client: the proxy is buffering the stream"
            finally:
                process.kill()
            stack.thread_id = tid

        stream()

        @check(f"{label} a 9.5 MB upload passes through and comes back byte for byte")
        def upload():
            tid = stack.thread_id
            run(["docker", "exec", c1, "sh", "-c",
                 r"printf '\211PNG\r\n\032\n' > /tmp/big.png && head -c 9961472 /dev/urandom >> /tmp/big.png && sha256sum /tmp/big.png | cut -d' ' -f1 > /tmp/big.sha"])
            sent = authed("POST", f"/api/v1/threads/{tid}/attachments", extra=("-F", "file=@/tmp/big.png;type=image/png"))
            assert sent.status == 201, (sent.status, sent.body[:300])
            attachment = sent.json()["id"]
            run(["docker", "exec", c1, "curl", "-sS", "-m", "60", "--cacert", "/tmp/ca.pem", "-b", "/tmp/jar", "-o", "/tmp/back.png",
                 stack.public(f"/api/v1/threads/{tid}/attachments/{attachment}")])
            original = run(["docker", "exec", c1, "cat", "/tmp/big.sha"]).stdout.strip()
            returned = run(["docker", "exec", c1, "sh", "-c", "sha256sum /tmp/back.png | cut -d' ' -f1"]).stdout.strip()
            assert original and original == returned, (original, returned)

        upload()

        @check(f"{label} /hooks/ reaches the app (its own answer, no sign-in at the proxy)")
        def hooks():
            answer = stack.request(phone, "POST", stack.public("/hooks/999999"), body={"x": 1})
            assert answer.status in (401, 404), (answer.status, answer.body[:200])
            assert "json" in answer.header("content-type"), (answer.header("content-type"), answer.body[:200])

        hooks()

        @check(f"{label} a second device pairs through the proxy and gets chat only")
        def pairing():
            issued = stack.app_exec("python", "-m", "app.auth.cli", "pairing-code", "--scope", "chat", "--label", "Proxy phone")
            assert issued.returncode == 0, issued.stdout + issued.stderr
            assert f"https://{DOMAIN}/pair#code=" in issued.stdout, issued.stdout
            code = next(line.split()[-1] for line in issued.stdout.splitlines() if line.startswith("pairing code:"))
            paired = stack.request(phone, "POST", stack.public("/api/v1/auth/pair"), body={"code": code, "label": "phone"})
            assert paired.status == 200, paired.body
            bearer = {"Authorization": f"Bearer {paired.json()['token']}"}
            assert stack.request(phone, "GET", stack.public("/api/v1/threads"), headers=bearer).status == 200
            assert stack.request(phone, "GET", stack.public("/api/v1/settings"), headers=bearer).status == 403
            again = stack.request(phone, "POST", stack.public("/api/v1/auth/pair"), body={"code": code, "label": "phone"})
            assert again.status in (400, 401, 403, 404, 410), "a pairing code must work only once"

        pairing()

        @check(f"{label} the app logged no complaint about missing forwarded headers")
        def quiet():
            logs = stack.app_logs()
            assert "arrived without X-Forwarded" not in logs, logs[-1500:]

        quiet()

        # -- the trust boundary, measured ------------------------------------------------------------
        app_ip = stack.app_address()
        direct = f"http://{app_ip}:8000"
        forged = {"Host": DOMAIN, "X-Forwarded-Proto": "https"}

        def burn_bucket(client, base, claims):
            for claim in claims[:-1]:
                assert stack.login(client, "wrong passphrase", headers={**forged, "X-Forwarded-For": claim}, base=base).status == 401
            return stack.login(client, PASSPHRASE, headers={**forged, "X-Forwarded-For": claims[-1]}, base=base)

        @check(f"{label} trust boundary: a stray container on the app's network is not believed (only the proxy's address is)")
        def rogue_not_believed():
            cookie = stack.login(rogue, PASSPHRASE, headers={**forged, "X-Forwarded-For": "203.0.113.50"}, base=direct)
            assert cookie.status == 200 and not cookie.secure_cookie, "a stray container's X-Forwarded-Proto was believed"
            final = burn_bucket(rogue, direct, [f"203.0.113.{n}" for n in range(1, 6)] + ["203.0.113.99"])
            assert final.status == 429, "a stray container's X-Forwarded-For chose its own lockout bucket"

        rogue_not_believed()

        @check(f"{label} trust boundary: a container on another network never gets forged headers believed (unreachable, or seen as the gateway)")
        def other_network():
            other = f"{PROJECT}-{kind}-other"
            run(["docker", "rm", "-f", other], check_exit=False)
            run(["docker", "run", "-d", "--name", other, "--network", "bridge", "--entrypoint", "sleep", CURL_IMAGE, "3600"])
            try:
                probe = run(["docker", "exec", other, "curl", "-s", "-m", "5", "-o", "/dev/null", "-w", "%{http_code}", f"{direct}/api/v1/health"], check_exit=False)
                # Any HTTP answer (the app refuses a bare-address Host with 400) means it can be reached; 000 means no connection.
                if probe.stdout.strip() in ("", "000"):
                    stack.reachable_from_other_network = False
                    return
                stack.reachable_from_other_network = True
                answer = stack.request(other, "POST", direct + "/api/v1/auth/login", body={"passphrase": PASSPHRASE}, headers={**forged, "X-Forwarded-For": "203.0.113.60"})
                assert answer.status == 200 and not answer.secure_cookie, "forged headers from another network were believed"
            finally:
                run(["docker", "rm", "-f", other], check_exit=False)

        other_network()
        print(f"      (a container on another network can reach the app here: {getattr(stack, 'reachable_from_other_network', '?')})")

        @check(f"{label} trust boundary, for contrast: trusting the whole network WOULD believe a stray container; back to the proxy only, it does not")
        def contrast():
            stack.overrides["OPEN_DOTS_TRUSTED_PROXIES"] = SUBNET
            stack.compose("up", "-d", "--no-build", "open-dots")
            stack.wait_healthy()
            new_direct = f"http://{stack.app_address()}:8000"
            believed = stack.login(rogue, PASSPHRASE, headers={**forged, "X-Forwarded-For": "203.0.113.150"}, base=new_direct)
            assert believed.status == 200 and believed.secure_cookie, "the whole-network trust did not behave as expected"
            stack.overrides.pop("OPEN_DOTS_TRUSTED_PROXIES")
            stack.compose("up", "-d", "--no-build", "open-dots")
            stack.wait_healthy()
            back = f"http://{stack.app_address()}:8000"
            again = stack.login(rogue, PASSPHRASE, headers={**forged, "X-Forwarded-For": "203.0.113.151"}, base=back)
            assert again.status in (200, 429) and not again.secure_cookie, "the proxy-only trust was not restored"
            # and the proxy itself still works after the app was recreated
            assert stack.request(c1, "GET", stack.public("/api/v1/health")).status == 200

        contrast()
    finally:
        if not args.keep:
            for name in names + [f"{kind}-other"]:
                run(["docker", "rm", "-f", f"{PROJECT}-{name}"], check_exit=False)
            stack.compose("down", "-v", "--remove-orphans", check_exit=False)
            shutil.rmtree(scratch, ignore_errors=True)
        else:
            print(f"left in place: compose project {PROJECT}, scratch {scratch}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--proxy", choices=("nginx", "caddy", "both"), default="both")
    parser.add_argument("--skip-build", action="store_true", help=f"use the existing image {IMAGE}")
    parser.add_argument("--keep", action="store_true", help="leave the stack, clients and scratch folder in place")
    args = parser.parse_args()
    if run(["docker", "version", "--format", "{{.Server.Version}}"], check_exit=False).returncode != 0:
        print("Docker is not available.", file=sys.stderr)
        return 2
    if not args.skip_build:
        run(["docker", "build", "-t", IMAGE, "."], timeout=1800)
    kinds = ["nginx", "caddy"] if args.proxy == "both" else [args.proxy]
    try:
        for kind in kinds:
            matrix(kind, args)
    except BaseException as error:
        print(f"\nSmoke test failed: {type(error).__name__}: {error}", file=sys.stderr)
        args.keep = True
        raise
    total = sum(seconds for _, seconds in CHECKS)
    print(f"\nall {len(CHECKS)} checks passed in {total:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
