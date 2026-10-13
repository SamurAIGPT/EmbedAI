"""Runs the user's own `claude` CLI on this machine, chat-only.

Every run is launched with all built-in tools, MCP servers and skills turned
off, because approvals for local tool use do not exist yet. If a flag is
missing the CLI refuses to start; there is no fallback to an unrestricted run.
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from fastapi.concurrency import run_in_threadpool

from app.core.config import settings

# Verified against Claude Code 2.1.232: init reported tools=[], mcp_servers=[],
# skills=[], and a prompt asking to run a shell command wrote nothing.
LOCKDOWN_ARGS = [
    "--tools",
    "",
    "--strict-mcp-config",
    "--disable-slash-commands",
    "--permission-mode",
    "dontAsk",
]

_NPM_SHIM_SUFFIXES = {".cmd", ".bat", ".ps1"}
_NPM_REAL_BINARY = Path("node_modules") / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"

# App secrets that must never reach a child process, even though the chat-only
# lockdown leaves it nothing to read them with.
_APP_SECRET_ENV = (
    "BOAT_API_KEY",
    "TOKEN_ENCRYPTION_KEYS",
    "HOOK_TOKEN",
    "TELEGRAM_BOT_TOKEN",
    "POSTGRES_PASSWORD",
)

_STATUS_TIMEOUT_SECONDS = 20

# stream-json repeats the answer (deltas, snapshots, final result) and adds
# bookkeeping events, so the raw stream is allowed to be larger than the text
# it carries. The text itself is capped separately, at LOCAL_MAX_OUTPUT_BYTES.
RAW_STREAM_SIZE_FACTOR = 8

# Return True to stop the run (the process tree is killed and the run is
# reported as having produced too much output).
LineHandler = Callable[[bytes], bool | None]


class ClaudeCliUnavailable(Exception):
    pass


@dataclass
class RunOutput:
    stdout: str
    stderr: str
    exit_code: int | None
    timed_out: bool = False
    output_truncated: bool = False


@dataclass
class CliStatus:
    installed: bool
    version: str | None = None
    signed_in: bool | None = None
    error: str | None = None


def _resolve_shim(path: Path) -> Path | None:
    """On Windows the npm install exposes `claude.cmd`; killing only that
    wrapper can orphan the real process, so find the real executable."""
    candidate = path.parent / _NPM_REAL_BINARY
    return candidate if candidate.is_file() else None


def resolve_claude_executable() -> Path | None:
    configured = settings.LOCAL_CLAUDE_CLI_PATH.strip()
    if configured:
        path = Path(configured)
        if not path.is_file():
            return None
        if path.suffix.lower() in _NPM_SHIM_SUFFIXES:
            return _resolve_shim(path)
        return path

    found = shutil.which("claude.exe") if sys.platform == "win32" else None
    found = found or shutil.which("claude")
    if not found:
        return None
    path = Path(found)
    if path.suffix.lower() in _NPM_SHIM_SUFFIXES:
        return _resolve_shim(path)
    return path


def workspace_dir() -> Path:
    root = Path(settings.LOCAL_WORKSPACE_ROOT) if settings.LOCAL_WORKSPACE_ROOT else (
        Path.home() / ".open-dots" / "local"
    )
    workspace = root / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


def _child_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in _APP_SECRET_ENV}


def _kill_tree(proc: subprocess.Popen) -> None:
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=15,
            )
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, subprocess.TimeoutExpired):
        pass
    try:
        proc.kill()
    except OSError:
        pass


def run_blocking(
    args: list[str],
    *,
    stdin_text: str | None,
    cwd: Path,
    timeout_seconds: float,
    max_output_bytes: int,
    on_stdout_line: LineHandler | None = None,
) -> RunOutput:
    """Run a process to completion with a time limit and an output cap,
    killing its whole process tree if either is exceeded.

    With `on_stdout_line`, stdout is handed over one line at a time, as it
    arrives (split on raw bytes at b"\n", a trailing b"\r" dropped), and is not
    kept in the returned `RunOutput`."""
    popen_kwargs: dict = {}
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    else:
        popen_kwargs["start_new_session"] = True

    proc = subprocess.Popen(
        args,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        env=_child_env(),
        **popen_kwargs,
    )

    state = {"timed_out": False, "truncated": False}
    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []

    def on_timeout() -> None:
        state["timed_out"] = True
        _kill_tree(proc)

    def drain(stream, chunks: list[bytes], *, fatal_on_overflow: bool) -> None:
        total = 0
        while True:
            chunk = stream.read(65536)
            if not chunk:
                return
            total += len(chunk)
            if total > max_output_bytes:
                if fatal_on_overflow:
                    state["truncated"] = True
                    _kill_tree(proc)
                return
            chunks.append(chunk)

    def drain_lines(stream) -> None:
        total = 0
        pending = b""

        def handle(line: bytes) -> bool:
            try:
                return bool(on_stdout_line(line.removesuffix(b"\r")))
            except Exception:
                return False  # a faulty handler must not end the run

        while True:
            # read1 returns what is available; read(n) would wait for n bytes
            # and defeat streaming.
            chunk = stream.read1(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > max_output_bytes:
                state["truncated"] = True
                _kill_tree(proc)
                return
            pending += chunk
            *lines, pending = pending.split(b"\n")
            for line in lines:
                if handle(line):
                    state["truncated"] = True
                    _kill_tree(proc)
                    return
        if pending and handle(pending):
            state["truncated"] = True
            _kill_tree(proc)

    timer = threading.Timer(timeout_seconds, on_timeout)
    stdout_reader = (
        threading.Thread(target=drain_lines, args=(proc.stdout,))
        if on_stdout_line is not None
        else threading.Thread(
            target=drain, args=(proc.stdout, stdout_chunks), kwargs={"fatal_on_overflow": True}
        )
    )
    readers = [
        stdout_reader,
        threading.Thread(target=drain, args=(proc.stderr, stderr_chunks), kwargs={"fatal_on_overflow": False}),
    ]
    timer.start()
    for reader in readers:
        reader.start()
    try:
        try:
            if stdin_text is not None:
                proc.stdin.write(stdin_text.encode("utf-8"))
            proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        proc.wait()
    finally:
        timer.cancel()
        # A killed child's grandchildren could still hold the pipes open.
        for reader in readers:
            reader.join(timeout=5)
        if proc.poll() is None:
            _kill_tree(proc)

    return RunOutput(
        stdout=b"".join(stdout_chunks).decode("utf-8", errors="replace"),
        stderr=b"".join(stderr_chunks).decode("utf-8", errors="replace"),
        exit_code=proc.returncode,
        timed_out=state["timed_out"],
        output_truncated=state["truncated"],
    )


def build_args(executable: Path, session_id: str) -> list[str]:
    # The prompt goes in on stdin, never on the command line.
    return [
        str(executable),
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--include-partial-messages",
        "--session-id",
        session_id,
        *LOCKDOWN_ARGS,
    ]


def _status_blocking() -> CliStatus:
    executable = resolve_claude_executable()
    if executable is None:
        return CliStatus(
            installed=False,
            error=(
                "Claude CLI not found. Install Claude Code, or set LOCAL_CLAUDE_CLI_PATH "
                "to the full path of the real claude executable."
            ),
        )

    cwd = workspace_dir()

    def call(*extra: str) -> RunOutput:
        return run_blocking(
            [str(executable), *extra],
            stdin_text=None,
            cwd=cwd,
            timeout_seconds=_STATUS_TIMEOUT_SECONDS,
            max_output_bytes=100_000,
        )

    version = call("--version")
    if version.exit_code != 0:
        return CliStatus(installed=False, error="The Claude CLI did not start.")

    status = CliStatus(installed=True, version=version.stdout.strip() or None)
    auth = call("auth", "status", "--json")
    try:
        # Only loggedIn is read: the payload also carries the account's email
        # and organisation, which must not be forwarded or logged.
        status.signed_in = bool(json.loads(auth.stdout).get("loggedIn"))
    except (json.JSONDecodeError, AttributeError):
        status.signed_in = None
        status.error = "Could not determine whether Claude is signed in."
        return status
    if not status.signed_in:
        status.error = "Claude is not signed in. Run `claude auth login` in a terminal first."
    return status


async def get_status() -> CliStatus:
    return await run_in_threadpool(_status_blocking)


def _run_blocking(
    prompt: str, session_id: str, on_stdout_line: LineHandler | None
) -> RunOutput:
    executable = resolve_claude_executable()
    if executable is None:
        raise ClaudeCliUnavailable("Claude CLI not found.")
    return run_blocking(
        build_args(executable, session_id),
        stdin_text=prompt,
        cwd=workspace_dir(),
        timeout_seconds=settings.LOCAL_TASK_TIMEOUT_SECONDS,
        max_output_bytes=settings.LOCAL_MAX_OUTPUT_BYTES * RAW_STREAM_SIZE_FACTOR,
        on_stdout_line=on_stdout_line,
    )


async def run_prompt(
    prompt: str, session_id: str, on_stdout_line: LineHandler | None = None
) -> RunOutput:
    return await run_in_threadpool(_run_blocking, prompt, session_id, on_stdout_line)
