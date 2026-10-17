#!/usr/bin/env python3
"""End-to-end smoke test of the real backend process (docs/RELIABILITY.md).

Starts the backend with uvicorn on a scratch SQLite database and a fake
`claude` CLI (backend/tests/e2e/fake_claude.py), then walks through what a
person does on a first day: set the owner passphrase, sign out and in, create
an agent, chat, approve a tool call, create and run a routine, and checks that
a second backend on the same data is refused, that SIGTERM mid-turn interrupts
the turn and leaves no engine process, and that a restart keeps everything.

Nothing outside the scratch folder is touched; no network, no real engine.

    cd backend && uv run python ../scripts/smoke-backend.py [--keep]

Exit code 0 when every check passed. Runs in CI (.github/workflows/ci.yml).
"""

import argparse
import shutil
import signal
import sys
import tempfile
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from tests.e2e.backend import Backend, Owner, alive, wait_for  # noqa: E402

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


def turn_status(b: Backend, turn_id: int) -> str:
    return b.query("SELECT status FROM turn WHERE id = ?", turn_id)[0][0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--keep", action="store_true", help="keep the scratch folder")
    args = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix="open-dots-smoke-"))
    b = Backend(root / "backend")
    owner = None
    try:
        start = check("backend starts on a fresh database (migrated with Alembic)")(lambda: b.start())
        start()
        owner = Owner(b)

        @check("signed out, the API is closed and first-run setup is offered")
        def signed_out():
            assert owner.get("/api/v1/threads").status_code == 401
            status = owner.get("/api/v1/auth/status").json()
            assert status.get("owner_configured") is False, status

        signed_out()

        @check("owner passphrase is set, then sign out and sign in again")
        def sign_in():
            owner.setup()
            assert owner.get("/api/v1/auth/session").json().get("signed_in", True)
            assert owner.post("/api/v1/auth/logout").status_code == 204
            assert owner.get("/api/v1/threads").status_code == 401
            owner.login()
            assert owner.get("/api/v1/threads").status_code == 200

        sign_in()

        @check("Claude engine is available")
        def engines():
            listed = {e["id"]: e for e in owner.get("/api/v1/engines", params={"refresh": "true"}).json()}
            assert listed["claude"]["available"], listed["claude"]

        engines()

        @check("an agent is created and answers a turn")
        def agent_turn():
            agent = owner.post("/api/v1/agents", json={"name": "Smoke", "instructions": "Be brief."})
            assert agent.status_code == 201, agent.text
            b.script({"type": "chat", "text": "Hello from the smoke test"})
            thread = owner.post("/api/v1/threads", json={"agent_id": agent.json()["id"]})
            assert thread.status_code == 201, thread.text
            turn = owner.post(f"/api/v1/threads/{thread.json()['id']}/turns", json={"prompt_text": "hi"})
            assert turn.status_code == 202, turn.text
            wait_for(lambda: turn_status(b, turn.json()["id"]) == "succeeded", 30, what="the turn")
            events = owner.get(f"/api/v1/threads/{thread.json()['id']}/events").json()["events"]
            assert any(e["type"] == "assistant.message" and "smoke test" in e["data"]["text"] for e in events)
            return agent.json()["id"]

        agent_id = agent_turn()

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
            log = owner.get("/api/v1/decisions").json()
            text = str(log)
            assert "allowed" in text and "shown" in text, text[:500]

        approval()

        @check("a routine is created, runs now, completes and notifies")
        def routine():
            b.script({"type": "chat", "text": "routine done"})
            created = owner.post("/api/v1/routines", json={"agent_id": agent_id, "name": "Daily", "prompt": "report"})
            assert created.status_code == 201, created.text
            run = owner.post(f"/api/v1/routines/{created.json()['id']}/run")
            assert run.status_code == 202, run.text
            wait_for(lambda: b.query("SELECT state FROM routine_run WHERE id = ?", run.json()["id"])[0][0] == "completed",
                     30, what="the routine run")
            kinds = [n["kind"] for n in owner.get("/api/v1/notifications").json()]
            assert "routine_completed" in kinds, kinds

        routine()

        @check("a second backend on the same database is refused")
        def second():
            other = Backend(root / "second", database=b.database)
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
            engine = wait_for(lambda: b.cli_log("hanging"), 30, what="the engine")[0]
            b.signal(signal.SIGTERM)
            code = b.wait_exit(timeout=30)
            assert code in (0, -signal.SIGTERM), code
            wait_for(lambda: not alive(engine["pid"]) and not alive(engine["child_pid"]), 10, what="the engine to end")
            assert turn_status(b, turn["id"]) == "interrupted"

        sigterm()

        @check("a restart keeps the owner, the agent and the history")
        def restart():
            b.script({"type": "chat", "text": "back"})
            b.start(migrate=False)
            owner.login()
            agents = owner.get("/api/v1/agents").json()
            assert any(a["id"] == agent_id for a in agents)
            assert len(owner.get("/api/v1/threads").json()) >= 3

        restart()
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
