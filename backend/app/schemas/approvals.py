from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class DecisionEntry(BaseModel):
    id: str
    at: datetime
    event: Literal["shown", "allowed", "denied", "auto_allowed", "expired", "cancelled"]
    thread_id: int | None = None
    turn_id: int | None = None
    approval_id: str | None = None
    tool: str | None = None
    kind: str | None = None
    # One line, secrets removed, cut short. Never the call's full input.
    summary: str | None = None
    # Who or what decided: user | saved_rule | timeout | provider
    source: str | None = None
    # Which rule decided: the saved command, or `safety_floor:<rule>` when a
    # saved rule matched but the call was asked about instead.
    rule: str | None = None
    # The person asked to always allow this command (on an `allowed` entry).
    remembered: bool | None = None


class DecisionsPage(BaseModel):
    items: list[DecisionEntry]
    # Pass as `before` to get the next (older) page; null on the last page.
    next_before: str | None = None


class ApprovalRuleCreate(BaseModel):
    match: Literal["exact", "prefix"] = "exact"
    command: str = Field(min_length=1, max_length=1000)
    # Absolute folder the rule applies in; omitted means any workspace.
    workspace: str | None = Field(default=None, max_length=4096)


class ApprovalRuleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    match: Literal["exact", "prefix"]
    command: str
    workspace: str | None = None
    created_at: datetime
