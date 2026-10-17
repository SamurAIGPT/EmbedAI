"""JSON-RPC 2.0 over a child process's stdin/stdout, one JSON object per line
(the framing both `codex app-server` and ACP agents use).

Responses resolve the matching `request()`. Everything else the process sends
(notifications, and requests it makes of this app) goes onto `incoming` in the
order it arrived, so an adapter handles them one at a time and its events keep
the engine's order. A line that is not JSON is skipped. The stream's total
size is capped; past it the process is killed and `over_limit` is set.
"""

import asyncio
import json
import sys

from app.services.cli_probe import kill_group
from app.services.local_claude_runner import child_env

STDERR_TAIL_BYTES = 4000
# One line can carry a whole command's output.
LINE_LIMIT_BYTES = 16 * 1024 * 1024


class RpcError(Exception):
    def __init__(self, error: dict | None) -> None:
        error = error if isinstance(error, dict) else {}
        message = error.get("message")
        super().__init__(message if isinstance(message, str) and message else "the engine refused the request")
        self.code = error.get("code")
        self.data = error.get("data")


class RpcClosed(Exception):
    """The process ended (or was killed) before it answered."""


class StdioRpc:
    def __init__(self, proc: asyncio.subprocess.Process, *, max_bytes: int) -> None:
        self.proc = proc
        self.max_bytes = max_bytes
        self.incoming: asyncio.Queue = asyncio.Queue()
        self.over_limit = False
        self.stderr_tail = ""
        self._next_id = 1
        self._pending: dict[int, asyncio.Future] = {}
        self._closed = asyncio.get_running_loop().create_future()
        self._reader = asyncio.create_task(self._read())
        self._stderr = asyncio.create_task(self._read_stderr())

    @classmethod
    async def spawn(cls, args: list[str], *, cwd: str, max_bytes: int, env: dict | None = None) -> "StdioRpc":
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env if env is not None else child_env(),
            start_new_session=sys.platform != "win32",
            limit=LINE_LIMIT_BYTES,
        )
        return cls(proc, max_bytes=max_bytes)

    @property
    def closed(self) -> bool:
        return self._closed.done()

    async def wait_closed(self) -> None:
        await asyncio.shield(self._closed)

    def _write(self, message: dict) -> None:
        if self.closed or self.proc.stdin is None:
            raise RpcClosed()
        try:
            self.proc.stdin.write((json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8"))
        except (BrokenPipeError, ConnectionResetError, RuntimeError) as exc:
            raise RpcClosed() from exc

    async def request(self, method: str, params: dict | None = None, *, timeout: float | None = None):
        request_id = self._next_id
        self._next_id += 1
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            self._write({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}})
            done, _ = await asyncio.wait({future, self._closed}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
            if future in done:
                return future.result()
            if not done:
                raise asyncio.TimeoutError(method)
            raise RpcClosed()
        finally:
            self._pending.pop(request_id, None)

    def notify(self, method: str, params: dict | None = None) -> bool:
        try:
            self._write({"jsonrpc": "2.0", "method": method, "params": params or {}})
            return True
        except RpcClosed:
            return False

    def respond(self, request_id, result=None, *, error: dict | None = None) -> bool:
        message = {"jsonrpc": "2.0", "id": request_id}
        if error is not None:
            message["error"] = error
        else:
            message["result"] = result if result is not None else {}
        try:
            self._write(message)
            return True
        except RpcClosed:
            return False

    async def _read(self) -> None:
        total = 0
        try:
            while True:
                try:
                    line = await self.proc.stdout.readline()
                except (ValueError, asyncio.LimitOverrunError):
                    self.over_limit = True
                    kill_group(self.proc)
                    break
                if not line:
                    break
                total += len(line)
                if total > self.max_bytes:
                    self.over_limit = True
                    kill_group(self.proc)
                    break
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(message, dict):
                    continue
                if "method" not in message and "id" in message:
                    future = self._pending.get(message.get("id"))
                    if future is not None and not future.done():
                        if "error" in message and message["error"] is not None:
                            future.set_exception(RpcError(message["error"]))
                        else:
                            future.set_result(message.get("result"))
                    continue
                self.incoming.put_nowait(message)
        finally:
            if not self._closed.done():
                self._closed.set_result(None)
            self.incoming.put_nowait(None)  # end of stream

    async def _read_stderr(self) -> None:
        try:
            while True:
                chunk = await self.proc.stderr.read(8192)
                if not chunk:
                    return
                self.stderr_tail = (self.stderr_tail + chunk.decode("utf-8", errors="replace"))[-STDERR_TAIL_BYTES:]
        except Exception:  # noqa: BLE001
            return

    def kill(self) -> None:
        kill_group(self.proc)

    async def close(self) -> None:
        """Kill the process and wait (briefly) for the readers to finish."""
        if self.proc.returncode is None:
            try:
                if self.proc.stdin is not None:
                    self.proc.stdin.close()
            except Exception:  # noqa: BLE001
                pass
            self.kill()
        try:
            await asyncio.wait_for(self.proc.wait(), 5)
        except (asyncio.TimeoutError, ProcessLookupError):
            pass
        for task in (self._reader, self._stderr):
            if not task.done():
                task.cancel()
        await asyncio.gather(self._reader, self._stderr, return_exceptions=True)
