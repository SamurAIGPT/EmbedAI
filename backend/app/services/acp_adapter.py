"""Any agent CLI that speaks the Agent Client Protocol (ACP: JSON-RPC 2.0, one
JSON object per line on stdio) as a thread engine, from one saved command
(`gemini --acp`, `npx -y some-agent acp`, your own wrapper).

One agent process per turn: `initialize`, then `session/new` (first turn) or
`session/load` (later turns, when the agent says it can load sessions; the
ACP session id is kept in the thread's `provider_state`), then
`session/prompt`, whose result (`stopReason`) ends the turn. History replayed
by `session/load` arrives before the prompt is sent and is not recorded. An
agent that cannot load sessions starts a new one each turn and is given the
conversation so far as text, so it still answers in context.

The command is split like a shell would split it but never run through a
shell. The agent is signed in from a terminal; this app has no sign-in step
for it. It gets this app's environment without the app's own secrets, no file
system or terminal access through the client (both advertised as off), and
no MCP servers.

Approvals: the agent decides what it asks about; every `session/request_permission`
goes through the approval gate. Only an option the agent marks `allow_once`
approves (never `allow_always`, which would grant more than was asked), and a
Deny picks `reject_once`; with no such option the request is cancelled. In
plan mode, or with tools off, every request is refused without a card; in
accept_edits an edit whose every location is inside the workspace is allowed
without one. What an agent does without asking is up to the agent.
"""

import asyncio
import json
import shlex
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from app.core import app_settings
from app.core.logging import get_logger
from app.services import local_claude_runner
from app.services.local_approval_broker import MAX_REVIEWABLE_INPUT_CHARS
from app.services.provider_adapter import (
    ALLOW,
    LOST,
    Capabilities,
    EngineProbe,
    ModelOption,
    ProviderStatus,
    TurnContext,
    TurnOutcome,
)
from app.services.stdio_rpc import RpcClosed, RpcError, StdioRpc
from app.services.thread_store import cap_tool_output
from app.services.turn_clock import TurnClock

logger = get_logger(__name__)

PROTOCOL_VERSION = 1
MAX_ERROR_CHARS = 2000
STARTUP_TIMEOUT_SECONDS = 120.0
PROBE_INIT_TIMEOUT_SECONDS = 8.0
CANCEL_GRACE_SECONDS = 3.0
RAW_STREAM_SIZE_FACTOR = 8
MAX_COMMAND_CHARS = 1000
MAX_LABEL_CHARS = 60
# How much of the conversation an agent that cannot load sessions is given.
HISTORY_CHARS = 60_000

NOT_CONFIGURED = "Set the agent's command in Settings → Engines, for example `gemini --acp`."


class InvalidConfig(ValueError):
    pass


def split_command(command: str) -> list[str]:
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        raise InvalidConfig(f"The command could not be read: {exc}.") from None
    if not argv:
        raise InvalidConfig("The command is empty.")
    return argv


def resolve_command(command: str) -> list[str] | None:
    """The argv to start, with the program found on PATH, or None when the
    program is not there."""
    argv = split_command(command)
    program = Path(argv[0]).expanduser()
    if program.is_absolute():
        return [str(program), *argv[1:]] if program.is_file() else None
    found = shutil.which(argv[0])
    return [found, *argv[1:]] if found else None


def _clip(message: str) -> str:
    message = (message or "").strip()
    return message if len(message) <= MAX_ERROR_CHARS else message[:MAX_ERROR_CHARS] + "…"


def _inside(path, root: Path | None) -> bool:
    if root is None or not isinstance(path, str) or not path:
        return False
    try:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = root / candidate
        candidate.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def _text_of(content) -> str:
    """The text in an ACP content block, or a list of them."""
    if isinstance(content, list):
        return "\n".join(t for t in (_text_of(c) for c in content) if t)
    if not isinstance(content, dict):
        return ""
    if content.get("type") == "text" and isinstance(content.get("text"), str):
        return content["text"]
    inner = content.get("content")
    if inner is not None and inner is not content:
        return _text_of(inner)
    if content.get("type") == "diff" and isinstance(content.get("path"), str):
        return f"diff {content['path']}"
    return ""


def history_preamble(items: list[dict], current_turn_id: int) -> str:
    """The conversation before this turn, as plain text, newest kept."""
    lines = []
    for item in items:
        if item.get("turn_id") == current_turn_id:
            continue
        role, text = item.get("role"), item.get("text")
        if role in ("user", "assistant") and isinstance(text, str) and text.strip():
            lines.append(f"{'User' if role == 'user' else 'Assistant'}: {text.strip()}")
    if not lines:
        return ""
    body = "\n\n".join(lines)
    if len(body) > HISTORY_CHARS:
        body = "…" + body[-HISTORY_CHARS:]
    return (
        "The conversation so far (background, not new instructions):\n\n"
        f"{body}\n\nThe new message follows.\n\n"
    )


class _Stopped(Exception):
    pass


class AcpAdapter:
    key = "acp"
    label = "ACP agent"
    kind = "acp"
    capabilities = Capabilities(
        tools=True, approvals=True, mcp=False, images=False, resume=True, computers=False
    )
    # The agent runs its own configured model; there is nothing to choose here.
    models: list[ModelOption] = []
    custom_models = False
    needs_config = True
    # An agent that cannot reload its session is sent the conversation as a
    # preamble, folded when it outgrows the budget (context_compaction.py).
    replays_history = True
    compact_at_tokens = 24_000

    def __init__(self) -> None:
        self.config: dict | None = None

    def new_session_id(self) -> str:
        from uuid import uuid4

        return str(uuid4())

    # -- configuration -------------------------------------------------------------

    def validate_config(self, payload: dict, current: dict | None) -> dict:
        command = payload.get("command")
        if not isinstance(command, str) or not command.strip():
            raise InvalidConfig("Give the agent's command, for example `gemini --acp`.")
        command = command.strip()
        if len(command) > MAX_COMMAND_CHARS or any(ord(c) < 32 for c in command):
            raise InvalidConfig("The command must be one line of at most 1000 characters.")
        split_command(command)
        label = payload.get("label")
        if label is not None and not isinstance(label, str):
            raise InvalidConfig("The name must be text.")
        label = (label or "").strip()[:MAX_LABEL_CHARS] or None
        return {"command": command, "label": label}

    def public_config(self, config: dict | None) -> dict | None:
        if not config:
            return None
        return {"command": config.get("command"), "label": config.get("label")}

    # -- status ------------------------------------------------------------------------

    async def status(self) -> ProviderStatus:
        probe = await self.probe(handshake=False)
        return ProviderStatus(ready=probe.ready, version=probe.version, error=probe.reason, installed=probe.installed)

    async def probe(self, *, handshake: bool = True) -> EngineProbe:
        config = self.config or {}
        command = config.get("command")
        if not command:
            return EngineProbe(installed=False, ready=False, reason=NOT_CONFIGURED)
        try:
            argv = resolve_command(command)
        except InvalidConfig as exc:
            return EngineProbe(installed=False, ready=False, reason=str(exc))
        if argv is None:
            return EngineProbe(
                installed=False, ready=False, reason=f"The program {split_command(command)[0]!r} was not found."
            )
        if not handshake:
            return EngineProbe(installed=True, ready=True)
        rpc = None
        try:
            rpc = await StdioRpc.spawn(argv, cwd=str(Path.home()), max_bytes=2_000_000)
            result = await asyncio.wait_for(rpc.request("initialize", _initialize_params()), PROBE_INIT_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            return EngineProbe(
                installed=True, ready=False,
                reason=f"The agent did not answer the ACP handshake within {PROBE_INIT_TIMEOUT_SECONDS:g} seconds.",
            )
        except (OSError, RpcError, RpcClosed) as exc:
            return EngineProbe(installed=True, ready=False, reason=f"The agent did not answer the ACP handshake ({exc.__class__.__name__}).")
        finally:
            if rpc is not None:
                await rpc.close()
        info = (result or {}).get("agentInfo") if isinstance(result, dict) else None
        version = None
        if isinstance(info, dict):
            name, number = info.get("name"), info.get("version")
            version = " ".join(str(v) for v in (name, number) if isinstance(v, str) and v) or None
        return EngineProbe(installed=True, ready=True, version=version)

    # -- a turn ---------------------------------------------------------------------------

    async def run_turn(self, ctx: TurnContext, sink, gate) -> TurnOutcome:
        config = ctx.engine_config or self.config or {}
        command = config.get("command")
        if not command:
            return TurnOutcome("failed", error=NOT_CONFIGURED, error_code="engine_not_configured")
        try:
            argv = resolve_command(command)
        except InvalidConfig as exc:
            return TurnOutcome("failed", error=str(exc), error_code="engine_not_configured")
        if argv is None:
            return TurnOutcome("failed", error="The agent's program was not found.", error_code="cli_unavailable")
        if ctx.computer is not None:
            return TurnOutcome(
                "failed", error="This engine cannot run its tools in an agent computer.",
                error_code="engine_without_computers",
            )
        return await _AcpTurn(ctx, sink, gate, argv).go()


def _initialize_params() -> dict:
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "clientInfo": {"name": "open-dots", "version": "0"},
        "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False},
    }


@dataclass(eq=False)
class _AcpTurn:
    ctx: TurnContext
    sink: object
    gate: object
    argv: list[str]
    rpc: StdioRpc | None = None
    session_id: str | None = None
    session_started: bool = False
    prompt_sent: bool = False
    stop_reason: str | None = None
    prompt_error: str | None = None
    finished: bool = False
    timed_out: bool = False
    over_text: bool = False
    text_bytes: int = 0
    buffer: str = ""
    messages: list = field(default_factory=list)
    usage: dict | None = None
    answering: set = field(default_factory=set)
    requests: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.tools = bool(app_settings.get("LOCAL_TOOLS_ENABLED"))
        self.mode = self.ctx.approval_mode or "ask"
        self.max_text = app_settings.get("LOCAL_MAX_OUTPUT_BYTES")
        self.clock = TurnClock(app_settings.get("LOCAL_TASK_TIMEOUT_SECONDS"))
        self.workspace = self.ctx.workspace or local_claude_runner.workspace_dir()
        state = self.ctx.provider_state if isinstance(self.ctx.provider_state, dict) else {}
        self.saved_session = state.get("acp_session_id") if isinstance(state.get("acp_session_id"), str) else None

    async def go(self) -> TurnOutcome:
        loop = asyncio.get_running_loop()
        self.aborted = asyncio.Event()
        self.ctx.on_abort(lambda: loop.call_soon_threadsafe(self.aborted.set))
        try:
            self.rpc = await StdioRpc.spawn(
                self.argv, cwd=str(self.workspace), max_bytes=self.max_text * RAW_STREAM_SIZE_FACTOR
            )
        except (OSError, ValueError):
            return TurnOutcome("failed", error="The agent's command could not be started.", error_code="cli_unavailable")
        try:
            early = await self._start()
            if early is not None:
                return early
            await self._prompt()
        except _Stopped:
            await self._cancel()
        except Exception:  # noqa: BLE001
            logger.exception("ACP run crashed", thread_id=self.ctx.thread_id)
            return self._outcome(crashed=True)
        finally:
            for task in list(self.answering):
                task.cancel()
            if self.answering:
                await asyncio.gather(*self.answering, return_exceptions=True)
            await self.rpc.close()
        return self._outcome()

    def _common(self) -> dict:
        return {
            "model": None,
            "usage": self.usage,
            "session_state": "active" if self.session_started else None,
            "provider_state": {"acp_session_id": self.session_id} if self.session_id else None,
        }

    def _failed(self, error: str, code: str, **more) -> TurnOutcome:
        return TurnOutcome("failed", error=_clip(error), error_code=code, **{**self._common(), **more})

    async def _call(self, method: str, params: dict, *, handle_incoming: bool = False):
        """A request that a Stop or the time limit can cut short. With
        `handle_incoming`, what the agent sends meanwhile is handled."""
        request = asyncio.create_task(self.rpc.request(method, params, timeout=None if handle_incoming else STARTUP_TIMEOUT_SECONDS))
        stop = asyncio.create_task(self.aborted.wait())
        pending_get: asyncio.Task | None = None
        try:
            while True:
                waiting = {request, stop}
                if handle_incoming:
                    if pending_get is None:
                        pending_get = asyncio.create_task(self.rpc.incoming.get())
                    waiting.add(pending_get)
                left = self.clock.remaining()
                done, _ = await asyncio.wait(
                    waiting, timeout=1.0 if left is None else max(0.0, min(left, 1.0)),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if pending_get is not None and pending_get in done:
                    message = pending_get.result()
                    pending_get = None
                    if message is not None:
                        await self._incoming(message)
                        if self.over_text or self.rpc.over_limit:
                            raise _Stopped()
                    continue
                if request in done:
                    # Anything that arrived before the answer is handled first.
                    while handle_incoming and not self.rpc.incoming.empty():
                        message = self.rpc.incoming.get_nowait()
                        if message is not None:
                            await self._incoming(message)
                    return request.result()
                if stop in done:
                    raise _Stopped()
                if self.clock.expired:
                    self.timed_out = True
                    raise _Stopped()
        finally:
            stop.cancel()
            if pending_get is not None:
                pending_get.cancel()
            if not request.done():
                request.cancel()
            await asyncio.gather(request, stop, return_exceptions=True)

    async def _start(self) -> TurnOutcome | None:
        try:
            init = await self._call("initialize", _initialize_params())
        except (RpcError, RpcClosed, asyncio.TimeoutError) as exc:
            return self._failed(self._why(exc, "The agent did not answer the ACP handshake."), "cli_error")
        init = init if isinstance(init, dict) else {}
        caps = init.get("agentCapabilities") if isinstance(init.get("agentCapabilities"), dict) else {}
        can_load = caps.get("loadSession") is True
        self.preamble = ""
        if self.ctx.resume and self.saved_session and can_load:
            try:
                # Replayed history arrives before the prompt and is dropped.
                await self._call(
                    "session/load",
                    {"sessionId": self.saved_session, "cwd": str(self.workspace), "mcpServers": []},
                    handle_incoming=True,
                )
            except RpcError as exc:
                return self._failed(
                    "The agent could not load this conversation's session, so it cannot continue it. "
                    f"Start a new session to go on. ({exc})",
                    "session_resume_failed",
                    session_state="broken",
                )
            except (RpcClosed, asyncio.TimeoutError) as exc:
                return self._failed(self._why(exc, "The agent did not load the session."), "cli_error")
            self.session_id = self.saved_session
        else:
            if self.ctx.resume:
                self.preamble = history_preamble(await self.ctx.history(), self.ctx.turn_id)
            try:
                created = await self._call(
                    "session/new", {"cwd": str(self.workspace), "mcpServers": []}, handle_incoming=True
                )
            except RpcError as exc:
                return self._failed(f"The agent refused to start a session: {exc}", "acp_error")
            except (RpcClosed, asyncio.TimeoutError) as exc:
                return self._failed(self._why(exc, "The agent did not start a session."), "cli_error")
            session_id = created.get("sessionId") if isinstance(created, dict) else None
            if not isinstance(session_id, str) or not session_id:
                return self._failed("The agent did not return a session id.", "acp_error")
            self.session_id = session_id
        self.session_started = True
        return None

    def _why(self, exc: Exception, default: str) -> str:
        if isinstance(exc, RpcError):
            return f"{default} ({exc})"
        tail = self.rpc.stderr_tail.strip() if self.rpc is not None else ""
        return f"{default} {tail}".strip() if tail else default

    def _prompt_text(self) -> str:
        parts = []
        if self.ctx.instructions:
            parts.append(f"Standing instructions for this conversation:\n{self.ctx.instructions}\n\n")
        parts.append(self.preamble)
        parts.append(self.ctx.prompt)
        return "".join(parts)

    async def _prompt(self) -> None:
        self.prompt_sent = True
        try:
            result = await self._call(
                "session/prompt",
                {"sessionId": self.session_id, "prompt": [{"type": "text", "text": self._prompt_text()}]},
                handle_incoming=True,
            )
        except RpcError as exc:
            self.prompt_error = str(exc)
            self.finished = True
            await self._flush()
            return
        except RpcClosed:
            await self._flush()
            return
        result = result if isinstance(result, dict) else {}
        self.stop_reason = result.get("stopReason") if isinstance(result.get("stopReason"), str) else None
        usage = result.get("usage") if isinstance(result.get("usage"), dict) else None
        if usage:
            self.usage = {
                ours: usage[theirs]
                for theirs, ours in (("inputTokens", "input_tokens"), ("outputTokens", "output_tokens"))
                if isinstance(usage.get(theirs), int)
            } or None
        self.finished = True
        await self._flush()

    async def _cancel(self) -> None:
        if self.rpc is None or self.rpc.closed or not self.session_id:
            return
        self.rpc.notify("session/cancel", {"sessionId": self.session_id})
        try:
            await asyncio.wait_for(self.rpc.wait_closed(), CANCEL_GRACE_SECONDS)
        except asyncio.TimeoutError:
            pass

    # -- what the agent sends ------------------------------------------------------------

    async def _emit(self, type_: str, data: dict) -> None:
        if self.sink.lost:
            return
        try:
            await self.sink.emit(type_, data)
        except Exception:  # noqa: BLE001
            logger.exception("could not record an ACP event", thread_id=self.ctx.thread_id)

    async def _flush(self) -> None:
        if self.buffer.strip():
            text = self.buffer
            self.messages.append(text)
            await self._emit("assistant.message", {"text": text})
        self.buffer = ""

    async def _incoming(self, message: dict) -> None:
        if "id" in message and "method" in message:
            await self._server_request(message)
            return
        if message.get("method") != "session/update" or not self.prompt_sent:
            return
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        if params.get("sessionId") not in (None, self.session_id):
            return
        update = params.get("update") if isinstance(params.get("update"), dict) else {}
        kind = update.get("sessionUpdate")
        if kind == "agent_message_chunk":
            text = _text_of(update.get("content"))
            if text:
                self.buffer += text
                self.text_bytes += len(text.encode("utf-8"))
                if self.text_bytes > self.max_text:
                    self.over_text = True
                if not self.sink.lost:
                    self.sink.delta(text)
        elif kind == "tool_call":
            await self._flush()
            call_id = update.get("toolCallId")
            if not isinstance(call_id, str):
                return
            raw = update.get("rawInput") if isinstance(update.get("rawInput"), dict) else {}
            title = update.get("title") if isinstance(update.get("title"), str) else (update.get("kind") or "tool")
            await self._emit(
                "tool.started",
                {"call_id": call_id, "name": str(update.get("kind") or "tool"), "summary": str(title)[:200], "input": raw},
            )
            if update.get("status") in ("completed", "failed"):
                await self._tool_done(update)
        elif kind == "tool_call_update" and update.get("status") in ("completed", "failed"):
            await self._tool_done(update)

    async def _tool_done(self, update: dict) -> None:
        call_id = update.get("toolCallId")
        if not isinstance(call_id, str):
            return
        raw = update.get("rawOutput")
        output = _text_of(update.get("content")) or (
            raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False) if raw is not None else ""
        )
        stored, truncated = cap_tool_output(output)
        await self._emit(
            "tool.completed",
            {"call_id": call_id, "ok": update.get("status") == "completed", "output": stored, "truncated": truncated},
        )

    # -- what the agent asks -------------------------------------------------------------------

    async def _server_request(self, message: dict) -> None:
        method, rid = message.get("method"), message.get("id")
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        if method != "session/request_permission":
            # fs/* and terminal/* were advertised as off; anything else is unknown.
            self.rpc.respond(rid, error={"code": -32601, "message": "Not supported by this client."})
            return
        options = [o for o in params.get("options") or [] if isinstance(o, dict) and isinstance(o.get("optionId"), str)]

        def pick(kind: str) -> str | None:
            return next((o["optionId"] for o in options if o.get("kind") == kind), None)

        allow_id = pick("allow_once")
        reject_id = pick("reject_once") or pick("reject_always")

        def payload(allow: bool) -> dict:
            chosen = allow_id if allow else reject_id
            if chosen is None:
                return {"outcome": {"outcome": "cancelled"}}
            return {"outcome": {"outcome": "selected", "optionId": chosen}}

        call = params.get("toolCall") if isinstance(params.get("toolCall"), dict) else {}
        kind = call.get("kind") if isinstance(call.get("kind"), str) else "other"
        raw = call.get("rawInput") if isinstance(call.get("rawInput"), dict) else {}
        if not self.tools or self.mode == "plan":
            self.rpc.respond(rid, payload(False))
            return
        locations = [loc.get("path") for loc in call.get("locations") or [] if isinstance(loc, dict)]
        if (
            self.mode == "accept_edits"
            and kind == "edit"
            and locations
            and all(_inside(path, self.workspace) for path in locations)
            and allow_id is not None
        ):
            self.rpc.respond(rid, payload(True))
            return
        command = raw.get("command")
        if kind == "execute" and isinstance(command, str) and command.strip():
            tool, tool_input, approval_kind = "Bash", {"command": command}, "command"
        else:
            tool = f"acp:{kind}"
            tool_input = {**raw, **({"locations": locations} if locations else {})}
            approval_kind = {"edit": "file_write", "delete": "file_write", "move": "file_write", "read": "file_read", "execute": "command"}.get(kind, "other")
        title = call.get("title") if isinstance(call.get("title"), str) else tool
        if len(json.dumps(tool_input, ensure_ascii=False)) > MAX_REVIEWABLE_INPUT_CHARS:
            self.rpc.respond(rid, payload(False))
            return
        request_id = f"acp-{rid}"
        handle = await self.gate.open(
            request_id=request_id, tool=tool, input=tool_input, summary=title[:200], kind=approval_kind
        )
        if handle is None:
            return
        handle.tool = tool
        task = asyncio.create_task(self._answer(handle, rid, payload))
        self.answering.add(task)
        task.add_done_callback(self.answering.discard)

    async def _answer(self, handle, rid, payload) -> None:
        self.clock.pause()
        try:
            decision = await self.gate.decide(handle)
        finally:
            self.clock.resume()
        if decision.kind == LOST:
            return
        self.rpc.respond(rid, payload(decision.kind == ALLOW))

    # -- how it ended ------------------------------------------------------------------------

    def _outcome(self, *, crashed: bool = False) -> TurnOutcome:
        partial = self.buffer or None

        def failed(error: str, code: str) -> TurnOutcome:
            return TurnOutcome("failed", error=_clip(error), error_code=code, partial_text=partial, **self._common())

        if self.ctx.aborted:
            return failed("The run was stopped because it no longer owns its turn.", "lost")
        if crashed:
            return failed("Local run failed unexpectedly", "runner_error")
        if self.timed_out:
            return failed(f"Timed out after {app_settings.get('LOCAL_TASK_TIMEOUT_SECONDS')} seconds", "timeout")
        if self.over_text or (self.rpc is not None and self.rpc.over_limit):
            return failed("The agent produced more output than allowed", "output_too_large")
        if self.prompt_error is not None:
            return failed(f"The agent reported an error: {self.prompt_error}", "acp_error")
        if not self.finished:
            tail = self.rpc.stderr_tail.strip() if self.rpc is not None else ""
            return failed(tail or "The agent ended without finishing the turn.", "cli_error")
        if self.stop_reason == "end_turn":
            return TurnOutcome("succeeded", result=self.messages[-1] if self.messages else "", **self._common())
        if self.stop_reason == "cancelled":
            return failed("The agent stopped the turn.", "interrupted")
        return failed(f"The agent stopped: {self.stop_reason or 'no reason given'}.", "acp_error")
