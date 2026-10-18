"""Approvals for local Claude's tool calls.

With tools on, the Claude CLI stops before each tool call it would ask a
person about and waits for an answer. This broker holds those questions until
someone answers them in the web UI (or the time runs out) and then replies to
the CLI. Nothing is ever approved by default: an unanswered, expired,
cancelled or unreadable request is a refusal.

Like the run hub this is in memory, on the event loop thread only (single
backend process). A restart ends the run it belonged to, so nothing here needs
to survive one. Durable approvals arrive with durable threads (F04).

The answer sent to the CLI always carries the exact input that was shown to
the person, so what was approved is what runs.
"""

import asyncio
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.core import app_settings
from app.services.local_claude_stream import summarize_tool
from app.services.local_run_hub import (
    APPROVAL_REQUESTED,
    APPROVAL_RESOLVED,
    RunHub,
)
from app.services.local_run_hub import hub as default_hub

# A call too big to show a person in full is refused, never approved unseen.
MAX_REVIEWABLE_INPUT_CHARS = 50_000

DENY_BY_USER = "The user denied this action."
DENY_EXPIRED = "Nobody answered in time, so this action was not allowed."
DENY_CANCELLED = "This action was cancelled."
DENY_TOO_LARGE = (
    "This action is too large to be reviewed, so it was not allowed. Ask for a smaller step."
)

# The only things a person can answer.
ALLOW = "allow"
DENY = "deny"


class ApprovalNotFound(Exception):
    """No such open approval for this run: wrong id, or already answered."""


@dataclass(eq=False)
class PendingApproval:
    id: str
    run_id: int
    request_id: str
    tool: str
    summary: str
    input: dict
    created_at: str
    respond: Callable[[dict], bool]
    on_close: Callable[[], None] | None = None
    timer: asyncio.TimerHandle | None = field(default=None, repr=False)

    def public(self) -> dict:
        return {
            "id": self.id,
            "task_id": self.run_id,
            "tool": self.tool,
            "summary": self.summary,
            "input": self.input,
            "created_at": self.created_at,
        }


def _allow_payload(request_id: str, tool_input: dict) -> dict:
    return {
        "type": "control_response",
        "response": {
            "subtype": "success",
            "request_id": request_id,
            "response": {"behavior": "allow", "updatedInput": tool_input},
        },
    }


def _deny_payload(request_id: str, message: str) -> dict:
    return {
        "type": "control_response",
        "response": {
            "subtype": "success",
            "request_id": request_id,
            "response": {"behavior": "deny", "message": message},
        },
    }


class ApprovalBroker:
    def __init__(self, hub: RunHub = default_hub) -> None:
        self._hub = hub
        self._pending: dict[str, PendingApproval] = {}

    def request(
        self,
        run_id: int,
        request_id: str,
        tool: str,
        tool_input: dict,
        respond: Callable[[dict], bool],
        *,
        on_open: Callable[[], None] | None = None,
        on_close: Callable[[], None] | None = None,
    ) -> PendingApproval | None:
        """Ask a person. `respond` sends the answer to the CLI. `on_open` and
        `on_close` bracket the wait (the runner pauses its time limit with
        them). Returns None when the request was refused straight away."""
        if len(json.dumps(tool_input, ensure_ascii=False)) > MAX_REVIEWABLE_INPUT_CHARS:
            respond(_deny_payload(request_id, DENY_TOO_LARGE))
            return None

        approval = PendingApproval(
            id=uuid.uuid4().hex,
            run_id=run_id,
            request_id=request_id,
            tool=tool,
            summary=summarize_tool(tool, tool_input),
            input=tool_input,
            created_at=datetime.now(UTC).isoformat(),
            respond=respond,
            on_close=on_close,
        )
        self._pending[approval.id] = approval
        if on_open is not None:
            on_open()
        loop = asyncio.get_running_loop()
        approval.timer = loop.call_later(
            app_settings.get("LOCAL_APPROVAL_TIMEOUT_SECONDS", live=True), self._expire, approval.id
        )
        self._hub.publish(run_id, APPROVAL_REQUESTED, approval.public())
        return approval

    def list_pending(self, run_id: int) -> list[dict]:
        return [a.public() for a in self._pending.values() if a.run_id == run_id]

    def answer(self, run_id: int, approval_id: str, decision: str) -> None:
        """Raises ApprovalNotFound for an id this run does not have open
        (including one that was already answered, expired or cancelled)."""
        if decision not in (ALLOW, DENY):
            raise ValueError("decision must be 'allow' or 'deny'")
        approval = self._pending.get(approval_id)
        if approval is None or approval.run_id != run_id:
            raise ApprovalNotFound(approval_id)
        if decision == ALLOW:
            self._resolve(approval, ALLOW, _allow_payload(approval.request_id, approval.input))
        else:
            self._resolve(approval, DENY, _deny_payload(approval.request_id, DENY_BY_USER))

    def cancel_request(self, run_id: int, request_id: str) -> None:
        """The CLI withdrew a question (the tool call was abandoned)."""
        for approval in list(self._pending.values()):
            if approval.run_id == run_id and approval.request_id == request_id:
                self._resolve(approval, "cancelled", _deny_payload(request_id, DENY_CANCELLED))

    def cancel_run(self, run_id: int) -> None:
        """The run ended: nothing it was waiting on can matter any more."""
        for approval in list(self._pending.values()):
            if approval.run_id == run_id:
                self._resolve(
                    approval, "cancelled", _deny_payload(approval.request_id, DENY_CANCELLED)
                )

    def _expire(self, approval_id: str) -> None:
        approval = self._pending.get(approval_id)
        if approval is not None:
            self._resolve(
                approval, "expired", _deny_payload(approval.request_id, DENY_EXPIRED)
            )

    def _resolve(self, approval: PendingApproval, outcome: str, payload: dict) -> None:
        if self._pending.pop(approval.id, None) is None:
            return  # someone else resolved it first; the first answer stands
        if approval.timer is not None:
            approval.timer.cancel()
        try:
            approval.respond(payload)
        finally:
            if approval.on_close is not None:
                approval.on_close()
            self._hub.publish(
                approval.run_id,
                APPROVAL_RESOLVED,
                {"id": approval.id, "outcome": outcome},
            )


broker = ApprovalBroker()
