"""The approval broker on its own: what is asked, what is answered, and the
guarantee that nothing is ever approved except by an explicit "allow"."""

import json

import pytest

from app.core.config import settings
from app.services import local_approval_broker as mod
from app.services.local_approval_broker import ApprovalBroker, ApprovalNotFound
from app.services.local_run_hub import RunHub


class Recorder:
    def __init__(self):
        self.payloads = []
        self.opened = 0
        self.closed = 0

    def respond(self, payload):
        self.payloads.append(payload)
        return True

    def open(self):
        self.opened += 1

    def close(self):
        self.closed += 1

    @property
    def behavior(self):
        return self.payloads[-1]["response"]["response"]["behavior"]


@pytest.fixture
def hub():
    hub = RunHub()
    hub.start(1)
    return hub


@pytest.fixture
def broker(hub):
    return ApprovalBroker(hub)


async def ask(broker, rec, tool_input=None, request_id="r1", run_id=1):
    return broker.request(
        run_id, request_id, "Bash", tool_input or {"command": "ls"}, rec.respond,
        on_open=rec.open, on_close=rec.close,
    )


async def test_nothing_is_sent_to_the_cli_until_someone_answers(broker):
    rec = Recorder()
    approval = await ask(broker, rec)
    assert approval is not None
    assert rec.payloads == []
    assert rec.opened == 1 and rec.closed == 0
    assert [a["id"] for a in broker.list_pending(1)] == [approval.id]
    broker.cancel_run(1)


async def test_allow_sends_the_original_input_back_unchanged(broker):
    rec = Recorder()
    tool_input = {"command": "git status", "description": "x"}
    approval = await ask(broker, rec, tool_input)
    broker.answer(1, approval.id, "allow")
    assert rec.payloads == [
        {
            "type": "control_response",
            "response": {
                "subtype": "success",
                "request_id": "r1",
                "response": {"behavior": "allow", "updatedInput": tool_input},
            },
        }
    ]
    assert rec.closed == 1
    assert broker.list_pending(1) == []


async def test_deny_cancel_and_expiry_all_refuse(broker, monkeypatch):
    for how in ("deny", "cancel_run", "cancel_request", "expire"):
        rec = Recorder()
        approval = await ask(broker, rec)
        if how == "deny":
            broker.answer(1, approval.id, "deny")
        elif how == "cancel_run":
            broker.cancel_run(1)
        elif how == "cancel_request":
            broker.cancel_request(1, "r1")
        else:
            broker._expire(approval.id)
        assert rec.behavior == "deny", how
        assert rec.closed == 1, how
        assert "updatedInput" not in rec.payloads[-1]["response"]["response"], how


async def test_decisions_other_than_allow_or_deny_are_rejected(broker):
    rec = Recorder()
    approval = await ask(broker, rec)
    for bad in ("", "ALLOW", "yes", "always", None):
        with pytest.raises(ValueError):
            broker.answer(1, approval.id, bad)
    assert rec.payloads == []
    broker.cancel_run(1)


async def test_an_input_too_large_to_review_is_refused_without_asking(broker):
    rec = Recorder()
    huge = {"command": "x" * (mod.MAX_REVIEWABLE_INPUT_CHARS + 1)}
    assert await ask(broker, rec, huge) is None
    assert rec.behavior == "deny"
    assert rec.payloads[0]["response"]["response"]["message"] == mod.DENY_TOO_LARGE
    assert broker.list_pending(1) == []
    assert rec.opened == 0, "no wait was started, so none can be left open"


async def test_the_answer_is_recorded_once_even_when_cancel_races_it(broker):
    rec = Recorder()
    approval = await ask(broker, rec)
    broker.answer(1, approval.id, "allow")
    broker.cancel_run(1)
    broker._expire(approval.id)
    assert len(rec.payloads) == 1 and rec.behavior == "allow"
    with pytest.raises(ApprovalNotFound):
        broker.answer(1, approval.id, "deny")


async def test_events_say_what_happened_without_a_second_copy_of_the_input(broker, hub):
    rec = Recorder()
    sub = hub.subscribe(1)
    approval = await ask(broker, rec, {"command": "rm -rf build"})
    broker.answer(1, approval.id, "deny")

    events = []
    while not sub.queue.empty():
        events.append(sub.queue.get_nowait())
    assert [e.type for e in events] == ["approval.requested", "approval.resolved"]
    assert events[0].data["input"] == {"command": "rm -rf build"}
    assert events[1].data == {"id": approval.id, "outcome": "deny"}


async def test_unanswered_approval_expires_by_itself(broker, monkeypatch):
    import asyncio

    monkeypatch.setattr(settings, "LOCAL_APPROVAL_TIMEOUT_SECONDS", 0.2)
    rec = Recorder()
    await ask(broker, rec)
    await asyncio.sleep(0.5)
    assert rec.behavior == "deny"
    assert rec.payloads[0]["response"]["response"]["message"] == mod.DENY_EXPIRED
    assert rec.closed == 1


def test_payloads_are_valid_json_lines():
    for payload in (mod._allow_payload("r", {"a": "é"}), mod._deny_payload("r", "no")):
        line = json.dumps(payload, ensure_ascii=False)
        assert "\n" not in line and json.loads(line) == payload
