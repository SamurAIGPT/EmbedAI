"""Broken engine programs on the real backend: one that hangs, one that
crashes. Startup is not held up, the engine list answers in bounded time with
the reasons, Claude keeps working meanwhile, and no probe process is left."""

import stat
import subprocess
import sys
import threading
import time

import httpx
import pytest

from tests.e2e.backend import Backend, wait_for

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="shell-script engines")


def program(path, body: str):
    path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def processes_running(marker: str) -> list[str]:
    out = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True).stdout
    return [line for line in out.splitlines() if marker in line and "ps -axo" not in line]


def test_a_hanging_and_a_crashing_engine_block_nothing(tmp_path):
    # Not `exec`: after an exec the process is just `sleep 600` and the marker
    # in the script path disappears from `ps` on Linux.
    hang = program(tmp_path / "hang-codex-od-test", "sleep 600")
    crash = program(tmp_path / "crash-acp-od-test", "echo 'boom' >&2; exit 3")
    b = Backend(tmp_path / "b", env={"LOOPBACK_TRUST": "programs", "LOCAL_CODEX_CLI_PATH": str(hang)})
    started = time.monotonic()
    b.start()
    try:
        # Nothing probes engines at startup.
        assert time.monotonic() - started < 30
        assert not processes_running("hang-codex-od-test")

        with b.client() as http:
            put = http.put("/api/v1/engines/acp/config", json={"command": str(crash)})
            assert put.status_code == 200, put.text
            assert put.json()["available"] is False

            # A listing in flight that waits on the hanging Codex...
            listing: dict = {}

            def list_engines():
                with httpx.Client(base_url=b.url, timeout=60) as other:
                    t0 = time.monotonic()
                    listing["response"] = other.get("/api/v1/engines", params={"refresh": "true"})
                    listing["seconds"] = time.monotonic() - t0

            reader = threading.Thread(target=list_engines)
            reader.start()
            try:
                wait_for(lambda: processes_running("hang-codex-od-test"), 10, what="the codex probe to start")
            except AssertionError as exc:
                everything = subprocess.run(["ps", "-axo", "pid=,ppid=,command="], capture_output=True, text=True).stdout
                raise AssertionError(
                    f"{exc}\nlisting finished: {'response' in listing} {listing.get('response') and listing['response'].status_code}"
                    f"\nprograms: {[p.name for p in hang.parent.iterdir()]} executable={hang.stat().st_mode & 0o111 != 0}"
                    f"\nprocesses:\n{everything[-3000:]}\nbackend log:\n{b.log()[-3000:]}"
                ) from None

            # ...does not hold up a Claude conversation.
            b.script({"type": "chat", "text": "fine here"})
            thread = http.post("/api/v1/threads", json={"provider": "claude"}).json()
            turn = http.post(f"/api/v1/threads/{thread['id']}/turns", json={"prompt_text": "hi"})
            assert turn.status_code == 202, turn.text
            wait_for(lambda: b.query("SELECT status FROM turn WHERE id = ?", turn.json()["id"])[0][0] == "succeeded",
                     20, what="the claude turn")
            assert reader.is_alive(), "the listing should still be waiting on codex"

            reader.join(60)
            response = listing["response"]
            assert response.status_code == 200, response.text
            engines = {e["id"]: e for e in response.json()}
            assert engines["claude"]["available"] is True
            assert engines["codex"]["available"] is False and engines["codex"]["reason"]
            assert engines["acp"]["available"] is False and engines["acp"]["reason"]
            assert "boom" not in engines["acp"]["reason"]
            assert listing["seconds"] < 30

            # A turn on the hanging engine is refused with its reason, not hung.
            codex_thread = http.post("/api/v1/threads", json={"provider": "codex"})
            assert codex_thread.status_code == 201, codex_thread.text
            refused = http.post(f"/api/v1/threads/{codex_thread.json()['id']}/turns", json={"prompt_text": "hi"},
                                timeout=60)
            assert refused.status_code == 503, refused.text

        # The probes killed what they started.
        wait_for(lambda: not processes_running("hang-codex-od-test"), 15, what="probe processes to end")
    finally:
        b.stop()
