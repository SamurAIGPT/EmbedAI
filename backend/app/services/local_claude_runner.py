"""Runs the user's own `claude` CLI on this machine.

By default a run is chat-only: all built-in tools, MCP servers and skills are
turned off. If a flag is missing the CLI refuses to start; there is no fallback
to an unrestricted run.

With tools on (`LOCAL_TOOLS_ENABLED`, a `RunControl` passed in) the CLI gets a
fixed set of tools in permission mode `default` and asks this app, over its
stdin/stdout, before each call it would prompt a person about. No run ever
skips permissions or pre-approves anything.
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
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

# With tools on, the CLI asks this app (over stdin/stdout) before every tool
# call it would otherwise prompt for in a terminal, and waits for the answer.
# Only these tools exist: no web access, sub-agents, notebooks or MCP.
TOOL_NAMES = "Read,Write,Edit,Glob,Grep,Bash"
TOOLS_ARGS = [
    "--input-format",
    "stream-json",
    "--permission-prompt-tool",
    "stdio",
    "--permission-mode",
    "default",
    "--tools",
    TOOL_NAMES,
    "--strict-mcp-config",
    "--disable-slash-commands",
]

_NPM_SHIM_SUFFIXES ={".cmd", ".bat", ".ps1"}
_NPM_REAL_BINARY = Path("node_modules") / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"

# App secrets that must never reach a child process. Chat-only runs have no
# tool to read them with, but with tools on an approved shell command could.
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


class RunControl:
    """Lets the caller talk to a running process: write lines to its stdin,
    close stdin, and pause the run's time limit while a human decides.

    Passing one to `run_blocking` keeps stdin open after the first message
    (the CLI reads approval answers from it) instead of closing it.
    All methods are safe to call from any thread and never raise.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._stdin_closed = False
        self._timeout_seconds = 0.0
        self._on_timeout: Callable[[], None] | None = None
        self._timer: threading.Timer | None = None
        self._deadline = 0.0
        self._pauses = 0
        self._remaining = 0.0
        self._finished = False
        self._killed = False

    def _attach(self, proc: subprocess.Popen, timeout_seconds: float, on_timeout) -> None:
        with self._lock:
            self._proc = proc
            self._timeout_seconds = timeout_seconds
            self._on_timeout = on_timeout
            self._start_timer(timeout_seconds)
            killed = self._killed
        if killed:  # kill() was called before the process existed
            _kill_tree(proc)

    @property
    def killed(self) -> bool:
        return self._killed

    def kill(self) -> None:
        """Kill the process and everything it started. Safe to call from any
        thread, at any time (before the process starts, while it runs, after it
        ended). Used when the run no longer owns its turn."""
        with self._lock:
            self._killed = True
            proc = self._proc
        if proc is not None:
            _kill_tree(proc)

    def _start_timer(self, seconds: float) -> None:
        self._deadline = time.monotonic() + seconds
        self._timer = threading.Timer(seconds, self._on_timeout)
        self._timer.daemon = True
        self._timer.start()

    def send(self, line: str) -> bool:
        """Write one line to the process. False if it can no longer be written."""
        with self._lock:
            proc = self._proc
            if proc is None or proc.stdin is None or self._stdin_closed:
                return False
            try:
                proc.stdin.write((line.rstrip("\n") + "\n").encode("utf-8"))
                proc.stdin.flush()
                return True
            except (BrokenPipeError, OSError, ValueError):
                return False

    def close_stdin(self) -> None:
        with self._lock:
            self._close_stdin_locked()

    def _close_stdin_locked(self) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None or self._stdin_closed:
            return
        self._stdin_closed = True
        try:
            proc.stdin.close()
        except (BrokenPipeError, OSError, ValueError):
            pass

    def pause_deadline(self) -> None:
        """Stop the clock (nested calls are counted). Waiting for a person is
        not Claude running too long; the wait has its own limit."""
        with self._lock:
            if self._finished:
                return
            self._pauses += 1
            if self._pauses == 1 and self._timer is not None:
                self._timer.cancel()
                self._remaining = max(self._deadline - time.monotonic(), 0.0)

    def resume_deadline(self) -> None:
        with self._lock:
            if self._finished or self._pauses == 0:
                return
            self._pauses -= 1
            if self._pauses == 0:
                self._start_timer(self._remaining)

    def _finish(self) -> None:
        with self._lock:
            self._finished = True
            if self._timer is not None:
                self._timer.cancel()
            self._close_stdin_locked()


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
    control: RunControl | None = None,
    keep_stdin_open: bool | None = None,
) -> RunOutput:
    """Run a process to completion with a time limit and an output cap,
    killing its whole process tree if either is exceeded.

    With `on_stdout_line`, stdout is handed over one line at a time, as it
    arrives (split on raw bytes at b"\n", a trailing b"\r" dropped), and is not
    kept in the returned `RunOutput`.

    With `control`, `stdin_text` is sent as one line and stdin stays open so
    the caller can keep writing; the caller (or the end of the run) closes it,
    and the time limit is owned by `control` so it can be paused. With
    `keep_stdin_open=False` a control only provides the kill switch and the
    pausable time limit: the text is written and stdin closed, as without one."""
    if keep_stdin_open is None:
        keep_stdin_open = control is not None
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

    timer = None if control is not None else threading.Timer(timeout_seconds, on_timeout)
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
    if control is not None:
        control._attach(proc, timeout_seconds, on_timeout)
    else:
        timer.start()
    for reader in readers:
        reader.start()
    try:
        if control is not None and keep_stdin_open:
            if stdin_text is not None:
                control.send(stdin_text)
        else:
            try:
                if stdin_text is not None:
                    proc.stdin.write(stdin_text.encode("utf-8"))
                proc.stdin.close()
            except (BrokenPipeError, OSError):
                pass
        proc.wait()
    finally:
        if control is not None:
            control._finish()
        else:
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


def build_args(
    executable: Path, session_id: str, *, tools: bool = False, resume: bool = False
) -> list[str]:
    # The prompt goes in on stdin, never on the command line. `resume` continues
    # the conversation that already holds `session_id`; otherwise the CLI is
    # told to create it under that id.
    common = [
        str(executable),
        "-p",
        "--verbose",
        "--include-partial-messages",
        "--resume" if resume else "--session-id",
        session_id,
    ]
    if not tools:
        return [*common, "--output-format", "stream-json", *LOCKDOWN_ARGS]
    return [*common, "--output-format", "stream-json", *TOOLS_ARGS]


def user_message_line(prompt: str) -> str:
    """The prompt as the one stream-json message a tools run starts with."""
    return json.dumps(
        {"type": "user", "message": {"role": "user", "content": prompt}},
        ensure_ascii=False,
    )


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
    prompt: str,
    session_id: str,
    on_stdout_line: LineHandler | None,
    control: RunControl | None,
    resume: bool = False,
    tools: bool | None = None,
) -> RunOutput:
    executable = resolve_claude_executable()
    if executable is None:
        raise ClaudeCliUnavailable("Claude CLI not found.")
    if tools is None:
        tools = control is not None
    extra = {"resume": True} if resume else {}
    args = (
        build_args(executable, session_id, tools=True, **extra)
        if tools
        else build_args(executable, session_id, **extra)
    )
    return run_blocking(
        args,
        stdin_text=user_message_line(prompt) if tools else prompt,
        cwd=workspace_dir(),
        timeout_seconds=settings.LOCAL_TASK_TIMEOUT_SECONDS,
        max_output_bytes=settings.LOCAL_MAX_OUTPUT_BYTES * RAW_STREAM_SIZE_FACTOR,
        on_stdout_line=on_stdout_line,
        control=control,
        keep_stdin_open=tools if control is not None else None,
    )


async def run_prompt(
    prompt: str,
    session_id: str,
    on_stdout_line: LineHandler | None = None,
    control: RunControl | None = None,
    *,
    resume: bool = False,
    tools: bool | None = None,
) -> RunOutput:
    """Chat-only by default. Passing a `RunControl` runs with tools on: the CLI
    then asks over stdin/stdout before each tool call, and the caller answers
    through `control`. `tools=False` with a control gives a chat-only run the
    caller can still kill; `resume=True` continues the session `session_id`
    instead of creating it."""
    return await run_in_threadpool(
        _run_blocking, prompt, session_id, on_stdout_line, control, resume, tools
    )
