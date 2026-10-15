"""The thread event stream through the frozen backend, on the data left by run_spike.py.

    uv run --project ../backend python spike/check_sse.py <data folder printed by run_spike.py>
"""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

data = Path(sys.argv[1]) / "userdata"
exe = Path(__file__).resolve().parents[1] / "build" / "dist" / "od-backend" / "od-backend.exe"
with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
env = {
    **os.environ, "OD_PORT": str(port), "OD_WATCH_STDIN": "1", "DATABASE_BACKEND": "sqlite",
    "SQLITE_PATH": str(data / "open-dots.db"), "LOCAL_WORKSPACE_ROOT": str(data / "workspace"),
    "LOCAL_EXECUTION_ENABLED": "true", "FRONTEND_ORIGINS": f"http://127.0.0.1:{port}",
    "TOKEN_ENCRYPTION_KEYS": (data / "token.key").read_text().strip(), "PYTHONUTF8": "1",
}
proc = subprocess.Popen([str(exe)], cwd=data, env=env, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
base = f"http://127.0.0.1:{port}/api/v1"
for _ in range(100):
    try:
        if httpx.get(f"{base}/threads").status_code == 200:
            break
    except httpx.HTTPError:
        time.sleep(0.3)
stored = [e["seq"] for e in httpx.get(f"{base}/threads/1/events", params={"after": 10, "limit": 500}).json()["events"]]
frames = []
with httpx.stream("GET", f"{base}/threads/1/events/stream", headers={"Last-Event-ID": "10"}, timeout=5) as r:
    print("status", r.status_code, r.headers.get("content-type"))
    try:
        for line in r.iter_lines():
            if line.startswith("id: "):
                frames.append(int(line[4:]))
            if len(frames) >= len(stored):
                break
    except httpx.ReadTimeout:
        pass
print("stream after 10 ->", frames, "| stored after 10 ->", stored, "| same:", frames == stored)
proc.stdin.close()
print("stopped cleanly:", proc.wait(timeout=30) == 0)
