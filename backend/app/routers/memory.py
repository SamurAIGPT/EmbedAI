"""Memory settings that belong to the whole installation, not to one agent
(docs/MEMORY.md): learning from group chats, the tidy-up hour, the helper
model per engine."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from app.services import memory_settings
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
