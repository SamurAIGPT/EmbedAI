from typing import Literal

from pydantic import BaseModel, Field


class LocalTaskCreateRequest(BaseModel):
    user_id: str
    prompt_text: str = Field(min_length=1, max_length=100_000)


class LocalStatusResponse(BaseModel):
    # LOCAL_EXECUTION_ENABLED on the backend.
    enabled: bool
    # Enabled, allowed for this caller, and the CLI is installed and signed in.
    available: bool
    # Why it is not available (shown to the user), when it is not.
    reason: str | None = None
    cli_version: str | None = None
    # LOCAL_TOOLS_ENABLED: Claude may use tools, each call waiting for approval.
    tools_enabled: bool = False


class LocalApprovalResponse(BaseModel):
    id: str
    task_id: int
    # The tool the CLI wants to run and the exact input it will run with.
    tool: str
    summary: str
    input: dict
    created_at: str


class LocalApprovalAnswer(BaseModel):
    decision: Literal["allow", "deny"]
