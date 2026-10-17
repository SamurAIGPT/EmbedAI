"""The seam between the thread runtime and an engine (the `claude` CLI, the
`codex` app-server, any ACP agent, an OpenAI-compatible endpoint).

An adapter turns one engine's protocol into the app's normalized events and
asks a person through the approval gate. It never touches the database: the
runtime and the recorder own storage, ordering and ownership. Everything an
adapter needs is on `TurnContext`; everything it reports goes through
`EventSink` and `TurnOutcome`.

An adapter also describes itself for the engine registry
(app/services/engine_registry.py): a label, capability flags, a model list and
a probe. All of these are optional for an adapter written against the older,
smaller contract (tests have such doubles): the registry fills in safe
defaults (no capabilities, any model) and derives the probe from `status()`.
See docs/ENGINES.md.
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


@dataclass(frozen=True)
class Capabilities:
    """What an engine can do through this app. The runtime and the API read
    these flags, never engine names.

    tools      the engine can run tools (commands, file edits) in the workspace
    approvals  its tool calls can be put to a person through the approval gate
    mcp        it can mount an MCP server this app gives it
    images     it accepts images attached to a message (TurnContext.images);
               an image sent to any other engine is refused before the turn
    resume     a conversation continues with the engine's earlier context
    computers  its turns can run their tools in an agent computer
    """

    tools: bool = False
    approvals: bool = False
    mcp: bool = False
    images: bool = False
    resume: bool = False
    computers: bool = False

    def as_dict(self) -> dict:
        return {
            "tools": self.tools,
            "approvals": self.approvals,
            "mcp": self.mcp,
            "images": self.images,
            "resume": self.resume,
            "computers": self.computers,
        }


NO_CAPABILITIES = Capabilities()


@dataclass(frozen=True)
class ModelOption:
    id: str
    label: str
    default: bool = False


@dataclass
class EngineProbe:
    """What a probe found. `ready` means a turn can start now; `reason` says
    why not, in words a person can act on. Never credentials or account
    details."""

    installed: bool
    ready: bool
    version: str | None = None
    signed_in: bool | None = None
    reason: str | None = None
    # A live model catalog, when the engine reports one.
    models: list[ModelOption] | None = None


@dataclass
class ProviderStatus:
    ready: bool
    version: str | None = None
    error: str | None = None
    # For the engine registry, when the adapter knows: is the CLI there, and
    # is it signed in. None means "not reported".
    installed: bool | None = None
    signed_in: bool | None = None


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
    # The thread's adapter-private state after this turn (for example the
    # engine's own conversation id), or None to leave it as it is. Never
    # secrets, endpoints or keys.
    provider_state: dict | None = None


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
    # The engine's saved configuration (app/services/engine_config.py), for an
    # engine that has one: an ACP command, an endpoint and its key. Never
    # logged, stored in events or returned by the API.
    engine_config: dict | None = None
    # Images attached to this message, as {"path", "mime", "name"}. Only ever
    # set for an engine whose capabilities include `images`; attached text
    # files are already folded into `prompt`.
    images: list[dict] = field(default_factory=list)
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
    """The contract every engine implements. Beyond `key`, `new_session_id`,
    `status` and `run_turn`, an engine adapter declares:

        label: str                  shown in pickers
        kind: str                   "cli", "acp" or "api"
        capabilities: Capabilities
        models: list[ModelOption]   the static catalog; [] means "the engine's
                                    own default" only
        custom_models: bool         any model id is accepted, not just `models`
        needs_config: bool          a saved configuration is required
        async probe() -> EngineProbe
                                    a safe check with short timeouts; it may
                                    raise or hang, and the registry turns that
                                    into "unavailable"
        validate_config(payload, current) -> dict
        public_config(config) -> dict
                                    for engines with a saved configuration;
                                    public_config never includes a secret

    The registry reads each of these with a default when it is missing."""

    key: str

    def new_session_id(self) -> str | None:
        """A fresh provider session id for a new thread (or after a reset), or
        None for a provider that keeps no session of its own."""

    async def status(self) -> ProviderStatus: ...

    async def run_turn(
        self, ctx: TurnContext, sink: EventSink, gate: ApprovalGate
    ) -> TurnOutcome: ...
