"""Small additions F04 made to the stream parser and the CLI runner."""

import json
import sys
import threading
import time
from pathlib import Path

from app.services import local_claude_runner as runner
from app.services.local_claude_stream import StreamNormalizer, ToolEvent


def feed(normalizer, *events):
    chunks = []
    for event in events:
        chunks += normalizer.feed(json.dumps(event))
    return chunks


def message_start(message_id):
    return {"type": "stream_event", "event": {"type": "message_start", "message": {"id": message_id}}}


def delta(text):
    return {
        "type": "stream_event",
        "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}},
    }


def assistant(message_id, *blocks):
    return {"type": "assistant", "message": {"id": message_id, "content": list(blocks)}}


def text_block(text):
    return {"type": "text", "text": text}


def normalizer():
    return StreamNormalizer(max_text_bytes=1_000_000)


# --- the stream parser -------------------------------------------------------------


def test_it_reports_the_session_model_and_usage():
    n = normalizer()
    assert not n.init_seen and n.model is None
    feed(n, {"type": "system", "subtype": "init", "model": "claude-x", "session_id": "s"})
    assert n.init_seen and n.model == "claude-x"
    feed(
        n,
        {
            "type": "result", "is_error": False, "result": "ok",
            "usage": {"input_tokens": 5, "output_tokens": 7, "other": 1}, "total_cost_usd": 0.5,
        },
    )
    assert n.usage == {"input_tokens": 5, "output_tokens": 7, "cost_usd": 0.5}
    other = normalizer()
    feed(other, {"type": "result", "is_error": False, "result": "ok"})
    assert other.usage is None


def test_each_text_block_of_the_answer_is_one_complete_message_once():
    n = normalizer()
    feed(n, message_start("m1"), delta("Let me "), delta("look."))
    assert n.take_messages() == []  # still typing
    feed(n, assistant("m1", text_block("Let me look.")))
    assert n.take_messages() == ["Let me look."]
    feed(n, assistant("m1", text_block("Let me look.")))  # the same block again
    assert n.take_messages() == []
    feed(n, assistant("m2", {"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}))
    assert n.take_messages() == []  # a call alone is not a message
    feed(n, assistant("m3", text_block("Done.")))  # a message that streamed no deltas
    assert n.take_messages() == ["Done."]
    assert n.text == "Let me look.Done."


def test_text_not_yet_covered_by_a_complete_message_is_what_a_crash_would_lose():
    n = normalizer()
    feed(n, message_start("m1"), delta("first "), delta("part"))
    assert n.unflushed_text == "first part"
    feed(n, assistant("m1", text_block("first part")))
    assert n.unflushed_text == ""
    feed(n, message_start("m2"), delta("second"))
    assert n.unflushed_text == "second"


def test_tool_events_carry_the_full_input_and_output_without_changing_their_payload():
    n = normalizer()
    big_input = {"command": "echo " + "x" * 5000}
    feed(n, assistant("m1", {"type": "tool_use", "id": "t1", "name": "Bash", "input": big_input}))
    feed(
        n,
        {
            "type": "user",
            "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "y" * 5000}]},
        },
    )
    started, completed = n.take_tool_events()
    assert started.extra["input"] == big_input
    assert len(completed.extra["output"]) == 5000
    assert set(started.data) == {"tool_use_id", "tool", "summary"}  # the live payload is as before
    assert len(completed.data["preview"]) <= 1001
    # Equality ignores the extra fields, as it did before they existed.
    assert started == ToolEvent("tool_started", started.data)


# --- the runner ----------------------------------------------------------------------


def test_resume_continues_the_session_instead_of_creating_it():
    exe = Path("claude")
    create = runner.build_args(exe, "sid-1")
    resume = runner.build_args(exe, "sid-1", resume=True)
    assert create[create.index("--session-id") + 1] == "sid-1" and "--resume" not in create
    assert resume[resume.index("--resume") + 1] == "sid-1" and "--session-id" not in resume
    assert runner.build_args(exe, "sid-1", tools=True, resume=True).count("--resume") == 1


SLEEPER = "import sys, time; sys.stdin.read(); time.sleep(60)"


def test_a_control_can_kill_a_chat_only_run_and_stdin_still_closes():
    """keep_stdin_open=False: the prompt is written and stdin closed (the script reads
    to EOF), yet the control can still stop the run."""
    control = runner.RunControl()
    started = time.monotonic()
    threading.Timer(1.0, control.kill).start()
    output = runner.run_blocking(
        [sys.executable, "-c", SLEEPER],
        stdin_text="a prompt",
        cwd=Path.cwd(),
        timeout_seconds=30,
        max_output_bytes=10_000,
        control=control,
        keep_stdin_open=False,
    )
    assert time.monotonic() - started < 20  # killed, not run to the 60 s sleep
    assert control.killed and output.exit_code != 0


def test_a_kill_requested_before_the_process_exists_still_kills_it():
    control = runner.RunControl()
    control.kill()
    started = time.monotonic()
    runner.run_blocking(
        [sys.executable, "-c", SLEEPER],
        stdin_text="x",
        cwd=Path.cwd(),
        timeout_seconds=30,
        max_output_bytes=10_000,
        control=control,
        keep_stdin_open=False,
    )
    assert time.monotonic() - started < 20


def test_a_control_without_an_explicit_choice_still_keeps_stdin_open_as_before():
    control = runner.RunControl()
    script = "import sys; line = sys.stdin.readline(); print(line.strip(), flush=True); sys.stdin.read()"
    output = runner.run_blocking(
        [sys.executable, "-c", script],
        stdin_text="hello",
        cwd=Path.cwd(),
        timeout_seconds=30,
        max_output_bytes=10_000,
        control=control,
        on_stdout_line=lambda line: control.close_stdin() or False,
    )
    assert output.exit_code == 0
