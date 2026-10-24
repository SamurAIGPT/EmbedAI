from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

Priority = Literal["low", "normal", "high", "urgent"]


class WorkFields(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=240)
    description: str = Field(default="", max_length=50000)
    priority: Priority = "normal"
    due_date: date | None = None
    due_at: AwareDatetime | None = None
    timezone: str = "UTC"
    agent_id: int | None = Field(default=None, gt=0)
    team_id: int | None = Field(default=None, gt=0)
    thread_id: int | None = Field(default=None, gt=0)

    @field_validator("title")
    @classmethod
    def clean_title(cls, value):
        if not value.strip():
            raise ValueError("Give the task a title")
        return value.strip()

    @field_validator("timezone")
    @classmethod
    def valid_zone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Choose a valid IANA timezone") from None
        return value


class WorkCreate(WorkFields):
    board_id: int = Field(gt=0)
    column_id: int = Field(gt=0)


class WorkUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1)
    title: str | None = None
    description: str | None = None
    priority: Priority | None = None
    due_date: date | None = None
    due_at: AwareDatetime | None = None
    timezone: str | None = None
    agent_id: int | None = Field(default=None, gt=0)
    team_id: int | None = Field(default=None, gt=0)
    thread_id: int | None = Field(default=None, gt=0)
    column_id: int | None = Field(default=None, gt=0)
    archived: bool | None = None


class WorkResponse(WorkFields):
    model_config = ConfigDict(from_attributes=True)
    id: int
    board_id: int
    column_id: int
    position: int
    revision: int
    archived: bool
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ColumnResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    key: str
    name: str
    position: int


class BoardResponse(BaseModel):
    id: int
    name: str
    columns: list[ColumnResponse]
