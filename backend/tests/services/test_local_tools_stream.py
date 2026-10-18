"""F03: tool activity and approval requests read from the CLI's stream."""

import json

from app.services.local_claude_stream import StreamNormalizer, summarize_tool


def assistant_tool_use(tool_id="tu1", name="Bash", tool_input=None, message="m9", parent=None):
    return json.dumps({
        "type": "assistant", "parent_tool_use_id": parent,
        "message": {"id": message, "content": [
            {"type": "tool_use", "id": tool_id, "name": name,
             "input": tool_input if tool_input is not None else {"command": "ls -la"}},
        ]},
    })


def tool_result(tool_id="tu1", content="ok", is_error=False, parent=None):
    return json.dumps({
        "type": "user", "parent_tool_use_id": parent,
        "message": {"content": [
            {"type": "tool_result", "tool_use_id": tool_id, "content": content,
             "is_error": is_error},
        ]},
    })


def control_request(request_id="r1", subtype="can_use_tool", tool="Write", tool_input=None):
    return json.dumps({
        "type": "control_request", "request_id": request_id,
        "request": {"subtype": subtype, "tool_name": tool, "tool_use_id": "tu1",
                    "input": tool_input if tool_input is not None else {"file_path": "a.txt"}},
    })


def tool_events(lines):
    parser = StreamNormalizer(max_text_bytes=10_000)
    for line in lines:
        parser.feed(line)
    return [(e.kind, e.data) for e in parser.take_tool_events()]


def test_a_tool_call_and_its_result_become_timeline_events():
    events = tool_events([
        assistant_tool_use(tool_input={"command": "ls -la"}),
        tool_result(content="file1\nfile2"),
    ])
    assert events == [
        ("tool_started", {"tool_use_id": "tu1", "tool": "Bash", "summary": "ls -la"}),
        ("tool_completed", {"tool_use_id": "tu1", "is_error": False, "preview": "file1\nfile2"}),
    ]


def test_the_same_tool_call_in_two_snapshots_is_reported_once():
    events = tool_events([assistant_tool_use(), assistant_tool_use(message="m10")])
    assert [kind for kind, _ in events] == ["tool_started"]


def test_a_failed_tool_result_is_marked_and_long_output_is_clipped():
    ((kind, data),) = tool_events([tool_result(content="x" * 5000, is_error=True)])
    assert kind == "tool_completed" and data["is_error"] is True
    assert len(data["preview"]) <= 1001 and data["preview"].endswith("…")


def test_tool_result_content_given_as_blocks_is_read():
    events = tool_events(
        [tool_result(content=[{"type": "text", "text": "a"}, {"type": "text", "text": "b"}])]
    )
    assert events[0][1]["preview"] == "ab"


def test_sub_agent_tool_activity_is_not_shown():
    assert tool_events([assistant_tool_use(parent="toolu_9"), tool_result(parent="toolu_9")]) == []


def test_take_tool_events_hands_each_event_over_once():
    parser = StreamNormalizer(max_text_bytes=100)
    parser.feed(assistant_tool_use())
    assert len(parser.take_tool_events()) == 1
    assert parser.take_tool_events() == []


def test_an_approval_request_carries_the_exact_tool_input():
    tool_input = {"file_path": "a.txt", "content": "hi"}
    events = tool_events([control_request(tool_input=tool_input)])
    assert events == [(
        "approval_requested",
        {"request_id": "r1", "tool": "Write", "tool_use_id": "tu1", "input": tool_input},
    )]


def test_a_withdrawn_request_is_reported():
    line = json.dumps({"type": "control_cancel_request", "request_id": "r1"})
    assert tool_events([line]) == [("approval_cancelled", {"request_id": "r1"})]


def test_a_control_question_this_app_cannot_answer_is_flagged_not_ignored():
    assert tool_events([control_request(subtype="hook_callback")]) == [
        ("control_unsupported", {"request_id": "r1"})
    ]


def test_malformed_tool_blocks_never_raise():
    lines = [
        json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use"}, "x", None]}}),
        json.dumps({"type": "user", "message": {"content": "just text"}}),
        json.dumps({"type": "user", "message": {"content": [{"type": "tool_result"}]}}),
        json.dumps({"type": "control_request", "request_id": 5, "request": "nope"}),
        json.dumps({
            "type": "control_request", "request_id": "r",
            "request": {"subtype": "can_use_tool", "input": "str"},
        }),
    ]
    assert tool_events(lines) == [(
        "approval_requested",
        {"request_id": "r", "tool": "unknown", "tool_use_id": None, "input": {}},
    )]


def test_summaries_name_the_thing_acted_on():
    assert summarize_tool("Read", {"file_path": "/a/b.txt"}) == "/a/b.txt"
    assert summarize_tool("Grep", {"pattern": "TODO"}) == "TODO"
    assert summarize_tool("Mystery", {"x": 1}) == "Mystery"
    assert summarize_tool("Bash", {}) == "Bash"
    assert len(summarize_tool("Bash", {"command": "y" * 1000})) <= 301
