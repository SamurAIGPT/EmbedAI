import sys
import time
from pathlib import Path

import pytest

from app.core.config import settings
from app.services import local_claude_runner as runner


def run(script: str, *, tmp_path: Path, stdin=None, timeout=10, cap=100_000):
    return runner.run_blocking(
        [sys.executable, "-c", script],
        stdin_text=stdin,
        cwd=tmp_path,
        timeout_seconds=timeout,
        max_output_bytes=cap,
    )


def test_prompt_is_delivered_on_stdin_and_output_captured(tmp_path):
    out = run("import sys; print(sys.stdin.read().upper())", stdin="hello", tmp_path=tmp_path)
    assert out.exit_code == 0
    assert out.stdout.strip() == "HELLO"
    assert not out.timed_out and not out.output_truncated


def test_stderr_and_exit_code_are_reported(tmp_path):
    out = run("import sys; sys.stderr.write('boom'); sys.exit(3)", tmp_path=tmp_path)
    assert out.exit_code == 3
    assert out.stderr == "boom"


def test_timeout_kills_the_whole_process_tree(tmp_path):
    marker = tmp_path / "orphan.txt"
    child = f"import time; time.sleep(2.5); open(r'{marker}', 'w').write('alive')"
    script = (
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, '-c', {child!r}])\n"
        "time.sleep(60)\n"
    )
    started = time.monotonic()
    out = run(script, timeout=1, tmp_path=tmp_path)
    assert out.timed_out
    assert time.monotonic() - started < 20
    time.sleep(4)
    assert not marker.exists(), "grandchild survived the kill"


def test_output_over_the_cap_stops_the_process(tmp_path):
    script = "import sys\nwhile True:\n    sys.stdout.write('x' * 10000)\n    sys.stdout.flush()\n"
    out = run(script, cap=50_000, timeout=30, tmp_path=tmp_path)
    assert out.output_truncated
    assert not out.timed_out


def test_app_secrets_are_not_passed_to_the_child(tmp_path, monkeypatch):
    monkeypatch.setenv("BOAT_API_KEY", "should-not-leak")
    monkeypatch.setenv("HARMLESS_VAR", "visible")
    out = run(
        "import os; print(os.environ.get('BOAT_API_KEY', 'absent'), os.environ.get('HARMLESS_VAR'))",
        tmp_path=tmp_path,
    )
    assert out.stdout.strip() == "absent visible"


def test_the_cli_auto_memory_is_off_for_runs_this_app_starts(tmp_path, monkeypatch):
    # Whatever the user's own shell says: app runs never let the CLI save notes
    # outside the workspace.
    monkeypatch.setenv("CLAUDE_CODE_DISABLE_AUTO_MEMORY", "0")
    out = run("import os; print(os.environ.get('CLAUDE_CODE_DISABLE_AUTO_MEMORY'))", tmp_path=tmp_path)
    assert out.stdout.strip() == "1"


def test_build_args_locks_tools_down_and_keeps_the_prompt_off_argv():
    args = runner.build_args(Path("claude.exe"), "sid-1")
    assert args[args.index("--tools") + 1] == ""
    for flag in ("--strict-mcp-config", "--disable-slash-commands"):
        assert flag in args
    assert args[args.index("--permission-mode") + 1] == "dontAsk"
    assert args[args.index("--session-id") + 1] == "sid-1"
    assert "-p" in args and "--output-format" in args


def test_configured_real_executable_is_used(tmp_path, monkeypatch):
    exe = tmp_path / "claude.exe"
    exe.write_text("")
    monkeypatch.setattr(settings, "LOCAL_CLAUDE_CLI_PATH", str(exe))
    assert runner.resolve_claude_executable() == exe


def test_missing_configured_path_is_not_resolved(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_CLAUDE_CLI_PATH", str(tmp_path / "nope.exe"))
    assert runner.resolve_claude_executable() is None


def test_npm_shim_resolves_to_the_real_binary(tmp_path, monkeypatch):
    shim = tmp_path / "claude.cmd"
    shim.write_text("")
    real = tmp_path / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
    real.parent.mkdir(parents=True)
    real.write_text("")
    monkeypatch.setattr(settings, "LOCAL_CLAUDE_CLI_PATH", str(shim))
    assert runner.resolve_claude_executable() == real


def test_npm_shim_without_the_real_binary_is_not_used(tmp_path, monkeypatch):
    shim = tmp_path / "claude.cmd"
    shim.write_text("")
    monkeypatch.setattr(settings, "LOCAL_CLAUDE_CLI_PATH", str(shim))
    assert runner.resolve_claude_executable() is None


async def test_status_reports_a_missing_cli(monkeypatch):
    monkeypatch.setattr(runner, "resolve_claude_executable", lambda: None)
    status = await runner.get_status()
    assert not status.installed
    assert "LOCAL_CLAUDE_CLI_PATH" in status.error


async def test_run_prompt_without_a_cli_raises(monkeypatch):
    monkeypatch.setattr(runner, "resolve_claude_executable", lambda: None)
    with pytest.raises(runner.ClaudeCliUnavailable):
        await runner.run_prompt("hi", "sid")


def run_lines(script: str, *, tmp_path: Path, handler, timeout=10, cap=100_000):
    return runner.run_blocking(
        [sys.executable, "-c", script],
        stdin_text=None,
        cwd=tmp_path,
        timeout_seconds=timeout,
        max_output_bytes=cap,
        on_stdout_line=handler,
    )


def test_streamed_lines_arrive_while_the_process_is_still_running(tmp_path):
    script = (
        "import sys, time\n"
        "print('first', flush=True)\n"
        "time.sleep(1.5)\n"
        "print('second', flush=True)\n"
    )
    arrivals = []
    out = run_lines(
        script, tmp_path=tmp_path, handler=lambda line: arrivals.append((line, time.monotonic()))
    )
    finished = time.monotonic()
    assert [line for line, _ in arrivals] == [b"first", b"second"]
    assert finished - arrivals[0][1] > 1.0, "first line was held back until the process ended"
    assert out.stdout == "" and out.exit_code == 0


def test_lines_are_split_on_bytes_with_crlf_and_a_final_unterminated_line(tmp_path):
    script = "import sys; sys.stdout.buffer.write(b'a\\r\\nb\\n\\nlast')"
    lines = []
    run_lines(script, tmp_path=tmp_path, handler=lines.append)
    assert lines == [b"a", b"b", b"", b"last"]


def test_a_multibyte_character_split_across_writes_is_intact(tmp_path):
    script = (
        "import sys, time\n"
        "data = 'héllo ✓\\n'.encode('utf-8')\n"
        "sys.stdout.buffer.write(data[:2]); sys.stdout.buffer.flush()\n"
        "time.sleep(0.3)\n"
        "sys.stdout.buffer.write(data[2:]); sys.stdout.buffer.flush()\n"
    )
    lines = []
    run_lines(script, tmp_path=tmp_path, handler=lines.append)
    assert lines == ["héllo ✓".encode("utf-8")]


def test_a_handler_can_stop_the_run_and_it_is_reported_as_too_much_output(tmp_path):
    script = "import time\nwhile True:\n    print('x', flush=True)\n    time.sleep(0.01)\n"
    started = time.monotonic()
    out = run_lines(script, tmp_path=tmp_path, handler=lambda line: True, timeout=30)
    assert out.output_truncated and not out.timed_out
    assert time.monotonic() - started < 20


def test_a_failing_handler_does_not_end_the_run(tmp_path):
    seen = []

    def handler(line):
        seen.append(line)
        raise RuntimeError("bug in the handler")

    out = run_lines("print('a'); print('b')", tmp_path=tmp_path, handler=handler)
    assert seen == [b"a", b"b"] and out.exit_code == 0


def test_streamed_output_over_the_raw_cap_stops_the_process(tmp_path):
    script = "import sys\nwhile True:\n    sys.stdout.write('x' * 10000 + '\\n')\n    sys.stdout.flush()\n"
    out = run_lines(script, tmp_path=tmp_path, handler=lambda line: False, cap=50_000, timeout=30)
    assert out.output_truncated and not out.timed_out


def test_timeout_in_line_mode_still_kills_the_whole_tree(tmp_path):
    marker = tmp_path / "orphan.txt"
    child = f"import time; time.sleep(2.5); open(r'{marker}', 'w').write('alive')"
    script = (
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, '-c', {child!r}])\n"
        "print('started', flush=True)\n"
        "time.sleep(60)\n"
    )
    lines = []
    started = time.monotonic()
    out = run_lines(script, tmp_path=tmp_path, handler=lines.append, timeout=1)
    assert out.timed_out and lines == [b"started"]
    assert time.monotonic() - started < 20
    time.sleep(4)
    assert not marker.exists(), "grandchild survived the kill"


def test_stderr_is_still_collected_in_line_mode(tmp_path):
    out = run_lines(
        "import sys; sys.stderr.write('oops'); print('ok')",
        tmp_path=tmp_path, handler=lambda line: None,
    )
    assert out.stderr == "oops"


def test_build_args_asks_for_a_partial_message_stream_and_keeps_the_lockdown():
    args = runner.build_args(Path("claude.exe"), "sid-1")
    assert args[args.index("--output-format") + 1] == "stream-json"
    assert "--verbose" in args and "--include-partial-messages" in args
    assert args[args.index("--tools") + 1] == ""
    assert args[args.index("--permission-mode") + 1] == "dontAsk"
    for flag in ("--strict-mcp-config", "--disable-slash-commands"):
        assert flag in args
