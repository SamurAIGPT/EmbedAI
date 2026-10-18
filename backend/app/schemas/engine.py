from pydantic import BaseModel, Field


class EngineModel(BaseModel):
    id: str
    label: str
    default: bool = False


class EngineCapabilities(BaseModel):
    tools: bool
    approvals: bool
    mcp: bool
    images: bool
    resume: bool
    computers: bool


class EngineResponse(BaseModel):
    """One engine. `available` says a turn can start now; `reason` says why
    not. Status comes from short, bounded checks of the engine's CLI or
    endpoint and never carries credentials or account details."""

    id: str
    label: str
    # cli | acp | api
    kind: str
    installed: bool
    version: str | None = None
    # None when the engine does not say (or has no sign-in).
    signed_in: bool | None = None
    available: bool
    reason: str | None = None
    models: list[EngineModel]
    # The model a conversation gets when none is chosen, when known.
    default_model: str | None = None
    # Any model id is accepted, not only those in `models`.
    custom_models: bool
    capabilities: EngineCapabilities
    # The engine needs a saved configuration (PUT /engines/{id}/config).
    configurable: bool = False
    # What is saved, without secrets: an ACP `command` and `label`, or an
    # endpoint's `base_url`, `model` and `has_api_key`.
    config: dict | None = None
    # Set while the engine is refusing turns for a usage or rate limit or a lack
    # of credits, as its last turn reported: {"kind": "usage_limit" |
    # "rate_limit" | "credits", "resets_at": ISO time or null, "since": ISO
    # time, "message": plain words}. Kept in this backend's memory; cleared at
    # the reset time or when a turn on the engine succeeds.
    limit: dict | None = None


class EngineConfigRequest(BaseModel):
    """ACP agent: `command` (and an optional `label`). OpenAI-compatible:
    `base_url`, optional `model`, and `api_key` (omit it to keep the saved key
    for the same address; `clear_api_key: true` removes it)."""

    command: str | None = Field(default=None, max_length=1000)
    label: str | None = Field(default=None, max_length=200)
    base_url: str | None = Field(default=None, max_length=500)
    model: str | None = Field(default=None, max_length=200)
    api_key: str | None = Field(default=None, max_length=4096)
    clear_api_key: bool = False
    # OpenAI-compatible: the model's context window in tokens; long
    # conversations are folded at 80% of it (docs/MEMORY.md).
    context_tokens: int | None = None
