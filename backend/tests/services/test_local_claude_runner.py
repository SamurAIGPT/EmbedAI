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
