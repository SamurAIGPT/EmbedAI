"""The helper model (docs/MEMORY.md): another connected engine that writes a
background text only when an agent's own engine cannot, off by default, with a
per-agent opt-out, redaction, a usage record and a safe fallback."""

import json

import httpx
import pytest
from fastapi import FastAPI

from app.core.config import settings
from app.routers.agents import get_runtime
from app.routers.memory import router as memory_router
from app.services import (
    agent_memory,
    agent_store,
    engine_config,
    helper_usage,
    local_claude_runner,
    memory_journal,
    memory_settings,
    side_jobs,
    usage_report,
)
from app.services.openai_chat_adapter import OpenAIChatAdapter
from app.services.thread_hub import ThreadHub
from app.services.thread_runtime import ThreadRuntime
from tests.services.test_openai_chat_adapter import BASE, KEY, data_of, sse, delta  # noqa: F401
from tests.services.thread_harness import cleanup, events_of, finished, owner, sessions  # noqa: F401

SECRET = "sk-ant-abcdefghijklmnop1234567890"


class NoCallAdapter(OpenAIChatAdapter):
    """An engine that replays history but cannot write a quick text call (like an ACP agent)."""

    generate_text = None


class Seen:
    def __init__(self):
        self.own: list[dict] = []
        self.helper: list[dict] = []
        self.helper_answer = "A summary written by the helper."
        self.helper_fails = False


@pytest.fixture
def seen():
    return Seen()


@pytest.fixture
async def rt(sessions, owner, monkeypatch, tmp_path, seen):
    monkeypatch.setattr(settings, "LOCAL_TOOLS_ENABLED", False)
    monkeypatch.setattr(settings, "THREAD_HEARTBEAT_SECONDS", 0.2)
    monkeypatch.setattr(settings, "MEMORY_CAPTURE_QUIET_SECONDS", 0)
    monkeypatch.setattr(settings, "HELPER_ENGINE", None)
    monkeypatch.setattr(settings, "HELPER_MODEL", None)
    monkeypatch.setattr(local_claude_runner, "workspace_dir", lambda: tmp_path)
    monkeypatch.setattr(memory_journal, "state_root", lambda: tmp_path / "state")

    def own(request):  # the agent's own engine ("nocall")
        body = json.loads(request.content)
        seen.own.append(body)
        return httpx.Response(200, content=sse(delta("ok"), delta("", "stop")), headers={"content-type": "text/event-stream"})

    def helper(request):  # the helper engine ("openai_compat")
        body = json.loads(request.content)
        seen.helper.append(body)
        if seen.helper_fails:
            return httpx.Response(500, json={})
        return httpx.Response(200, json={
            "choices": [{"message": {"content": seen.helper_answer}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 5},
        })

    adapters = {
        "nocall": NoCallAdapter(transport=httpx.MockTransport(own)),
        "openai_compat": OpenAIChatAdapter(transport=httpx.MockTransport(helper)),
    }
    runtime = ThreadRuntime(hub=ThreadHub(), adapters=adapters, status_ttl=0, _session_factory=sessions, owner_id=owner)
    async with sessions() as db:
        await engine_config.save(db, owner, "nocall", {"base_url": BASE, "model": "own-model", "api_key": KEY, "context_tokens": 1024})
        await engine_config.save(db, owner, "openai_compat", {"base_url": "http://helper.test/v1", "model": "helper-model", "api_key": "helper-key"})
    yield runtime
    await runtime.shutdown()


async def make_agent(sessions, owner, provider="nocall", **kwargs):
    async with sessions() as db:
        return await agent_store.create_agent(db, owner_id=owner, name="Scout", provider=provider, **kwargs)


def helper_on(monkeypatch, engine="openai_compat", model=None):
    monkeypatch.setattr(settings, "HELPER_ENGINE", engine)
    monkeypatch.setattr(settings, "HELPER_MODEL", model)


# --- who writes a job -----------------------------------------------------------------


async def test_no_helper_is_configured_by_default(rt, sessions, owner):
    agent = await make_agent(sessions, owner)
    assert side_jobs.configured() is None
    assert await rt.capture.source(agent, "nocall", None) is None
    assert await side_jobs.helper_source(rt, agent, side_jobs.MEMORY) is None


async def test_the_helper_writes_for_an_engine_without_a_call(rt, sessions, owner, seen, monkeypatch):
    helper_on(monkeypatch, model="tiny-model")
    agent = await make_agent(sessions, owner)
    source = await rt.capture.source(agent, "nocall", None)
    assert source.via == "helper" and source.engine == "openai_compat" and source.model == "tiny-model"
    assert await rt.capture.ask(agent, "Say something.") == seen.helper_answer
    assert [r["model"] for r in seen.helper] == ["tiny-model"] and seen.own == []


async def test_an_agent_whose_engine_can_write_always_writes_it_itself(rt, sessions, owner, seen, monkeypatch):
    helper_on(monkeypatch)
    agent = await make_agent(sessions, owner, provider="openai_compat")
    source = await rt.capture.source(agent, "openai_compat", None)
    assert source.via == "engine"
    # Even when the helper is another engine, the agent's own engine took the job.
    helper_on(monkeypatch, engine="nocall")
    assert (await rt.capture.source(agent, "openai_compat", None)).via == "engine"


async def test_an_agent_can_opt_out_of_the_helper(rt, sessions, owner, seen, monkeypatch):
    helper_on(monkeypatch)
    agent = await make_agent(sessions, owner, use_helper=False)
    assert await rt.capture.source(agent, "nocall", None) is None
    assert await rt.capture.ask(agent, "x") is None and seen.helper == []


async def test_a_helper_that_cannot_write_or_is_not_connected_reads_as_no_helper(rt, sessions, owner, monkeypatch):
    agent = await make_agent(sessions, owner)
    helper_on(monkeypatch, engine="nocall")  # an engine without a quick call
    assert await rt.capture.source(agent, "nocall", None) is None
    helper_on(monkeypatch, engine="nowhere")
    assert await rt.capture.source(agent, "nocall", None) is None


async def test_the_environment_wins_over_the_saved_helper_and_can_switch_it_off(rt, sessions, owner, monkeypatch):
    memory_settings.update(helper={"engine": "openai_compat", "model": "saved-model"})
    assert side_jobs.configured() == side_jobs.Helper("openai_compat", "saved-model", "settings")
    helper_on(monkeypatch, model="env-model")
    assert side_jobs.configured() == side_jobs.Helper("openai_compat", "env-model", "environment")
    monkeypatch.setattr(settings, "HELPER_ENGINE", "")
    assert side_jobs.configured() is None
    # A model name that could be taken for an option switches the helper off.
    monkeypatch.setattr(settings, "HELPER_ENGINE", "openai_compat")
    monkeypatch.setattr(settings, "HELPER_MODEL", "--dangerous")
    assert side_jobs.configured() is None


async def test_a_failing_helper_costs_nothing_but_the_call_and_is_still_recorded(rt, sessions, owner, seen, monkeypatch):
    helper_on(monkeypatch)
    seen.helper_fails = True
    agent = await make_agent(sessions, owner)
    assert await rt.capture.ask(agent, "x") is None
    (row,) = helper_usage.rows()
    assert row["ok"] is False and row["engine"] == "openai_compat" and row["agent"] == "Scout"


async def test_what_leaves_is_redacted_and_the_call_is_recorded_without_the_text(rt, sessions, owner, seen, monkeypatch):
    helper_on(monkeypatch, model="tiny-model")
    agent = await make_agent(sessions, owner)
    await rt.capture.ask(agent, f"The person said their key is {SECRET} and likes tea.")
    sent = seen.helper[0]["messages"][0]["content"]
    assert SECRET not in sent and "likes tea" in sent
    (row,) = helper_usage.rows()
    assert row["job"] == "memory" and row["model"] == "tiny-model"
    assert (row["input_tokens"], row["output_tokens"], row["ok"]) == (11, 5, True)
    assert SECRET not in json.dumps(row)


# --- the compaction summary through a real turn on an engine with no call ------------------


async def fold(rt, sessions, agent):
    thread = await rt.create_thread(provider="nocall", agent_id=agent.id)
    events = []
    for n in range(1, 9):
        await rt.send_turn(thread.id, f"message {n} {'x' * 600} {SECRET if n == 1 else ''}")
        await finished(sessions, thread.id, count=n)
        events = await events_of(sessions, thread.id)
    return data_of(events, "context.compacted")


async def test_the_compaction_summary_is_written_by_the_helper_for_an_engine_without_a_call(rt, sessions, owner, seen, monkeypatch):
    helper_on(monkeypatch, model="tiny-model")
    agent = await make_agent(sessions, owner)
    compactions = await fold(rt, sessions, agent)
    assert compactions and compactions[0]["method"] == "model"
    assert compactions[0]["written_by"] == {"via": "helper", "engine": "openai_compat", "model": "tiny-model"}
    assert "written by the helper" in compactions[0]["summary"]
    assert seen.helper and SECRET not in seen.helper[0]["messages"][0]["content"]
    assert [r["job"] for r in helper_usage.rows()] == ["compaction"] * len(seen.helper)


async def test_without_a_helper_or_for_an_opted_out_agent_the_excerpts_stand(rt, sessions, owner, seen, monkeypatch):
    agent = await make_agent(sessions, owner)
    compactions = await fold(rt, sessions, agent)
    assert compactions and {c["method"] for c in compactions} == {"excerpts"} and seen.helper == []


async def test_an_opted_out_agent_never_sends_its_conversation_to_the_helper(rt, sessions, owner, seen, monkeypatch):
    helper_on(monkeypatch)
    agent = await make_agent(sessions, owner, use_helper=False)
    compactions = await fold(rt, sessions, agent)
    assert compactions and {c["method"] for c in compactions} == {"excerpts"} and seen.helper == []


async def test_a_failing_helper_keeps_the_excerpts(rt, sessions, owner, seen, monkeypatch):
    helper_on(monkeypatch)
    seen.helper_fails = True
    agent = await make_agent(sessions, owner)
    compactions = await fold(rt, sessions, agent)
    assert compactions and {c["method"] for c in compactions} == {"excerpts"} and seen.helper


# --- the settings, the usage report ---------------------------------------------------------


@pytest.fixture
async def api(rt):
    app = FastAPI()
    app.include_router(memory_router, prefix="/api/v1")
    app.dependency_overrides[get_runtime] = lambda: rt
    transport = httpx.ASGITransport(app, client=("127.0.0.1", 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
        yield client


@pytest.fixture(autouse=True)
def local_access(monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_EXECUTION_ENABLED", True)
    monkeypatch.setattr(settings, "FRONTEND_ORIGINS", "http://localhost:3000")
    monkeypatch.setattr(settings, "LOCAL_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]")


async def test_the_helper_is_set_and_removed_through_the_settings(api):
    first = (await api.get("/api/v1/memory/settings")).json()
    assert first["helper"] is None and first["helper_active"] is None
    saved = await api.put("/api/v1/memory/settings", json={"helper": {"engine": "openai_compat", "model": "tiny-model"}})
    assert saved.status_code == 200
    assert saved.json()["helper"] == {"engine": "openai_compat", "model": "tiny-model"}
    assert saved.json()["helper_active"] == {"engine": "openai_compat", "model": "tiny-model", "source": "settings"}
    bad = await api.put("/api/v1/memory/settings", json={"helper": {"engine": "nocall"}})
    assert bad.status_code == 422 and bad.json()["detail"]["error_code"] == "invalid_setting"
    assert (await api.put("/api/v1/memory/settings", json={"helper": {"engine": "openai_compat", "model": "--x"}})).status_code == 422
    assert (await api.put("/api/v1/memory/settings", json={"helper": None})).json()["helper"] is None


async def test_the_usage_report_lists_helper_calls(rt, sessions, owner, seen, monkeypatch):
    helper_on(monkeypatch, model="tiny-model")
    agent = await make_agent(sessions, owner)
    await rt.capture.ask(agent, "one")
    await rt.capture.ask(agent, "two")
    async with sessions() as db:
        summary = await usage_report.month_summary(db, owner, None)
        _, csv_text = await usage_report.month_csv(db, owner, None)
    helper = summary["helper"]
    assert (helper["calls"], helper["input_tokens"], helper["output_tokens"]) == (2, 22, 10)
    assert helper["groups"][0]["agent"] == "Scout" and helper["groups"][0]["engine"] == "openai_compat"
    rows = [line for line in csv_text.splitlines() if ",helper," in line]
    assert len(rows) == 2 and "Helper model: memory upkeep for Scout" in rows[0] and "tiny-model" in rows[0]


async def test_noticed_facts_are_filed_from_a_conversation_on_an_engine_without_a_call_via_the_helper(rt, sessions, owner, seen, monkeypatch):
    import asyncio

    from app.services import memory_capture

    helper_on(monkeypatch, model="tiny-model")
    seen.helper_answer = json.dumps([{"text": "The person lives in Pune", "kind": "fact", "confidence": 0.9}])
    rt.capture._quiet = lambda: 0.2
    agent = await make_agent(sessions, owner)
    thread = await rt.create_thread(provider="nocall", agent_id=agent.id, title="Moving")
    await rt.send_turn(thread.id, f"I moved to Pune last month for a new job, and my key is {SECRET}")
    await finished(sessions, thread.id)
    await asyncio.sleep(0.5)
    await rt.capture.idle()
    assert "The person lives in Pune" in agent_memory.read_memory(agent.id).text
    last = memory_capture.read_status(agent.id)["last_capture"]
    assert last["via"] == "helper" and last["added"] == 1
    assert SECRET not in seen.helper[0]["messages"][0]["content"]
    assert [r["job"] for r in helper_usage.rows()] == ["memory"]


async def test_the_tidy_up_asks_the_helper_for_an_engine_without_a_call(rt, sessions, owner, seen, monkeypatch):
    agent = await make_agent(sessions, owner)
    agent_memory.write_memory(
        agent.id,
        "- 2026-10-01 · from chat \"A\" (noticed) · The office is in Pune\n"
        "- 2026-10-02 · from chat \"B\" (noticed) · The office is in Delhi\n"
        "- 2026-10-03 · from chat \"C\" (noticed) · Likes tea\n"
        "- 2026-10-04 · from chat \"D\" (noticed) · Likes coffee\n"
        "- 2026-10-05 · from chat \"E\" (noticed) · Has a dog\n",
    )
    without = await rt.capture.tidy(agent.id)
    assert "cannot make the quick call" in without["note"] and seen.helper == []
    helper_on(monkeypatch)
    seen.helper_answer = '{"pairs": []}'
    with_helper = await rt.capture.tidy(agent.id)
    assert with_helper["note"] is None and seen.helper
    assert {r["job"] for r in helper_usage.rows()} == {"memory"}
