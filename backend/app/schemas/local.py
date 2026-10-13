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
