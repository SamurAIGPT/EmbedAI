"""Exercise planner persistence and HTTP contracts against isolated SQLite files."""
import asyncio
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.base import Base
from app.db.engine import create_app_async_engine
from app.db.models import Agent, Routine, RoutineRun, Team, Thread, WorkBoard, WorkColumn, WorkItem
from app.routers.threads import get_runtime
from app.routers.work_planner import router
import app.routers.work_planner as planner_routes
from app.services import work_planner as service
from app.services.local_access_policy import require_local_access


@pytest.fixture
async def sessions(tmp_path):
    engine = create_app_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'planner.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.fixture
async def client(sessions):
    app = FastAPI()
    app.include_router(router)
    runtime = SimpleNamespace(session=sessions, owner_id="alice")
    app.dependency_overrides[get_runtime] = lambda: runtime
    app.dependency_overrides[require_local_access] = lambda: None
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://planner.test") as client:
        yield client


async def create(client, **changes):
    board = (await client.get("/planner/board")).json()
    data = dict(board_id=board["id"], column_id=board["columns"][0]["id"], title="Prepare briefing")
    response = await client.post("/planner/items", json={**data, **changes})
    assert response.status_code == 201, response.text
    return response.json()


async def test_default_board_initialization_is_durable_and_owner_scoped(client, sessions):
    first = (await client.get("/planner/board")).json()
    second = (await client.get("/planner/board")).json()
    assert first == second
    assert [column["key"] for column in first["columns"]] == [key for key, _ in service.COLUMNS]
    async with sessions() as db:
        other = await service.default_board(db, "bob")
        assert other.id != first["id"]
    response = await client.get("/planner/items", params={"board_id": other.id})
    assert response.status_code == 404


async def test_simultaneous_tabs_initialize_one_board(sessions):
    async def initialize():
        async with sessions() as db:
            return (await service.default_board(db, "alice")).id
    assert len(set(await asyncio.gather(initialize(), initialize(), initialize()))) == 1
    async with sessions() as db:
        assert len((await db.scalars(select(WorkBoard))).all()) == 1
        assert len((await db.scalars(select(WorkColumn))).all()) == 6


async def test_create_edit_reload_and_archive_restore(client, sessions):
    item = await create(client, title="  Prepare briefing  ", description="Sources and decisions", priority="high", due_date="2026-10-25")
    assert item["title"] == "Prepare briefing"
    assert item["due_at"] is None
    response = await client.patch(f"/planner/items/{item['id']}", json={"revision": 1, "title": "Updated briefing", "archived": True})
    assert response.status_code == 200, response.text
    assert response.json()["revision"] == 2
    assert (await client.get("/planner/items", params={"board_id": item["board_id"]})).json() == []
    archived = (await client.get("/planner/items", params={"board_id": item["board_id"], "archived": True})).json()
    assert [record["title"] for record in archived] == ["Updated briefing"]
    response = await client.patch(f"/planner/items/{item['id']}", json={"revision": 2, "archived": False})
    assert response.status_code == 200
    async with sessions() as db:
        persisted = await db.get(WorkItem, item["id"])
        assert persisted.title == "Updated briefing"
        assert persisted.due_date == date(2026, 10, 25)
        assert persisted.archived is False
        assert persisted.revision == 3


async def test_stale_edit_cannot_overwrite_saved_changes(client):
    item = await create(client)
    path = f"/planner/items/{item['id']}"
    assert (await client.patch(path, json={"revision": 1, "description": "Saved in window A"})).status_code == 200
    response = await client.patch(path, json={"revision": 1, "description": "Stale window B"})
    assert response.status_code == 409
    records = (await client.get("/planner/items", params={"board_id": item["board_id"]})).json()
    assert records[0]["description"] == "Saved in window A"


async def test_archived_card_can_be_reloaded_after_conflict(client, sessions):
    item = await create(client)
    path = f"/planner/items/{item['id']}"
    archived = await client.patch(path, json={"revision": 1, "archived": True})
    assert archived.status_code == 200
    stale = await client.patch(path, json={"revision": 1, "description": "Stale edit"})
    assert stale.status_code == 409
    assert (await client.get("/planner/items", params={"board_id": item["board_id"]})).json() == []
    latest = await client.get(path)
    assert latest.status_code == 200
    assert latest.json()["archived"] is True
    assert latest.json()["revision"] == 2
    async with sessions() as db:
        foreign_board = await service.default_board(db, "bob")
        foreign_column = await db.scalar(select(WorkColumn).where(WorkColumn.board_id == foreign_board.id))
        foreign_item = await service.create_item(db, "bob", dict(board_id=foreign_board.id, column_id=foreign_column.id, title="Private task"))
    assert (await client.get(f"/planner/items/{foreign_item.id}")).status_code == 404
    assert (await client.get("/planner/items/999999999")).status_code == 404


async def test_simultaneous_edits_have_one_winner(client):
    item = await create(client)
    path = f"/planner/items/{item['id']}"
    responses = await asyncio.gather(*[client.patch(path, json={"revision": 1, "title": title}) for title in ("Window A", "Window B")])
    assert sorted(response.status_code for response in responses) == [200, 409]


async def test_move_done_and_reopen_maintain_completion_and_order(client):
    item = await create(client)
    board = (await client.get("/planner/board")).json()
    done_id = board["columns"][-1]["id"]
    other = await create(client, title="Already done", column_id=done_id)
    assert other["completed_at"] is not None
    response = await client.patch(f"/planner/items/{item['id']}", json={"revision": 1, "column_id": done_id})
    completed = response.json()
    assert response.status_code == 200, response.text
    assert completed["completed_at"] is not None
    assert completed["position"] > other["position"]
    response = await client.patch(f"/planner/items/{item['id']}", json={"revision": 2, "column_id": item["column_id"]})
    assert response.status_code == 200
    assert response.json()["completed_at"] is None


async def test_foreign_items_boards_columns_and_links_are_rejected(client, sessions):
    async with sessions() as db:
        board = await service.default_board(db, "bob")
        column = await db.scalar(select(WorkColumn).where(WorkColumn.board_id == board.id))
        foreign_item = await service.create_item(db, "bob", dict(board_id=board.id, column_id=column.id, title="Private"))
        agent, thread, team = Agent(owner_id="bob", name="Private agent"), Thread(owner_id="bob", provider="claude"), Team(owner_id="bob", name="Private team")
        db.add_all([agent, thread, team])
        await db.commit()
    response = await client.patch(f"/planner/items/{foreign_item.id}", json={"revision": 1, "title": "Stolen"})
    assert response.status_code == 404
    own = (await client.get("/planner/board")).json()
    data = dict(board_id=own["id"], column_id=own["columns"][0]["id"], title="Mine")
    for changes in ({"board_id": board.id}, {"column_id": column.id}, {"agent_id": agent.id}, {"thread_id": thread.id}, {"team_id": team.id}):
        response = await client.post("/planner/items", json={**data, **changes})
        assert response.status_code in (404, 422), response.text


async def test_deleted_assignments_and_threads_preserve_work_item_text(client, sessions):
    async with sessions() as db:
        agent, thread = Agent(owner_id="alice", name="Writer"), Thread(owner_id="alice", provider="claude")
        db.add_all([agent, thread])
        await db.commit()
    item = await create(client, agent_id=agent.id, thread_id=thread.id, description="Keep this after deletion")
    async with sessions() as db:
        await db.execute(delete(Agent).where(Agent.id == agent.id))
        await db.execute(delete(Thread).where(Thread.id == thread.id))
        await db.commit()
    response = await client.patch(f"/planner/items/{item['id']}", json={"revision": 1, "priority": "urgent"})
    assert response.status_code == 200, response.text
    assert response.json()["description"] == "Keep this after deletion"
    # A complete editor form may repeat an unchanged dangling link.
    response = await client.patch(f"/planner/items/{item['id']}", json={"revision": 2, "agent_id": agent.id, "thread_id": thread.id, "title": "Edited later"})
    assert response.status_code == 200, response.text
    response = await client.patch(f"/planner/items/{item['id']}", json={"revision": 3, "agent_id": None, "thread_id": None})
    assert response.status_code == 200
    assert response.json()["agent_id"] is None


@pytest.mark.parametrize("changes", [
    {"title": "   "}, {"title": "x" * 241}, {"timezone": "Invalid/Zone"},
    {"due_at": "2026-10-25T09:00:00"}, {"priority": "critical"},
    {"due_date": "2026-10-25", "due_at": "2026-10-25T09:00:00Z"},
    {"unexpected": True},
])
async def test_invalid_create_fields_fail_without_persisting(client, changes):
    board = (await client.get("/planner/board")).json()
    response = await client.post("/planner/items", json={"board_id": board["id"], "column_id": board["columns"][0]["id"], "title": "Valid title", **changes})
    assert response.status_code == 422, response.text
    assert (await client.get("/planner/items", params={"board_id": board["id"]})).json() == []


@pytest.mark.parametrize("changes", [{"title": None}, {"column_id": None}, {"archived": None}, {"title": " "}, {"timezone": "Invalid/Zone"}, {"priority": None}])
async def test_invalid_updates_preserve_revision_and_contents(client, changes):
    item = await create(client)
    response = await client.patch(f"/planner/items/{item['id']}", json={"revision": 1, **changes})
    assert response.status_code == 422, response.text
    persisted = (await client.get("/planner/items", params={"board_id": item["board_id"]})).json()[0]
    assert persisted["title"] == item["title"]
    assert persisted["revision"] == 1


async def test_timed_deadline_roundtrips_as_utc_and_never_creates_runs(client, sessions):
    item = await create(client, due_at="2030-03-10T09:30:00-04:00", timezone="America/New_York")
    assert datetime.fromisoformat(item["due_at"]) == datetime(2030, 3, 10, 13, 30, tzinfo=UTC)
    assert item["due_date"] is None
    async with sessions() as db:
        assert (await db.scalars(select(Routine))).all() == []
        assert (await db.scalars(select(RoutineRun))).all() == []


async def test_assigning_agent_and_team_together_is_rejected(client, sessions):
    async with sessions() as db:
        agent, team = Agent(owner_id="alice", name="Writer"), Team(owner_id="alice", name="My team")
        db.add_all([agent, team])
        await db.commit()
    item = await create(client, agent_id=agent.id)
    response = await client.patch(f"/planner/items/{item['id']}", json={"revision": 1, "team_id": team.id})
    assert response.status_code == 422


async def test_calendar_projects_only_owners_active_routines_and_links_receipts(client, sessions):
    start = datetime.now(UTC).replace(microsecond=0) + timedelta(days=2)
    end = start + timedelta(days=2)
    async with sessions() as db:
        def once(owner, name, at, paused=False):
            return Routine(owner_id=owner, agent_id=1, name=name, prompt="Hello", schedule_kind="once", schedule_spec={"at": at.isoformat()}, timezone="UTC", paused=paused)
        live, paused, foreign = once("alice", "Visible", start), once("alice", "Paused", start, True), once("bob", "Private", start)
        db.add_all([live, paused, foreign])
        await db.flush()
        # A receipt at a scheduled instant supersedes its future projection.
        db.add(RoutineRun(routine_id=live.id, trigger="schedule", state="completed", scheduled_for=start, thread_id=42))
        db.add(RoutineRun(routine_id=foreign.id, trigger="schedule", state="completed", scheduled_for=start, thread_id=99))
        await db.commit()
    response = await client.get("/planner/routine-calendar", params={"start": start.isoformat(), "end": end.isoformat()})
    assert response.status_code == 200, response.text
    events = response.json()["events"]
    assert len(events) == 1
    assert events[0]["title"] == "Visible"
    assert events[0]["kind"] == "run"
    assert events[0]["state"] == "completed"
    assert events[0]["thread_id"] == 42


async def test_calendar_end_is_exclusive_and_pause_does_not_hide_history(client, sessions):
    start = datetime.now(UTC).replace(microsecond=0) - timedelta(days=2)
    end = start + timedelta(days=1)
    async with sessions() as db:
        routine = Routine(owner_id="alice", agent_id=1, name="Past routine", prompt="Hello", schedule_kind="manual", schedule_spec={}, timezone="UTC", paused=True)
        db.add(routine)
        await db.flush()
        db.add_all([RoutineRun(routine_id=routine.id, trigger="manual", state="failed", created_at=start),
                    RoutineRun(routine_id=routine.id, trigger="manual", state="completed", created_at=end)])
        await db.commit()
    response = await client.get("/planner/routine-calendar", params={"start": start.isoformat(), "end": end.isoformat()})
    assert response.status_code == 200, response.text
    assert [event["state"] for event in response.json()["events"]] == ["failed"]


@pytest.mark.parametrize("start,end", [("2030-01-01T00:00:00Z", "2030-01-01T00:00:00Z"), ("2030-01-01T00:00:00Z", "2030-05-01T00:00:00Z"), ("2030-01-01", "2030-01-02")])
async def test_calendar_rejects_invalid_windows(client, start, end):
    response = await client.get("/planner/routine-calendar", params={"start": start, "end": end})
    assert response.status_code == 422


async def test_calendar_global_projection_limit_is_visible(client, sessions, monkeypatch):
    start = datetime.now(UTC) + timedelta(days=2)
    end = start + timedelta(days=2)
    monkeypatch.setattr(planner_routes, "MAX_PROJECTED_EVENTS", 3)
    async with sessions() as db:
        routine = Routine(owner_id="alice", agent_id=1, name="Frequent routine", prompt="Hello", schedule_kind="interval",
                          schedule_spec={"every_minutes": 5, "anchor": start.isoformat()}, timezone="UTC")
        db.add(routine)
        await db.commit()
    response = await client.get("/planner/routine-calendar", params={"start": start.isoformat(), "end": end.isoformat()})
    assert response.status_code == 200, response.text
    assert len(response.json()["events"]) == 3
    assert any("first 3 scheduled occurrences" in warning for warning in response.json()["warnings"])


async def test_calendar_high_frequency_routine_has_its_own_cap(client, sessions):
    start = datetime.now(UTC) + timedelta(days=2)
    end = start + timedelta(days=2)
    async with sessions() as db:
        db.add(Routine(owner_id="alice", agent_id=1, name="Frequent routine", prompt="Hello", schedule_kind="interval",
                       schedule_spec={"every_minutes": 5, "anchor": start.isoformat()}, timezone="UTC"))
        await db.commit()
    response = await client.get("/planner/routine-calendar", params={"start": start.isoformat(), "end": end.isoformat()})
    assert response.status_code == 200
    assert len(response.json()["events"]) == 500
    assert any("first 500 occurrences" in warning for warning in response.json()["warnings"])
