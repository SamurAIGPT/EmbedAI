"""The Needs you card: what is listed and when it leaves, when the card is written,
edited and notified, and the presence rules."""
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app.db.models import (
    Agent, Event, NeedsYouState, Notification, Routine, RoutineRun, Team, Turn,
)
from app.services import agent_store
from app.services import thread_store as store
from app.team import needs_you, service
from tests.services.thread_harness import cleanup, owner, rt, sessions  # noqa: F401

T0 = datetime(2026, 10, 24, 9, 0, tzinfo=UTC)
SIGNED_OUT = {"id": "claude", "label": "Claude", "installed": True, "signed_in": False, "limit": None}
LIMITED = {"id": "codex", "label": "Codex", "installed": True, "signed_in": True,
           "limit": {"kind": "usage_limit", "resets_at": "2026-10-24T12:00:00+00:00", "message": "Weekly limit"}}
BROKE = {"id": "api", "label": "API", "installed": True, "signed_in": True,
         "limit": {"kind": "credits", "resets_at": None, "message": "No credit"}}


class Clock:
    def __init__(self, now=T0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, **delta):
        self.now += timedelta(**delta)


@pytest.fixture(autouse=True)
async def tidy(sessions, owner):
    yield
    async with sessions() as db:
        ids = select(Routine.id).where(Routine.owner_id == owner)
        await db.execute(delete(RoutineRun).where(RoutineRun.routine_id.in_(ids)))
        await db.execute(delete(Routine).where(Routine.owner_id == owner))
        await db.execute(delete(Notification).where(Notification.owner_id == owner))
        await db.execute(delete(NeedsYouState).where(NeedsYouState.owner_id == owner))
        await db.execute(delete(Team).where(Team.owner_id == owner))
        await db.execute(delete(Agent).where(Agent.owner_id == owner))
        await db.commit()


@pytest.fixture
async def lead(sessions, owner):
    async with sessions() as db:
        agent = await agent_store.create_agent(db, owner_id=owner, name="Lead")
        await service.set_lead(db, owner, agent.id)
    return agent


def make(rt, clock, engines=(), issues=()):
    async def engines_():
        return list(engines)

    async def issues_():
        return list(issues)
    return needs_you.NeedsYou(rt, clock, engines=engines_, issues=issues_)


async def pending_approval(sessions, rt, agent, summary="Run the tests"):
    thread = await rt.create_thread(agent_id=agent.id, title="Work")
    async with sessions() as db:
        turn = await store.start_turn(db, thread.id, "go", owner_instance="t")
        approval = await store.create_approval(
            db, thread_id=thread.id, turn_id=turn.id, request_id=uuid4().hex, tool="Bash",
            input={}, summary=summary, kind="command", owner_token=turn.owner_token)
    return thread, turn, approval


async def routine_with_runs(sessions, owner, agent, *states, name="Digest"):
    async with sessions() as db:
        routine = Routine(owner_id=owner, agent_id=agent.id, name=name, prompt="x",
                          schedule_kind="interval", schedule_spec={}, next_run_at=T0)
        db.add(routine)
        await db.flush()
        for i, state in enumerate(states):
            db.add(RoutineRun(routine_id=routine.id, trigger="schedule", state=state,
                              error="boom" if state == "failed" else None,
                              scheduled_for=T0 + timedelta(minutes=i),
                              finished_at=T0 + timedelta(minutes=i), created_at=T0 + timedelta(minutes=i)))
        await db.commit()
        return routine


async def events(sessions, thread_id):
    async with sessions() as db:
        return [e for e in (await store.list_all_events(db, thread_id)) if e.type == needs_you.CARD_EVENT]


# --- zones ---------------------------------------------------------------------------


def test_zone_and_day():
    assert needs_you.valid_zone("Europe/Paris") == "Europe/Paris"
    for bad in ("", "Nope/Nowhere", "/etc/passwd", None, 5, "x" * 80):
        with pytest.raises(ValueError):
            needs_you.valid_zone(bad)
    late = datetime(2026, 10, 24, 23, 30, tzinfo=UTC)
    assert needs_you.local_day(late, needs_you.zone_of("UTC")) == "2026-10-24"
    assert needs_you.local_day(late, needs_you.zone_of("Asia/Kolkata")) == "2026-10-25"
    assert needs_you.zone_of("garbage").key == "UTC"


# --- what is listed ------------------------------------------------------------------


async def test_approval_listed_while_pending_only(sessions, rt, owner, lead):
    thread, turn, approval = await pending_approval(sessions, rt, lead)
    async with sessions() as db:
        items = await needs_you.collect(db, owner, now=T0, engines=[], issues=[])
    assert [(i.kind, i.title, i.thread_id) for i in items] == [("approval", "Run the tests", thread.id)]
    async with sessions() as db:
        await store.decide_approval(db, approval.id, "allowed")
        items = await needs_you.collect(db, owner, now=T0, engines=[], issues=[])
    assert items == []


async def test_routine_leaves_when_a_later_run_completes(sessions, rt, owner, lead):
    await routine_with_runs(sessions, owner, lead, "completed", "failed")
    async with sessions() as db:
        items = await needs_you.collect(db, owner, now=T0, engines=[], issues=[])
    assert [i.title for i in items] == ["Digest failed"] and items[0].detail == "boom"
    await routine_with_runs(sessions, owner, lead, "failed", "completed", name="Fixed")
    await routine_with_runs(sessions, owner, lead, "missed", name="Late")
    async with sessions() as db:
        titles = {i.title for i in await needs_you.collect(db, owner, now=T0, engines=[], issues=[])}
    assert titles == {"Digest failed", "Late missed its time"}


async def test_engines_and_repeats_ordered_most_blocking_first(sessions, rt, owner, lead):
    await pending_approval(sessions, rt, lead)
    await routine_with_runs(sessions, owner, lead, "failed")
    issue = {"sig": "s1", "kind": "retry_loop", "title": "Bash failed the same way", "severity": "high",
             "count": 4, "last_at": T0, "thread_id": None}
    quiet = {"sig": "s2", "kind": "tool_error", "title": "once", "severity": "low", "count": 1, "last_at": T0}
    async with sessions() as db:
        items = await needs_you.collect(db, owner, now=T0, engines=[LIMITED, BROKE, SIGNED_OUT], issues=[issue, quiet])
    assert [i.kind for i in items] == [
        "signed_out", "out_of_credit", "usage_limit", "approval", "routine", "repeated_failure"]
    # An engine that is not installed is not "signed out".
    async with sessions() as db:
        gone = await needs_you.collect(db, owner, now=T0, engines=[{**SIGNED_OUT, "installed": False}], issues=[])
    assert all(i.kind != "signed_out" for i in gone)


async def test_card_caps_rows_and_counts_the_rest():
    items = [needs_you.Item("approval", f"a{i}", f"t{i}", None, None, T0) for i in range(9)]
    card = needs_you.build_card(items, day="2026-10-24", now=T0, zone=needs_you.zone_of("UTC"))
    assert len(card["items"]) == needs_you.CARD_ROWS and card["more"] == 3
    assert card["subtitle"] == "Saturday, 24 October" and card["card_id"] == "needs-you:2026-10-24"


# --- the card's life -----------------------------------------------------------------


async def test_never_posted_empty_and_needs_a_lead(sessions, rt, owner):
    clock = Clock()
    assert await make(rt, clock, [SIGNED_OUT]).render(create=True) is None  # no lead
    async with sessions() as db:
        agent = await agent_store.create_agent(db, owner_id=owner, name="Lead")
        await service.set_lead(db, owner, agent.id)
    assert await make(rt, clock).render(create=True) is None  # nothing to say
    async with sessions() as db:
        assert (await db.get(NeedsYouState, owner)) is None


async def test_card_written_once_edited_in_place_and_notified_once(sessions, rt, owner, lead):
    clock = Clock()
    engines = [SIGNED_OUT]
    ny = make(rt, clock, engines)
    opened = await ny.render(create=False)
    assert opened["agent_id"] == lead.id
    thread_id = opened["thread_id"]
    first = await events(sessions, thread_id)
    assert len(first) == 1 and first[0].data["revision"] == 1
    assert first[0].data["items"][0]["key"] == "signed_out:claude"
    # Nothing changed: no new event, no second notification.
    clock.advance(minutes=1)
    assert (await ny.render(create=False))["thread_id"] == thread_id
    assert len(await events(sessions, thread_id)) == 1
    # Something new: same card id, next revision, a second notification.
    engines.append(LIMITED)
    await ny.render(create=False)
    cards = await events(sessions, thread_id)
    assert [c.data["revision"] for c in cards] == [1, 2]
    assert {c.data["card_id"] for c in cards} == {"needs-you:2026-10-24"}
    async with sessions() as db:
        notes = (await db.execute(select(Notification).where(Notification.owner_id == owner))).scalars().all()
    assert len(notes) == 2 and notes[0].kind == "needs_you" and notes[0].thread_id == thread_id
    # It clears: the card is edited to the empty text rather than left stale.
    engines.clear()
    assert await ny.render(create=False) is None
    last = (await events(sessions, thread_id))[-1].data
    assert last["items"] == [] and last["revision"] == 3 and last["empty_text"] == needs_you.EMPTY_TEXT
    async with sessions() as db:
        assert len((await db.execute(select(Notification).where(Notification.owner_id == owner))).scalars().all()) == 2


async def test_next_day_is_a_new_card(sessions, rt, owner, lead):
    clock = Clock()
    ny = make(rt, clock, [SIGNED_OUT])
    thread_id = (await ny.render(create=False))["thread_id"]
    clock.advance(days=1)
    await ny.render(create=False)
    cards = await events(sessions, thread_id)
    assert [(c.data["day"], c.data["revision"]) for c in cards] == [("2026-10-24", 1), ("2026-10-25", 1)]


async def test_never_written_into_a_running_turn(sessions, rt, owner, lead):
    clock = Clock()
    thread = await rt.create_thread(agent_id=lead.id, title="Chat")
    async with sessions() as db:
        await store.start_turn(db, thread.id, "busy", owner_instance="t")
    ny = make(rt, clock, [SIGNED_OUT])
    assert await ny.render(create=True) is None
    assert await events(sessions, thread.id) == []
    async with sessions() as db:
        turn = (await db.execute(select(Turn).where(Turn.thread_id == thread.id))).scalar_one()
        turn.status = "succeeded"
        await db.commit()
    assert (await ny.render(create=True))["thread_id"] == thread.id  # the lead's existing chat is reused
    assert len(await events(sessions, thread.id)) == 1


# --- presence ------------------------------------------------------------------------


async def test_presence_stores_zone_and_opens_only_on_return_or_new_day(sessions, rt, owner, lead):
    clock = Clock()
    ny = make(rt, clock, [SIGNED_OUT])
    with pytest.raises(ValueError):
        await ny.presence("Not/AZone")
    first = await ny.presence("Asia/Kolkata")
    assert first["returned"] and first["first_today"] and first["open"]["agent_id"] == lead.id
    async with sessions() as db:
        state = await db.get(NeedsYouState, owner)
        assert state.time_zone == "Asia/Kolkata" and state.card_day == "2026-10-24"
    # A ping a few minutes later records the time and opens nothing.
    clock.advance(minutes=5)
    quick = await ny.presence("Asia/Kolkata")
    assert not quick["returned"] and not quick["first_today"] and quick["open"] is None
    # Back after three hours: edits the same card in place.
    clock.advance(hours=3)
    back = await ny.presence("Asia/Kolkata")
    assert back["returned"] and back["open"]["thread_id"] == first["open"]["thread_id"]
    assert len(await events(sessions, first["open"]["thread_id"])) == 1
    # A clock that ran backwards counts as neither a return nor a new day.
    clock.advance(hours=-1)
    skew = await ny.presence("Asia/Kolkata")
    assert not skew["returned"] and not skew["first_today"]


async def test_tick_waits_until_the_person_has_been_here(sessions, rt, owner, lead):
    clock = Clock()
    ny = make(rt, clock, [SIGNED_OUT])
    await ny.tick()
    async with sessions() as db:
        assert await db.get(NeedsYouState, owner) is None
    async with sessions() as db:
        db.add(NeedsYouState(owner_id=owner, time_zone="UTC", last_active_at=T0, notified=[]))
        await db.commit()
    await ny.tick()
    async with sessions() as db:
        assert (await db.get(NeedsYouState, owner)).card_thread_id is not None


# --- who started a turn --------------------------------------------------------------


async def test_turn_records_who_started_it(sessions, rt, owner, lead):
    thread = await rt.create_thread(agent_id=lead.id)
    async with sessions() as db:
        turn = await store.start_turn(db, thread.id, "hi", owner_instance="t", origin="routine")
        assert (await db.get(Turn, turn.id)).origin == "routine"


# --- routes --------------------------------------------------------------------------


async def test_routes_read_and_presence(sessions, rt, owner, lead):
    import httpx
    from fastapi import FastAPI

    from app.routers import needs_you as route

    app = FastAPI()
    app.include_router(route.router)
    app.dependency_overrides[route.get_runtime] = lambda: rt
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
        read = await client.get("/needs-you")
        assert read.status_code == 200 and read.json()["items"] == [] and read.json()["title"] == "Needs you"
        bad = await client.post("/needs-you/presence", json={"time_zone": "Mars/Base"})
        assert bad.status_code == 422
        ok = await client.post("/needs-you/presence", json={"time_zone": "Europe/Paris"})
        assert ok.status_code == 200 and ok.json()["returned"] is True and ok.json()["open"] is None  # nothing to say
    async with sessions() as db:
        assert (await db.get(NeedsYouState, owner)).time_zone == "Europe/Paris"
