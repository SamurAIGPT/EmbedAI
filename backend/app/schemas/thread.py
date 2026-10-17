from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.computer import ComputerSummary


class ThreadCreateRequest(BaseModel):
    # An unknown provider is a 422. Claude is the only one registered today.
    provider: str = Field(default="claude", min_length=1, max_length=64)
    # What the person asked for; omitted means the provider's default.
    model: str | None = Field(default=None, max_length=200)
    # Omitted: set from the first prompt.
    title: str | None = Field(default=None, max_length=200)
    # Start the conversation from a named agent: its provider is used (naming a
    # different one is a 422), its workspace (or its own folder under the
    # default root) is pinned, and each message uses its
    # current instructions, approval mode and, unless `model` is set here, model.
    agent_id: int | None = None


class ThreadUpdateRequest(BaseModel):
    """Rename and/or archive. Send at least one field."""

    title: str | None = Field(default=None, max_length=200)
    archived: bool | None = None

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("title must not be blank")
        return value

    @model_validator(mode="after")
    def _something_to_change(self):
        if self.title is None and self.archived is None:
            raise ValueError("send a title, archived, or both")
        return self


class ThreadResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str | None
    provider: str
    model: str | None
    workspace_path: str | None = None
    # The agent this conversation was started from; null if none, or if it
    # was deleted.
    agent_id: int | None = None
    # The computer this conversation's turns run their tools in (its agent's),
    # or null when they run on this machine.
    computer: ComputerSummary | None = None
    # new | active | broken | none. `broken` means a resume failed and the
    # session must be reset before the thread can take another message.
    session_state: str
    # The seq of the thread's newest stored event.
    last_seq: int
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime


class TurnResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    seq: int
    prompt_text: str
    # running | succeeded | failed | cancelled | interrupted
    status: str
    # Machine-readable cause, for example session_resume_failed, timeout.
    error_code: str | None
    error: str | None
    result: str | None
    model: str | None
    usage: dict | None
    started_at: datetime
    finished_at: datetime | None


class ThreadDetailResponse(ThreadResponse):
    turns: list[TurnResponse]
    # The turn that is running now, if any.
    active_turn_id: int | None = None


class TurnCreateRequest(BaseModel):
    prompt_text: str = Field(min_length=1, max_length=100_000)

    @field_validator("prompt_text")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("prompt_text must not be blank")
        return value


class EventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    seq: int
    type: str
    turn_id: int | None
    data: dict
    created_at: datetime


class EventsPageResponse(BaseModel):
    events: list[EventResponse]
    # Pass this as `after` to read on.
    next_after: int
    has_more: bool


class ApprovalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    turn_id: int
    # The provider's own tool name, the exact input the call will run with, and
    # a one-line label.
    tool: str
    input: dict
    summary: str | None
    # command | file_write | file_read | network | other: for rendering.
    kind: str | None
    # pending | allowed | denied | expired | cancelled
    status: str
    created_at: datetime
    decided_at: datetime | None


class StopResponse(BaseModel):
    # The turn was told to stop; its end (`turn.cancelled`) arrives as an event.
    status: Literal["stopping"] = "stopping"
    turn_id: int


class ApprovalAnswerRequest(BaseModel):
    decision: Literal["allow", "deny"]
    # With an allow on a Bash call: also always allow this exact command from
    # now on (see /api/v1/approval-rules). Ignored for a deny or other tools.
    remember: bool = False


class ApprovalAnswerResponse(BaseModel):
    status: Literal["recorded"] = "recorded"
    decision: Literal["allow", "deny"]
    # With `remember`: the saved rule (DELETE /api/v1/approval-rules/{id}
    # undoes it), or null with `remember_error` saying why it was allowed this
    # once only (a credential, the safety floor, no folder, ...).
    rule_id: int | None = None
    remember_error: str | None = None
