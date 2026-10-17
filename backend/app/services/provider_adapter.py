"""The seam between the thread runtime and a provider (Claude today; Codex or a
local model later).

An adapter turns one provider's protocol into the app's normalized events and
asks a person through the approval gate. It never touches the database: the
runtime and the recorder own storage, ordering and ownership. Everything a
provider-specific adapter needs is on `TurnContext`; everything it reports goes
through `EventSink` and `TurnOutcome`.

Deliberately small. How Codex or an app-driven local model maps onto this has
not been verified and is not guessed at here.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

# What the person (or the clock) decided about one question.
ALLOW = "allow"
DENY = "deny"
CANCELLED = "cancelled"  # the provider withdrew the question
EXPIRED = "expired"  # nobody answered in time
LOST = "lost"  # this run no longer owns its turn: send nothing, stop


@dataclass
class Decision:
    kind: str
    message: str | None = None


@dataclass
class ProviderStatus:
    ready: bool
    version: str | None = None
    error: str | None = None


@dataclass
class TurnOutcome:
    """How a turn ended. `status` is one of succeeded, failed, cancelled."""

    status: str
    result: str | None = None
    error: str | None = None
    # Machine-readable cause so the UI can offer the right action, for example
    # session_resume_failed, timeout, output_too_large.
    error_code: str | None = None
    model: str | None = None
    usage: dict | None = None
    # Text streamed but not covered by any completed assistant message. Stored
    # as an `incomplete` assistant message when the turn did not succeed.
    partial_text: str | None = None
    # The thread's session state after this turn (`active`, `broken`), or None to
    # leave it as it is.
    session_state: str | None = None


@dataclass
class TurnContext:
    thread_id: int
    turn_id: int
    prompt: str
    workspace: Path | None
    model: str | None
    provider_session_id: str | None
    provider_state: dict | None
    # True when the thread's session already exists and must be continued.
    resume: bool
    # The thread's stored history, for a provider that keeps no session of its
    # own. A session provider (Claude) never calls it.
    history: Callable[[], Awaitable[list[dict]]]
    # ask | accept_edits | plan (approval_modes.py); None means ask. Set by the
    # runtime from the thread's agent; an adapter maps it onto its own provider.
    approval_mode: str | None = None
    # Extra instructions from the thread's named agent, for the provider's
    # system prompt (appended, never replacing it), or None. Read fresh each turn.
    instructions: str | None = None
    # The agent's computer (app/computers/service.TurnComputer) when it has
    # one: the turn's tools then run there, never on the host.
    computer: object | None = None
    _abort_callbacks: list[Callable[[], None]] = field(default_factory=list)
    aborted: bool = False

    def on_abort(self, callback: Callable[[], None]) -> None:
        """Register what stops the provider's work (kill the process, close the
        connection). Called at most once, from any thread, when the run no
        longer owns its turn. If the run was already aborted it is called now."""
        self._abort_callbacks.append(callback)
        if self.aborted:
            callback()

    def abort(self) -> None:
        if self.aborted:
            return
        self.aborted = True
        for callback in list(self._abort_callbacks):
            try:
                callback()
            except Exception:  # noqa: BLE001  (aborting must never raise)
                pass


class EventSink(Protocol):
    """Where an adapter reports what happened. Runs on the event loop."""

    @property
    def lost(self) -> bool: ...

    async def emit(self, type: str, data: dict) -> bool:
        """Store and announce one normalized event. False means the run no
        longer owns its turn: the adapter must stop."""

    def delta(self, text: str) -> None:
        """Typing text of the message in progress. Display only, never stored."""


@dataclass
class ApprovalHandle:
    approval_id: str
    request_id: str
    # The provider's tool name, filled in by an adapter that needs it.
    tool: str | None = None


class ApprovalGate(Protocol):
    async def open(
        self, *, request_id: str, tool: str, input: dict, summary: str | None, kind: str | None
    ) -> ApprovalHandle | None:
        """Record the question (it becomes visible to the person). None means the
        run lost its turn."""

    async def decide(self, handle: ApprovalHandle) -> Decision:
        """Wait for the answer. An Allow is returned only after the run's
        ownership was re-checked atomically, so sending it is safe."""

    async def cancel(self, request_id: str) -> None:
        """The provider withdrew the question."""


class ProviderAdapter(Protocol):
    key: str

    def new_session_id(self) -> str | None:
        """A fresh provider session id for a new thread (or after a reset), or
        None for a provider that keeps no session of its own."""

    async def status(self) -> ProviderStatus: ...

    async def run_turn(
        self, ctx: TurnContext, sink: EventSink, gate: ApprovalGate
    ) -> TurnOutcome: ...
