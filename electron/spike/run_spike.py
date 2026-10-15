"""The spike's verification run (Windows). Drives the real Electron app, the
packaged backend and the real Claude CLI.

    uv run --project ../backend python spike/run_spike.py

1. Start Electron with Python removed from PATH. It must start the packaged
   backend by itself and show the existing page.
2. Through that backend: a real Claude conversation, including a tool approval.
3. Quit Electron the normal way; the backend must go too.
4. Start Electron again on the same data folder: the thread, its history, the
   page's stored state and the Claude session must all still be there.
5. Kill Electron hard; the backend must not be left running.
"""

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ELECTRON = Path(__file__).resolve().parents[1]
ELECTRON_EXE = ELECTRON / "node_modules" / "electron" / "dist" / "electron.exe"
DATA = Path(tempfile.mkdtemp(prefix="od-spike-"))


def python_free_env() -> dict:
    """The environment with every PATH folder that holds a Python removed."""
    removed = []
    keep = []
    for entry in os.environ["PATH"].split(os.pathsep):
        has_python = entry and any((Path(entry) / n).exists() for n in ("python.exe", "python3.exe", "python"))
        (removed if has_python else keep).append(entry)
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("VIRTUAL_ENV", "PYTHON", "UV_", "ELECTRON_RUN_AS_NODE"))}
    env["PATH"] = os.pathsep.join(keep)
    print("PATH folders removed because they hold a Python:", len(removed))
    probe = subprocess.run(["where", "python"], env=env, capture_output=True, text=True)
    print("`where python` with that PATH:", "found " + probe.stdout.strip() if probe.returncode == 0 else "not found")
    print("`where claude` with that PATH:", subprocess.run(["where", "claude"], env=env, capture_output=True, text=True).stdout.strip().splitlines()[:1])
    return env


def pid_alive(pid: int) -> bool:
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
    return str(pid) in out


def launch(env: dict, tag: str, set_mark: bool):
    report = DATA / f"report-{tag}.json"
    full = {**env, "OD_SPIKE_DATA": str(DATA / "userdata"), "OD_SPIKE_REPORT": str(report)}
    if set_mark:
        full["OD_SPIKE_SETMARK"] = "1"
    (DATA / "userdata").mkdir(exist_ok=True)
    (DATA / "userdata" / "spike-state.json").unlink(missing_ok=True)
    started = time.monotonic()
    proc = subprocess.Popen([str(ELECTRON_EXE), "."], cwd=ELECTRON, env=full, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    while not report.exists():
        if proc.poll() is not None:
            raise SystemExit(f"Electron exited early ({proc.returncode}); see {DATA / 'userdata' / 'backend.log'}")
        if time.monotonic() - started > 120:
            raise SystemExit("no report within 120 s")
        time.sleep(0.3)
    time.sleep(0.3)
    state = json.loads((DATA / "userdata" / "spike-state.json").read_text())
    return proc, json.loads(report.read_text()), state, time.monotonic() - started


class Api:
    def __init__(self, port: int):
        self.base = f"http://127.0.0.1:{port}/api/v1"
        self.c = httpx.AsyncClient(timeout=60)

    async def events(self, tid, after=0):
        return (await self.c.get(f"{self.base}/threads/{tid}/events", params={"after": after, "limit": 500})).json()["events"]

    async def turn(self, tid, prompt, approve=False, timeout=240):
        before = len([e for e in await self.events(tid) if e["type"].startswith("turn.") and e["type"] != "turn.started"])
        r = await self.c.post(f"{self.base}/threads/{tid}/turns", json={"prompt_text": prompt})
        assert r.status_code == 202, (r.status_code, r.text)
        asked = None
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if approve and asked is None:
                pend = (await self.c.get(f"{self.base}/threads/{tid}/approvals", params={"status": "pending"})).json()
                if pend:
                    asked = pend[0]
                    self.last_ask = asked
                    ans = await self.c.post(f"{self.base}/threads/{tid}/approvals/{asked['id']}", json={"decision": "allow"})
                    assert ans.status_code == 200, (ans.status_code, ans.text)
            events = await self.events(tid)
            done = [e for e in events if e["type"].startswith("turn.") and e["type"] != "turn.started"]
            if len(done) > before:
                return done[-1]["type"]
            await asyncio.sleep(0.5)
        raise SystemExit("turn timed out")

    async def last_result(self, tid):
        return (await self.c.get(f"{self.base}/threads/{tid}")).json()["turns"][-1]["result"]


async def main():
    env = python_free_env()
    print("data folder:", DATA)

    # ---- 1. start Electron
    proc, rep, state, took = launch(env, "1", set_mark=True)
    print(f"\n[1] Electron window up after {took:.1f} s (backend ready {rep['readyMs']} ms after start)")
    print("    url:", rep["url"], "| title:", rep["title"], "| node leaked into the page:", rep["nodeLeak"])
    print("    page text starts:", repr(rep["text"][:160]))
    print("    backend pid", state["backendPid"], "alive:", pid_alive(state["backendPid"]))

    # ---- 2. a real conversation with an approval
    api = Api(state["port"])
    tid = (await api.c.post(f"{api.base}/threads", json={})).json()["id"]
    print("\n[2] thread", tid)
    print("    turn 1:", await api.turn(tid, "Remember the code word PELICAN. Reply with just: noted"), "->", repr(await api.last_result(tid)))
    ws = DATA / "userdata" / "workspace"
    out = await api.turn(tid, "Use the Write tool to create a file named spike.txt containing exactly: hello from the packaged backend", approve=True)
    found = list(ws.rglob("spike.txt"))
    print("    turn 2 (approval):", out, "| asked:", api.last_ask["tool"], "| file written after Allow:", [f.read_text().strip() for f in found])
    events = await api.events(tid)
    seq_before = max(e["seq"] for e in events)
    print("    stored events:", len(events), "last seq", seq_before)
    await api.c.aclose()

    # ---- 3. normal quit
    t = time.monotonic()
    subprocess.run(["taskkill", "/PID", str(proc.pid)], capture_output=True)
    try:
        proc.wait(timeout=30)
        print(f"\n[3] Electron quit normally in {time.monotonic() - t:.1f} s | backend still alive: {pid_alive(state['backendPid'])}")
    except subprocess.TimeoutExpired:
        print("\n[3] Electron did not quit on a normal close request; forcing")
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
        print("    backend still alive:", pid_alive(state["backendPid"]))

    # ---- 4. restart on the same data folder
    proc, rep, state2, took = launch(env, "2", set_mark=False)
    print(f"\n[4] Electron restarted: window up after {took:.1f} s | port {state2['port']} (was {state['port']}) | new backend pid {state2['backendPid']}")
    print("    page state kept (localStorage mark):", rep["mark"])
    api = Api(state2["port"])
    threads = (await api.c.get(f"{api.base}/threads")).json()
    print("    threads after restart:", [(x["id"], x["session_state"]) for x in threads])
    events2 = await api.events(tid)
    print("    history intact:", len(events2) == len(events) and [e["seq"] for e in events2] == [e["seq"] for e in events], f"({len(events2)} events)")
    out = await api.turn(tid, "What was the code word I asked you to remember? Reply with just the word.")
    result = await api.last_result(tid)
    print("    next message:", out, "->", repr(result), "| remembered PELICAN:", "PELICAN" in (result or "").upper())
    await api.c.aclose()

    # ---- 5. hard kill of Electron only
    subprocess.run(["taskkill", "/F", "/PID", str(proc.pid)], capture_output=True)
    end = time.monotonic() + 30
    while pid_alive(state2["backendPid"]) and time.monotonic() < end:
        time.sleep(0.5)
    print(f"\n[5] Electron killed hard | backend still alive after wait: {pid_alive(state2['backendPid'])}")
    if pid_alive(state2["backendPid"]):
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(state2["backendPid"])], capture_output=True)

    print("\nlog tail:", (DATA / "userdata" / "backend.log").read_text(errors="replace").strip().splitlines()[-3:])
    print("data folder:", DATA)


asyncio.run(main())
