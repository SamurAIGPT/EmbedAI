"""Shared setup for the thread tests: a real child process plays the Claude CLI
(tests/services/fake_claude_cli.py), a runtime wired to committed database
sessions, and small helpers. Import the fixtures you need into a test module:

    from tests.services.thread_harness import *  # noqa: F401,F403
"""

import asyncio
import json
import sys
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import settings
from app.db.engine import create_app_async_engine
from app.db.models import Approval, Event, Thread, Turn
from app.services import local_claude_runner, thread_runtime
from app.services import thread_store as store
from app.services.claude_adapter import ClaudeAdapter
from app.services.provider_adapter import ProviderStatus
from app.services.thread_hub import ThreadHub
from app.services.thread_runtime import (
    ProviderUnavailable,
    SessionBroken,
    ThreadRuntime,
    UnknownProvider,
)

FAKE_CLI = Path(__file__).with_name("fake_claude_cli.py")
TERMINAL = {"turn.completed", "turn.failed", "turn.cancelled", "turn.interrupted"}
TOOL_INPUT = {"file_path": "notes.txt", "content": "hello"}


@pytest.fixture
async def sessions():
    engine = create_app_async_engine()
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.fixture
def owner():
    return "od_rt_" + uuid4().hex


@pytest.fixture(autouse=True)
async def cleanup(sessions, owner):
    yield
    async with sessions() as db:
        ids = select(Thread.id).where(Thread.owner_id == owner)
        await db.execute(delete(Approval).where(Approval.thread_id.in_(ids)))
        await db.execute(delete(Event).where(Event.thread_id.in_(ids)))
        await db.execute(delete(Turn).where(Turn.thread_id.in_(ids)))
        await db.execute(delete(Thread).where(Thread.owner_id == owner))
        await db.commit()


class Cli:
    """What the test sees of the fake CLI: set its script, read what it logged."""

    def __init__(self, plan: Path) -> None:
        self.plan = plan
        self.script()

    def script(self, *steps: dict) -> None:
        self.plan.write_text(json.dumps({"steps": list(steps) or [{"type": "chat"}]}))

    def log(self, event: str | None = None) -> list[dict]:
        path = self.plan.parent / "log.jsonl"
        if not path.exists():
            return []
        entries = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        return [e for e in entries if event is None or e["event"] == event]

    def launches(self) -> list[tuple[str, str]]:
        return [(e["mode"], e["sid"]) for e in self.log("start")]


@pytest.fixture
def cli(monkeypatch, tmp_path):
    fake = Cli(tmp_path / "plan.json")
    monkeypatch.setattr(local_claude_runner, "resolve_claude_executable", lambda: tmp_path / "claude")
    monkeypatch.setattr(local_claude_runner, "workspace_dir", lambda: tmp_path)

    def fake_build_args(executable, session_id, tools=False, resume=False, approval_mode=None, **_extra):
        args = [
            sys.executable, str(FAKE_CLI), str(fake.plan),
            "resume" if resume else "new", session_id, "1" if tools else "0",
        ]
        return args + [approval_mode] if approval_mode else args

    monkeypatch.setattr(local_claude_runner, "build_args", fake_build_args)
    return fake


class ReadyClaude(ClaudeAdapter):
    async def status(self) -> ProviderStatus:
        return ProviderStatus(ready=True, version="fake")


@pytest.fixture
async def rt(sessions, owner, monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_TOOLS_ENABLED", False)
    monkeypatch.setattr(settings, "THREAD_HEARTBEAT_SECONDS", 0.2)
    monkeypatch.setattr(thread_runtime, "APPROVAL_POLL_SECONDS", 0.1)
    runtime = ThreadRuntime(
        hub=ThreadHub(),
        adapters={"claude": ReadyClaude()},
        status_ttl=0,
        _session_factory=sessions,
        owner_id=owner,
    )
    yield runtime
    await runtime.shutdown()  # never leave a fake CLI running


@pytest.fixture
def tools_on(monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_TOOLS_ENABLED", True)


async def wait_until(check, timeout=20.0):
    """Poll `check()` (sync or async) until it returns something truthy."""
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        value = check()
        if asyncio.iscoroutine(value):
            value = await value
        if value:
            return value
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("timed out waiting")
        await asyncio.sleep(0.05)


async def new_thread(sessions, owner, **kwargs):
    kwargs.setdefault("provider", "claude")
    kwargs.setdefault("provider_session_id", uuid4().hex)
    async with sessions() as db:
        return await store.create_thread(db, owner_id=owner, **kwargs)


async def events_of(sessions, thread_id):
    async with sessions() as db:
        return await store.list_all_events(db, thread_id)


async def types_of(sessions, thread_id):
    return [e.type for e in await events_of(sessions, thread_id)]


async def finished(sessions, thread_id, count=1):
    """Wait until `count` turns of the thread have a terminal event."""

    async def look():
        done = [t for t in await types_of(sessions, thread_id) if t in TERMINAL]
        return len(done) >= count

    await wait_until(look)


async def reload(sessions, model, key):
    async with sessions() as db:
        return await db.get(model, key)


async def pending_approval(sessions, thread_id):
    async def look():
        async with sessions() as db:
            items = await store.list_approvals(db, thread_id, status="pending")
        return items[0] if items else None

    return await wait_until(look)


async def interrupt_like_another_process(sessions, turn_id):
    """What another process's lease sweep does to a turn whose heartbeat is old."""
    async with sessions() as db:
        return await store.interrupt_turn(db, turn_id, stale_before=store.utcnow() + timedelta(minutes=1))
