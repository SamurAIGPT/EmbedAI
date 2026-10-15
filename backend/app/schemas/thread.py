from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ThreadCreateRequest(BaseModel):
    # An unknown provider is a 422. Claude is the only one registered today.
    provider: str = Field(default="claude", min_length=1, max_length=64)
    # What the person asked for; omitted means the provider's default.
    model: str | None = Field(default=None, max_length=200)
    # Omitted: set from the first prompt.
    title: str | None = Field(default=None, max_length=200)


class ThreadResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str | None
    provider: str
    model: str | None
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


class ApprovalAnswerRequest(BaseModel):
    decision: Literal["allow", "deny"]


class ApprovalAnswerResponse(BaseModel):
    status: Literal["recorded"] = "recorded"
    decision: Literal["allow", "deny"]
