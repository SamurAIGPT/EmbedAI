from fastapi import APIRouter
from pydantic import BaseModel

from app.core.config import settings

router = APIRouter(prefix="/providers", tags=["providers"])


class ProviderStatus(BaseModel):
    enabled: bool
    # Why it is off, in words the page can show; None when enabled.
    reason: str | None = None


class ProvidersResponse(BaseModel):
    boat: ProviderStatus


@router.get("", response_model=ProvidersResponse)
async def list_providers() -> ProvidersResponse:
    """Which optional providers are configured, so the page can hide what cannot
    work. Reveals only whether a provider is on, never a key or a URL. Local
    Claude reports its own, richer status at /local/status."""
    if settings.BOAT_ENABLED:
        return ProvidersResponse(boat=ProviderStatus(enabled=True))
    return ProvidersResponse(
        boat=ProviderStatus(enabled=False, reason="BOAT_API_KEY is not set in backend/.env")
    )
