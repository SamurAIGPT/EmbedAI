import json
import threading
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs
from types import SimpleNamespace
import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Bot

from app import telegram_bot
from app.db.models.task import Tasks


@pytest.fixture
def bot_api():
    """Owned loopback HTTP fixture implementing Telegram's plain-text limit."""
    messages = []
    state = {"polls": 0, "finish_after": 125, "elapsed": 0}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            assert self.path == "/sandboxes/owned-box/commands/1"
            state["polls"] += 1
            running = state["finish_after"] is None or state["polls"] < state["finish_after"]
            body = json.dumps({"ok": True, "type": "command.status", "success": True,
                               "processId": 1, "status": "running" if running else "exited",
                               "running": running, "exitCode": None if running else 0,
                               "stdout": "" if running else json.dumps({"result": "completed after five minutes"}),
                               "stderr": ""}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            data = parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
            if self.path.endswith("/getMe"):
                payload = {"ok": True, "result": {"id": 123, "is_bot": True, "first_name": "Owned", "username": "owned_bot"}}
                status = 200
            else:
                assert self.path.endswith("/sendMessage")
                text = data["text"][0]
                if not 0 < len(text) <= 4096:
                    payload = {"ok": False, "error_code": 400, "description": "Bad Request: message is too long"}
                    status = 400
                else:
                    messages.append(text)
                    payload = {"ok": True, "result": {"message_id": len(messages), "date": 1700000000, "chat": {"id": 1, "type": "private"}, "text": text}}
                    status = 200
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", messages, state
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
        assert not worker.is_alive()


@pytest.mark.parametrize("finish_after", [1, 125, None], ids=["short-positive", "later-success", "bounded-still-active"])
async def test_task_poll_uses_command_budget(db_session, user_id, monkeypatch, bot_api, finish_after):
    task = Tasks(user_id=user_id, provider="claude", box_id="owned-box", prompt_text="report",
                 status="running", prompt_id="1")
    db_session.add(task)
    await db_session.flush()

    @asynccontextmanager
    async def session():
        yield db_session

    base_url, messages, state = bot_api
    state["finish_after"] = finish_after
    original_sleep = asyncio.sleep

    async def advance(seconds):
        # Only skip elapsed waiting; HTTP, provider SDK and database status transitions remain real.
        state["elapsed"] += seconds
        await original_sleep(0)

    monkeypatch.setattr(telegram_bot, "AsyncSessionLocal", session)
    monkeypatch.setattr(telegram_bot, "asyncio", SimpleNamespace(sleep=advance))
    monkeypatch.setattr(telegram_bot.settings, "BOAT_BASE_URL", base_url)
    async with Bot(token="123:owned-fixture", base_url=base_url + "/bot") as bot:
        await telegram_bot._poll_task(bot, 1, task.id)
    if finish_after is None:
        assert task.status == "running"
        assert telegram_bot.task_execution_service.MAX_TASK_SECONDS <= state["elapsed"] <= telegram_bot.task_execution_service.MAX_TASK_SECONDS + telegram_bot.POLL_INTERVAL_SECONDS
        assert "still" in messages[-1].lower()
        assert "failed" not in messages[-1].lower()
    else:
        assert task.status == "succeeded"
        assert messages == ["completed after five minutes"]
        assert state["polls"] == finish_after
