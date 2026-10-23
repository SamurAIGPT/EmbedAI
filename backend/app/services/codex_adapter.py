"""The user's own `codex` CLI as a thread engine, driven over `codex app-server`
(JSON-RPC, one JSON object per line on stdin/stdout).

One app-server process per turn: `initialize`, then `thread/start` (first
turn) or `thread/resume` (later turns, with the Codex thread id kept in the
thread's `provider_state`), then `turn/start`. The turn ends with Codex's
`turn/completed`. Checked against codex-cli 0.162.1.

Approvals. Codex asks this app (server-to-client requests) before a command
or a file change its approval policy does not allow by itself, and each
question goes through the approval gate like a Claude tool call. The approval
mode is mapped onto Codex's own policy and sandbox, sent on `thread/start`,
`thread/resume` and every `turn/start`, and the policy Codex reports back is
checked before the turn starts (a mismatch fails the turn):

    ask           approvalPolicy "untrusted", sandbox "workspace-write":
                  commands other than Codex's known read-only ones, and every
                  file change, wait for Allow or Deny
    accept_edits  the same, but a file change whose every path is inside the
                  workspace is accepted without a card
    plan          approvalPolicy "never", sandbox "read-only": nothing is
                  asked and nothing can be written
    tools off     (LOCAL_TOOLS_ENABLED unset, no agent computer) as plan

Any question that still arrives in plan or with tools off is declined. MCP
tool approvals Codex raises for servers in the person's own Codex config go
through the gate too; every other question this app does not know is
refused. Codex reads files under its read-only sandbox without asking, more
than Claude's ask mode allows; see docs/ENGINES.md.

Session rules, like Claude's: a missing Codex thread on resume fails the turn
with `session_resume_failed` and marks the session `broken`; a fresh thread is
never started silently.
"""

import asyncio
import json
import os
import re
import shlex
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from app.core import app_settings
from app.core.config import settings
from app.core.logging import get_logger
from app.services import cli_probe, local_claude_runner
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

MAX_ERROR_CHARS = 2000
# An app-server starting with the person's MCP servers can take a while.
STARTUP_TIMEOUT_SECONDS = 120.0
INTERRUPT_GRACE_SECONDS = 3.0
MODEL_LIST_TIMEOUT_SECONDS = 8.0
MODEL_CACHE_SECONDS = 600.0
RAW_STREAM_SIZE_FACTOR = 8

NOT_FOUND = (
    "Codex CLI not found. Install it (npm install -g @openai/codex), run `codex login`, "
    "or set LOCAL_CODEX_CLI_PATH to the full path of the codex executable."
)

COMMAND_REQUESTS = ("item/commandExecution/requestApproval", "execCommandApproval")
FILE_REQUESTS = ("item/fileChange/requestApproval", "applyPatchApproval")
LEGACY_REQUESTS = ("execCommandApproval", "applyPatchApproval")
TOOL_ITEMS = ("commandExecution", "fileChange", "mcpToolCall", "webSearch")


@dataclass(frozen=True)
class CodexPolicy:
    approval: str
    sandbox: str  # thread/start and thread/resume spelling
    sandbox_type: str  # turn/start spelling


def codex_policy(mode: str | None, tools: bool) -> CodexPolicy:
    if not tools or mode == "plan":
        return CodexPolicy("never", "read-only", "readOnly")
    return CodexPolicy("untrusted", "workspace-write", "workspaceWrite")


def resolve_codex_executable() -> Path | None:
    configured = (settings.LOCAL_CODEX_CLI_PATH or "").strip()
    if configured:
        path = Path(configured)
        return path if path.is_file() else None
    found = shutil.which("codex")
    return Path(found) if found else None


def _clip(message: str) -> str:
    message = (message or "").strip()
    return message if len(message) <= MAX_ERROR_CHARS else message[:MAX_ERROR_CHARS] + "…"


def _inside(path: str, root: Path | None) -> bool:
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


def _command_text(params: dict) -> str:
    command = params.get("command")
    if isinstance(command, list):
        return shlex.join(str(part) for part in command)
    return command if isinstance(command, str) else ""


def _display_command(params: dict, full: str) -> str:
    """What the person reads on the card: the command Codex wraps in
    `<shell> -lc '...'`, when Codex parsed exactly one."""
    actions = params.get("commandActions")
    if isinstance(actions, list) and len(actions) == 1 and isinstance(actions[0], dict):
        inner = actions[0].get("command")
        if isinstance(inner, str) and inner.strip():
            return inner
    return full


def _changes(raw) -> list[dict]:
    out = []
    if isinstance(raw, list):
        for change in raw:
            if isinstance(change, dict) and isinstance(change.get("path"), str):
                kind = change.get("kind")
                if isinstance(kind, dict):
                    kind = kind.get("type")
                diff = change.get("diff") if isinstance(change.get("diff"), str) else ""
                out.append({"path": change["path"], "kind": kind if isinstance(kind, str) else "update", "diff": diff[:4000]})
    elif isinstance(raw, dict):  # legacy applyPatchApproval: {path: change}
        for path, change in raw.items():
            if isinstance(path, str):
                kind = change.get("type") if isinstance(change, dict) else None
                out.append({"path": path, "kind": kind if isinstance(kind, str) else "update", "diff": ""})
    return out


class _Stopped(Exception):
    pass


class CodexAdapter:
    setup = {
        "install": "npm install -g @openai/codex", "sign_in": "codex login",
        "credential": "terminal", "docs_url": "https://developers.openai.com/codex/cli/",
        "notes": ["Install the CLI and sign in from a terminal, then check again."],
    }
    key = "codex"
    label = "Codex"
    kind = "cli"
    # Tools with approvals and resume. No MCP from this app, no images, and no
    # agent computers (Codex runs its tools on this machine).
    capabilities = Capabilities(
        tools=True, approvals=True, mcp=False, images=False, resume=True, computers=False
    )
    models: list[ModelOption] = []
    custom_models = True
    needs_config = False
    version_timeout = 5.0
    login_timeout = 8.0

    def __init__(self) -> None:
        self._models: tuple[float, list[ModelOption]] | None = None

    def new_session_id(self) -> str:
        # The app's own id for the conversation's session; Codex's thread id
        # is learned on the first turn and kept in provider_state.
        return str(uuid4())

    async def status(self) -> ProviderStatus:
        probe = await self.probe(models=False)
        return ProviderStatus(
            ready=probe.ready,
            version=probe.version,
            error=probe.reason,
            installed=probe.installed,
            signed_in=probe.signed_in,
        )

    async def probe(self, *, models: bool = True) -> EngineProbe:
        exe = resolve_codex_executable()
        if exe is None:
            return EngineProbe(installed=False, ready=False, reason=NOT_FOUND)
        version = await cli_probe.run([str(exe), "--version"], timeout=self.version_timeout)
        if not version.ok:
            return EngineProbe(
                installed=False,
                ready=False,
                reason="The Codex CLI did not answer `codex --version`."
                if version.timed_out or version.failed_to_start is None
                else "The Codex CLI could not be started.",
            )
        number = version.stdout.strip() or None
        login = await cli_probe.run([str(exe), "login", "status"], timeout=self.login_timeout)
        said = f"{login.stdout}\n{login.stderr}"
        # Only the first words are read; the rest can name the account.
        if login.ok and re.search(r"^\s*logged in\b", said, re.IGNORECASE | re.MULTILINE):
            signed_in: bool | None = True
        elif re.search(r"^\s*not logged in\b", said, re.IGNORECASE | re.MULTILINE):
            signed_in = False
        else:
            signed_in = None
        if signed_in is not True:
            return EngineProbe(
                installed=True,
                ready=False,
                version=number,
                signed_in=signed_in,
                reason="Codex is not signed in. Run `codex login` in a terminal first."
                if signed_in is False
                else "Could not tell whether Codex is signed in.",
            )
        catalog = await self._live_models(exe) if models else None
        return EngineProbe(installed=True, ready=True, version=number, signed_in=True, models=catalog)

    async def _live_models(self, exe: Path) -> list[ModelOption] | None:
        loop = asyncio.get_running_loop()
        if self._models is not None and loop.time() - self._models[0] < MODEL_CACHE_SECONDS:
            return self._models[1]
        rpc = None
        try:
            rpc = await StdioRpc.spawn([str(exe), "app-server"], cwd=os.path.expanduser("~"), max_bytes=4_000_000)

            async def ask():
                await rpc.request("initialize", {"clientInfo": {"name": "open-dots", "version": "0"}})
                rpc.notify("initialized", {})
                return await rpc.request("model/list", {})

            result = await asyncio.wait_for(ask(), MODEL_LIST_TIMEOUT_SECONDS)
        except (OSError, asyncio.TimeoutError, RpcError, RpcClosed):
            return None
        finally:
            if rpc is not None:
                await rpc.close()
        options = []
        for item in (result or {}).get("data") or []:
            if not isinstance(item, dict) or item.get("hidden") or not isinstance(item.get("id"), str):
                continue
            label = item.get("displayName") if isinstance(item.get("displayName"), str) else item["id"]
            options.append(ModelOption(item["id"], label, default=bool(item.get("isDefault"))))
        if options:
            self._models = (loop.time(), options)
        return options or None

    async def run_turn(self, ctx: TurnContext, sink, gate) -> TurnOutcome:
        exe = resolve_codex_executable()
        if exe is None:
            return TurnOutcome("failed", error=NOT_FOUND, error_code="cli_unavailable")
        state = ctx.provider_state if isinstance(ctx.provider_state, dict) else {}
        codex_thread = state.get("codex_thread_id") if isinstance(state.get("codex_thread_id"), str) else None
        if ctx.resume and not codex_thread:
            return TurnOutcome(
                "failed",
                error="Codex has no session for this conversation, so it cannot continue it. "
                "Start a new session to go on.",
                error_code="session_resume_failed",
                session_state="broken",
            )
        if ctx.computer is not None:
            return TurnOutcome(
                "failed",
                error="Codex cannot run its tools in an agent computer.",
                error_code="engine_without_computers",
            )
        run = _CodexTurn(ctx, sink, gate, exe, codex_thread)
        return await run.go()


@dataclass(eq=False)
class _CodexTurn:
    ctx: TurnContext
    sink: object
    gate: object
    exe: Path
    codex_thread: str | None
    rpc: StdioRpc | None = None
    thread_id: str | None = None
    codex_turn_id: str | None = None
    model: str | None = None
    session_started: bool = False
    completed: bool = False
    turn_status: str | None = None
    turn_error: str | None = None
    last_error: str | None = None
    final_text: str | None = None
    last_text: str | None = None
    usage: dict | None = None
    timed_out: bool = False
    text_bytes: int = 0
    over_text: bool = False
    partial: dict = field(default_factory=dict)
    file_items: dict = field(default_factory=dict)
    requests: dict = field(default_factory=dict)
    answering: set = field(default_factory=set)

    def __post_init__(self) -> None:
        self.tools = bool(app_settings.get("LOCAL_TOOLS_ENABLED"))
        self.mode = self.ctx.approval_mode or "ask"
        self.policy = codex_policy(self.mode, self.tools)
        self.max_text = app_settings.get("LOCAL_MAX_OUTPUT_BYTES")
        self.clock = TurnClock(app_settings.get("LOCAL_TASK_TIMEOUT_SECONDS"))
        self.workspace = self.ctx.workspace or local_claude_runner.workspace_dir()

    # -- running -----------------------------------------------------------------

    async def go(self) -> TurnOutcome:
        loop = asyncio.get_running_loop()
        self.aborted = asyncio.Event()
        self.ctx.on_abort(lambda: loop.call_soon_threadsafe(self.aborted.set))
        try:
            self.rpc = await StdioRpc.spawn(
                [str(self.exe), "app-server"],
                cwd=str(self.workspace),
                max_bytes=self.max_text * RAW_STREAM_SIZE_FACTOR,
            )
        except (OSError, ValueError):
            return TurnOutcome("failed", error="The Codex CLI could not be started.", error_code="cli_unavailable")
        try:
            early = await self._start()
            if early is not None:
                return early
            await self._pump()
        except _Stopped:
            await self._interrupt()
        except Exception:  # noqa: BLE001
            logger.exception("Codex run crashed", thread_id=self.ctx.thread_id)
            return self._outcome(crashed=True)
        finally:
            for task in list(self.answering):
                task.cancel()
            if self.answering:
                await asyncio.gather(*self.answering, return_exceptions=True)
            await self.rpc.close()
        return self._outcome()

    async def _call(self, method: str, params: dict):
        """A request that a Stop or the time limit can cut short."""
        assert self.rpc is not None
        request = asyncio.create_task(self.rpc.request(method, params, timeout=STARTUP_TIMEOUT_SECONDS))
        stop = asyncio.create_task(self.aborted.wait())
        try:
            left = self.clock.remaining()
            done, _ = await asyncio.wait(
                {request, stop}, timeout=max(left, 0) if left is not None else None,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if request in done:
                return request.result()
            if not done:
                self.timed_out = True
            raise _Stopped()
        finally:
            stop.cancel()
            if not request.done():
                request.cancel()
            await asyncio.gather(request, stop, return_exceptions=True)

    def _failed(self, error: str, code: str, **more) -> TurnOutcome:
        return TurnOutcome("failed", error=_clip(error), error_code=code, **{**self._common(), **more})

    def _common(self) -> dict:
        return {
            "model": self.model,
            "usage": self.usage,
            "session_state": "active" if self.session_started else None,
            "provider_state": {"codex_thread_id": self.thread_id} if self.thread_id else None,
        }

    async def _start(self) -> TurnOutcome | None:
        try:
            await self._call("initialize", {"clientInfo": {"name": "open-dots", "title": "Open Duet", "version": "0"}})
        except (RpcError, RpcClosed, asyncio.TimeoutError) as exc:
            return self._failed(self._why(exc, "Codex did not start its app-server."), "cli_error")
        self.rpc.notify("initialized", {})
        common = {
            "cwd": str(self.workspace),
            "approvalPolicy": self.policy.approval,
            "approvalsReviewer": "user",
            "sandbox": self.policy.sandbox,
        }
        if self.ctx.model:
            common["model"] = self.ctx.model
        if self.ctx.instructions:
            common["developerInstructions"] = self.ctx.instructions
        try:
            if self.codex_thread:
                started = await self._call("thread/resume", {"threadId": self.codex_thread, **common})
            else:
                started = await self._call("thread/start", {**common, "ephemeral": False})
        except RpcError as exc:
            if self.codex_thread:
                return self._failed(
                    "Codex could not find this conversation's session, so it cannot continue it. "
                    f"Start a new session to go on. (Codex said: {exc})",
                    "session_resume_failed",
                    session_state="broken",
                    provider_state=None,
                )
            return self._failed(f"Codex refused to start a session: {exc}", "codex_error")
        except (RpcClosed, asyncio.TimeoutError) as exc:
            return self._failed(self._why(exc, "Codex did not start a session."), "cli_error")
        thread = (started or {}).get("thread") if isinstance(started, dict) else None
        self.thread_id = thread.get("id") if isinstance(thread, dict) and isinstance(thread.get("id"), str) else None
        if not self.thread_id:
            return self._failed("Codex did not return a session id.", "codex_error")
        self.session_started = True
        self.model = started.get("model") if isinstance(started.get("model"), str) else self.ctx.model
        reported_policy = started.get("approvalPolicy")
        sandbox = started.get("sandbox")
        reported_sandbox = sandbox.get("type") if isinstance(sandbox, dict) else None
        if reported_policy != self.policy.approval or reported_sandbox != self.policy.sandbox_type:
            # Fail closed: the turn never starts with permissions it was not given.
            return self._failed(
                "Codex did not apply the requested approval policy and sandbox, so the turn was not "
                "started. Update Codex and try again.",
                "codex_policy_mismatch",
            )
        turn_params = {
            "threadId": self.thread_id,
            "input": [{"type": "text", "text": self.ctx.prompt}],
            "approvalPolicy": self.policy.approval,
            "approvalsReviewer": "user",
            "sandboxPolicy": {"type": self.policy.sandbox_type},
        }
        if self.ctx.model:
            turn_params["model"] = self.ctx.model
        try:
            turn = await self._call("turn/start", turn_params)
        except RpcError as exc:
            return self._failed(f"Codex refused the message: {exc}", "codex_error")
        except (RpcClosed, asyncio.TimeoutError) as exc:
            return self._failed(self._why(exc, "Codex did not take the message."), "cli_error")
        turn_info = (turn or {}).get("turn") if isinstance(turn, dict) else None
        if isinstance(turn_info, dict) and isinstance(turn_info.get("id"), str):
            self.codex_turn_id = turn_info["id"]
        return None

    def _why(self, exc: Exception, default: str) -> str:
        tail = self.rpc.stderr_tail.strip() if self.rpc is not None else ""
        if isinstance(exc, RpcError):
            return f"{default} ({exc})"
        return f"{default} {tail}".strip() if tail else default

    async def _pump(self) -> None:
        assert self.rpc is not None
        stop = asyncio.create_task(self.aborted.wait())
        pending_get: asyncio.Task | None = None
        try:
            while not self.completed:
                if pending_get is None:
                    pending_get = asyncio.create_task(self.rpc.incoming.get())
                left = self.clock.remaining()
                done, _ = await asyncio.wait(
                    {pending_get, stop},
                    timeout=1.0 if left is None else max(0.0, min(left, 1.0)),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if stop in done:
                    raise _Stopped()
                if pending_get not in done:
                    if self.clock.expired:
                        self.timed_out = True
                        raise _Stopped()
                    continue
                message = pending_get.result()
                pending_get = None
                if message is None:
                    return  # the process ended
                if "method" in message and "id" in message:
                    await self._server_request(message)
                else:
                    await self._notification(message)
                if self.over_text or self.rpc.over_limit:
                    raise _Stopped()
        finally:
            stop.cancel()
            if pending_get is not None:
                pending_get.cancel()

    async def _interrupt(self) -> None:
        """Ask Codex to stop the turn, then end the process whatever it does."""
        if self.rpc is None or self.rpc.closed or not (self.thread_id and self.codex_turn_id):
            return
        try:
            await asyncio.wait_for(
                self.rpc.request("turn/interrupt", {"threadId": self.thread_id, "turnId": self.codex_turn_id}),
                INTERRUPT_GRACE_SECONDS,
            )
        except (RpcError, RpcClosed, asyncio.TimeoutError):
            pass

    # -- what Codex sends ------------------------------------------------------------

    async def _emit(self, type_: str, data: dict) -> None:
        if self.sink.lost:
            return
        try:
            await self.sink.emit(type_, data)
        except Exception:  # noqa: BLE001
            logger.exception("could not record a Codex event", thread_id=self.ctx.thread_id)

    async def _notification(self, message: dict) -> None:
        method = message.get("method")
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        if params.get("threadId") not in (None, self.thread_id):
            return
        if method == "item/agentMessage/delta":
            delta = params.get("delta")
            if isinstance(delta, str) and delta:
                item_id = params.get("itemId") or "_"
                self.partial[item_id] = self.partial.get(item_id, "") + delta
                self._count(delta)
                if not self.sink.lost:
                    self.sink.delta(delta)
        elif method == "item/started":
            await self._item_started(params.get("item") if isinstance(params.get("item"), dict) else {})
        elif method == "item/completed":
            await self._item_completed(params.get("item") if isinstance(params.get("item"), dict) else {})
        elif method == "thread/tokenUsage/updated":
            total = (params.get("tokenUsage") or {}).get("total") if isinstance(params.get("tokenUsage"), dict) else None
            if isinstance(total, dict):
                usage = {}
                for theirs, ours in (("inputTokens", "input_tokens"), ("outputTokens", "output_tokens"), ("cachedInputTokens", "cached_input_tokens")):
                    value = total.get(theirs)
                    if isinstance(value, int) and not isinstance(value, bool):
                        usage[ours] = value
                self.usage = usage or self.usage
        elif method == "serverRequest/resolved":
            request_id = self.requests.pop(params.get("requestId"), None)
            if request_id is not None:
                await self.gate.cancel(request_id)
        elif method == "error":
            error = params.get("error") if isinstance(params.get("error"), dict) else {"message": params.get("message")}
            if params.get("willRetry") is not True and isinstance(error.get("message"), str):
                self.last_error = error["message"]
        elif method == "turn/completed":
            turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
            if self.codex_turn_id is None or turn.get("id") in (None, self.codex_turn_id):
                self.completed = True
                self.turn_status = turn.get("status") if isinstance(turn.get("status"), str) else None
                error = turn.get("error")
                if isinstance(error, dict) and isinstance(error.get("message"), str):
                    self.turn_error = error["message"]

    def _count(self, text: str) -> None:
        self.text_bytes += len(text.encode("utf-8"))
        if self.text_bytes > self.max_text:
            self.over_text = True

    async def _item_started(self, item: dict) -> None:
        kind, item_id = item.get("type"), item.get("id")
        if kind not in TOOL_ITEMS or not isinstance(item_id, str):
            return
        if kind == "commandExecution":
            full = item.get("command") if isinstance(item.get("command"), str) else ""
            name, summary = "Bash", _display_command(item, full)
            tool_input = {"command": full, "cwd": item.get("cwd")}
        elif kind == "fileChange":
            changes = _changes(item.get("changes"))
            self.file_items[item_id] = changes
            name = "apply_patch"
            paths = [c["path"] for c in changes]
            summary = ("Edit " + ", ".join(paths))[:200] if paths else "Edit files"
            tool_input = {"changes": [{"path": c["path"], "kind": c["kind"]} for c in changes]}
        elif kind == "mcpToolCall":
            name = f"mcp:{item.get('server')}.{item.get('tool')}"
            summary = name
            tool_input = item.get("arguments") if isinstance(item.get("arguments"), dict) else {}
        else:
            name, summary = "web_search", str(item.get("query") or "web search")[:200]
            tool_input = {"query": item.get("query")}
        await self._emit("tool.started", {"call_id": item_id, "name": name, "summary": summary, "input": tool_input})

    async def _item_completed(self, item: dict) -> None:
        kind, item_id = item.get("type"), item.get("id")
        if kind == "agentMessage":
            text = item.get("text") if isinstance(item.get("text"), str) else ""
            streamed = self.partial.pop(item_id, None) if isinstance(item_id, str) else None
            if streamed is None:
                self._count(text)
            if text.strip():
                self.last_text = text
                if item.get("phase") == "final_answer":
                    self.final_text = text
                await self._emit("assistant.message", {"text": text})
            return
        if kind not in TOOL_ITEMS or not isinstance(item_id, str):
            return
        status = item.get("status")
        if kind == "commandExecution":
            output = item.get("aggregatedOutput") if isinstance(item.get("aggregatedOutput"), str) else ""
            if item.get("exitCode") not in (None, 0):
                output = f"{output}\n(exit code {item.get('exitCode')})".strip()
        elif kind == "fileChange":
            output = "\n".join(f"{c['kind']} {c['path']}" for c in _changes(item.get("changes")))
        else:
            raw = item.get("error") or item.get("result") or item.get("action")
            output = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False) if raw is not None else ""
        if status == "declined":
            output = (output + "\n" if output else "") + "(declined)"
        stored, truncated = cap_tool_output(output)
        await self._emit(
            "tool.completed",
            {"call_id": item_id, "ok": status not in ("failed", "declined"), "output": stored, "truncated": truncated},
        )

    # -- what Codex asks -----------------------------------------------------------------

    async def _server_request(self, message: dict) -> None:
        method = message.get("method")
        rid = message.get("id")
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        meta = params.get("_meta") if isinstance(params.get("_meta"), dict) else {}
        if method in COMMAND_REQUESTS:
            kind = "command"
        elif method in FILE_REQUESTS:
            kind = "file"
        elif method == "mcpServer/elicitation/request" and meta.get("codex_approval_kind") == "mcp_tool_call":
            kind = "mcp"
        elif method == "mcpServer/elicitation/request":
            self.rpc.respond(rid, {"action": "decline"})
            return
        elif method == "item/permissions/requestApproval":
            self.rpc.respond(rid, {"permissions": {}, "scope": "turn"})
            return
        else:
            self.rpc.respond(rid, error={"code": -32601, "message": "Not supported by this app."})
            return

        legacy = method in LEGACY_REQUESTS

        def payload(allow: bool) -> dict:
            if kind == "mcp":
                return {"action": "accept", "content": {}} if allow else {"action": "decline"}
            if legacy:
                return {"decision": "approved" if allow else "denied"}
            return {"decision": "accept" if allow else "decline"}

        if not self.tools or self.mode == "plan":
            self.rpc.respond(rid, payload(False))
            return

        if kind == "command":
            full = _command_text(params)
            tool, approval_kind = "Bash", "command"
            tool_input = {"command": full}
            if isinstance(params.get("cwd"), str):
                tool_input["cwd"] = params["cwd"]
            summary = _display_command(params, full)[:200]
        elif kind == "file":
            changes = self.file_items.get(params.get("itemId")) or _changes(params.get("fileChanges"))
            if (
                self.mode == "accept_edits"
                and changes
                and not params.get("grantRoot")
                and all(_inside(c["path"], self.workspace) for c in changes)
            ):
                self.rpc.respond(rid, payload(True))
                return
            tool, approval_kind = "apply_patch", "file_write"
            tool_input = {"changes": changes}
            if params.get("grantRoot"):
                tool_input["grant_root"] = params["grantRoot"]
            if isinstance(params.get("reason"), str):
                tool_input["reason"] = params["reason"]
            paths = [c["path"] for c in changes]
            summary = ("Edit " + ", ".join(paths))[:200] if paths else "Edit files"
        else:
            server = params.get("serverName") if isinstance(params.get("serverName"), str) else "mcp"
            tool, approval_kind = f"mcp:{server}", "other"
            text = params.get("message") if isinstance(params.get("message"), str) else ""
            tool_input = {"server": server, "message": text}
            summary = text[:200] or tool

        if len(json.dumps(tool_input, ensure_ascii=False)) > MAX_REVIEWABLE_INPUT_CHARS:
            self.rpc.respond(rid, payload(False))  # too big to show in full: refused
            return
        request_id = f"codex-{rid}"
        self.requests[rid] = request_id
        handle = await self.gate.open(
            request_id=request_id, tool=tool, input=tool_input, summary=summary, kind=approval_kind
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
        self.requests.pop(rid, None)
        if decision.kind == LOST:
            return
        self.rpc.respond(rid, payload(decision.kind == ALLOW))

    # -- how it ended ----------------------------------------------------------------------

    def _outcome(self, *, crashed: bool = False) -> TurnOutcome:
        partial = "".join(self.partial.values()) or None

        def failed(error: str, code: str, **more) -> TurnOutcome:
            return TurnOutcome(
                "failed", error=_clip(error), error_code=code, partial_text=partial, **{**self._common(), **more}
            )

        if self.ctx.aborted:
            return failed("The run was stopped because it no longer owns its turn.", "lost")
        if crashed:
            return failed("Local run failed unexpectedly", "runner_error")
        if self.timed_out:
            return failed(f"Timed out after {app_settings.get('LOCAL_TASK_TIMEOUT_SECONDS')} seconds", "timeout")
        if self.over_text or (self.rpc is not None and self.rpc.over_limit):
            return failed("Codex produced more output than allowed", "output_too_large")
        if not self.completed:
            tail = self.rpc.stderr_tail.strip() if self.rpc is not None else ""
            return failed(tail or "Codex ended without finishing the turn.", "cli_error")
        if self.turn_status == "completed":
            text = self.final_text or self.last_text or ""
            return TurnOutcome("succeeded", result=text, **self._common())
        if self.turn_status == "interrupted":
            return failed("Codex stopped the turn.", "interrupted")
        return failed(self.turn_error or self.last_error or "Codex reported an error", "codex_error")
