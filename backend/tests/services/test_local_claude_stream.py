import json

from app.services.local_claude_stream import StreamNormalizer


def start(message="m1", **extra):
    return json.dumps({
        "type": "stream_event",
        "event": {"type": "message_start", "message": {"id": message}}, **extra,
    })


def delta(text, **extra):
    return json.dumps({
        "type": "stream_event",
        "event": {"type": "content_block_delta", "index": 0,
                  "delta": {"type": "text_delta", "text": text}}, **extra,
    })


def snapshot(*texts, message="m1", **extra):
    return json.dumps({
        "type": "assistant",
        "message": {"id": message, "content": [{"type": "text", "text": t} for t in texts]},
        **extra,
    })


def feed_all(lines, limit=10_000):
    parser = StreamNormalizer(max_text_bytes=limit)
    emitted = []
    for line in lines:
        emitted.extend(parser.feed(line))
    return parser, emitted


def test_deltas_are_emitted_in_order():
    parser, emitted = feed_all([start(), delta("Hel"), delta("lo"), delta("!")])
    assert emitted == ["Hel", "lo", "!"]
    assert parser.text == "Hello!"


def test_a_snapshot_after_deltas_is_not_added_again():
    parser, emitted = feed_all([start(), delta("a"), delta("b"), snapshot("ab")])
    assert parser.text == "ab" and emitted == ["a", "b"]


def test_a_snapshot_is_used_when_there_were_no_deltas():
    parser, emitted = feed_all([snapshot("whole")])
    assert parser.text == "whole" and emitted == ["whole"]


def test_repeated_and_growing_snapshots_add_only_what_is_new():
    parser, _ = feed_all([snapshot("ab"), snapshot("ab"), snapshot("abcd")])
    assert parser.text == "abcd"


def test_snapshots_carrying_separate_blocks_are_joined():
    parser, _ = feed_all([snapshot("A", message="m2"), snapshot("B", message="m2")])
    assert parser.text == "AB"


def test_messages_are_tracked_separately():
    parser, _ = feed_all([start("m1"), delta("one"), snapshot("one", message="m1"), snapshot("two", message="m2")])
    assert parser.text == "onetwo"


def test_informational_and_unknown_events_are_ignored():
    lines = [
        json.dumps({"type": "system", "subtype": "init", "tools": [], "mcp_servers": []}),
        json.dumps({"type": "system", "subtype": "status", "status": "requesting"}),
        json.dumps({"type": "system", "subtype": "thinking_tokens", "n": 1}),
        json.dumps({"type": "rate_limit_event", "rate_limit_info": {"status": "allowed"}}),
        json.dumps({"type": "brand_new_event", "payload": {"a": [1, 2]}}),
        json.dumps({"type": "stream_event", "event": {"type": "ping"}}),
        json.dumps({"type": "stream_event", "event": {"type": "content_block_stop"}}),
        start(), delta("ok"),
        json.dumps({"type": "rate_limit_event", "rate_limit_info": {}}),
    ]
    parser, emitted = feed_all(lines)
    assert emitted == ["ok"] and parser.final is None


def test_malformed_and_odd_lines_never_raise():
    lines = [
        "", "   ", "not json", "{", "[1, 2]", "42", "null", '"text"',
        '{"type": "stream_event"}',
        '{"type": "stream_event", "event": "nope"}',
        '{"type": "stream_event", "event": {"type": "content_block_delta"}}',
        '{"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": 5}}}',
        '{"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "input_json_delta", "partial_json": "{"}}}',
        '{"type": "assistant"}',
        '{"type": "assistant", "message": {"content": "str"}}',
        '{"type": "assistant", "message": {"content": [null, 3, {"type": "tool_use"}]}}',
        '{"type": "result"}',
        b"\xff\xfe\x00",
    ]
    parser, emitted = feed_all(lines)
    assert emitted == [] and parser.text == ""
    assert parser.skipped_lines > 0


def test_the_final_result_is_read():
    parser, _ = feed_all([json.dumps(
        {"type": "result", "is_error": False, "result": "done", "permission_denials": []}
    )])
    assert parser.final.text == "done"
    assert not parser.final.is_error and not parser.final.permission_denied


def test_an_error_result_and_denials_are_flagged():
    parser, _ = feed_all([json.dumps({"type": "result", "is_error": True, "result": "bad"})])
    assert parser.final.is_error
    parser, _ = feed_all([json.dumps(
        {"type": "result", "is_error": False, "result": "x", "permission_denials": [{"tool": "Bash"}]}
    )])
    assert parser.final.permission_denied and not parser.final.is_error


def test_a_result_without_text_has_no_text():
    parser, _ = feed_all([json.dumps({"type": "result", "is_error": False})])
    assert parser.final.text is None


def test_sub_agent_output_is_not_part_of_the_answer():
    parser, emitted = feed_all([
        delta("inner", parent_tool_use_id="toolu_1"),
        snapshot("inner", message="mx", parent_tool_use_id="toolu_1"),
        start(), delta("outer"),
    ])
    assert emitted == ["outer"]


def test_text_over_the_limit_is_cut_off_and_flagged():
    parser, emitted = feed_all([start(), delta("12345"), delta("67890"), delta("x")], limit=10)
    assert parser.text == "1234567890" and parser.over_limit
    assert emitted == ["12345", "67890"]


def test_the_limit_counts_bytes_not_characters():
    parser, _ = feed_all([start(), delta("é" * 4)], limit=6)
    assert parser.over_limit and parser.text == ""


def test_multibyte_text_and_bytes_input_work():
    line = delta("héllo wörld ✓").encode("utf-8")
    parser, emitted = feed_all([start(), line])
    assert parser.text == "héllo wörld ✓"


def test_bytes_with_a_carriage_return_are_accepted():
    parser, _ = feed_all([start().encode() + b"\r", delta("x").encode() + b"\r"])
    assert parser.text == "x"
