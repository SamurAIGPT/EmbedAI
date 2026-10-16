"""Desktop-only non-secret settings; no bootstrap or optional-provider editing."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import app_settings
from app.db.session import get_db
from app.desktop import profile
from app.services import local_claude_runner
from app.services.local_access_policy import require_local_access


def desktop_only():
    if not profile.active():
        raise HTTPException(404, "Settings are available in the desktop app. Headless installs use environment configuration.")


router = APIRouter(prefix="/settings", tags=["Settings"], dependencies=[Depends(desktop_only), Depends(require_local_access)])


class SettingChange(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    value: object
    expected_version: int = Field(ge=0)


@router.get("")
async def read_settings():
    desktop = profile.current()
    return {
        "settings": app_settings.describe(),
        "storage": {
            "data_folder": str(desktop.data_dir),
            "database": str(desktop.database_file),
            "backups": str(desktop.backup_dir),
            "encryption_key": "Stored in the operating system's secret storage",
        },
    }


@router.put("/{key}")
async def change_setting(key: str, change: SettingChange, db: AsyncSession = Depends(get_db)):
    try:
        result = await app_settings.save(db, key, change.value, change.expected_version)
        return app_settings.as_dict(result)
    except KeyError:
        raise HTTPException(404, "Unknown setting.") from None
    except app_settings.VersionConflict:
        raise HTTPException(409, "This setting changed elsewhere. Reload settings and try again.") from None
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from None


@router.post("/test/claude")
async def test_claude():
    status = await local_claude_runner.get_status()
    return {"available": status.installed and status.signed_in is True, "version": status.version, "error": status.error}
