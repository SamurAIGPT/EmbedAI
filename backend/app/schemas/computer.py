from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

Network = Literal["none", "default"]


class ComputerSettings(BaseModel):
    # A Docker image reference; a digest (`name@sha256:…`) pins it. Omitted on
    # create: the pinned default image.
    image: str | None = Field(default=None, min_length=1, max_length=300)
    cpus: float | None = Field(default=None, ge=0.1, le=64)
    memory_mb: int | None = Field(default=None, ge=128, le=262_144)
    pids_limit: int | None = Field(default=None, ge=32, le=65_536)
    # none: no network at all. default: Docker's default bridge (internet, no
    # published ports).
    network: Network | None = None
    # Stop after this many idle minutes; 0 never.
    idle_minutes: int | None = Field(default=None, ge=0, le=7 * 24 * 60)
    auto_start: bool | None = None
    browser_enabled: bool | None = None


class ComputerCreateRequest(ComputerSettings):
    kind: Literal["shell", "desktop"] = "shell"
    # Also make and start the machine now (needs the image on this computer).
    start: bool = False


class ComputerUpdateRequest(ComputerSettings):
    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def _something_to_change(self):
        if not self.model_fields_set:
            raise ValueError("send at least one field to change")
        for name in self.model_fields_set:
            if getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self


class ComputerHealthResponse(BaseModel):
    provider: str
    available: bool
    version: str | None = None
    error: str | None = None


class LastCommandResponse(BaseModel):
    tool: str | None = None
    subject: str | None = None
    exit_code: int | None = None
    timed_out: bool = False
    output: str = ""
    at: str | None = None


class ComputerSummary(BaseModel):
    """What an agent or a conversation shows of its computer."""

    id: int
    provider: str
    kind: Literal["shell", "desktop"] = "shell"
    name: str
    image: str
    network: Network
    # missing | creating | running | stopped | error | deleting
    state: str


class ComputerResponse(ComputerSummary):
    agent_id: int
    image_pinned: bool
    image_id: str | None
    cpus: float
    memory_mb: int
    pids_limit: int
    idle_minutes: int
    auto_start: bool
    browser_enabled: bool = False
    # What the provider reports right now (running, stopped, missing, foreign,
    # unsafe, unavailable), or null when it was not asked or cannot be reached.
    live_state: str | None = None
    # The machine was made from other settings; recreate it for them to apply.
    needs_recreate: bool = False
    problem: str | None = None
    last_error: str | None
    workspace_path: str | None
    guest_workspace: str
    container_id: str | None
    # A turn is using it now.
    busy: bool = False
    desktop_ready: bool = False
    last_used_at: datetime | None
    last_command: LastCommandResponse | None = None
    health: ComputerHealthResponse | None = None
    created_at: datetime
    updated_at: datetime


class ComputerDeleteResponse(BaseModel):
    # False: the machine could not be removed now (Docker stopped); it is
    # removed as soon as Docker answers, and the computer no longer shows.
    removed: bool
