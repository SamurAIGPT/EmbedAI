"""Saved rules, approval modes and the decision log, through a real runtime and
a real child process playing the Claude CLI."""

import pytest

from app.core.config import settings
from app.services import approval_allowlist as allow
from app.services import decision_log
from app.services import thread_store as store
from app.services.thread_runtime import ThreadRuntime
from tests.services.thread_harness import (  # noqa: F401
    ReadyClaude,
    cleanup,
    cli,
    events_of,
    finished,
    new_thread,
    owner,
    pending_approval,
    reload,
    rt,
    sessions,
    tools_on,
    types_of,
)

BASH = {"type": "tool", "tool_name": "Bash", "tool_input": {"command": "git status -s"}}


def logged(event=None):
    items, _ = decision_log.read_page(limit=200, event=event)
    return list(reversed(items))


async def save_rule(rt, kind, command):
    async with rt.session() as db:
        await allow.add_rule(db, rt.owner_id, kind, command)


async def test_a_card_that_is_shown_and_allowed_leaves_two_records(rt, sessions, cli, tools_on, owner):
    thread = await new_thread(sessions, owner)
    cli.script(BASH)
    await rt.send_turn(thread.id, "status")
    approval = await pending_approval(sessions, thread.id)
    assert [e["event"] for e in logged()] == ["shown"]
    assert await rt.answer_approval(approval.id, "allow")
    await finished(sessions, thread.id)
    shown, allowed = logged()
    assert (shown["event"], shown["tool"], shown["summary"]) == ("shown", "Bash", approval.summary)
    assert (allowed["event"], allowed["source"], allowed["approval_id"]) == ("allowed", "user", approval.id)
    assert allowed["thread_id"] == thread.id


async def test_a_denial_is_recorded(rt, sessions, cli, tools_on, owner):
    thread = await new_thread(sessions, owner)
    cli.script(BASH)
    await rt.send_turn(thread.id, "status")
    approval = await pending_approval(sessions, thread.id)
    assert await rt.answer_approval(approval.id, "deny")
    await finished(sessions, thread.id)
    assert [e["event"] for e in logged()] == ["shown", "denied"]


async def test_a_timeout_is_recorded(rt, sessions, cli, tools_on, monkeypatch, owner):
    monkeypatch.setattr(settings, "LOCAL_APPROVAL_TIMEOUT_SECONDS", 1)
    thread = await new_thread(sessions, owner)
    cli.script(BASH)
    await rt.send_turn(thread.id, "status")
    await finished(sessions, thread.id)
    events = logged()
    assert [e["event"] for e in events] == ["shown", "expired"]
    assert events[1]["source"] == "timeout"


async def test_a_saved_exact_rule_allows_the_command_without_asking(rt, sessions, cli, tools_on, owner):
    await save_rule(rt, allow.EXACT, "git status -s")
    thread = await new_thread(sessions, owner)
    cli.script(BASH)
    await rt.send_turn(thread.id, "status")
    await finished(sessions, thread.id)  # nobody answered
    [reply] = cli.log("reply")
    assert reply["reply"]["response"]["response"]["behavior"] == "allow"
    assert reply["reply"]["response"]["response"]["updatedInput"] == BASH["tool_input"]
    [approval] = await _approvals(sessions, thread.id)
    assert approval.status == "allowed" and approval.delivered_at is not None
    assert "approval.resolved" in await types_of(sessions, thread.id)  # still on the timeline
    [entry] = logged()
    assert (entry["event"], entry["source"]) == ("auto_allowed", "saved_rule")
    assert "git status -s" in entry["rule"]


async def test_a_saved_prefix_rule_allows_a_longer_command_but_not_a_chained_one(rt, sessions, cli, tools_on, owner):
    await save_rule(rt, allow.PREFIX, "git status")
    thread = await new_thread(sessions, owner)
    cli.script(BASH)
    await rt.send_turn(thread.id, "status")
    await finished(sessions, thread.id)
    assert cli.log("reply")[0]["reply"]["response"]["response"]["behavior"] == "allow"

    chained = {"type": "tool", "tool_name": "Bash", "tool_input": {"command": "git status; rm -rf ~"}}
    cli.script(chained)
    await rt.send_turn(thread.id, "again")
    approval = await pending_approval(sessions, thread.id)  # asked: the rule does not cover it
    assert approval.summary and "rm -rf" in approval.summary
    assert await rt.answer_approval(approval.id, "deny")
    await finished(sessions, thread.id, count=2)


async def test_a_rule_for_another_command_or_tool_does_not_apply(rt, sessions, cli, tools_on, owner):
    await save_rule(rt, allow.EXACT, "ls")
    thread = await new_thread(sessions, owner)
    cli.script(BASH)
    await rt.send_turn(thread.id, "status")
    approval = await pending_approval(sessions, thread.id)
    assert cli.log("reply") == []
    assert await rt.answer_approval(approval.id, "deny")
    await finished(sessions, thread.id)


async def test_always_allow_saves_the_exact_command_for_next_time(rt, sessions, cli, tools_on, owner):
    thread = await new_thread(sessions, owner)
    cli.script(BASH)
    await rt.send_turn(thread.id, "status")
    approval = await pending_approval(sessions, thread.id)
    assert await rt.answer_approval(approval.id, "allow", remember=True)
    await finished(sessions, thread.id)
    async with rt.session() as db:
        [rule] = await allow.list_rules(db, owner)
    assert (allow.split_scope(rule.connector)[0], rule.tool) == ("exact", "git status -s")
    assert logged("allowed")[0]["remembered"] is True

    await rt.send_turn(thread.id, "again")
    await finished(sessions, thread.id, count=2)  # no card this time
    assert [e["event"] for e in logged()].count("auto_allowed") == 1


async def test_a_deny_or_a_non_bash_tool_is_never_remembered(rt, sessions, cli, tools_on, owner):
    thread = await new_thread(sessions, owner)
    cli.script(BASH)
    await rt.send_turn(thread.id, "status")
    approval = await pending_approval(sessions, thread.id)
    assert await rt.answer_approval(approval.id, "deny", remember=True)
    await finished(sessions, thread.id)

    cli.script({"type": "tool"})  # a Write
    await rt.send_turn(thread.id, "write")
    approval = await pending_approval(sessions, thread.id)
    assert await rt.answer_approval(approval.id, "allow", remember=True)
    await finished(sessions, thread.id, count=2)
    async with rt.session() as db:
        assert await allow.list_rules(db, owner) == []


async def test_the_log_never_holds_the_full_input_or_a_secret(rt, sessions, cli, tools_on, owner):
    secret = "sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123"
    cli.script({"type": "tool", "tool_name": "Bash",
                "tool_input": {"command": f"curl -H 'Authorization: Bearer {secret}' https://x.test"}})
    thread = await new_thread(sessions, owner)
    await rt.send_turn(thread.id, "call")
    approval = await pending_approval(sessions, thread.id)
    assert secret in approval.input["command"]  # the person sees the real call
    assert await rt.answer_approval(approval.id, "deny")
    await finished(sessions, thread.id)
    text = " ".join(str(e) for e in logged())
    assert secret not in text and "input" not in " ".join(k for e in logged() for k in e)


class PlanClaude(ReadyClaude):
    async def run_turn(self, ctx, sink, gate):
        ctx.approval_mode = "plan"
        return await super().run_turn(ctx, sink, gate)


async def test_the_turn_context_mode_reaches_the_cli(sessions, owner, cli, tools_on, monkeypatch):
    from app.services.thread_hub import ThreadHub

    runtime = ThreadRuntime(
        hub=ThreadHub(), adapters={"claude": PlanClaude()}, status_ttl=0,
        _session_factory=sessions, owner_id=owner,
    )
    try:
        thread = await new_thread(sessions, owner)
        cli.script({"type": "chat"})
        await runtime.send_turn(thread.id, "hello")
        await finished(sessions, thread.id)
        assert cli.log("start")[0]["approval_mode"] == "plan"
    finally:
        await runtime.shutdown()


async def test_with_no_mode_chosen_the_cli_gets_no_mode_argument(rt, sessions, cli, tools_on, owner):
    thread = await new_thread(sessions, owner)
    cli.script({"type": "chat"})
    await rt.send_turn(thread.id, "hello")
    await finished(sessions, thread.id)
    assert cli.log("start")[0]["approval_mode"] is None


async def _approvals(sessions, thread_id):
    async with sessions() as db:
        return await store.list_approvals(db, thread_id)


async def test_a_saved_rule_never_allows_a_command_on_the_safety_floor(rt, sessions, cli, tools_on, owner):
    # A rule that predates the floor, or was written straight into the table.
    async with rt.session() as db:
        from app.db.models.permission_rule import PermissionRule

        db.add(PermissionRule(user_id=owner, bucket=allow.BUCKET, connector=allow.PREFIX, tool="git push", decision="allow"))
        await db.commit()
    thread = await new_thread(sessions, owner)
    cli.script({"type": "tool", "tool_name": "Bash", "tool_input": {"command": "git push --force origin main"}})
    await rt.send_turn(thread.id, "push")
    approval = await pending_approval(sessions, thread.id)  # asked, not auto-allowed
    assert cli.log("reply") == []
    [entry] = logged()
    assert (entry["event"], entry["rule"]) == ("shown", "safety_floor:force-push")
    assert await rt.answer_approval(approval.id, "deny")
    await finished(sessions, thread.id)


async def test_a_saved_rule_does_not_allow_a_command_that_cannot_be_read(rt, sessions, cli, tools_on, owner):
    async with rt.session() as db:  # written straight into the table: add_rule refuses it
        from app.db.models.permission_rule import PermissionRule

        db.add(PermissionRule(user_id=owner, bucket=allow.BUCKET, connector=allow.EXACT, tool="echo $(whoami)", decision="allow"))
        await db.commit()
    thread = await new_thread(sessions, owner)
    cli.script({"type": "tool", "tool_name": "Bash", "tool_input": {"command": "echo $(whoami)"}})
    await rt.send_turn(thread.id, "who")
    approval = await pending_approval(sessions, thread.id)
    assert logged()[0]["rule"] == "safety_floor:unreadable"
    assert await rt.answer_approval(approval.id, "deny")
    await finished(sessions, thread.id)


async def test_a_rule_tied_to_another_folder_does_not_apply_here(rt, sessions, cli, tools_on, owner, tmp_path):
    async with rt.session() as db:
        await allow.add_rule(db, owner, allow.EXACT, "git status -s", str(tmp_path / "elsewhere"))
    thread = await new_thread(sessions, owner)
    cli.script(BASH)
    await rt.send_turn(thread.id, "status")
    approval = await pending_approval(sessions, thread.id)
    assert await rt.answer_approval(approval.id, "deny")
    await finished(sessions, thread.id)


async def test_a_rule_tied_to_this_folder_applies(rt, sessions, cli, tools_on, owner, tmp_path):
    async with rt.session() as db:
        await allow.add_rule(db, owner, allow.EXACT, "git status -s", str(tmp_path.resolve()))
    thread = await new_thread(sessions, owner)
    cli.script(BASH)
    await rt.send_turn(thread.id, "status")
    await finished(sessions, thread.id)
    assert cli.log("reply")[0]["reply"]["response"]["response"]["behavior"] == "allow"


async def test_always_allow_ties_the_rule_to_the_conversations_folder(rt, sessions, cli, tools_on, owner, tmp_path):
    thread = await new_thread(sessions, owner)
    cli.script(BASH)
    await rt.send_turn(thread.id, "status")
    approval = await pending_approval(sessions, thread.id)
    assert await rt.answer_approval(approval.id, "allow", remember=True)
    await finished(sessions, thread.id)
    async with rt.session() as db:
        [rule] = await allow.list_rules(db, owner)
    assert rule.connector == f"exact@{tmp_path.resolve()}"


async def test_always_allow_on_a_floor_command_is_allowed_once_and_not_saved(rt, sessions, cli, tools_on, owner):
    cli.script({"type": "tool", "tool_name": "Bash", "tool_input": {"command": "git push -f"}})
    thread = await new_thread(sessions, owner)
    await rt.send_turn(thread.id, "push")
    approval = await pending_approval(sessions, thread.id)
    assert await rt.answer_approval(approval.id, "allow", remember=True)
    await finished(sessions, thread.id)
    assert cli.log("reply")[0]["reply"]["response"]["response"]["behavior"] == "allow"
    async with rt.session() as db:
        assert await allow.list_rules(db, owner) == []
