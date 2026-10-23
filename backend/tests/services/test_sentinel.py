"""Sentinel: the pure detectors, and one scan over real stored events and a
real decision log."""

from datetime import UTC, datetime, timedelta

import pytest

from app.services import decision_log
from app.services import sentinel as s
from app.services import thread_store as store
from tests.services.thread_harness import cleanup, new_thread, owner, sessions  # noqa: F401

NOW = datetime(2026, 10, 1, 12, tzinfo=UTC)


def tool(n, text, *, turn=1, thread=1, name="Bash", minutes=0):
    return s.ToolFailure(event_id=n, thread_id=thread, turn_id=turn, tool=name, text=text, at=NOW + timedelta(minutes=minutes))


def test_normalize_removes_what_is_specific_to_the_work():
    text = 'open "/Users/ann/secret plan.txt" failed at line 12 for ann@example.com id 3f2a9c8b1d'
    out = s.normalize(text)
    unc = s.normalize(r"cannot open \\files\users\priya\secret.txt")
    assert "priya" not in unc and "secret" not in unc and "<path>" in unc
    for leaked in ("ann", "secret", "12", "3f2a9c8b1d", "example"):
        assert leaked not in out
    assert s.normalize("no layer 12 in /a/b") == s.normalize("no layer 104 in /c/d")


def test_a_shell_failure_groups_by_exit_code_and_class_only():
    a = s.failure_key("Bash", "ls: /home/ann/x: No such file or directory\nexit code 1")
    b = s.failure_key("Bash", "cat: notes-for-bob.txt: No such file or directory\nexit code 1")
    assert a == b and a[1] == "exit 1: no such file"
    assert s.failure_key("Bash", "timed out after 120s exit code 143")[1] == "timed out"


def test_three_same_failures_in_one_turn_are_a_retry_loop():
    failures = [tool(i, "exit code 2: syntax error", minutes=i) for i in range(3)]
    [loop] = s.retry_loops(failures)
    assert (loop.kind, loop.count, loop.turn_id, loop.severity) == ("retry_loop", 3, 1, "high")
    assert loop.evidence == {"source": "event", "id": 2}
    assert s.retry_loops(failures[:2]) == []
    # Spread over two turns it is not a loop.
    assert s.retry_loops([*failures[:2], tool(9, "exit code 2: syntax error", turn=2)]) == []


def test_tool_errors_group_by_tool_and_signature():
    issues = s.tool_errors([
        tool(1, "Error: file /a/b.txt has 12 lines", name="Read"),
        tool(2, "Error: file /c.txt has 99 lines", name="Read"),
        tool(3, "Error: file /c.txt has 99 lines", name="Edit"),
    ])
    assert sorted(i.count for i in issues) == [1, 2]
    assert all("/a/b.txt" not in i.title for i in issues)


def test_repeated_denials_in_one_thread():
    denials = [
        s.Denial(decision_id=f"d{i}", thread_id=5, tool="Bash", summary=f"sleep {i + 1}0", at=NOW) for i in range(2)
    ]
    [issue] = s.repeated_denials(denials)
    assert (issue.kind, issue.count, issue.thread_id) == ("repeated_denial", 2, 5)
    assert issue.evidence["source"] == "decision"
    assert s.repeated_denials(denials[:1]) == []


def test_turn_failures_leave_out_stops():
    issues = s.turn_failures([
        s.TurnFailure(event_id=1, thread_id=1, turn_id=1, error_code="cli_error", at=NOW),
        s.TurnFailure(event_id=2, thread_id=1, turn_id=2, error_code="cli_error", at=NOW),
        s.TurnFailure(event_id=3, thread_id=1, turn_id=3, error_code="stopped", at=NOW),
    ])
    assert [(i.title, i.count) for i in issues] == [("turn failed: cli_error", 2)]


def test_scan_records_is_worst_first_and_holds_no_raw_text():
    secret = "token in /Users/ann/private.txt"
    issues = s.scan_records([tool(i, secret + " exit code 1", minutes=i) for i in range(3)], [], [])
    assert [i["kind"] for i in issues] == ["retry_loop", "tool_error"]
    assert all("ann" not in i["title"] and "private" not in i["title"] for i in issues)


@pytest.mark.parametrize("hours", [1, 24])
async def test_a_scan_reads_stored_events_and_the_log(sessions, owner, decision_log_dir, hours):
    thread = await new_thread(sessions, owner, workspace_path="/tmp")
    other = await new_thread(sessions, "someone-else", workspace_path="/tmp")
    try:
        async with sessions() as db:
            turn = await store.start_turn(db, thread.id, "go")
        for n in range(3):
            async with sessions() as db:
                await store.append_turn_event(db, turn.id, turn.owner_token, "tool.started", {"call_id": f"c{n}", "name": "Bash", "input": {}})
                await store.append_turn_event(db, turn.id, turn.owner_token, "tool.completed", {"call_id": f"c{n}", "ok": False, "output": "bash: nope: command not found", "truncated": False})
        async with sessions() as db:
            await store.append_turn_event(db, turn.id, turn.owner_token, "tool.completed", {"call_id": "ok", "ok": True, "output": "fine"})
        for n in range(2):
            decision_log.record("denied", thread_id=thread.id, tool="Bash", summary="rm -rf build", source="user")
        decision_log.record("denied", thread_id=other.id, tool="Bash", summary="x", source="user")
        decision_log.record("denied", thread_id=other.id, tool="Bash", summary="x", source="user")

        report = await s.scan(sessions, owner, hours)
        kinds = {i["kind"]: i for i in report["issues"]}
        assert set(kinds) == {"retry_loop", "tool_error", "repeated_denial"}
        assert kinds["retry_loop"]["title"] == "repeated in one turn: command not found"
        assert kinds["retry_loop"]["tool"] == "Bash" and kinds["retry_loop"]["thread_id"] == thread.id
        assert kinds["repeated_denial"]["thread_id"] == thread.id  # another owner's thread is not read
        assert report["scanned_decisions"] == 2

        later = await s.scan(sessions, owner, hours, now=datetime.now(UTC) + timedelta(hours=hours + 1))
        assert later["issues"] == []
    finally:
        async with sessions() as db:
            from sqlalchemy import delete
            from app.db.models import Event, Thread, Turn
            await db.execute(delete(Event).where(Event.thread_id == other.id))
            await db.execute(delete(Turn).where(Turn.thread_id == other.id))
            await db.execute(delete(Thread).where(Thread.id == other.id))
            await db.commit()


def test_the_cli_prints_a_summary(monkeypatch, capsys):
    async def fake_scan(factory, owner_id, hours):
        return {"since": NOW, "scanned_events": 0, "scanned_decisions": 0, "issues": []}

    monkeypatch.setattr(s, "scan", fake_scan)
    assert s.main(["--hours", "2"]) == 0
    assert "No issues" in capsys.readouterr().out
    assert s.main(["--json"]) == 0
    assert '"issues": []' in capsys.readouterr().out
