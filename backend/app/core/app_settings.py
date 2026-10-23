"""Application settings registry and resolver (D-013).

Desktop: committed database snapshot over profile defaults, refreshed only at
startup and after a save. Headless: existing Settings/env behaviour is retained.
Turns freeze next-run values in a context; new approvals read the live snapshot.
"""
import asyncio
import os
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import settings
from app.desktop import profile


@dataclass(frozen=True)
class Definition:
    kind: str
    sensitive: bool
    applies: str
    minimum: int | None = None
    maximum: int | None = None
    classification: str = "application"


REGISTRY = {
    "TEAM_ENABLED": Definition("boolean", False, "next_run"),
    "TEAM_LEAD_AUTO": Definition("boolean", True, "next_run"),
    "DESKTOP_ONBOARDING_COMPLETED": Definition("boolean", False, "immediate"),
    "LOCAL_CLAUDE_CLI_PATH": Definition("file", True, "next_run"),
    "LOCAL_WORKSPACE_ROOT": Definition("directory", True, "next_run"),
    "LOCAL_TOOLS_ENABLED": Definition("boolean", True, "next_run"),
    "LOCAL_TASK_TIMEOUT_SECONDS": Definition("integer", False, "next_run", 30, 3600),
    "LOCAL_APPROVAL_TIMEOUT_SECONDS": Definition("integer", True, "next_run", 30, 3600),
    "LOCAL_MAX_OUTPUT_BYTES": Definition("integer", False, "next_run", 1024, 16_000_000),
}


class SettingUnavailable(ValueError):
    pass


class VersionConflict(Exception):
    pass


@dataclass(frozen=True)
class Resolved:
    key: str
    value: object
    effective_value: object
    source: str
    default: object
    applies: str
    sensitive: bool
    version: int = 0
    error: str | None = None


_rows: dict[str, tuple[object, int]] = {}
_context: ContextVar[dict[str, Resolved] | None] = ContextVar("application_settings", default=None)
_save_lock = asyncio.Lock()


def default(key: str):
    desktop = profile.current()
    if desktop is not None:
        return desktop.settings().get(key, type(settings).model_fields[key].default)
    return type(settings).model_fields[key].default


def validate(key: str, value: object, *, prepare: bool = False):
    definition = REGISTRY[key]
    if definition.kind == "boolean":
        if type(value) is not bool:
            raise ValueError("Must be a boolean.")
    elif definition.kind == "integer":
        if type(value) is not int or not definition.minimum <= value <= definition.maximum:
            raise ValueError(f"Must be an integer from {definition.minimum} to {definition.maximum}.")
    else:
        if not isinstance(value, str) or len(value) > 4096 or any(ord(c) < 32 for c in value):
            raise ValueError("Must be a full path.")
        value = value.strip()
        if value:
            path = Path(value)
            if not path.is_absolute():
                raise ValueError("Must be a full absolute path.")
            if definition.kind == "file":
                if not path.is_file():
                    raise ValueError("The Claude executable is missing or is not a file. Choose it again in Settings.")
            else:
                if prepare:
                    path.mkdir(parents=True, exist_ok=True, mode=0o700)
                if not path.is_dir() or not os.access(path, os.R_OK | os.W_OK | os.X_OK):
                    raise ValueError("The workspace root is missing or inaccessible. Choose another folder in Settings.")
    return value


def resolve(key: str, *, live: bool = False) -> Resolved:
    if not live and _context.get() is not None:
        return _context.get()[key]
    definition = REGISTRY[key]
    fallback = default(key)
    if not profile.active():
        # Preserve all existing headless values and defaults, including test tuning.
        value = getattr(settings, key)
        source = "environment" if key in settings.model_fields_set else "default"
        return Resolved(key, value, value, source, fallback, definition.applies, definition.sensitive)
    if key not in _rows:
        return Resolved(key, fallback, fallback, "default", fallback, definition.applies, definition.sensitive)
    value, version = _rows[key]
    try:
        effective = validate(key, value)
        if key == "LOCAL_WORKSPACE_ROOT" and effective == "":
            effective = fallback
        error = None
    except (ValueError, OSError) as exc:
        error = str(exc)
        effective = False if key == "LOCAL_TOOLS_ENABLED" else (
            None if definition.kind in ("file", "directory") else fallback
        )
    return Resolved(key, value, effective, "database", fallback, definition.applies, definition.sensitive, version, error)


def get(key: str, *, live: bool = False):
    value = resolve(key, live=live)
    if value.effective_value is None:
        raise SettingUnavailable(value.error or f"{key} is unavailable.")
    return value.effective_value


def capture() -> dict[str, Resolved]:
    return {key: resolve(key, live=True) for key in REGISTRY}


@contextmanager
def use(snapshot: dict[str, Resolved]):
    token = _context.set(snapshot)
    try:
        yield
    finally:
        _context.reset(token)


async def refresh(db) -> None:
    from sqlalchemy import select
    from app.db.models.app_setting import AppSetting
    global _rows
    rows = (await db.execute(select(AppSetting))).scalars().all()
    _rows = {row.key: (row.value_json, row.version) for row in rows if row.key in REGISTRY}


async def save(db, key: str, value: object, expected_version: int, actor: str = "local-owner") -> Resolved:
    from sqlalchemy import select, update
    from sqlalchemy.exc import IntegrityError
    from app.db.models.app_setting import AppSetting, SettingAudit
    from app.db.transactions import begin_write
    if key not in REGISTRY:
        raise KeyError(key)
    if not profile.active():
        raise PermissionError("Desktop settings can only be changed in the app.")
    async with _save_lock:
        try:
            await begin_write(db)
            row = (await db.execute(select(AppSetting).where(AppSetting.key == key).with_for_update())).scalar_one_or_none()
            version = row.version if row is not None else 0
            if version != expected_version:
                raise VersionConflict()
            value = validate(key, value, prepare=True)
            old = row.value_json if row is not None else default(key)
            now = datetime.now(UTC)
            if row is None:
                db.add(AppSetting(key=key, value_json=value, version=1, updated_at=now, updated_by=actor))
            else:
                changed = await db.execute(update(AppSetting).where(
                    AppSetting.key == key, AppSetting.version == expected_version
                ).values(value_json=value, version=version + 1, updated_at=now, updated_by=actor))
                if changed.rowcount != 1:
                    raise VersionConflict()
            if REGISTRY[key].sensitive:
                db.add(SettingAudit(key=key, old_value_json=old, new_value_json=value, at=now, actor=actor))
            await db.commit()
        except IntegrityError:
            await db.rollback()
            raise VersionConflict() from None
        except BaseException:
            await db.rollback()
            raise
        # One desktop process, no polling. Only committed values become visible.
        await refresh(db)
        await db.rollback()  # release the read snapshot before any slow work
        return resolve(key, live=True)


def as_dict(value: Resolved) -> dict:
    result = asdict(value)
    if value.key == "LOCAL_WORKSPACE_ROOT":
        from app.services.workspaces import canonical_workspace
        result["resolved_path"] = str(canonical_workspace(value.effective_value)) if value.effective_value is not None else None
    return result


def describe() -> list[dict]:
    return [as_dict(resolve(key, live=True)) for key in REGISTRY]
