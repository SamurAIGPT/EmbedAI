#!/usr/bin/env python3
"""A real conversation through the server run mode (`python -m app.server`).

scripts/smoke-server.py checks the server's own behaviour (pages, sign-in, pairing,
proxy headers). This one checks that the server can do the work it exists for. It
starts `python -m app.server` on a scratch SQLite database with a fake `claude`
command (backend/tests/e2e/fake_claude.py), and walks through: first-run sign-in,
an agent answering a turn with local execution switched on in the settings, the live
event stream (also while a turn runs), a tool call that waits for an approval, an engine configured through the
API without its key ever being stored in the clear, a routine run now and one that fires by
itself, a refused second server, SIGTERM in the middle of a turn, a restart that keeps
everything, and a backup restored into a different folder where a conversation carries on.

POSIX only (the fake command is a shell wrapper). Nothing outside the scratch folder
is touched; no network, no real engine.

    cd backend && uv run python ../scripts/smoke-server-chat.py --ui-dir ../frontend/out [--keep]
    cd backend && uv run python ../scripts/smoke-server-chat.py --stand-in-ui

Exit code 0 when every check passed.
"""

import argparse
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from tests.e2e.backend import Backend, Owner, alive, wait_for  # noqa: E402

CHECKS: list[tuple[str, float]] = []
SECRET_KEY = "sk-smoke-test-do-not-store-in-the-clear-0123456789"


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


def make_stand_in_ui(folder: Path) -> Path:
    for page in ("", "login", "pair", "chat", "settings"):
        (folder / page).mkdir(parents=True, exist_ok=True)
        (folder / page / "index.html").write_text(f"<html><title>{page or 'home'}</title></html>")
    return folder


class ServerBackend(Backend):
    """The same scratch backend, started the way a server install starts it."""

    def __init__(self, root: Path, ui_dir: Path, **kwargs) -> None:
        super().__init__(root, **kwargs)
        self.ui_dir = ui_dir

    def env(self) -> dict:
        env = super().env()
        env["LOCAL_EXECUTION_ENABLED"] = "true"  # a server install turns this on in its settings file
        env.update(OPEN_DOTS_UI_DIR=str(self.ui_dir), OPEN_DOTS_PORT=str(self.port))
        return env

    def migrate(self) -> None:
        self.database.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [sys.executable, "-m", "app.server", "migrate"],
            cwd=self.root, env=self.env(), capture_output=True, text=True, timeout=180,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    def start(self, *, wait: bool = True, migrate: bool = True) -> "ServerBackend":
        if migrate:
            self.migrate()
        log = self.log_path.open("ab")
        self.process = subprocess.Popen(
            [sys.executable, "-m", "app.server"], cwd=self.root, env=self.env(), stdout=log, stderr=subprocess.STDOUT
        )
        log.close()
        if wait:
            self.wait_ready()
        return self


def turn_status(b: Backend, turn_id: int) -> str:
    return b.query("SELECT status FROM turn WHERE id = ?", turn_id)[0][0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ui-dir", help="the folder made by scripts/build-ui.js (frontend/out)")
    parser.add_argument("--stand-in-ui", action="store_true", help="use a few stand-in pages instead of a web build")
    parser.add_argument("--keep", action="store_true", help="keep the scratch folder")
    args = parser.parse_args()
    if sys.platform == "win32":
        print("This check needs a POSIX system (the fake engine command is a shell script).", file=sys.stderr)
        return 2
    if not args.ui_dir and not args.stand_in_ui:
        parser.error("give --ui-dir, or --stand-in-ui for a run with no web build")

    root = Path(tempfile.mkdtemp(prefix="open-dots-smoke-server-chat-"))
    ui_dir = make_stand_in_ui(root / "ui") if args.stand_in_ui else Path(args.ui_dir).resolve()
    b = ServerBackend(root / "server", ui_dir)
    owner = None
    try:
        check("the server starts on a fresh database (python -m app.server migrate, then serve)")(lambda: b.start())()
        owner = Owner(b)

        @check("the pages load, and signed out the API is closed")
        def closed():
            assert b.client().get("/login/").status_code == 200
            assert owner.get("/api/v1/threads").status_code == 401
            assert owner.get("/api/v1/auth/status").json().get("owner_configured") is False

        closed()

        @check("first-run passphrase from the server itself, then sign out and in again")
        def sign_in():
            owner.setup()
            assert owner.post("/api/v1/auth/logout").status_code == 204
            owner.login()
            assert owner.get("/api/v1/threads").status_code == 200

        sign_in()

        @check("the engine is ready with local execution switched on in the settings")
        def engines():
            listed = {e["id"]: e for e in owner.get("/api/v1/engines", params={"refresh": "true"}).json()}
            assert listed["claude"]["available"], listed["claude"]

        engines()

        @check("an agent answers a turn, and the live stream carries the answer")
        def turn_and_stream():
            agent = owner.post("/api/v1/agents", json={"name": "Smoke", "instructions": "Be brief."})
            assert agent.status_code == 201, agent.text
            b.script({"type": "chat", "text": "Hello from the server smoke test"})
            thread = owner.post("/api/v1/threads", json={"agent_id": agent.json()["id"]})
            assert thread.status_code == 201, thread.text
            thread_id = thread.json()["id"]
            turn = owner.post(f"/api/v1/threads/{thread_id}/turns", json={"prompt_text": "hi"})
            assert turn.status_code == 202, turn.text
            wait_for(lambda: turn_status(b, turn.json()["id"]) == "succeeded", 30, what="the turn")
            seen = ""
            with owner.http.stream("GET", f"/api/v1/threads/{thread_id}/events/stream", timeout=20) as stream:
                assert stream.headers["content-type"].startswith("text/event-stream")
                assert stream.headers.get("x-accel-buffering") == "no"
                for chunk in stream.iter_text():
                    seen += chunk
                    if "server smoke test" in seen:
                        break
            assert "assistant.message" in seen and "id:" in seen, seen[:400]
            return agent.json()["id"]

        agent_id = turn_and_stream()

        @check("the live stream delivers events while a turn is still running, and the stop that ends it")
        def live_stream():
            b.script({"type": "hang"})
            thread = owner.post("/api/v1/threads", json={"agent_id": agent_id}).json()
            collected: list[str] = []
            opened = threading.Event()

            def read() -> None:
                # Opened before the turn exists: everything it sees was sent to it live, none replayed.
                try:
                    with owner.http.stream("GET", f"/api/v1/threads/{thread['id']}/events/stream", timeout=30) as stream:
                        opened.set()
                        for chunk in stream.iter_text():
                            collected.append(chunk)
                except httpx.HTTPError:
                    pass

            reader = threading.Thread(target=read, daemon=True)
            reader.start()
            assert opened.wait(10), "the stream did not open"
            turn = owner.post(f"/api/v1/threads/{thread['id']}/turns", json={"prompt_text": "keep going"}).json()
            wait_for(lambda: "turn.started" in "".join(collected), 15, what="turn.started on the open stream")
            assert turn_status(b, turn["id"]) == "running", "the event must arrive while the turn is still running"
            engine = wait_for(lambda: b.cli_log("hanging"), 30, what="the engine")[0]
            assert owner.post(f"/api/v1/threads/{thread['id']}/stop").status_code == 202
            wait_for(lambda: "turn.cancelled" in "".join(collected), 15, what="turn.cancelled on the open stream")
            wait_for(lambda: turn_status(b, turn["id"]) == "cancelled", 15, what="the stopped turn")
            wait_for(lambda: not alive(engine["pid"]), 10, what="the engine to end")

        live_stream()

        @check("a tool call waits for an approval, runs after Allow, and is in the decision log")
        def approval():
            b.script({"type": "tool", "tool_name": "Write", "tool_input": {"file_path": "notes.txt", "content": "hi"}})
            thread = owner.post("/api/v1/threads", json={"agent_id": agent_id}).json()
            turn = owner.post(f"/api/v1/threads/{thread['id']}/turns", json={"prompt_text": "write it"}).json()

            def pending():
                items = owner.get(f"/api/v1/threads/{thread['id']}/approvals", params={"status": "pending"}).json()
                return items[0] if items else None

            card = wait_for(pending, 30, what="the approval")
            answer = owner.post(f"/api/v1/threads/{thread['id']}/approvals/{card['id']}", json={"decision": "allow"})
            assert answer.status_code == 200, answer.text
            wait_for(lambda: turn_status(b, turn["id"]) == "succeeded", 30, what="the approved turn")
            text = str(owner.get("/api/v1/decisions").json())
            assert "allowed" in text and "shown" in text, text[:500]

        approval()

        @check("an engine is configured through the API; its key is never returned, stored or logged in the clear")
        def engine_config():
            saved = owner.put("/api/v1/engines/openai_compat/config",
                              json={"base_url": "http://llm.invalid/v1", "model": "m1", "api_key": SECRET_KEY})
            assert saved.status_code == 200, saved.text
            assert saved.json()["config"] == {"base_url": "http://llm.invalid/v1", "model": "m1", "has_api_key": True}, saved.text
            assert SECRET_KEY not in saved.text and SECRET_KEY not in owner.get("/api/v1/engines").text
            stored = str(b.query("SELECT provider, ciphertext FROM user_credential WHERE provider LIKE 'engine:%'"))
            assert "engine:openai_compat" in stored and SECRET_KEY not in stored, stored[:300]
            assert SECRET_KEY not in b.log()
            assert owner.delete("/api/v1/engines/openai_compat/config").status_code in (200, 204)

        engine_config()

        @check("a routine is created, runs now, completes and notifies")
        def routine():
            b.script({"type": "chat", "text": "routine done"})
            created = owner.post("/api/v1/routines", json={"agent_id": agent_id, "name": "Daily", "prompt": "report"})
            assert created.status_code == 201, created.text
            run = owner.post(f"/api/v1/routines/{created.json()['id']}/run")
            assert run.status_code == 202, run.text
            wait_for(lambda: b.query("SELECT state FROM routine_run WHERE id = ?", run.json()["id"])[0][0] == "completed",
                     30, what="the routine run")
            assert "routine_completed" in [n["kind"] for n in owner.get("/api/v1/notifications").json()]

        routine()

        @check("a one-time routine fires by itself at its time, once")
        def scheduled_routine():
            b.script({"type": "chat", "text": "scheduled done"})
            at = (datetime.now(UTC) + timedelta(seconds=4)).isoformat()
            created = owner.post("/api/v1/routines", json={"agent_id": agent_id, "name": "Once", "prompt": "later",
                                                           "schedule_kind": "once", "schedule_spec": {"at": at}})
            assert created.status_code == 201, created.text
            rid = created.json()["id"]
            assert b.query("SELECT count(*) FROM routine_run WHERE routine_id = ?", rid)[0][0] == 0  # nothing yet: it waits for its time
            wait_for(lambda: b.query("SELECT state FROM routine_run WHERE routine_id = ?", rid) == [("completed",)],
                     40, what="the scheduled run")
            time.sleep(2)  # past another poll: it must not fire again
            assert b.query("SELECT count(*) FROM routine_run WHERE routine_id = ?", rid)[0][0] == 1

        scheduled_routine()

        @check("a second server on the same database is refused")
        def second():
            other = ServerBackend(root / "second", ui_dir, database=b.database)
            other.start(wait=False, migrate=False)
            code = other.wait_exit(timeout=60)
            assert code != 0
            assert "already using the database" in other.log(), other.log()[-2000:]

        second()

        @check("SIGTERM mid-turn interrupts the turn and leaves no engine process")
        def sigterm():
            b.script({"type": "hang_with_child"})
            thread = owner.post("/api/v1/threads", json={"agent_id": agent_id}).json()
            turn = owner.post(f"/api/v1/threads/{thread['id']}/turns", json={"prompt_text": "long job"}).json()
            engine = wait_for(lambda: [e for e in b.cli_log("hanging") if "child_pid" in e], 30, what="the engine")[0]
            b.signal(signal.SIGTERM)
            code = b.wait_exit(timeout=30)
            assert code in (0, -signal.SIGTERM), code
            wait_for(lambda: not alive(engine["pid"]) and not alive(engine["child_pid"]), 10, what="the engine to end")
            assert turn_status(b, turn["id"]) == "interrupted"
            assert "Traceback" not in b.log(), b.log()[-1500:]

        sigterm()

        @check("a restart keeps the owner, the agent and the history, and the pages still load")
        def restart():
            b.script({"type": "chat", "text": "back"})
            b.start(migrate=True)  # migrate first, as a service would
            owner.login()
            assert any(a["id"] == agent_id for a in owner.get("/api/v1/agents").json())
            assert len(owner.get("/api/v1/threads").json()) >= 3
            assert httpx.get(f"{b.url}/login/", timeout=5).status_code == 200

        restart()

        @check("backup, then restore into a different folder: history and files come back, paths move, a conversation continues after a session reset")
        def backup_and_restore():
            old_threads = b.query("SELECT id, workspace_path FROM thread ORDER BY id")
            assert old_threads and all(str(b.root / "work") in (path or "") for _, path in old_threads), old_threads
            resumable = b.query("SELECT id FROM thread WHERE session_state = 'active' ORDER BY id")[0][0]
            owner.close()
            b.stop()  # a quiet server gives a quiet backup

            def run(backend: ServerBackend, *command: str) -> str:
                result = subprocess.run([sys.executable, "-m", "app.server", *command], cwd=backend.root, env=backend.env(),
                                        capture_output=True, text=True, timeout=180)
                assert result.returncode == 0, f"{command}: {result.stdout}{result.stderr}"
                return result.stdout

            out = run(b, "backup", "--out", str(root / "backups"))
            assert "Backup written to" in out and "TOKEN_ENCRYPTION_KEYS" in out, out
            archive = next((root / "backups").glob("open-dots-backup-*.tar.gz"))

            moved = ServerBackend(root / "restored", ui_dir)  # other data, workspace, attachments and engine home
            said = run(moved, "restore", str(archive))
            assert "workspace moved from" in said and "new session" in said, said
            moved.start(migrate=False)
            fresh = Owner(moved)
            try:
                fresh.login()  # the owner and the sign-in are part of the database
                assert [r[0] for r in moved.query("SELECT id FROM thread ORDER BY id")] == [r[0] for r in old_threads]
                new_paths = [r[0] or "" for r in moved.query("SELECT workspace_path FROM thread")]
                assert all(str(moved.root / "work") in p and str(b.root) not in p for p in new_paths), new_paths
                events = fresh.get(f"/api/v1/threads/{resumable}/events").json()
                assert "Hello from the server smoke test" in str(events), "the earlier answer is gone"

                # The engine's own memory of the conversation was not in the backup: the first message says so
                # clearly and marks the thread, instead of quietly starting a different conversation.
                moved.script({"type": "chat", "text": "after the restore"})
                first = fresh.post(f"/api/v1/threads/{resumable}/turns", json={"prompt_text": "are you there?"})
                assert first.status_code == 202, first.text
                wait_for(lambda: moved.query("SELECT session_state FROM thread WHERE id = ?", resumable)[0][0] == "broken",
                         30, what="the thread to report its missing session")
                assert "session_resume_failed" in str(fresh.get(f"/api/v1/threads/{resumable}/events").json())
                blocked = fresh.post(f"/api/v1/threads/{resumable}/turns", json={"prompt_text": "again"})
                assert blocked.status_code == 409 and "session_broken" in blocked.text, blocked.text
                assert fresh.post(f"/api/v1/threads/{resumable}/session/reset").status_code == 200
                moved.script({"type": "chat", "text": "after the restore"})
                again = fresh.post(f"/api/v1/threads/{resumable}/turns", json={"prompt_text": "start over"})
                assert again.status_code == 202, again.text
                wait_for(lambda: turn_status(moved, again.json()["id"]) == "succeeded", 30, what="the turn after the reset")
                assert "Hello from the server smoke test" in str(fresh.get(f"/api/v1/threads/{resumable}/events").json())
            finally:
                fresh.close()
                moved.stop()

        backup_and_restore()
    except BaseException as error:
        print(f"\nSmoke test failed: {type(error).__name__}: {error}", file=sys.stderr)
        print(f"Server log tail:\n{b.log()[-2500:]}", file=sys.stderr)
        args.keep = True
        raise
    finally:
        if owner is not None:
            owner.close()
        b.stop()
        if args.keep:
            print(f"scratch folder kept: {root}")
        else:
            shutil.rmtree(root, ignore_errors=True)
    total = sum(seconds for _, seconds in CHECKS)
    print(f"\nall {len(CHECKS)} checks passed in {total:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
