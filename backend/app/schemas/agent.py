from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.computer import ComputerSummary

ApprovalMode = Literal["ask", "accept_edits", "plan"]

# Standing instructions are capped in bytes, not characters, so the limit means
# the same thing in every language.
INSTRUCTIONS_MAX_BYTES = 24_000


def _one_line(value: str, what: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError(f"{what} must not be blank")
    if "\n" in value or "\r" in value:
        raise ValueError(f"{what} must fit on one line")
    return value


def _instructions_fit(value: str) -> str:
    if len(value.encode("utf-8")) > INSTRUCTIONS_MAX_BYTES:
        raise ValueError(f"instructions must be at most {INSTRUCTIONS_MAX_BYTES} bytes")
    return value


def _optional_line(value: str | None) -> str | None:
    """A title: one line, or null/blank for none."""
    if value is None or not value.strip():
        return None
    return _one_line(value, "title")


class AgentCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    # A short role line and a longer description, for lists and pickers.
    title: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    instructions: str = ""
    # An engine id from GET /api/v1/engines; an unknown one is a 422, and the
    # model is checked against it (422 `unknown_model`).
    provider: str = Field(default="claude", min_length=1, max_length=64)
    model: str | None = Field(default=None, max_length=200)
    # Omitted: the agent gets its own folder under the default workspace root.
    # Otherwise an absolute path (or `~/...`) to an existing folder.
    workspace_path: str | None = Field(default=None, max_length=1000)
    approval_mode: ApprovalMode = "ask"
    pinned: bool = False
    memory_enabled: bool = True
    # Before each turn, add relevant notes and earlier conversations to the
    # person's message (docs/MEMORY.md). Has no effect while memory is off.
    recall_enabled: bool = True
    # Optional: another engine (and its model; null for its default) a turn
    # moves to when this agent's engine refuses it for a usage or rate limit
    # before starting on it (docs/ENGINES.md). It must differ from `provider`.
    fallback_provider: str | None = Field(default=None, min_length=1, max_length=64)
    fallback_model: str | None = Field(default=None, max_length=200)
    # Optional first contents of the agent's notes (MEMORY.md).
    starter_notes: str | None = Field(default=None, max_length=256 * 1024)

    @field_validator("name")
    @classmethod
    def _name_one_line(cls, value: str) -> str:
        return _one_line(value, "name")

    @field_validator("title")
    @classmethod
    def _title_one_line(cls, value: str | None) -> str | None:
        return _optional_line(value)

    @field_validator("instructions")
    @classmethod
    def _instructions_size(cls, value: str) -> str:
        return _instructions_fit(value)


class AgentUpdateRequest(BaseModel):
    """Change any of these; send at least one. `title`, `description`, `model`
    and `workspace_path` accept an explicit null to go back to the default."""

    name: str | None = Field(default=None, min_length=1, max_length=80)
    title: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    instructions: str | None = None
    # Another engine. Its conversations keep the engine they started on; a
    # change without `model` goes back to the new engine's default model.
    provider: str | None = Field(default=None, min_length=1, max_length=64)
    model: str | None = Field(default=None, max_length=200)
    workspace_path: str | None = Field(default=None, max_length=1000)
    approval_mode: ApprovalMode | None = None
    pinned: bool | None = None
    memory_enabled: bool | None = None
    recall_enabled: bool | None = None
    # An explicit null removes the fallback engine (and its model).
    fallback_provider: str | None = Field(default=None, min_length=1, max_length=64)
    fallback_model: str | None = Field(default=None, max_length=200)
    archived: bool | None = None

    @field_validator("name")
    @classmethod
    def _name_one_line(cls, value: str | None) -> str | None:
        return None if value is None else _one_line(value, "name")

    @field_validator("title")
    @classmethod
    def _title_one_line(cls, value: str | None) -> str | None:
        return _optional_line(value)

    @field_validator("instructions")
    @classmethod
    def _instructions_size(cls, value: str | None) -> str | None:
        return None if value is None else _instructions_fit(value)

    @model_validator(mode="after")
    def _something_to_change(self):
        if not self.model_fields_set:
            raise ValueError("send at least one field to change")
        for field in ("name", "instructions", "provider", "approval_mode", "pinned", "memory_enabled", "recall_enabled", "archived"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class AgentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    title: str | None
    description: str | None
    instructions: str
    provider: str
    model: str | None
    # Null: a thread started from this agent gets its own folder, named by the
    # agent's id, under the default workspace root.
    workspace_path: str | None
    approval_mode: ApprovalMode
    pinned: bool
    memory_enabled: bool
    recall_enabled: bool = True
    fallback_provider: str | None = None
    fallback_model: str | None = None
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime
    # The agent's own computer (docs/COMPUTERS.md), or null: its turns then
    # run on this machine. Manage it under /agents/{id}/computer.
    computer_id: int | None = None
    computer: ComputerSummary | None = None


class AgentMemoryResponse(BaseModel):
    """The agent's notes and how much of them loads into a turn."""

    model_config = ConfigDict(from_attributes=True)

    path: str
    text: str
    # Send back as `expected_hash` so a save never overwrites a change the
    # agent made in the meantime.
    hash: str
    lines: int
    bytes: int
    loaded_lines: int
    loaded_bytes: int
    truncated: bool
    max_lines: int
    max_bytes: int
    enabled: bool
    # Set on a save: how many things that looked like secrets were replaced.
    redacted: int = 0


class AgentMemoryWriteRequest(BaseModel):
    text: str
    expected_hash: str | None = Field(default=None, max_length=64)


class AgentExportResponse(BaseModel):
    file_name: str
    # The portable file itself, to be saved as JSON.
    document: dict
    # Parts in which something that looked like a secret was replaced.
    redacted: list[str]


class AgentImportRequest(BaseModel):
    document: dict
    # False leaves the file's notes out even when it carries some.
    include_notes: bool = True


# --- topic notes, the journal and proposed notes (docs/MEMORY.md) ------------------------


class TopicInfoResponse(BaseModel):
    name: str
    title: str | None
    aliases: list[str]
    bytes: int
    modified_at: datetime


class TopicListResponse(BaseModel):
    topics: list[TopicInfoResponse]
    max_topics: int
    max_bytes: int
    folder: str


class TopicDocResponse(BaseModel):
    name: str
    path: str
    text: str
    # Send back as `expected_hash`; a note not created yet has the hash of "".
    hash: str
    bytes: int
    exists: bool
    redacted: int = 0


class TopicWriteRequest(BaseModel):
    text: str
    expected_hash: str | None = Field(default=None, max_length=64)


class MemoryJournalRow(BaseModel):
    id: str
    at: datetime
    path: str
    # person | proposal | undo
    actor: str
    # created | edited | deleted
    kind: str
    before_hash: str | None
    after_hash: str | None
    diff: str
    added: int
    removed: int
    can_undo: bool
    reason: str | None = None


class MemoryProposalResponse(BaseModel):
    id: str
    at: datetime
    # The person's own words the proposal quotes.
    text: str
    # The line Accept appends.
    line: str
    target: str
    thread_id: int | None
    thread_title: str | None
    turn_id: int | None


class MemoryProposalAcceptRequest(BaseModel):
    # The line to save instead of the proposed one (edited by the person).
    line: str | None = Field(default=None, max_length=2000)
    # A topic file name (travel.md) instead of MEMORY.md.
    target: str | None = Field(default=None, max_length=80)
