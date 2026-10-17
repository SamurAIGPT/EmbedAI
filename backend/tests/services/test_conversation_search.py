"""Conversation search (docs/EVERYDAY.md): titles and stored messages, on
SQLite and PostgreSQL alike, with snippets, highlights and filters."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import update

from app.core.config import settings
from app.db.models import Event, Thread
from app.routers.search import router as search_router
from app.routers.threads import get_runtime
from app.services import conversation_search
from app.services import thread_store as store
from app.services.conversation_search import make_snippet, query_terms
from tests.services.thread_harness import cleanup, owner, rt, sessions  # noqa: F401


async def thread_with(sessions, owner, tmp_path, *, title=None, user="", answer="", agent_id=None):
    async with sessions() as db:
        thread = await store.create_thread(
            db, owner_id=owner, provider="claude", title=title, workspace_path=str(tmp_path), agent_id=agent_id
        )
    async with sessions() as db:
        turn = await store.start_turn(db, thread.id, user or "hello")
    if answer:
        async with sessions() as db:
            await store.append_turn_event(db, turn.id, turn.owner_token, "assistant.message", {"text": answer})
    async with sessions() as db:
        await store.finish_turn(db, turn.id, turn.owner_token, status="succeeded", event_type="turn.completed", event_data={})
    return thread


async def find(sessions, owner, query, **kwargs):
    async with sessions() as db:
        return await conversation_search.search(db, owner, query, **kwargs)


def test_terms_are_distinct_lowercase_and_bounded():
    assert query_terms("Blue  blue GREEN") == ["green", "blue"]
    assert len(query_terms("a b c d e f g h")) == conversation_search.MAX_TERMS


def test_snippet_centres_on_the_match_and_marks_every_word():
    text = ("lorem ipsum " * 30) + "the Quarterly report is ready " + ("dolor sit " * 30)
    snippet, spans = make_snippet(text, ["quarterly", "report"])
    assert snippet.startswith("…") and snippet.endswith("…")
    assert len(snippet) <= 170
    marked = [snippet[s : s + n].lower() for s, n in spans]
    assert marked == ["quarterly", "report"]


async def test_finds_user_and_assistant_messages_and_titles(sessions, owner, tmp_path):
    a = await thread_with(sessions, owner, tmp_path, title="Trip planning", user="Book a hotel in Lisbon")
    b = await thread_with(sessions, owner, tmp_path, user="what is up", answer="The LISBON forecast is sunny")
    hits = await find(sessions, owner, "lisbon")
    assert {(h.thread_id, h.kind) for h in hits} == {(a.id, "user"), (b.id, "assistant")}
    assert all(h.snippet[s : s + n].lower() == "lisbon" for h in hits for s, n in h.highlights)
    title_hits = await find(sessions, owner, "trip")
    assert [(h.thread_id, h.kind) for h in title_hits] == [(a.id, "title")]
    assert hits[0].at >= hits[-1].at  # newest first


async def test_every_word_must_appear_in_one_message(sessions, owner, tmp_path):
    t = await thread_with(sessions, owner, tmp_path, user="red apples", answer="green pears")
    assert await find(sessions, owner, "red pears") == []
    hits = await find(sessions, owner, "APPLES red")
    assert [(h.thread_id, h.kind) for h in hits] == [(t.id, "user")]


async def test_wildcards_in_the_query_are_literal(sessions, owner, tmp_path):
    await thread_with(sessions, owner, tmp_path, user="growth was 100% this year")
    await thread_with(sessions, owner, tmp_path, user="growth was 1000 this year")
    hits = await find(sessions, owner, "100%")
    assert len(hits) == 1 and "100%" in hits[0].snippet
    assert await find(sessions, owner, "a_b") == []


async def test_json_keys_are_not_searched(sessions, owner, tmp_path):
    await thread_with(sessions, owner, tmp_path, user="hello", answer="world")
    assert await find(sessions, owner, "prompt_text") == []
    assert await find(sessions, owner, "incomplete") == []


async def test_short_query_finds_nothing(sessions, owner, tmp_path):
    await thread_with(sessions, owner, tmp_path, user="a b c")
    assert await find(sessions, owner, "a") == []
    assert await find(sessions, owner, "   ") == []


async def test_filters_archived_agent_and_dates(sessions, owner, tmp_path):
    kept = await thread_with(sessions, owner, tmp_path, user="needle one", agent_id=7)
    archived = await thread_with(sessions, owner, tmp_path, user="needle two")
    async with sessions() as db:
        await store.set_archived(db, archived.id, True)
    assert {h.thread_id for h in await find(sessions, owner, "needle")} == {kept.id}
    both = await find(sessions, owner, "needle", include_archived=True)
    assert {h.thread_id for h in both} == {kept.id, archived.id}
    assert next(h for h in both if h.thread_id == archived.id).archived is True
    assert {h.thread_id for h in await find(sessions, owner, "needle", agent_id=7)} == {kept.id}
    assert await find(sessions, owner, "needle", agent_id=8) == []

    old = datetime(2025, 1, 15, 12, tzinfo=UTC)
    async with sessions() as db:
        await db.execute(update(Event).where(Event.thread_id == kept.id).values(created_at=old))
        await db.execute(update(Thread).where(Thread.id == kept.id).values(updated_at=old))
        await db.commit()
    since = await find(sessions, owner, "needle", since=old + timedelta(days=1), include_archived=True)
    assert {h.thread_id for h in since} == {archived.id}
    until = await find(sessions, owner, "needle", until=old + timedelta(days=1), include_archived=True)
    assert {h.thread_id for h in until} == {kept.id}


async def test_another_owner_never_sees_my_conversations(sessions, owner, tmp_path):
    await thread_with(sessions, owner, tmp_path, user="private words here")
    assert await find(sessions, owner + "-other", "private words") == []


# --- the route -------------------------------------------------------------------------


@pytest.fixture
def api(rt, monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_EXECUTION_ENABLED", True)
    monkeypatch.setattr(settings, "LOCAL_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]")
    app = FastAPI()
    app.include_router(search_router, prefix="/api/v1")
    app.dependency_overrides[get_runtime] = lambda: rt
    transport = httpx.ASGITransport(app, client=("127.0.0.1", 50000))
    return httpx.AsyncClient(transport=transport, base_url="http://localhost")


async def test_route_returns_hits_with_highlights(api, rt, sessions, tmp_path):
    thread = await thread_with(sessions, rt.owner_id, tmp_path, user="where is the invoice", answer="Invoice 42 is paid")
    async with api:
        response = await api.get("/api/v1/search", params={"q": "invoice"})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["query"] == "invoice"
        assert {h["kind"] for h in body["hits"]} == {"user", "assistant"}
        hit = body["hits"][0]
        assert hit["thread_id"] == thread.id and hit["seq"] is not None and hit["highlights"]

        bad = await api.get("/api/v1/search", params={"q": "invoice", "since": "yesterday"})
        assert bad.status_code == 422 and bad.json()["detail"]["error_code"] == "bad_date"
        day = datetime.now(UTC).date().isoformat()
        today = await api.get("/api/v1/search", params={"q": "invoice", "since": day, "until": day})
        assert len(today.json()["hits"]) == 2


async def test_route_refuses_a_remote_client(rt, monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_EXECUTION_ENABLED", True)
    app = FastAPI()
    app.include_router(search_router, prefix="/api/v1")
    app.dependency_overrides[get_runtime] = lambda: rt
    transport = httpx.ASGITransport(app, client=("203.0.113.9", 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as remote:
        assert (await remote.get("/api/v1/search", params={"q": "x"})).status_code == 403
