"""Usage and cost (docs/EVERYDAY.md): totals from what engines reported,
never estimated."""

import csv
import io
from datetime import UTC, datetime

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import update

from app.core.config import settings
from app.db.models import Turn
from app.routers.threads import get_runtime
from app.routers.usage import router as usage_router
from app.services import thread_store as store
from app.services import usage_report
from tests.services.thread_harness import cleanup, cli, owner, rt, sessions  # noqa: F401


@pytest.fixture(autouse=True)
def local_enabled(monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_EXECUTION_ENABLED", True)
    monkeypatch.setattr(settings, "LOCAL_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]")


@pytest.fixture
async def api(rt):
    app = FastAPI()
    app.include_router(usage_router, prefix="/api/v1")
    app.dependency_overrides[get_runtime] = lambda: rt
    transport = httpx.ASGITransport(app, client=("127.0.0.1", 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
        yield client


async def turn(sessions, thread_id, usage, *, at=None, model=None, status="succeeded"):
    async with sessions() as db:
        started = await store.start_turn(db, thread_id, "hi")
    async with sessions() as db:
        await store.finish_turn(db, started.id, started.owner_token, status=status, usage=usage, model=model)
    if at is not None:
        async with sessions() as db:
            await db.execute(update(Turn).where(Turn.id == started.id).values(started_at=at))
            await db.commit()


async def thread(sessions, owner, tmp_path, title, provider="claude"):
    async with sessions() as db:
        return await store.create_thread(db, owner_id=owner, provider=provider, title=title, workspace_path=str(tmp_path))


def test_only_well_formed_numbers_count():
    assert usage_report.reported({"input_tokens": 5, "output_tokens": True, "cost_usd": "1"}) == {
        "input_tokens": 5,
        "output_tokens": None,
        "cache_read_input_tokens": None,
        "cache_creation_input_tokens": None,
        "cost_usd": None,
    }
    assert usage_report.reported(None)["cost_usd"] is None
    assert usage_report.reported({"cost_usd": float("nan")})["cost_usd"] is None


def test_month_ranges():
    assert usage_report.month_range("2026-12")[1:] == (
        datetime(2026, 12, 1, tzinfo=UTC),
        datetime(2027, 1, 1, tzinfo=UTC),
    )
    with pytest.raises(ValueError):
        usage_report.month_range("2026-13")


async def test_month_totals_never_invent_numbers(api, sessions, owner, tmp_path):
    october = datetime(2026, 10, 5, 9, tzinfo=UTC)
    a = await thread(sessions, owner, tmp_path, "Reported")
    b = await thread(sessions, owner, tmp_path, "Silent", provider="codex")
    await turn(sessions, a.id, {"input_tokens": 100, "output_tokens": 20, "cache_read_input_tokens": 7, "cost_usd": 0.01}, at=october, model="haiku")
    await turn(sessions, a.id, {"input_tokens": 50, "output_tokens": 5, "cost_usd": 0.002}, at=october, model="haiku")
    await turn(sessions, b.id, None, at=october)
    await turn(sessions, b.id, {"input_tokens": 9, "output_tokens": 1}, at=october)
    await turn(sessions, a.id, {"input_tokens": 1, "cost_usd": 5.0}, at=datetime(2026, 9, 30, 23, tzinfo=UTC))

    body = (await api.get("/api/v1/usage", params={"month": "2026-10"})).json()
    total = body["total"]
    assert body["month"] == "2026-10"
    assert (total["turns"], total["reported_turns"], total["unreported_turns"]) == (4, 3, 1)
    assert (total["input_tokens"], total["output_tokens"], total["cache_read_input_tokens"]) == (159, 26, 7)
    assert total["cost_usd"] == pytest.approx(0.012)
    by_thread = {g["label"]: g for g in body["by_thread"]}
    assert by_thread["Silent"]["cost_usd"] is None  # tokens reported, cost never: not zero
    assert by_thread["Silent"]["input_tokens"] == 9 and by_thread["Silent"]["unreported_turns"] == 1
    assert by_thread["Reported"]["thread_id"] == a.id
    assert [g["key"] for g in body["by_engine"]] == ["claude/haiku", "codex/default"]
    assert [g["key"] for g in body["by_day"]] == ["2026-10-05"]

    september = (await api.get("/api/v1/usage", params={"month": "2026-09"})).json()
    assert september["total"]["cost_usd"] == 5.0
    empty = (await api.get("/api/v1/usage", params={"month": "2020-01"})).json()
    assert empty["total"]["turns"] == 0 and empty["total"]["cost_usd"] is None and empty["by_thread"] == []


async def test_running_turns_are_not_counted(api, sessions, owner, tmp_path):
    a = await thread(sessions, owner, tmp_path, "Busy")
    async with sessions() as db:
        await store.start_turn(db, a.id, "still going")
    body = (await api.get("/api/v1/usage")).json()
    assert body["total"]["turns"] == 0


async def test_csv_has_one_row_per_turn_and_neutralises_formulas(api, sessions, owner, tmp_path):
    at = datetime(2026, 8, 2, tzinfo=UTC)
    a = await thread(sessions, owner, tmp_path, "=HYPERLINK(\"http://x\")")
    await turn(sessions, a.id, {"input_tokens": 3, "output_tokens": 4, "cost_usd": 0.5}, at=at, model="sonnet")
    await turn(sessions, a.id, None, at=at, status="failed")
    response = await api.get("/api/v1/usage.csv", params={"month": "2026-08"})
    assert response.status_code == 200
    assert response.headers["content-disposition"] == 'attachment; filename="open-dots-usage-2026-08.csv"'
    rows = list(csv.reader(io.StringIO(response.text)))
    assert rows[0][:6] == ["started_at", "thread_id", "conversation", "engine", "model", "status"]
    assert len(rows) == 3
    assert rows[1][2].startswith("'=") and rows[1][-1] == "0.5" and rows[1][4] == "sonnet"
    assert rows[2][5] == "failed" and rows[2][-1] == ""


async def test_bad_month_is_a_422(api):
    assert (await api.get("/api/v1/usage", params={"month": "October"})).status_code == 422
    assert (await api.get("/api/v1/usage", params={"month": "2026-13"})).json()["detail"]["error_code"] == "bad_month"
    assert (await api.get("/api/v1/usage.csv", params={"month": "2026-00"})).status_code == 422


async def test_another_owner_sees_none_of_it(sessions, owner, tmp_path):
    a = await thread(sessions, owner, tmp_path, "Mine")
    await turn(sessions, a.id, {"cost_usd": 1.0})
    async with sessions() as db:
        body = await usage_report.month_summary(db, owner + "-other", None)
    assert body["total"]["turns"] == 0


async def test_a_finished_turn_records_what_claude_reported(rt, cli, sessions, owner, tmp_path):
    from tests.services.thread_harness import finished, new_thread

    thread = await new_thread(sessions, owner, workspace_path=str(tmp_path))
    await rt.send_turn(thread.id, "hello")
    await finished(sessions, thread.id)
    async with sessions() as db:
        events = await store.list_all_events(db, thread.id)
        turns = await store.list_turns(db, thread.id)
    completed = next(e for e in events if e.type == "turn.completed")
    reported = {"input_tokens": 3, "output_tokens": 2, "cost_usd": 0.001}
    assert completed.data["usage"] == reported and turns[0].usage == reported
