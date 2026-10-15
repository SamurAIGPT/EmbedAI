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
from dataclasses import dataclass, field
from datetime import timedelta
from uuid import uuid4

from app.core.config import settings
from app.core.logging import get_logger
from app.services import thread_store as store
from app.services.claude_adapter import ClaudeAdapter
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
        self._last_seq = event.seq
        if type == "assistant.message":
            self._runtime.hub.message_done(run.thread_id)
        self._runtime.hub.notify(run.thread_id)
        return True

    def delta(self, text: str) -> None:
        if not self._lost:
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
        runtime._waiters[approval.id] = asyncio.get_running_loop().create_future()
        self._by_request[request_id] = approval.id
        runtime.hub.notify(run.thread_id)
        return ApprovalHandle(approval_id=approval.id, request_id=request_id)

    async def decide(self, handle: ApprovalHandle) -> Decision:
        run, runtime = self._run, self._runtime
        loop = asyncio.get_running_loop()
        future = runtime._waiters.get(handle.approval_id)
        deadline = loop.time() + settings.LOCAL_APPROVAL_TIMEOUT_SECONDS
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
    adapters: dict[str, ProviderAdapter] = field(default_factory=lambda: {"claude": ClaudeAdapter()})
    status_ttl: float = STATUS_CACHE_SECONDS
    instance_id: str = field(default_factory=lambda: uuid4().hex)
    owner_id: str = LOCAL_OWNER_ID
    _runs: dict[int, ActiveRun] = field(default_factory=dict)
    _waiters: dict[str, asyncio.Future] = field(default_factory=dict)
    _status_cache: dict[str, tuple[float, ProviderStatus]] = field(default_factory=dict)
    _session_factory: object | None = None

    def session(self):
        """A new database session. Looked up late so tests can substitute it."""
        if self._session_factory is not None:
            return self._session_factory()
        from app.db.session import AsyncSessionLocal

        return AsyncSessionLocal()

    # -- threads ----------------------------------------------------------------------

    async def create_thread(
        self, *, provider: str = "claude", model: str | None = None, title: str | None = None
    ) -> store.Thread:
        """A new thread for this runtime's owner. Raises UnknownProvider."""
        adapter = self.adapters.get(provider)
        if adapter is None:
            raise UnknownProvider(provider)
        session_id = adapter.new_session_id()
        async with self.session() as db:
            return await store.create_thread(
                db,
                owner_id=self.owner_id,
                provider=provider,
                model=model,
                title=title,
                provider_session_id=session_id,
                session_state="new" if session_id is not None else "none",
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

    async def send_turn(self, thread_id: int, prompt_text: str) -> store.Turn:
        """Start a turn and run it in the background. Raises ThreadNotFound,
        UnknownProvider, SessionBroken, ProviderUnavailable, or ThreadBusy (a
        turn is already running)."""
        async with self.session() as db:
            thread = await store.get_thread(db, thread_id)
        if thread is None:
            raise store.ThreadNotFound(thread_id)
        adapter = self.adapters.get(thread.provider)
        if adapter is None:
            raise UnknownProvider(thread.provider)
        if thread.session_state == "broken":
            raise SessionBroken(thread_id)
        status = await self._provider_status(adapter)
        if not status.ready:
            raise ProviderUnavailable(status.error or f"{thread.provider} is not ready")

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
            workspace=None,
            model=thread.model,
            provider_session_id=thread.provider_session_id,
            provider_state=thread.provider_state,
            resume=thread.session_state == "active",
            history=history,
        )
        run = ActiveRun(thread_id=thread_id, turn_id=turn.id, owner_token=turn.owner_token, ctx=ctx)
        run.recorder = TurnRecorder(self, run)
        run.gate = ThreadApprovalGate(self, run)
        self._runs[turn.id] = run
        run.task = asyncio.create_task(self._run(run, adapter))
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

    async def _provider_status(self, adapter: ProviderAdapter) -> ProviderStatus:
        loop = asyncio.get_running_loop()
        cached = self._status_cache.get(adapter.key)
        if cached is not None and loop.time() - cached[0] < self.status_ttl:
            return cached[1]
        status = await adapter.status()
        if status.ready:
            self._status_cache[adapter.key] = (loop.time(), status)
        return status

    # -- one turn ------------------------------------------------------------------

    async def _run(self, run: ActiveRun, adapter: ProviderAdapter) -> None:
        heartbeat = asyncio.create_task(self._heartbeat(run))
        try:
            try:
                outcome = await adapter.run_turn(run.ctx, run.recorder, run.gate)
            except Exception:  # noqa: BLE001
                logger.exception("provider run crashed", thread_id=run.thread_id)
                outcome = TurnOutcome(
                    "failed", error="Local run failed unexpectedly", error_code="runner_error"
                )
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
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
            )
        if not recorded:
            run.recorder.lose()
            logger.warning("turn ended after losing its owner; nothing recorded", turn_id=run.turn_id)

    # -- approvals ----------------------------------------------------------------------

    async def answer_approval(self, approval_id: str, decision: str) -> bool:
        """The person's answer (`allow` or `deny`). True if it was recorded; False
        means it changed nothing (already answered, cancelled, or its turn is no
        longer running) and the caller must answer 409. An Allow authorizes
        nothing until the run that owns the turn re-checks and delivers it."""
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
            self.hub.notify(approval.thread_id)
        return won

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

    async def shutdown(self) -> None:
        """This process is stopping. Kill its provider processes and interrupt
        its own turns at once, instead of leaving them for the lease to expire."""
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
