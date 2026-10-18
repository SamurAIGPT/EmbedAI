"""Features verified together on the real backend process, with the real auth
middleware and the fake engines (docs/RELIABILITY.md):

- an agent with a computer runs a routine that needs an approval, signed in;
- the webhook ingress app against the main app's auth;
- a paired chat-scope device chats but cannot reach admin routes;
- approvals and the decision log across Claude, Codex and an ACP agent.
"""

import json
import stat
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from tests.computers.harness import FakeDocker
from tests.e2e.backend import Backend, Owner, free_port, wait_for

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX fake engines")

SERVICES = Path(__file__).parents[1] / "services"


@pytest.fixture
def backends():
    started: list = []
    yield started
    for b in started:
        b.stop()


def make(backends, root: Path, **kwargs) -> Backend:
    b = Backend(root, **kwargs)
    backends.append(b)
    return b


def run_state(b: Backend, run_id: int) -> str:
    return b.query("SELECT state FROM routine_run WHERE id = ?", run_id)[0][0]


def decisions(b: Backend) -> list[dict]:
    folder = b.root / "decisions"
    rows = []
    for path in sorted(folder.glob("decisions-*.ndjson")):
        rows += [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return rows


def pending(client, thread_id: int) -> dict | None:
    items = client.get(f"/api/v1/threads/{thread_id}/approvals", params={"status": "pending"}).json()
    return items[0] if items else None


# --- an agent with a computer, a routine, an approval, signed in --------------------------


def test_a_routine_on_an_agent_with_a_computer_waits_for_an_approval_under_sign_in(backends, tmp_path):
    docker = FakeDocker(tmp_path / "fake-docker")
    docker.add_image()
    b = make(backends, tmp_path / "b", env={"OPEN_DOTS_DOCKER": str(docker.path)}).start()
    owner = Owner(b)
    try:
        # Signed out, nothing but the public routes answers.
        assert owner.get("/api/v1/agents").status_code == 401
        owner.setup()
        agent = owner.post("/api/v1/agents", json={"name": "Builder", "approval_mode": "ask"})
        assert agent.status_code == 201, agent.text
        agent_id = agent.json()["id"]
        computer = owner.post(f"/api/v1/agents/{agent_id}/computer", json={})
        assert computer.status_code == 201, computer.text

        # A write without the CSRF header is refused even when signed in.
        no_csrf = owner.http.post("/api/v1/routines", json={"agent_id": agent_id, "name": "x", "prompt": "y"})
        assert no_csrf.status_code == 403

        b.script({"type": "tool", "tool_name": "mcp__computer__exec",
                  "tool_input": {"command": "echo built > out.txt && cat out.txt"}})
        routine = owner.post("/api/v1/routines", json={
            "agent_id": agent_id, "name": "Build", "prompt": "build it", "approval_mode": "ask",
        })
        assert routine.status_code == 201, routine.text
        run = owner.post(f"/api/v1/routines/{routine.json()['id']}/run")
        assert run.status_code == 202, run.text
        run_id = run.json()["id"]

        wait_for(lambda: run_state(b, run_id) == "needs_input", 30, what="the run to wait for an approval")
        thread_id = b.query("SELECT thread_id FROM routine_run WHERE id = ?", run_id)[0][0]
        approval = pending(owner, thread_id)
        assert approval["tool"] == "mcp__computer__exec"
        notes = owner.get("/api/v1/notifications").json()
        assert any(n["kind"] == "routine_needs_input" for n in notes)
        # Nothing ran in the computer before the Allow.
        assert not any(c[:1] == ["exec"] for c in docker.calls())

        answer = owner.post(f"/api/v1/threads/{thread_id}/approvals/{approval['id']}", json={"decision": "allow"})
        assert answer.status_code == 200, answer.text
        wait_for(lambda: run_state(b, run_id) == "completed", 30, what="the run to complete")

        result = b.cli_log("mcp_result")[0]
        assert not result["is_error"] and "built" in result["content"]
        assert any(c[:1] == ["exec"] for c in docker.calls())
        events = [(d["event"], d.get("source")) for d in decisions(b) if d.get("thread_id") == thread_id]
        assert any(event == "shown" for event, _ in events)
        assert ("allowed", "user") in events
    finally:
        owner.close()


# --- the webhook ingress app and the main app ----------------------------------------------


class Ingress:
    """app/ingress.py as its own process, on the main backend's database."""

    def __init__(self, main: Backend) -> None:
        self.main = main
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.process = None

    def start(self):
        log = (self.main.root / "ingress.log").open("ab")
        self.process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.ingress:app", "--host", "127.0.0.1", "--port", str(self.port)],
            cwd=self.main.root, env=self.main.env(), stdout=log, stderr=subprocess.STDOUT,
        )
        log.close()

        def up():
            try:
                return httpx.get(f"{self.url}/health", timeout=2).status_code == 200
            except httpx.HTTPError:
                return False

        wait_for(up, 60, what="the ingress app")
        return self

    def stop(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=20)


def test_the_webhook_ingress_serves_only_hooks_and_the_main_app_keeps_its_auth(backends, tmp_path):
    b = make(backends, tmp_path / "b").start()
    owner = Owner(b)
    ingress = Ingress(b).start()
    try:
        owner.setup()
        agent = owner.post("/api/v1/agents", json={"name": "Hooked"}).json()
        routine = owner.post("/api/v1/routines", json={"agent_id": agent["id"], "name": "On push", "prompt": "react"})
        routine_id = routine.json()["id"]
        secret = owner.post(f"/api/v1/routines/{routine_id}/webhook").json()["secret"]
        b.script({"type": "chat", "text": "reacted"})

        with httpx.Client(base_url=ingress.url, timeout=30) as hook:
            # Only the webhook and health exist on the ingress: no API, no docs.
            for path in ("/api/v1/threads", "/api/v1/auth/status", "/docs", "/openapi.json"):
                assert hook.get(path).status_code == 404, path
            assert hook.post(f"/hooks/{routine_id}", json={"x": 1}).status_code == 401
            assert hook.post(f"/hooks/{routine_id}", json={"x": 1},
                             headers={"Authorization": "Bearer wrong"}).status_code in (401, 403)
            # A public name works on the ingress (that is what a tunnel sends)...
            accepted = hook.post(f"/hooks/{routine_id}", json={"event": "push"},
                                 headers={"Authorization": f"Bearer {secret}", "Host": "hooks.example.com"})
            assert accepted.status_code == 202, accepted.text

        # ...while the main app refuses an unknown Host outright, even for /hooks.
        with httpx.Client(base_url=b.url, timeout=30) as main:
            refused = main.post(f"/hooks/{routine_id}", json={"event": "push"},
                                headers={"Authorization": f"Bearer {secret}", "Host": "hooks.example.com"})
            assert refused.status_code in (400, 403, 421), refused.text
            # And without a session the API is closed; with the secret alone too.
            assert main.get("/api/v1/routines").status_code == 401
            assert main.get("/api/v1/routines", headers={"Authorization": f"Bearer {secret}"}).status_code == 401

        # The ingress only queued the run; the main backend's scheduler ran it.
        def completed():
            rows = b.query("SELECT state, trigger FROM routine_run WHERE routine_id = ?", routine_id)
            return rows and rows[0] == ("completed", "webhook")

        wait_for(completed, 30, what="the webhook run to complete")
    finally:
        ingress.stop()
        owner.close()


# --- a paired chat-scope device --------------------------------------------------------


def test_a_chat_scope_device_chats_and_answers_approvals_but_reaches_no_admin_route(backends, tmp_path):
    b = make(backends, tmp_path / "b").start()
    owner = Owner(b)
    try:
        owner.setup()
        code = owner.post("/api/v1/auth/pairing-codes", json={"label": "Phone", "scope": "chat"})
        assert code.status_code == 201, code.text
        with b.client() as anon:
            paired = anon.post("/api/v1/auth/pair", json={"code": code.json()["code"]})
        assert paired.status_code == 200, paired.text
        token = paired.json()["token"]
        device = b.client(headers={"Authorization": f"Bearer {token}"})

        b.script({"type": "tool", "tool_name": "Bash", "tool_input": {"command": "ls"}})
        thread = device.post("/api/v1/threads", json={})
        assert thread.status_code == 201, thread.text
        thread_id = thread.json()["id"]
        turn = device.post(f"/api/v1/threads/{thread_id}/turns", json={"prompt_text": "list files"})
        assert turn.status_code == 202, turn.text
        approval = wait_for(lambda: pending(device, thread_id), 30, what="the approval")
        assert device.post(f"/api/v1/threads/{thread_id}/approvals/{approval['id']}",
                           json={"decision": "deny"}).status_code == 200
        wait_for(lambda: b.query("SELECT status FROM turn WHERE id = ?", turn.json()["id"])[0][0] != "running",
                 30, what="the turn to end")
        assert device.get(f"/api/v1/threads/{thread_id}/events").status_code == 200
        assert device.get("/api/v1/engines").status_code == 200

        admin_only = [
            ("GET", "/api/v1/settings"), ("GET", "/api/v1/routines"), ("GET", "/api/v1/approval-rules"),
            ("GET", "/api/v1/auth/devices"), ("POST", "/api/v1/auth/pairing-codes"), ("GET", "/api/v1/computers"),
            ("POST", "/api/v1/agents"), ("GET", "/api/v1/notifications"), ("DELETE", f"/api/v1/threads/{thread_id}"),
            ("PUT", "/api/v1/engines/acp/config"),
        ]
        for method, path in admin_only:
            response = device.request(method, path, json={} if method in ("POST", "PUT") else None)
            assert response.status_code == 403, (method, path, response.status_code)
            assert response.json().get("error_code", response.json().get("detail", {})) is not None

        # Revoked from the owner's Devices list: the token stops working at once.
        devices = owner.get("/api/v1/auth/devices").json()
        phone = next(d for d in devices if d["kind"] == "device")
        assert owner.delete(f"/api/v1/auth/devices/{phone['id']}").status_code == 204
        assert device.get("/api/v1/threads").status_code == 401
        device.close()
    finally:
        owner.close()


# --- approvals and the decision log across engines --------------------------------------


def executable(path: Path, body: str) -> Path:
    path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def test_approvals_and_the_decision_log_work_the_same_on_claude_codex_and_acp(backends, tmp_path):
    codex_dir = tmp_path / "codex"
    codex_dir.mkdir()
    (codex_dir / "plan.json").write_text(json.dumps({"login": "in", "steps": [
        {"type": "command", "command": "ls -la", "then": "listed"}]}))
    codex = executable(tmp_path / "codex-bin", f'exec "{sys.executable}" "{SERVICES / "fake_codex_cli.py"}" "$@"')
    acp_dir = tmp_path / "acp"
    acp_dir.mkdir()
    (acp_dir / "plan.json").write_text(json.dumps({"steps": [
        {"type": "permission", "kind": "execute", "command": "ls"}]}))
    acp = executable(tmp_path / "acp-agent", f'exec "{sys.executable}" "{SERVICES / "fake_acp_agent.py"}" "$@"')

    b = make(backends, tmp_path / "b", env={
        "LOCAL_CODEX_CLI_PATH": str(codex), "FAKE_CODEX_DIR": str(codex_dir), "FAKE_ACP_DIR": str(acp_dir),
    }).start()
    owner = Owner(b)
    try:
        owner.setup()
        configured = owner.put("/api/v1/engines/acp/config", json={"command": str(acp)})
        assert configured.status_code == 200, configured.text
        engines = {e["id"]: e for e in owner.get("/api/v1/engines", params={"refresh": "true"}).json()}
        for engine in ("claude", "codex", "acp"):
            assert engines[engine]["available"], (engine, engines[engine]["reason"])

        b.script({"type": "tool", "tool_name": "Bash", "tool_input": {"command": "ls"}})
        threads = {}
        for engine, decision in (("claude", "allow"), ("codex", "deny"), ("acp", "allow")):
            thread = owner.post("/api/v1/threads", json={"provider": engine})
            assert thread.status_code == 201, thread.text
            thread_id = thread.json()["id"]
            turn = owner.post(f"/api/v1/threads/{thread_id}/turns", json={"prompt_text": "list the files"})
            assert turn.status_code == 202, (engine, turn.text)
            approval = wait_for(lambda: pending(owner, thread_id), 30, what=f"the {engine} approval")
            answer = owner.post(f"/api/v1/threads/{thread_id}/approvals/{approval['id']}", json={"decision": decision})
            assert answer.status_code == 200, (engine, answer.text)
            wait_for(lambda: b.query("SELECT status FROM turn WHERE id = ?", turn.json()["id"])[0][0] != "running",
                     30, what=f"the {engine} turn to end")
            status = b.query("SELECT status FROM turn WHERE id = ?", turn.json()["id"])[0][0]
            assert status == "succeeded", (engine, status, b.query("SELECT error FROM turn WHERE id = ?",
                                                                   turn.json()["id"]))
            threads[engine] = (thread_id, decision)

        log = decisions(b)
        for engine, (thread_id, decision) in threads.items():
            mine = [d for d in log if d.get("thread_id") == thread_id]
            assert any(d["event"] == "shown" for d in mine), (engine, mine)
            assert any(d["event"] == ("allowed" if decision == "allow" else "denied") and d.get("source") == "user"
                       for d in mine), (engine, mine)
        # The decision log is readable through the API too, newest first.
        listed = owner.get("/api/v1/decisions")
        assert listed.status_code == 200, listed.text
    finally:
        owner.close()
