"""F03: tools mode in the runner (stdin stays open, the time limit can be
paused) and the exact CLI arguments it uses."""

import json
import sys
import threading
import time
from pathlib import Path

from app.services import local_claude_runner as runner
from app.services import local_run_hub


def run_controlled(script, *, tmp_path, control, stdin=None, timeout=10, handler=None):
    return runner.run_blocking(
        [sys.executable, "-c", script],
        stdin_text=stdin,
        cwd=tmp_path,
        timeout_seconds=timeout,
        max_output_bytes=100_000,
        on_stdout_line=handler or (lambda line: None),
        control=control,
    )


def test_stdin_stays_open_so_the_caller_can_keep_talking(tmp_path):
    script = (
        "import sys\n"
        "for line in sys.stdin:\n"
        "    print('got ' + line.strip(), flush=True)\n"
        "print('eof', flush=True)\n"
    )
    control = runner.RunControl()
    seen = []

    def handler(line):
        seen.append(line.decode())
        if line == b"got first":
            control.send("second")
        if line == b"got second":
            control.close_stdin()

    out = run_controlled(script, tmp_path=tmp_path, control=control, stdin="first", handler=handler)
    assert seen == ["got first", "got second", "eof"]
    assert out.exit_code == 0 and not out.timed_out


def test_stdin_is_closed_when_the_run_ends_even_if_nobody_closed_it(tmp_path):
    control = runner.RunControl()
    run_controlled("print('x')", tmp_path=tmp_path, control=control)
    assert control.send("late") is False


def test_sending_to_a_process_that_already_exited_returns_false_not_raises(tmp_path):
    control = runner.RunControl()
    run_controlled("pass", tmp_path=tmp_path, control=control)
    assert control.send("x") is False
    control.close_stdin()  # harmless twice


def test_without_a_pause_the_time_limit_still_applies(tmp_path):
    out = run_controlled(
        "import time; time.sleep(30)", tmp_path=tmp_path, control=runner.RunControl(), timeout=1
    )
    assert out.timed_out


def _pausing_handler(control, *, hold_seconds, nested=False):
    def handler(line):
        if line != b"go":
            return

        def work():
            control.pause_deadline()
            if nested:
                control.pause_deadline()
                control.resume_deadline()
            time.sleep(hold_seconds)
            control.resume_deadline()

        threading.Thread(target=work).start()

    return handler


def test_a_paused_clock_does_not_count(tmp_path):
    control = runner.RunControl()
    script = "import time; print('go', flush=True); time.sleep(1.6); print('done', flush=True)"
    out = run_controlled(
        script, tmp_path=tmp_path, control=control, timeout=2,
        handler=_pausing_handler(control, hold_seconds=2.5),  # longer than the whole limit
    )
    assert not out.timed_out and out.exit_code == 0


def test_the_clock_resumes_with_what_was_left_not_a_fresh_limit(tmp_path):
    control = runner.RunControl()
    script = "import time; print('go', flush=True); time.sleep(30)"
    started = time.monotonic()
    out = run_controlled(
        script, tmp_path=tmp_path, control=control, timeout=1.5,
        handler=_pausing_handler(control, hold_seconds=0.5),
    )
    assert out.timed_out
    assert time.monotonic() - started < 10


def test_overlapping_pauses_are_counted(tmp_path):
    control = runner.RunControl()
    script = "import time; print('go', flush=True); time.sleep(1.2)"
    out = run_controlled(
        script, tmp_path=tmp_path, control=control, timeout=1,
        handler=_pausing_handler(control, hold_seconds=2.0, nested=True),
    )
    assert not out.timed_out


def test_tools_arguments_ask_this_app_and_never_skip_permissions():
    args = runner.build_args(Path("claude.exe"), "sid-2", tools=True)
    assert args[args.index("--permission-prompt-tool") + 1] == "stdio"
    assert args[args.index("--permission-mode") + 1] == "default"
    assert args[args.index("--input-format") + 1] == "stream-json"
    assert args[args.index("--session-id") + 1] == "sid-2"
    assert set(args[args.index("--tools") + 1].split(",")) == {
        "Read", "Write", "Edit", "Glob", "Grep", "Bash",
    }
    for flag in ("--strict-mcp-config", "--disable-slash-commands"):
        assert flag in args
    joined = " ".join(args)
    for forbidden in ("dangerously", "bypassPermissions", "dontAsk", "acceptEdits"):
        assert forbidden not in joined


def test_chat_only_arguments_are_unchanged_by_the_tools_option():
    args = runner.build_args(Path("claude.exe"), "sid-3")
    assert "--permission-prompt-tool" not in args and "--input-format" not in args
    assert args[args.index("--tools") + 1] == ""


def test_the_prompt_goes_in_as_one_json_line():
    line = runner.user_message_line('multi\nline "quoted" é')
    assert "\n" not in line
    assert json.loads(line) == {
        "type": "user", "message": {"role": "user", "content": 'multi\nline "quoted" é'},
    }


def test_a_late_subscriber_is_given_the_tool_timeline_again():
    hub = local_run_hub.RunHub()
    hub.start(1)
    hub.publish(1, local_run_hub.TOOL_STARTED, {"tool_use_id": "t1", "tool": "Bash"})
    hub.publish(1, local_run_hub.TOOL_COMPLETED, {"tool_use_id": "t1"})
    hub.publish(1, local_run_hub.TEXT_DELTA, {"text": "hi", "mode": "append"})
    hub.publish(1, local_run_hub.APPROVAL_REQUESTED, {"id": "a"})

    # Approvals are not replayed: the approvals endpoint lists what is pending.
    assert [e.type for e in hub.subscribe(1).initial] == [
        "tool.started", "tool.completed", "text.delta",
    ]


def test_the_replayed_timeline_is_bounded():
    hub = local_run_hub.RunHub()
    hub.start(1)
    for i in range(local_run_hub.MAX_REPLAYED_EVENTS + 50):
        hub.publish(1, local_run_hub.TOOL_STARTED, {"tool_use_id": str(i)})
    assert len(hub.subscribe(1).initial) == local_run_hub.MAX_REPLAYED_EVENTS
