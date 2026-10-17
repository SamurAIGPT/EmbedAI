"""Short, safe runs of an engine's CLI for the engine registry (`--version`,
`login status`).

A probe never hangs and never raises: the process gets no stdin, its output is
capped, and when it does not finish in time its whole process group is killed
and the probe reports that it timed out. Output is returned to the caller to
read one fact from; it is never logged, because a status command can print an
account's email or organisation.
"""

import asyncio
import os
import signal
import sys
from dataclasses import dataclass

from app.core import engine_processes
from app.services.local_claude_runner import child_env

MAX_PROBE_OUTPUT_BYTES = 64_000


@dataclass
class ProbeRun:
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool = False
    # The process could not be started at all (missing, not executable).
    failed_to_start: str | None = None

    @property
    def ok(self) -> bool:
        return not self.timed_out and self.failed_to_start is None and self.exit_code == 0


def kill_group(proc: asyncio.subprocess.Process) -> None:
    """Kill a process started with `start_new_session=True` and everything it
    started. Never raises."""
    try:
        if sys.platform == "win32":
            proc.kill()
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        proc.kill()
    except (ProcessLookupError, OSError):
        pass


async def run(args: list[str], *, timeout: float, cwd: str | None = None) -> ProbeRun:
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd or os.path.expanduser("~"),
            env=child_env(),
            start_new_session=sys.platform != "win32",
        )
    except (OSError, ValueError) as exc:
        return ProbeRun(None, "", "", failed_to_start=exc.__class__.__name__)
    await asyncio.to_thread(engine_processes.record, proc.pid, args[0])
    try:
        return await _finish_probe(proc, timeout)
    finally:
        engine_processes.forget(proc.pid)


async def _finish_probe(proc: asyncio.subprocess.Process, timeout: float) -> ProbeRun:

    async def read(stream) -> bytes:
        data = b""
        while len(data) <= MAX_PROBE_OUTPUT_BYTES:
            chunk = await stream.read(8192)
            if not chunk:
                break
            data += chunk
        return data[:MAX_PROBE_OUTPUT_BYTES]

    async def finish():
        out, err = await asyncio.gather(read(proc.stdout), read(proc.stderr))
        await proc.wait()
        return out, err

    try:
        out, err = await asyncio.wait_for(finish(), timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
        kill_group(proc)
        try:
            await asyncio.wait_for(proc.wait(), 2)
        except (asyncio.TimeoutError, ProcessLookupError):
            pass
        if isinstance(exc, asyncio.CancelledError):
            raise
        return ProbeRun(None, "", "", timed_out=True)
    return ProbeRun(
        proc.returncode,
        out.decode("utf-8", errors="replace"),
        err.decode("utf-8", errors="replace"),
    )
