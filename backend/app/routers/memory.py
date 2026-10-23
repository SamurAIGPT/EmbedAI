"""Memory settings that belong to the whole installation, not to one agent
(docs/MEMORY.md): learning from group chats, the tidy-up hour, the helper
model per engine."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from app.services import about_me, memory_settings
from app.services.local_access_policy import require_local_access

router = APIRouter(prefix="/memory", tags=["Memory"], dependencies=[Depends(require_local_access)])


class SettingsChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    learn_from_rooms: bool | None = None
    tidy_hour: int | None = None
    helper_models: dict[str, str] | None = None


@router.get("/settings")
async def read_settings():
    return memory_settings.read()


@router.put("/settings")
async def change_settings(change: SettingsChange):
    try:
        return memory_settings.update(**change.model_dump(exclude_unset=True))
    except memory_settings.InvalidSetting as exc:
        raise HTTPException(
            status_code=422, detail={"error_code": "invalid_setting", "message": str(exc)}
        ) from None


# --- About me ----------------------------------------------------------------------------


class AboutMeWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    expected_hash: str | None = None


def _about(state: dict, redacted: int = 0) -> dict:
    return {**state, "redacted": redacted}


@router.get("/about-me")
async def read_about_me():
    """What the person wrote about themselves, which every agent reads, and
    the facts their agents added from the person's own words (each can be removed)."""
    return _about(about_me.read())


@router.put("/about-me")
async def write_about_me(body: AboutMeWrite):
    try:
        state, redacted = about_me.write(body.text, expected_hash=body.expected_hash)
    except about_me.MemoryConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "memory_changed",
                "message": "About me changed since it was opened.",
                "current": exc.current.text,
                "current_hash": exc.current.hash,
            },
        ) from None
    except about_me.MemoryTooLarge as exc:
        raise HTTPException(status_code=413, detail={"error_code": "memory_too_large", "message": str(exc)}) from None
    return _about(state, redacted)


@router.delete("/about-me/learned/{fact_id}")
async def remove_learned_fact(fact_id: str):
    """Take a fact an agent added out of About me; it is never added again."""
    try:
        return _about(about_me.remove_learned(fact_id))
    except about_me.NoSuchFact:
        raise HTTPException(status_code=404, detail={"error_code": "no_such_fact", "message": "No such fact."}) from None
