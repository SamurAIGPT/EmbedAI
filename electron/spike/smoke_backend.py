"""Does the frozen backend run on its own, with no Python on PATH?

Starts build/dist/od-backend/od-backend.exe with a clean environment (PATH is the
system folders only), a temporary data folder and the exported web page, then
checks the API, the page, the database and a clean stop.

    python spike/smoke_backend.py
"""

import base64
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ELECTRON = Path(__file__).resolve().parents[1]
EXE = ELECTRON / "build" / "dist" / ("od-backend.exe" if sys.platform == "win32" else "od-backend")
EXE = EXE if EXE.exists() else ELECTRON / "build" / "dist" / "od-backend" / EXE.name
UI = ELECTRON.parent / "frontend" / "out"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def get(url: str):
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def main() -> None:
    data = Path(tempfile.mkdtemp(prefix="od-smoke-"))
    port = free_port()
    system = os.environ.get("SystemRoot", r"C:\Windows")
    env = {
        "PATH": rf"{system}\System32;{system}" if sys.platform == "win32" else "/usr/bin:/bin",
        "SystemRoot": system,
        "OD_PORT": str(port),
        "OD_UI_DIR": str(UI),
        "OD_WATCH_STDIN": "1",
        "DATABASE_BACKEND": "sqlite",
        "SQLITE_PATH": str(data / "open-dots.db"),
        "LOCAL_WORKSPACE_ROOT": str(data / "workspace"),
        "FRONTEND_ORIGINS": f"http://127.0.0.1:{port}",
        "TOKEN_ENCRYPTION_KEYS": base64.urlsafe_b64encode(os.urandom(32)).decode(),
        "PYTHONUTF8": "1",
    }
    print("exe:", EXE, f"({EXE.stat().st_size // 1024} KB, folder {sum(f.stat().st_size for f in EXE.parent.rglob('*') if f.is_file()) // 2**20} MB)")
    log = open(data / "backend.log", "wb")
    started = time.monotonic()
    proc = subprocess.Popen([str(EXE)], cwd=data, env=env, stdin=subprocess.PIPE, stdout=log, stderr=log)
    base = f"http://127.0.0.1:{port}"
    ready = None
    while time.monotonic() - started < 90:
        if proc.poll() is not None:
            break
        try:
            if get(f"{base}/api/v1/providers")[0] == 200:
                ready = time.monotonic() - started
                break
        except OSError:
            time.sleep(0.2)
    if ready is None:
        proc.kill()
        print("NOT READY. exit code:", proc.poll(), "\n", (data / "backend.log").read_text(errors="replace")[-3000:])
        raise SystemExit(1)
    print(f"ready after {ready:.1f} s (includes migrations)")

    status, body = get(f"{base}/api/v1/providers")
    print("GET /api/v1/providers ->", status, body[:120])
    status, body = get(f"{base}/")
    print("GET / ->", status, "| looks like the page:", b"<html" in body.lower(), len(body), "bytes")
    status, body = get(f"{base}/api/v1/threads")
    print("GET /api/v1/threads ->", status, body[:120])
    print("database file created:", (data / "open-dots.db").exists())

    t = time.monotonic()
    proc.stdin.close()
    try:
        code = proc.wait(timeout=30)
        print(f"clean stop on stdin close: exit {code} in {time.monotonic() - t:.1f} s")
    except subprocess.TimeoutExpired:
        proc.kill()
        print("did not stop within 30 s; killed")
    print("log tail:", (data / "backend.log").read_text(errors="replace").strip().splitlines()[-3:])
    print("data dir:", data)


main()
