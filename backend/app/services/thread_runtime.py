"""Runs the turns of durable threads (F04, slice 2).

For each turn this module: starts it in the database, runs the provider's
adapter, stores every event before announcing it, keeps the turn's lease alive,
asks a person through durable approvals, and records how the turn ended. It is
the only code that writes a turn's events, and it does so only while it still
owns the turn (`owner_token`).

What it never does: resume or replay an interrupted turn, start a fresh
provider session on its own, or send an Allow to a provider from a run that
lost its turn. A run that loses its turn kills its provider process and writes
nothing more.

Typing deltas and live approval answers live in this process's memory (the
single-process limit of D-004 and D-006); stored events and approvals are in the
database, so after a restart everything that was stored is still there.
"""

import asyncio
import json
from dataclasses import dataclass, field
from datetime import timedelta
from uuid import uuid4
from pathlib import Path

from app.core.config import settings
from app.core import app_settings
from app.core.logging import get_logger
from app.services import agent_memory, agent_store
from app.services import thread_store as store
from app.services import local_claude_runner
from app.services.workspaces import require_workspace
from app.services import engine_config
from app.services.engine_registry import EngineRegistry, InvalidModel, UnknownEngine, capabilities_of, default_adapters
from app.computers import policy as computer_policy
from app.computers import service as computer_service
from app.services import approval_allowlist, approval_modes, decision_log, safety_floor
from app.services.provider_adapter import (
    ALLOW,
    CANCELLED,
    DENY,
    EXPIRED,
    LOST,
    ApprovalHandle,
    Decision,
    ProviderAdapter,
    ProviderStatus,
    TurnContext,
    TurnOutcome,
)
from app.services.thread_hub import ThreadHub
from app.services.thread_hub import hub as default_hub

logger = get_logger(__name__)

# How often a waiting run looks at its approval row, so an answer recorded by
# another process is noticed without needing a live push.
APPROVAL_POLL_SECONDS = 1.0
# After the watchdog has stopped a stalled engine, how long the adapter gets to
# return on its own before the runtime stops waiting for it.
STALL_GRACE_SECONDS = 5.0
STATUS_CACHE_SECONDS = 30.0
SHUTDOWN_WAIT_SECONDS = 10.0


# Every thread belongs to the person at this machine. A runtime can be given
# another owner id (tests do, so they never touch real threads).
LOCAL_OWNER_ID = "local-owner"

RESET_MESSAGE = (
    "A new session was started. The provider no longer has the earlier conversation as "
    "context; the transcript above is still stored and visible."
)


class UnknownProvider(Exception):
    pass


class EngineModelInvalid(Exception):
    """The model is not one the engine accepts (message says why)."""


class UnknownAgent(Exception):
    """No such agent for this owner."""


class AgentArchived(Exception):
    """A new conversation cannot start from an archived agent."""


class AgentProviderMismatch(Exception):
    """The request names a provider other than the agent's."""


class SessionNotResettable(Exception):
    """This provider keeps no session of its own, so there is nothing to reset."""


class NoRunningTurn(Exception):
    """The thread has no running turn to stop."""


class TurnNotHere(Exception):
    """The thread's turn is running, but not in this process, so this process
    cannot stop it. It ends by itself when its owner finishes, or is interrupted
    when its lease runs out."""


class ProviderUnavailable(Exception):
    pass


class SessionBroken(Exception):
    """The thread's provider session cannot be continued; reset it first."""


class _EngineWithoutComputers(Exception):
    pass


@dataclass(eq=False)
class ActiveRun:
    thread_id: int
    turn_id: int
    owner_token: str
    ctx: TurnContext
    recorder: "TurnRecorder | None" = None
    gate: "ThreadApprovalGate | None" = None
    task: asyncio.Task | None = None
    # The person asked to stop this turn. What the provider reports after it is
    # killed is then recorded as a stop, not as a failure.
    stop_requested: bool = False
    settings_snapshot: dict = field(default_factory=dict)
    # The named agent the turn runs as, if any (its computer is attached at start).
    agent_id: int | None = None
    # Seconds an approval waits before it expires (is denied); None means the
    # LOCAL_APPROVAL_TIMEOUT_SECONDS setting. Routines set their own.
    approval_timeout: float | None = None
    # The turn watchdog: when the engine last did anything (event loop time),
    # and whether the watchdog stopped it for doing nothing for too long.
    last_activity: float = 0.0
    stalled: bool = False
    adapter_task: asyncio.Task | None = None

    def touch(self) -> None:
        try:
            self.last_activity = asyncio.get_running_loop().time()
        except RuntimeError:
            pass


class TurnRecorder:
    """The adapter's event sink for one turn. Every event is stored by a
    statement that also proves the run still owns the turn, committed, and only
    then announced."""

    def __init__(self, runtime: "ThreadRuntime", run: ActiveRun) -> None:
        self._runtime = runtime
        self._run = run
        self._lost = False
        # Seq of the last event this run stored (None before the first), used to
        # tag typing deltas so a stream can drop one a stored message supersedes.
        self._last_seq: int | None = None

    @property
    def lost(self) -> bool:
        return self._lost

    def lose(self) -> None:
        """This run no longer owns its turn: stop the provider and write nothing
        more."""
        if not self._lost:
            self._lost = True
            self._run.ctx.abort()

    async def emit(self, type: str, data: dict) -> bool:
        if self._lost:
            return False
        run = self._run
        async with self._runtime.session() as db:
            event = await store.append_turn_event(db, run.turn_id, run.owner_token, type, data)
        if event is None:
            self.lose()
            return False
        run.touch()
        self._last_seq = event.seq
        if type == "assistant.message":
            self._runtime.hub.message_done(run.thread_id)
        self._runtime.hub.notify(run.thread_id)
        return True

    def delta(self, text: str) -> None:
        if not self._lost:
            self._run.touch()
            self._runtime.hub.delta(
                self._run.thread_id, self._run.turn_id, text, after_seq=self._last_seq
            )


class ThreadApprovalGate:
    """Durable approvals for one turn. The question is a database row; the wait
    is in memory, with a poll of the row so an answer recorded elsewhere is
    still seen."""

    def __init__(self, runtime: "ThreadRuntime", run: ActiveRun) -> None:
        self._runtime = runtime
        self._run = run
        self._by_request: dict[str, str] = {}
        self._deadlines: dict[str, float] = {}
        # What each open approval was about, for the decision log.
        self._info: dict[str, tuple[str, str | None, str | None]] = {}

    @property
    def waiting(self) -> bool:
        """An approval is open: the turn waits on a person, not on the engine
        (the approval has its own timeout)."""
        return bool(self._deadlines)

    def _log(self, event: str, approval_id: str, **more) -> None:
        tool, kind, summary = self._info.get(approval_id, (None, None, None))
        decision_log.record(
            event,
            thread_id=self._run.thread_id,
            turn_id=self._run.turn_id,
            approval_id=approval_id,
            tool=tool,
            kind=kind,
            summary=summary,
            mode=self._run.ctx.approval_mode or approval_modes.DEFAULT_MODE,
            **more,
        )

    def _workspace(self) -> str | None:
        workspace = self._run.ctx.workspace
        return str(workspace) if workspace is not None else None

    async def _saved_rule(self, tool: str, tool_input: dict):
        """The person's saved "always allow" rule for this call, if any. A
        failure to look is no rule: the call is asked about."""
        try:
            async with self._runtime.session() as db:
                return await approval_allowlist.matching_rule(
                    db, self._runtime.owner_id, tool, tool_input, self._workspace()
                )
        except Exception:  # noqa: BLE001
            logger.exception("could not read saved command rules", thread_id=self._run.thread_id)
            return None

    async def open(
        self, *, request_id: str, tool: str, input: dict, summary: str | None, kind: str | None
    ) -> ApprovalHandle | None:
        run, runtime = self._run, self._runtime
        async with runtime.session() as db:
            try:
                approval = await store.create_approval(
                    db,
                    thread_id=run.thread_id,
                    turn_id=run.turn_id,
                    request_id=request_id,
                    tool=tool,
                    input=input,
                    summary=summary,
                    kind=kind,
                    owner_token=run.owner_token,
                )
            except store.TurnNotRunning:
                run.recorder.lose()
                return None
        future = asyncio.get_running_loop().create_future()
        runtime._waiters[approval.id] = future
        self._by_request[request_id] = approval.id
        timeout = run.approval_timeout
        if timeout is None:
            timeout = app_settings.get("LOCAL_APPROVAL_TIMEOUT_SECONDS", live=True)
        self._deadlines[approval.id] = asyncio.get_running_loop().time() + timeout
        self._info[approval.id] = (tool, kind, summary)
        auto = computer_policy.automatic(tool, run.ctx.approval_mode or approval_modes.DEFAULT_MODE)
        if auto is not None:
            status, source = auto
            async with runtime.session() as db:
                won = await store.decide_approval(db, approval.id, status, by={"by": source})
            if won:
                future.set_result(status)
                self._log("auto_allowed" if status == "allowed" else "denied", approval.id, source=source)
            runtime.hub.notify(run.thread_id)
            return ApprovalHandle(approval_id=approval.id, request_id=request_id)
        rule = await self._saved_rule(tool, input)
        held = None
        if rule is not None:
            # A command on the safety floor, or one that could not be read, is
            # never allowed by a rule: a person decides on the card.
            reading = safety_floor.floor_reading(input.get("command", "")) if isinstance(input.get("command"), str) else None
            if reading is not None and not reading.clear:
                held = reading.verdict.rule if reading.verdict else "unreadable"
                rule = None
        if rule is not None:
            # Still recorded as an approval, answered at once, so the timeline
            # shows what ran and the usual ownership re-check still applies.
            async with runtime.session() as db:
                won = await store.decide_approval(
                    db, approval.id, "allowed", by={"by": "saved_rule", "rule_id": rule.id}
                )
            if won:
                future.set_result("allowed")
                self._log(
                    "auto_allowed", approval.id, source="saved_rule", rule=approval_allowlist.rule_label(rule)
                )
        else:
            self._log("shown", approval.id, rule=f"safety_floor:{held}" if held else None)
        runtime.hub.notify(run.thread_id)
        return ApprovalHandle(approval_id=approval.id, request_id=request_id)

    async def decide(self, handle: ApprovalHandle) -> Decision:
        run, runtime = self._run, self._runtime
        loop = asyncio.get_running_loop()
        future = runtime._waiters.get(handle.approval_id)
        # A cancel that landed before this wait dropped the deadline: finish at once.
        deadline = self._deadlines.get(handle.approval_id, loop.time())
        status: str | None = None
        try:
            while status is None:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                if future is not None:
                    done, _ = await asyncio.wait({future}, timeout=min(APPROVAL_POLL_SECONDS, remaining))
                    if done:
                        status = future.result()
                        break
                else:
                    await asyncio.sleep(min(APPROVAL_POLL_SECONDS, remaining))
                current = await self._status(handle.approval_id)
                if current != "pending":  # answered, cancelled or expired elsewhere
                    status = current
            if status is None:
                # Time is up. Expiry only wins if nobody answered a moment ago.
                async with runtime.session() as db:
                    expired = await store.expire_approval(db, handle.approval_id)
                status = "expired" if expired else await self._status(handle.approval_id)
        finally:
            runtime._waiters.pop(handle.approval_id, None)
            self._by_request.pop(handle.request_id, None)
            self._deadlines.pop(handle.approval_id, None)

        if status in ("expired", "cancelled"):
            self._log(status, handle.approval_id, source="timeout" if status == "expired" else "provider")
        self._info.pop(handle.approval_id, None)
        if status == "allowed":
            # Re-check, atomically, that this run still owns its turn right
            # before the Allow is sent. The caller sends it only on ALLOW.
            async with runtime.session() as db:
                delivered = await store.mark_allow_delivered(db, handle.approval_id, run.owner_token)
            if not delivered:
                run.recorder.lose()
                return Decision(LOST)
            return Decision(ALLOW)
        if status == "denied":
            return Decision(DENY)
        if status == "expired":
            return Decision(EXPIRED)
        return Decision(CANCELLED)

    async def cancel(self, request_id: str) -> None:
        approval_id = self._by_request.get(request_id)
        if approval_id is None:
            return
        self._deadlines.pop(approval_id, None)
        async with self._runtime.session() as db:
            await store.cancel_approval(db, approval_id)
        future = self._runtime._waiters.get(approval_id)
        if future is not None and not future.done():
            future.set_result("cancelled")
        self._runtime.hub.notify(self._run.thread_id)

    async def _status(self, approval_id: str) -> str:
        async with self._runtime.session() as db:
            approval = await store.get_approval(db, approval_id)
        return approval.status if approval is not None else "cancelled"


@dataclass
class ThreadRuntime:
    hub: ThreadHub = default_hub
    adapters: dict[str, ProviderAdapter] = field(default_factory=default_adapters)
    status_ttl: float = STATUS_CACHE_SECONDS
    instance_id: str = field(default_factory=lambda: uuid4().hex)
    owner_id: str = LOCAL_OWNER_ID
    _runs: dict[int, ActiveRun] = field(default_factory=dict)
    _waiters: dict[str, asyncio.Future] = field(default_factory=dict)
    _status_cache: dict[str, tuple[float, ProviderStatus]] = field(default_factory=dict)
    _session_factory: object | None = None
    # Agent computers (app/computers/service.py), sharing this runtime's
    # database sessions and owner.
    computers: computer_service.ComputerService | None = None
    # The engine registry over `adapters` (app/services/engine_registry.py).
    engines: EngineRegistry | None = None
    # Set by shutdown(): no new turn starts in a process that is stopping.
    _closing: bool = False

    def __post_init__(self) -> None:
        if self.computers is None:
            self.computers = computer_service.ComputerService(
                owner_id=self.owner_id, _session_factory=self.session
            )
        if self.engines is None:
            self.engines = EngineRegistry(self)

    # -- engines ------------------------------------------------------------------------

    async def engine_config(self, engine_id: str) -> dict | None:
        """The engine's saved configuration for this owner, or None."""
        async with self.session() as db:
            return await engine_config.load(db, self.owner_id, engine_id)

    async def prepare_adapter(self, engine_id: str, adapter) -> dict | None:
        """Give an engine that needs a saved configuration its current one
        (read fresh, so a change applies to the next turn and probe)."""
        if not getattr(adapter, "needs_config", False):
            return None
        try:
            config = await self.engine_config(engine_id)
        except Exception:  # noqa: BLE001  (an unreadable configuration is none)
            logger.exception("could not read an engine configuration", engine=engine_id)
            config = None
        adapter.config = config
        return config

    def check_engine_model(self, provider: str, model: str | None) -> None:
        """Raise UnknownProvider or EngineModelInvalid."""
        try:
            self.engines.check_model(provider, model)
        except UnknownEngine:
            raise UnknownProvider(provider) from None
        except InvalidModel as exc:
            raise EngineModelInvalid(str(exc)) from None

    def session(self):
        """A new database session. Looked up late so tests can substitute it."""
        if self._session_factory is not None:
            return self._session_factory()
        from app.db.session import AsyncSessionLocal

        return AsyncSessionLocal()

    # -- threads ----------------------------------------------------------------------

    async def create_thread(
        self,
        *,
        provider: str | None = None,
        model: str | None = None,
        title: str | None = None,
        agent_id: int | None = None,
    ) -> store.Thread:
        """A new thread for this runtime's owner. With `agent_id` the thread is
        started from that agent: its provider is used and its workspace is
        pinned now (later edits to the agent's workspace do not move the
        thread). Raises UnknownProvider, UnknownAgent, AgentArchived,
        AgentProviderMismatch, or OSError when the agent's folder is gone."""
        agent = None
        if agent_id is not None:
            async with self.session() as db:
                agent = await agent_store.get_agent(db, agent_id)
            if agent is None or agent.owner_id != self.owner_id:
                raise UnknownAgent(agent_id)
            if agent.archived_at is not None:
                raise AgentArchived(agent_id)
            if provider is not None and provider != agent.provider:
                raise AgentProviderMismatch(provider)
            provider = agent.provider
        provider = provider or "claude"
        adapter = self.adapters.get(provider)
        if adapter is None:
            raise UnknownProvider(provider)
        self.check_engine_model(provider, model)
        session_id = adapter.new_session_id()
        if agent is not None and agent.workspace_path:
            workspace = require_workspace(Path(agent.workspace_path))
        elif agent is not None:
            # No folder chosen: the agent gets its own, keyed by id so a rename
            # never moves it and two agents never share files.
            workspace = local_claude_runner.workspace_dir() / "agents" / str(agent.id)
            workspace.mkdir(parents=True, exist_ok=True)
        else:
            workspace = local_claude_runner.workspace_dir()
        async with self.session() as db:
            return await store.create_thread(
                db,
                owner_id=self.owner_id,
                provider=provider,
                model=model,
                title=title,
                provider_session_id=session_id,
                session_state="new" if session_id is not None else "none",
                workspace_path=str(workspace.resolve()),
                agent_id=agent.id if agent is not None else None,
            )

    async def reset_session(self, thread_id: int) -> store.Thread:
        """Start the thread's provider session over, so a broken thread is never
        stuck. Raises ThreadNotFound, UnknownProvider, SessionNotResettable, or
        ThreadBusy (a turn is running)."""
        async with self.session() as db:
            thread = await store.get_thread(db, thread_id)
        if thread is None or thread.owner_id != self.owner_id:
            raise store.ThreadNotFound(thread_id)
        adapter = self.adapters.get(thread.provider)
        if adapter is None:
            raise UnknownProvider(thread.provider)
        session_id = adapter.new_session_id()
        if session_id is None:
            raise SessionNotResettable(thread.provider)
        async with self.session() as db:
            thread = await store.reset_session(db, thread_id, session_id, message=RESET_MESSAGE)
        self.hub.notify(thread_id)
        return thread

    # -- sending a turn ----------------------------------------------------------

    async def send_turn(
        self,
        thread_id: int,
        prompt_text: str,
        *,
        approval_mode: str | None = None,
        approval_timeout: float | None = None,
    ) -> store.Turn:
        """Start a turn and run it in the background. Raises ThreadNotFound,
        UnknownProvider, SessionBroken, ProviderUnavailable, or ThreadBusy (a
        turn is already running).

        `approval_mode` replaces the agent's mode for this turn (a routine
        passes the stricter of its own and the agent's); `approval_timeout`
        replaces the approval timeout setting for this turn's approvals."""
        if self._closing:
            raise ProviderUnavailable("The app is shutting down; send this again after it restarts.")
        async with self.session() as db:
            thread = await store.get_thread(db, thread_id)
        if thread is None:
            raise store.ThreadNotFound(thread_id)
        adapter = self.adapters.get(thread.provider)
        if adapter is None:
            raise UnknownProvider(thread.provider)
        if thread.session_state == "broken":
            raise SessionBroken(thread_id)
        if not thread.workspace_path:
            raise ProviderUnavailable("This conversation has no pinned workspace. Upgrade its database before continuing.")
        try:
            require_workspace(Path(thread.workspace_path))
        except OSError as exc:
            raise ProviderUnavailable(str(exc)) from None
        snapshot = app_settings.capture()
        config = await self.prepare_adapter(thread.provider, adapter)
        with app_settings.use(snapshot):
            status = await self._provider_status(adapter, config)
        if not status.ready:
            raise ProviderUnavailable(status.error or f"{thread.provider} is not ready")

        # A named agent's current settings apply to every turn. A deleted agent
        # leaves the thread with its own provider and model only.
        agent = None
        if thread.agent_id is not None:
            async with self.session() as db:
                agent = await agent_store.get_agent(db, thread.agent_id)

        async with self.session() as db:
            turn = await store.start_turn(
                db, thread_id, prompt_text, owner_instance=self.instance_id
            )

        async def history() -> list[dict]:
            async with self.session() as db:
                return store.history_items(await store.list_all_events(db, thread_id))

        ctx = TurnContext(
            thread_id=thread_id,
            turn_id=turn.id,
            prompt=prompt_text,
            workspace=Path(thread.workspace_path) if thread.workspace_path else None,
            # A model chosen for this conversation wins over its agent's. An
            # agent's model is for the agent's engine: a conversation on another
            # engine (the agent changed engine since) uses that engine's default.
            model=thread.model
            or (agent.model if agent is not None and agent.provider == thread.provider else None),
            provider_session_id=thread.provider_session_id,
            provider_state=thread.provider_state,
            resume=thread.session_state == "active",
            history=history,
            # The agent's instructions, then its notes (MEMORY.md) when on.
            instructions=agent_memory.turn_instructions(agent) if agent is not None else None,
            approval_mode=approval_mode
            if approval_mode is not None
            else (agent.approval_mode if agent is not None else None),
            engine_config=config,
        )
        run = ActiveRun(
            thread_id=thread_id,
            turn_id=turn.id,
            owner_token=turn.owner_token,
            ctx=ctx,
            agent_id=agent.id if agent is not None else None,
            approval_timeout=approval_timeout,
        )
        run.settings_snapshot = snapshot
        run.recorder = TurnRecorder(self, run)
        run.gate = ThreadApprovalGate(self, run)
        self._runs[turn.id] = run
        run.task = asyncio.create_task(self._run(run, adapter, capabilities_of(adapter).computers))
        self.hub.notify(thread_id)
        return turn

    async def stop_turn(self, thread_id: int) -> int:
        """Stop the thread's running turn: the provider process is killed, the
        text written so far is kept (marked incomplete), any open approval is
        cancelled, and the turn ends as `cancelled`. The conversation continues
        with its session. Returns the turn's id; the end arrives as an event.
        Raises NoRunningTurn, or TurnNotHere when the turn belongs to another
        process."""
        run = next((r for r in self._runs.values() if r.thread_id == thread_id), None)
        if run is None:
            async with self.session() as db:
                running = await store.running_turn_id(db, thread_id)
            raise TurnNotHere(thread_id) if running is not None else NoRunningTurn(thread_id)
        run.stop_requested = True
        run.ctx.abort()
        return run.turn_id

    async def _provider_status(self, adapter: ProviderAdapter, config: dict | None = None) -> ProviderStatus:
        loop = asyncio.get_running_loop()
        cli = app_settings.resolve("LOCAL_CLAUDE_CLI_PATH")
        # A changed engine configuration is a new status (only a fingerprint of
        # it is kept in memory, never the values).
        fingerprint = hash(json.dumps(config, sort_keys=True, default=str)) if config else None
        cache_key = (adapter.key, cli.effective_value, cli.error, fingerprint)
        cached = self._status_cache.get(cache_key)
        if cached is not None and loop.time() - cached[0] < self.status_ttl:
            return cached[1]
        status = await adapter.status()
        if status.ready:
            self._status_cache[cache_key] = (loop.time(), status)
        return status

    # -- one turn ------------------------------------------------------------------

    async def _run(self, run: ActiveRun, adapter: ProviderAdapter, computers: bool = True) -> None:
        heartbeat = asyncio.create_task(self._heartbeat(run))
        watchdog = None
        binding = None
        try:
            try:
                # An agent with a computer runs its tools there or not at all:
                # a computer that cannot be used fails the turn, never falls
                # back to the host. An engine that cannot use a computer never
                # runs for an agent that has one.
                if not computers and run.agent_id is not None and await self.computers.for_agent(run.agent_id):
                    raise _EngineWithoutComputers()
                binding = await self.computers.attach_for_turn(
                    run.agent_id, run.turn_id, str(run.ctx.workspace) if run.ctx.workspace else None
                )
                run.ctx.computer = binding
                # Watched from here: starting a computer can take a while, and
                # is not the engine doing nothing.
                run.touch()
                watchdog = asyncio.create_task(self._watchdog(run))
                with app_settings.use(run.settings_snapshot):
                    run.adapter_task = asyncio.ensure_future(adapter.run_turn(run.ctx, run.recorder, run.gate))
                try:
                    outcome = await run.adapter_task
                except asyncio.CancelledError:
                    task = asyncio.current_task()
                    if not run.stalled or (task is not None and task.cancelling()):
                        raise
                    # The watchdog gave up on an adapter that did not return
                    # after its engine was stopped.
                    outcome = TurnOutcome("failed", error_code="engine_stalled")
            except _EngineWithoutComputers:
                outcome = TurnOutcome(
                    "failed",
                    error="This agent has a computer, and this engine cannot run its tools in one. "
                    "Switch the agent to Claude, or remove its computer.",
                    error_code="engine_without_computers",
                )
            except computer_service.ComputerUnavailable as exc:
                outcome = TurnOutcome("failed", error=str(exc), error_code="computer_unavailable")
            except Exception:  # noqa: BLE001
                logger.exception("provider run crashed", thread_id=run.thread_id)
                outcome = TurnOutcome(
                    "failed", error="Local run failed unexpectedly", error_code="runner_error"
                )
        finally:
            heartbeat.cancel()
            if watchdog is not None:
                watchdog.cancel()
            await asyncio.gather(heartbeat, *([watchdog] if watchdog else []), return_exceptions=True)
            await self.computers.release_turn(binding, run.turn_id)
        if run.stop_requested and outcome.status != "succeeded":
            # Killed on request. A turn that finished on its own just before the
            # stop arrived keeps its real result.
            outcome = TurnOutcome(
                "cancelled",
                error_code="stopped",
                model=outcome.model,
                usage=outcome.usage,
                partial_text=outcome.partial_text,
                session_state=outcome.session_state,
                provider_state=outcome.provider_state,
            )
        elif run.stalled and outcome.status != "succeeded":
            seconds = settings.TURN_STALL_SECONDS
            outcome = TurnOutcome(
                "failed",
                error=f"The engine stopped responding: nothing happened for {seconds:g} seconds, "
                "so it was stopped. Send the message again to retry.",
                error_code="engine_stalled",
                model=outcome.model,
                usage=outcome.usage,
                partial_text=outcome.partial_text,
                session_state=outcome.session_state,
                provider_state=outcome.provider_state,
            )
        try:
            await self._finish(run, outcome)
        except Exception:  # noqa: BLE001
            # Nothing more can be done here; the lease will expire and the sweep
            # will mark the turn interrupted. Never replayed.
            logger.exception("could not record the end of a turn", turn_id=run.turn_id)
        finally:
            self._runs.pop(run.turn_id, None)
            self.hub.message_done(run.thread_id)
            self.hub.notify(run.thread_id)

    async def _watchdog(self, run: ActiveRun) -> None:
        """Fail a turn whose engine has done nothing (no event, no typing) for
        TURN_STALL_SECONDS: its process died without the adapter noticing, or
        hung. A turn waiting on an approval is not stalled. The engine is
        stopped like a Stop (its process group killed); an adapter that still
        does not return is given up on after STALL_GRACE_SECONDS."""
        stall = settings.TURN_STALL_SECONDS
        if not stall or stall <= 0:
            return
        loop = asyncio.get_running_loop()
        while True:
            await asyncio.sleep(min(max(stall / 4, 0.05), 5.0))
            if run.gate is not None and run.gate.waiting:
                run.touch()
                continue
            if loop.time() - run.last_activity < stall:
                continue
            run.stalled = True
            logger.warning("turn stalled; stopping its engine", turn_id=run.turn_id, seconds=stall)
            run.ctx.abort()
            await asyncio.sleep(STALL_GRACE_SECONDS)
            task = run.adapter_task
            if task is not None and not task.done():
                logger.warning("engine did not stop; giving up on it", turn_id=run.turn_id)
                task.cancel()
            return

    async def _heartbeat(self, run: ActiveRun) -> None:
        while True:
            await asyncio.sleep(settings.THREAD_HEARTBEAT_SECONDS)
            try:
                async with self.session() as db:
                    owned = await store.heartbeat(db, run.turn_id, run.owner_token)
            except Exception:  # noqa: BLE001
                logger.exception("heartbeat failed", turn_id=run.turn_id)
                continue
            if not owned:
                run.recorder.lose()
                return

    async def _finish(self, run: ActiveRun, outcome: TurnOutcome) -> None:
        if run.recorder.lost:
            logger.warning("turn lost its owner; nothing more recorded", turn_id=run.turn_id)
            return
        succeeded = outcome.status == "succeeded"
        extra_events: list[tuple[str, dict]] = []
        if not succeeded and outcome.partial_text:
            extra_events.append(
                ("assistant.message", {"text": outcome.partial_text, "incomplete": True})
            )
        if succeeded:
            event_type = "turn.completed"
            event_data = {"status": "succeeded", "result": outcome.result or ""}
        else:
            event_type = "turn.cancelled" if outcome.status == "cancelled" else "turn.failed"
            event_data = {
                "status": outcome.status,
                "error": outcome.error,
                "error_code": outcome.error_code,
                "result": outcome.partial_text or "",
            }
        async with self.session() as db:
            recorded = await store.finish_turn(
                db,
                run.turn_id,
                run.owner_token,
                status=outcome.status,
                result=outcome.result if succeeded else outcome.partial_text,
                error=outcome.error,
                error_code=outcome.error_code,
                model=outcome.model,
                usage=outcome.usage,
                event_type=event_type,
                event_data=event_data,
                extra_events=extra_events,
                session_state=outcome.session_state,
                provider_state=getattr(outcome, "provider_state", None),
            )
        if not recorded:
            run.recorder.lose()
            logger.warning("turn ended after losing its owner; nothing recorded", turn_id=run.turn_id)

    # -- approvals ----------------------------------------------------------------------

    async def answer_approval(
        self, approval_id: str, decision: str, *, remember: bool = False
    ) -> bool:
        """The person's answer (`allow` or `deny`). True if it was recorded; False
        means it changed nothing (already answered, cancelled, or its turn is no
        longer running) and the caller must answer 409. An Allow authorizes
        nothing until the run that owns the turn re-checks and delivers it.
        With `remember`, an allowed Bash command is also saved as an exact
        "always allow" rule (only when the Allow was recorded)."""
        if decision not in (ALLOW, DENY):
            raise ValueError("decision must be 'allow' or 'deny'")
        status = "allowed" if decision == ALLOW else "denied"
        async with self.session() as db:
            won = await store.decide_approval(db, approval_id, status)
            approval = await store.get_approval(db, approval_id)
        if won:
            future = self._waiters.get(approval_id)
            if future is not None and not future.done():
                future.set_result(status)
            if approval is not None:
                decision_log.record(
                    status,
                    thread_id=approval.thread_id,
                    turn_id=approval.turn_id,
                    approval_id=approval_id,
                    tool=approval.tool,
                    kind=approval.kind,
                    summary=approval.summary,
                    source="user",
                    remembered=remember and decision == ALLOW,
                    mode=self._mode_of(approval.turn_id),
                )
            if remember and decision == ALLOW and approval is not None:
                await self._remember(approval)
        if approval is not None:
            self.hub.notify(approval.thread_id)
        return won

    def _mode_of(self, turn_id: int) -> str | None:
        """The approval mode a turn running here uses; None if it is not here."""
        run = self._runs.get(turn_id)
        if run is None:
            return None
        return run.ctx.approval_mode or approval_modes.DEFAULT_MODE

    async def _remember(self, approval) -> None:
        command = approval.input.get("command") if isinstance(approval.input, dict) else None
        if approval.tool not in approval_allowlist.COMMAND_TOOLS or not isinstance(command, str):
            return
        try:
            async with self.session() as db:
                thread = await store.get_thread(db, approval.thread_id)
                workspace = thread.workspace_path if thread is not None else None
                if not workspace:
                    return  # no folder to tie the rule to: allowed this once, not saved
                await approval_allowlist.add_rule(
                    db, self.owner_id, approval_allowlist.EXACT, command, str(Path(workspace))
                )
        except approval_allowlist.InvalidRule:
            pass  # a credential, the safety floor, too long or too many: allowed this once, not saved
        except Exception:  # noqa: BLE001
            logger.exception("could not save an always-allow rule", thread_id=approval.thread_id)

    # -- recovery and shutdown ------------------------------------------------------------

    async def sweep_stale(self) -> list[int]:
        """Interrupt every running turn whose lease expired (its owner stopped
        heartbeating). Nothing is resumed or replayed."""
        async with self.session() as db:
            interrupted = await store.interrupt_stale_turns(
                db, stale_after=timedelta(seconds=settings.THREAD_LEASE_SECONDS)
            )
            for turn_id in interrupted:
                turn = await store.get_turn(db, turn_id)
                if turn is not None:
                    self.hub.notify(turn.thread_id)
        if interrupted:
            logger.warning("interrupted turns whose owner stopped", turn_ids=interrupted)
        return interrupted

    def reopen(self) -> None:
        """The app is (re)starting in this process: turns may start again."""
        self._closing = False

    async def shutdown(self) -> None:
        """This process is stopping. Kill its provider processes and interrupt
        its own turns at once, instead of leaving them for the lease to expire."""
        self._closing = True
        runs = list(self._runs.values())
        for run in runs:
            run.recorder.lose()
        for run in runs:
            try:
                async with self.session() as db:
                    await store.interrupt_own_turn(db, run.turn_id, run.owner_token, reason="shutdown")
            except Exception:  # noqa: BLE001
                logger.exception("could not interrupt a turn on shutdown", turn_id=run.turn_id)
            self.hub.notify(run.thread_id)
        tasks = [run.task for run in runs if run.task is not None]
        if tasks:
            await asyncio.wait(tasks, timeout=SHUTDOWN_WAIT_SECONDS)


runtime = ThreadRuntime()
