import json
import re
from datetime import datetime
from types import SimpleNamespace

import httpx
from fastapi import FastAPI
import threading
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Bot, Update
from telegram.error import BadRequest

from app import telegram_bot
from app.db.models.task import Tasks


@pytest.fixture
def bot_api():
    """Owned loopback HTTP fixture implementing Telegram's plain-text limit."""
    messages = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

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
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/bot", messages
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
        assert not worker.is_alive()


@pytest.mark.parametrize("status, output", [
    ("succeeded", "short report"),
    ("succeeded", "report\n" * 1300),
    ("succeeded", "界" * 5000),
    ("failed", "upstream error\n" * 500),
    ("succeeded", ""),
], ids=["short-success", "long-report", "unicode-report", "long-failure", "empty-success"])
async def test_completed_task_delivers_all_output(
    db_session: AsyncSession, user_id: str, monkeypatch, bot_api, status: str, output: str
):
    task = Tasks(user_id=user_id, provider="claude", box_id="owned-box", prompt_text="report",
                 status=status, result=output if status == "succeeded" else None,
                 error=output if status == "failed" else None)
    db_session.add(task)
    await db_session.flush()

    @asynccontextmanager
    async def session():
        yield db_session

    monkeypatch.setattr(telegram_bot, "AsyncSessionLocal", session)
    base_url, messages = bot_api
    async with Bot(token="123:owned-fixture", base_url=base_url) as bot:
        await telegram_bot._poll_task(bot, 1, task.id)
    expected = (output or "Task completed.") if status == "succeeded" else f"Task failed: {output}"
    assert "".join(messages) == expected
    assert all(0 < len(message) <= 4096 for message in messages)


@pytest.mark.parametrize("count, fill, paused", [
    (0, "x", False), (1, "x", True), (70, "x", False), (70, "界", False),
], ids=["empty-list", "short-paused", "long-ascii-list", "long-unicode-list"])
async def test_schedule_list_delivers_every_entry(
    db_session: AsyncSession, monkeypatch, bot_api, count: int, fill: str, paused: bool
):
    from app.db.session import get_db
    from app.routers.schedule import router

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: db_session
    user = "1001"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://owned"
    ) as client:
        for index in range(count):
            response = await client.post("/api/v1/schedules", json={
                "user_id": user, "provider": "claude", "box_id": "owned-box",
                "prompt_text": (f"report-{index:03} " + fill * 60)[:60],
                "cron_expression": "0 8 * * *", "timezone": "UTC",
            })
            assert response.status_code == 200
            if paused:
                response = await client.post(
                    f"/api/v1/schedules/{response.json()['id']}/pause",
                    params={"user_id": user},
                )
                assert response.status_code == 200
        response = await client.post("/api/v1/schedules", json={
            "user_id": "1002", "provider": "claude", "box_id": "owned-box",
            "prompt_text": "other-user-owned-marker",
            "cron_expression": "0 8 * * *", "timezone": "UTC",
        })
        assert response.status_code == 200
        response = await client.get("/api/v1/schedules", params={"user_id": user})
        assert response.status_code == 200
        rows = response.json()
        assert len(rows) == count

    @asynccontextmanager
    async def session():
        yield db_session

    monkeypatch.setattr(telegram_bot, "AsyncSessionLocal", session)
    base_url, messages = bot_api
    async with Bot(token="123:owned-fixture", base_url=base_url) as bot:
        # Check the real SDK HTTP/protocol fixture before product execution.
        with pytest.raises(BadRequest) as rejected:
            await bot.send_message(chat_id=1001, text="x" * 4097)
        assert "message is too long" in str(rejected.value).lower()
        assert messages == []
        update = Update.de_json({
            "update_id": 1, "message": {
                "message_id": 1, "date": 1700000000,
                "chat": {"id": 1001, "type": "private"},
                "from": {"id": 1001, "is_bot": False, "first_name": "Owned"},
                "text": "/schedules",
                "entities": [{"type": "bot_command", "offset": 0, "length": 10}],
            },
        }, bot)
        try:
            await telegram_bot.schedules_handler(update, SimpleNamespace(bot=bot))
        except BadRequest as exc:
            pytest.fail(f"schedule command lost its listing at the SDK HTTP boundary: {exc}")

    delivered = "".join(messages)
    assert all(0 < len(message) <= 4096 for message in messages)
    assert "other-user-owned-marker" not in delivered
    if not rows:
        assert messages == ["You have no schedules. Create one with /schedule."]
        return
    assert [int(item) for item in re.findall(r"^#(\d+) ", delivered, re.MULTILINE)] == [
        row["id"] for row in rows
    ]
    for row in rows:
        assert f"[{row['cron_expression']}] {row['prompt_text']}" in delivered
        status = "paused" if not row["is_active"] else (
            "next run " + datetime.fromisoformat(row["next_run_at"]).strftime("%Y-%m-%d %H:%M UTC")
        )
        assert f"— {status}" in delivered
    if count == 70:
        assert len(delivered) > 4096
        assert len(messages) > 1
    else:
        assert len(messages) == 1
