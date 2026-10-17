from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ScheduleKind = Literal["cron", "weekdays", "interval", "once", "manual"]
ApprovalMode = Literal["ask", "accept_edits", "plan"]


class RoutineCreateRequest(BaseModel):
    agent_id: int
    name: str = Field(min_length=1, max_length=80)
    prompt: str = Field(min_length=1, max_length=20_000)
    schedule_kind: ScheduleKind = "manual"
    schedule_spec: dict = Field(default_factory=dict)
    timezone: str = Field(default="UTC", min_length=1, max_length=64)
    paused: bool = False
    run_limit_minutes: int = 30
    approval_mode: ApprovalMode = "ask"
    approval_timeout_minutes: int = 30
    catch_up_minutes: int = 60


class RoutineUpdateRequest(BaseModel):
    agent_id: int | None = None
    name: str | None = Field(default=None, min_length=1, max_length=80)
    prompt: str | None = Field(default=None, min_length=1, max_length=20_000)
    schedule_kind: ScheduleKind | None = None
    schedule_spec: dict | None = None
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    run_limit_minutes: int | None = None
    approval_mode: ApprovalMode | None = None
    approval_timeout_minutes: int | None = None
    catch_up_minutes: int | None = None


class SchedulePreviewRequest(BaseModel):
    schedule_kind: ScheduleKind
    schedule_spec: dict = Field(default_factory=dict)
    timezone: str = Field(default="UTC", min_length=1, max_length=64)


class SchedulePreviewResponse(BaseModel):
    summary: str
    next_dates: list[datetime]


class RoutineRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    routine_id: int
    trigger: str
    state: str
    scheduled_for: datetime | None
    thread_id: int | None
    turn_id: int | None
    result: str | None
    error: str | None
    waiting_since: datetime | None
    waited_seconds: int
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


class RoutineResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    agent_id: int
    name: str
    prompt: str
    schedule_kind: str
    schedule_spec: dict
    timezone: str
    paused: bool
    paused_reason: str | None
    next_run_at: datetime | None
    run_limit_minutes: int
    approval_mode: str
    approval_timeout_minutes: int
    catch_up_minutes: int
    last_run_at: datetime | None
    created_at: datetime
    updated_at: datetime
    # Derived for the client.
    summary: str = ""
    next_dates: list[datetime] = Field(default_factory=list)
    webhook_enabled: bool = False
    latest_run: RoutineRunResponse | None = None


class WebhookSecretResponse(BaseModel):
    routine: RoutineResponse
    # Shown once: only its hash is stored.
    secret: str
    path: str


class RoutineImportRequest(BaseModel):
    agent_id: int
    documents: list[dict] = Field(min_length=1, max_length=50)


class NotificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    title: str
    body: str | None
    routine_id: int | None
    run_id: int | None
    thread_id: int | None
    read_at: datetime | None
    created_at: datetime


class UnreadCountResponse(BaseModel):
    unread: int
