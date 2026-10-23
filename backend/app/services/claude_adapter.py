"""The user's own `claude` CLI as a thread provider.

Wraps what already exists (`local_claude_runner`, `StreamNormalizer`,
`RunControl`) and adds what a durable thread needs: it continues the thread's
session with `--resume`, reports complete assistant messages and tool calls as
normalized events in the order the CLI produced them, and asks a person through
the approval gate.

Session rules (verified against Claude Code 2.1.232):
- A thread's first turn creates the session with `--session-id <uuid>`; later
  turns continue it with `--resume <uuid>`.
- If a first turn is cut off after the CLI created the session but before the
  app learned of it, the next `--session-id <uuid>` is refused with "already in
  use". The id is a fresh uuid this app made, so the only way it can be in use
  is that its own earlier attempt created it: the turn is retried once with
  `--resume`, losing nothing.
- If `--resume` finds no such session, the turn fails with
  `session_resume_failed` and the thread's session becomes `broken`. A fresh
  session is never started silently: it would answer from an empty context
  while looking like a continuation.
"""

import asyncio
import json
from dataclasses import dataclass
from uuid import uuid4

from app.computers import grants
from app.computers.service import COMPUTER_TOOL_PREFIX
from app.core import app_settings
from app.core.logging import get_logger
from app.services import engine_errors, local_claude_runner
from app.services.local_approval_broker import (
    DENY_BY_USER,
    DENY_CANCELLED,
    DENY_EXPIRED,
    DENY_TOO_LARGE,
    MAX_REVIEWABLE_INPUT_CHARS,
    _allow_payload,
    _deny_payload,
)
from app.services.local_claude_stream import StreamNormalizer, ToolEvent, summarize_tool
from app.services.provider_adapter import (
    ALLOW,
    CANCELLED,
    DENY,
    EXPIRED,
    LOST,
    ApprovalGate,
    ApprovalHandle,
    Capabilities,
    EventSink,
    ModelOption,
    ProviderStatus,
    TurnContext,
    TurnOutcome,
)
from app.services.thread_store import cap_tool_output

logger = get_logger(__name__)

MAX_ERROR_CHARS = 2000

# What a call does, in provider-neutral words, for the approval card.
_KINDS = {
    "Bash": "command",
    "Write": "file_write",
    "Edit": "file_write",
    "Read": "file_read",
    "Glob": "file_read",
    "Grep": "file_read",
    COMPUTER_TOOL_PREFIX + "exec": "command",
    COMPUTER_TOOL_PREFIX + "write_file": "file_write",
    COMPUTER_TOOL_PREFIX + "read_file": "file_read",
    COMPUTER_TOOL_PREFIX + "list_files": "file_read",
}

from app.computers.policy import SCREEN_INPUT, SCREEN_READ
_KINDS.update({tool: "screen_input" for tool in SCREEN_INPUT})
_KINDS.update({tool: "screen_read" for tool in SCREEN_READ})
_KINDS.update({"mcp__team__ask_agent": "delegate", "mcp__team__create_agent": "create_agent", "mcp__team__say_in_room": "room_post"})

DENY_NO_PERMIT = "The approval could not be handed to the computer, so this action was not allowed."

_DENY_MESSAGES = {
    DENY: DENY_BY_USER,
    EXPIRED: DENY_EXPIRED,
    CANCELLED: DENY_CANCELLED,
}


def _clip(message: str) -> str:
    message = message.strip()
    return message if len(message) <= MAX_ERROR_CHARS else message[:MAX_ERROR_CHARS] + "…"


def _put_all(queue: asyncio.Queue, items: list) -> None:
    for item in items:
        queue.put_nowait(item)


@dataclass
class _Attempt:
    """One launch of the CLI."""

    resume: bool
    normalizer: StreamNormalizer
    output: local_claude_runner.RunOutput | None = None
    error: Exception | None = None

    @property
    def already_in_use(self) -> bool:
        out, n = self.output, self.normalizer
        return (
            out is not None
            and not n.init_seen
            and n.final is None
            and out.exit_code not in (0, None)
            and "already in use" in out.stderr.lower()
        )


def _issue_permit(ctx: TurnContext, handle: ApprovalHandle, tool_input: dict) -> bool:
    """For an allowed computer call, the one-use permit the computer's MCP
    server must find before it runs the call. Other calls need none."""
    tool = getattr(handle, "tool", None) or ""
    if ctx.computer is None or not tool.startswith(COMPUTER_TOOL_PREFIX):
        return True
    try:
        grants.issue(ctx.computer.grants_dir, tool[len(COMPUTER_TOOL_PREFIX):], tool_input)
        return True
    except OSError:
        logger.exception("could not write a computer permit", thread_id=ctx.thread_id)
        return False


class ClaudeAdapter:
    setup = {
        "sign_in": "claude auth login", "credential": "terminal",
        "docs_url": "https://code.claude.com/docs/en/setup",
        "notes": ["Install the CLI and sign in from a terminal, then check again."],
    }
    key = "claude"
    label = "Claude"
    kind = "cli"
    # Tools with approvals (the CLI asks over stdio), the computer's MCP server,
    # `--resume`, agent computers, and images as stream-json content blocks.
    capabilities = Capabilities(
        tools=True, approvals=True, mcp=True, images=True, resume=True, computers=True
    )
    # The CLI's own aliases; any model id the CLI accepts also works.
    models = [
        ModelOption("sonnet", "Sonnet"),
        ModelOption("opus", "Opus"),
        ModelOption("haiku", "Haiku"),
    ]
    custom_models = True
    needs_config = False

    def new_session_id(self) -> str:
        return str(uuid4())

    async def status(self) -> ProviderStatus:
        cli = await local_claude_runner.get_status()
        ready = cli.installed and cli.signed_in is True
        return ProviderStatus(
            ready=ready,
            version=cli.version,
            error=None if ready else (cli.error or "Claude CLI is not ready for local use."),
            installed=cli.installed,
            signed_in=cli.signed_in,
        )

    async def generate_text(
        self, prompt: str, *, config: dict | None = None, model: str | None = None, timeout: float = 60
    ) -> str:
        """One quick answer to `prompt` for background work (memory upkeep),
        from the same CLI and sign-in as the agent's own turns. No tools, no
        MCP servers, no session saved, started in a new empty folder; the
        prompt goes in on stdin. Raises RuntimeError when the CLI fails or does
        not answer in time."""
        import tempfile
        from pathlib import Path

        from fastapi.concurrency import run_in_threadpool

        executable = local_claude_runner.resolve_claude_executable()
        if executable is None:
            raise RuntimeError("The Claude CLI was not found.")
        args = [
            str(executable), "-p", "--output-format", "text", "--no-session-persistence",
            *local_claude_runner.LOCKDOWN_ARGS,
        ]
        if model:
            args += ["--model", model]

        def call() -> local_claude_runner.RunOutput:
            with tempfile.TemporaryDirectory(prefix="open-dots-helper-") as empty:
                return local_claude_runner.run_blocking(
                    args, stdin_text=prompt, cwd=Path(empty), timeout_seconds=timeout,
                    max_output_bytes=256 * 1024,
                )

        out = await run_in_threadpool(call)
        if out.exit_code != 0 or not out.stdout.strip():
            raise RuntimeError("The Claude CLI gave no answer for the background call.")
        return out.stdout

    async def run_turn(
        self, ctx: TurnContext, sink: EventSink, gate: ApprovalGate
    ) -> TurnOutcome:
        if not ctx.provider_session_id:
            return TurnOutcome(
                "failed", error="This thread has no Claude session id.", error_code="no_session"
            )
        # A computer is the agent's sandbox: its turns always have tools, and
        # they are the computer's tools only.
        tools = app_settings.get("LOCAL_TOOLS_ENABLED") or ctx.computer is not None or ctx.team_launch is not None
        attempt = await self._attempt(ctx, sink, gate, resume=ctx.resume, tools=tools)
        if attempt.already_in_use and not attempt.resume and not ctx.aborted:
            attempt = await self._attempt(ctx, sink, gate, resume=True, tools=tools)
        return self._outcome(ctx, attempt, tools=tools)

    # -- one launch ------------------------------------------------------------

    async def _attempt(
        self,
        ctx: TurnContext,
        sink: EventSink,
        gate: ApprovalGate,
        *,
        resume: bool,
        tools: bool,
    ) -> _Attempt:
        loop = asyncio.get_running_loop()
        normalizer = StreamNormalizer(max_text_bytes=app_settings.get("LOCAL_MAX_OUTPUT_BYTES"))
        attempt = _Attempt(resume=resume, normalizer=normalizer)
        control = local_claude_runner.RunControl()
        ctx.on_abort(control.kill)
        def hold_clock():
            control.pause_deadline()
            return control.resume_deadline
        ctx.hold_clock = hold_clock
        queue: asyncio.Queue = asyncio.Queue()
        answering: set[asyncio.Task] = set()
        tools_by_request: dict[str, str] = {}

        def on_line(line: bytes) -> bool:
            # Runs on the runner's reader thread. Everything it learns goes onto
            # one queue, in order, and is handled on the event loop.
            items: list = [("delta", chunk) for chunk in normalizer.feed(line)]
            items += [("message", text) for text in normalizer.take_messages()]
            tool_events = normalizer.take_tool_events()
            if tools:
                items += [("tool", event) for event in tool_events]
                if normalizer.final is not None:
                    control.close_stdin()  # the turn is over; let the CLI exit
            if items:
                loop.call_soon_threadsafe(_put_all, queue, items)
            return normalizer.over_limit

        async def answer(handle: ApprovalHandle, tool_input: dict) -> None:
            handle.tool = tools_by_request.get(handle.request_id)
            control.pause_deadline()  # waiting for a person is not Claude running long
            try:
                decision = await gate.decide(handle)
            finally:
                control.resume_deadline()
            if decision.kind == LOST:
                return
            if decision.kind == ALLOW and not _issue_permit(ctx, handle, tool_input):
                payload = _deny_payload(handle.request_id, DENY_NO_PERMIT)
            elif decision.kind == ALLOW:
                payload = _allow_payload(handle.request_id, tool_input)
            else:
                payload = _deny_payload(
                    handle.request_id,
                    decision.message or _DENY_MESSAGES.get(decision.kind, DENY_BY_USER),
                )
            control.send(json.dumps(payload, ensure_ascii=False))

        async def handle_tool_event(event: ToolEvent) -> None:
            data = event.data
            if event.kind == "tool_started":
                await sink.emit(
                    "tool.started",
                    {
                        "call_id": data["tool_use_id"],
                        "name": data["tool"],
                        "summary": data["summary"],
                        "input": event.extra.get("input", {}),
                    },
                )
            elif event.kind == "tool_completed":
                output, truncated = cap_tool_output(event.extra.get("output", ""))
                await sink.emit(
                    "tool.completed",
                    {
                        "call_id": data["tool_use_id"],
                        "ok": not data["is_error"],
                        "output": output,
                        "truncated": truncated,
                    },
                )
            elif event.kind == "approval_requested":
                await ask(data)
            elif event.kind == "approval_cancelled":
                await gate.cancel(data["request_id"])
            elif event.kind == "control_unsupported":
                # A question this app does not know how to put to a person: say no.
                control.send(
                    json.dumps(
                        {
                            "type": "control_response",
                            "response": {
                                "subtype": "error",
                                "request_id": data["request_id"],
                                "error": "Not supported by this app.",
                            },
                        }
                    )
                )

        async def ask(data: dict) -> None:
            request_id, tool, tool_input = data["request_id"], data["tool"], data["input"]
            if len(json.dumps(tool_input, ensure_ascii=False)) > MAX_REVIEWABLE_INPUT_CHARS:
                # Too big to show a person in full: refused, never approved unseen.
                control.send(json.dumps(_deny_payload(request_id, DENY_TOO_LARGE)))
                return
            tools_by_request[request_id] = tool
            # Recorded here, in stream order, so the approval appears after the
            # tool call that caused it; the wait happens in its own task.
            handle = await gate.open(
                request_id=request_id,
                tool=tool,
                input=tool_input,
                summary=summarize_tool(tool, tool_input),
                kind=_KINDS.get(tool, "other"),
            )
            if handle is None:
                return
            task = asyncio.create_task(answer(handle, tool_input))
            answering.add(task)
            task.add_done_callback(answering.discard)

        async def pump() -> None:
            while True:
                kind, payload = await queue.get()
                if kind == "end":
                    return
                if sink.lost:
                    continue  # keep draining; write nothing
                try:
                    if kind == "delta":
                        sink.delta(payload)
                    elif kind == "message":
                        await sink.emit("assistant.message", {"text": payload})
                    else:
                        await handle_tool_event(payload)
                except Exception:  # noqa: BLE001
                    logger.exception("could not record a Claude event", thread_id=ctx.thread_id)

        pump_task = asyncio.create_task(pump())
        # Only what the thread's agent or request set is passed on, so a plain
        # thread launches exactly as before.
        extra = {}
        if ctx.model:
            extra["model"] = ctx.model
        notes = [ctx.instructions] if ctx.instructions else []
        if ctx.computer is not None:
            notes.append(ctx.computer.prompt_note())
            extra["computer"] = ctx.computer.launch
        if ctx.team_launch is not None:
            extra["mcp_servers"] = {"team": ctx.team_launch}
        if notes:
            extra["system_prompt"] = "\n\n".join(notes)
        if ctx.images:
            extra["images"] = ctx.images
        try:
            attempt.output = await local_claude_runner.run_prompt(
                ctx.prompt,
                ctx.provider_session_id,
                on_line,
                control,
                resume=resume,
                tools=tools,
                workspace=ctx.workspace,
                approval_mode=ctx.approval_mode,
                **extra,
            )
        except Exception as exc:  # noqa: BLE001
            if not isinstance(exc, local_claude_runner.ClaudeCliUnavailable):
                logger.exception("Claude run crashed", thread_id=ctx.thread_id)
            attempt.error = exc
        finally:
            queue.put_nowait(("end", None))
            await pump_task
            for task in list(answering):
                task.cancel()
            if answering:
                await asyncio.gather(*answering, return_exceptions=True)
        return attempt

    # -- what the run amounts to -------------------------------------------------

    def _outcome(self, ctx: TurnContext, attempt: _Attempt, *, tools: bool) -> TurnOutcome:
        n = attempt.normalizer
        common = {
            "model": n.model,
            "usage": n.usage,
            "session_state": "active" if n.init_seen else None,
        }

        def failed(error: str, code: str, **more) -> TurnOutcome:
            return TurnOutcome(
                "failed",
                error=_clip(error),
                error_code=code,
                partial_text=n.unflushed_text or None,
                **{**common, **more},
            )

        if ctx.aborted:
            return failed("The run was stopped because it no longer owns its turn.", "lost")
        if isinstance(attempt.error, local_claude_runner.ClaudeCliUnavailable):
            return failed(str(attempt.error), "cli_unavailable")
        if attempt.error is not None or attempt.output is None:
            return failed("Local run failed unexpectedly", "runner_error")

        out, final = attempt.output, n.final
        stderr = out.stderr.strip()

        # --resume found no session: an error the person sees, never a quiet
        # fresh start.
        if attempt.resume and not n.init_seen:
            said = f"{stderr} {final.text if final and final.text else ''}".lower()
            if "no conversation found" in said:
                return failed(
                    "Claude could not find this conversation's session, so it cannot continue "
                    "it. Start a new session to go on.",
                    "session_resume_failed",
                    session_state="broken",
                )

        if out.timed_out:
            return failed(
                f"Timed out after {app_settings.get('LOCAL_TASK_TIMEOUT_SECONDS')} seconds", "timeout"
            )
        if out.output_truncated or n.over_limit:
            return failed("Claude produced more output than allowed", "output_too_large")
        if final is None:
            if stderr:
                return failed(stderr, "cli_error")
            if out.exit_code not in (0, None):
                return failed(f"Claude exited with code {out.exit_code}", "cli_error")
            return failed("Claude ended without a result", "no_result")
        if final.is_error:
            # A refused API call (a usage or rate limit) keeps Claude's own
            # words and the reset time it reported; the runtime classifies it.
            said = final.text or n.api_error or stderr or "Claude reported an error"
            if n.limit_refused and n.api_error and n.api_error not in said:
                said = f"{said} {n.api_error}"
            if n.limit_refused and engine_errors.classify(said) is None:
                # The CLI said the account's limit refused the call, in words
                # that do not say so (older versions blamed the model).
                said = f"Usage limit reached. {said}"
            return failed(said, "claude_error", limit_resets_at=n.limit_resets_at)
        if final.permission_denied and not tools:
            return failed(
                "Claude tried to use a tool, which is turned off for local chat", "tools_disabled"
            )

        # Prefer the CLI's own final text; if it sent none, keep what was streamed.
        text = final.text if final.text and final.text.strip() else n.text
        return TurnOutcome("succeeded", result=text, **common)
