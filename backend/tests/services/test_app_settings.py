"""Resolver, CAS writes and audit share the real database transaction boundary."""
import asyncio
from pathlib import Path

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core import app_settings as config
from app.core.config import settings
from app.db.engine import create_app_async_engine
from app.db.models.app_setting import AppSetting, SettingAudit
from app.desktop import profile


@pytest.fixture
async def configured(monkeypatch, tmp_path):
    monkeypatch.setattr(profile, "_current", profile.DesktopProfile(tmp_path / "data", "http://127.0.0.1:1", "test-key"))
    monkeypatch.setattr(config, "_rows", {})
    monkeypatch.setattr(config, "_save_lock", asyncio.Lock())
    engine = create_app_async_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        await db.execute(delete(SettingAudit))
        await db.execute(delete(AppSetting))
        await db.commit()
    yield sessions
    async with sessions() as db:
        await db.execute(delete(SettingAudit))
        await db.execute(delete(AppSetting))
        await db.commit()
    await engine.dispose()


async def put(sessions, key, value, version=0):
    async with sessions() as db:
        return await config.save(db, key, value, version)


async def test_version_conflict_and_sensitive_audit_are_atomic(configured):
    row = await put(configured, "LOCAL_TOOLS_ENABLED", False)
    assert (row.value, row.version, row.source) == (False, 1, "database")
    with pytest.raises(config.VersionConflict):
        await put(configured, "LOCAL_TOOLS_ENABLED", True, 0)
    async with configured() as db:
        audits = (await db.execute(select(SettingAudit))).scalars().all()
        assert [(a.old_value_json, a.new_value_json) for a in audits] == [(True, False)]
    await put(configured, "LOCAL_TASK_TIMEOUT_SECONDS", 90)
    async with configured() as db:
        assert len((await db.execute(select(SettingAudit))).scalars().all()) == 1


async def test_racing_first_writes_have_exactly_one_winner(configured):
    async def attempt(value):
        try:
            await put(configured, "LOCAL_TOOLS_ENABLED", value)
            return "saved"
        except config.VersionConflict:
            return "conflict"
    assert sorted(await asyncio.gather(attempt(True), attempt(False))) == ["conflict", "saved"]


@pytest.mark.parametrize("key,bad,effective", [
    ("LOCAL_TOOLS_ENABLED", "true", False),
    ("LOCAL_CLAUDE_CLI_PATH", "relative.exe", None),
    ("LOCAL_WORKSPACE_ROOT", "relative", None),
    ("LOCAL_TASK_TIMEOUT_SECONDS", -1, 300),
    ("LOCAL_APPROVAL_TIMEOUT_SECONDS", "forever", 300),
    ("LOCAL_MAX_OUTPUT_BYTES", 0, 1_000_000),
])
async def test_invalid_stored_values_fail_closed_without_startup_failure(configured, key, bad, effective):
    # Simulate manual database damage, then the same refresh used at startup.
    async with configured() as db:
        db.add(AppSetting(key=key, value_json=bad, version=1, updated_by="fixture"))
        await db.commit()
        await config.refresh(db)
    result = config.resolve(key)
    assert result.value == bad and result.error
    assert result.effective_value == effective


async def test_turn_snapshot_and_new_approval_apply_rules(configured):
    old = config.capture()
    with config.use(old):
        await put(configured, "LOCAL_TOOLS_ENABLED", False)
        await put(configured, "LOCAL_TASK_TIMEOUT_SECONDS", 90)
        await put(configured, "LOCAL_APPROVAL_TIMEOUT_SECONDS", 60)
        assert config.get("LOCAL_TOOLS_ENABLED") is True
        assert config.get("LOCAL_TASK_TIMEOUT_SECONDS") == 300
        assert config.get("LOCAL_APPROVAL_TIMEOUT_SECONDS", live=True) == 60
    assert config.get("LOCAL_TOOLS_ENABLED") is False
    assert config.get("LOCAL_TASK_TIMEOUT_SECONDS") == 90


async def test_saved_paths_are_validated_and_missing_cli_never_autodetects(configured, tmp_path):
    cli = tmp_path / "claude.exe"
    cli.write_text("fixture")
    await put(configured, "LOCAL_CLAUDE_CLI_PATH", str(cli))
    cli.unlink()
    assert config.resolve("LOCAL_CLAUDE_CLI_PATH").error
    from app.services.local_claude_runner import resolve_claude_executable, ClaudeCliUnavailable
    with pytest.raises((config.SettingUnavailable, ClaudeCliUnavailable)):
        resolve_claude_executable()
    with pytest.raises(ValueError):
        await put(configured, "LOCAL_CLAUDE_CLI_PATH", "relative", 1)


async def test_desktop_ignores_all_six_hostile_environment_values(configured, monkeypatch):
    for key in config.REGISTRY:
        monkeypatch.setenv(key, "hostile")
        assert config.resolve(key).source == "default"
        assert config.get(key) != "hostile"


async def test_empty_workspace_uses_the_desktop_data_folder_not_the_headless_default(configured):
    await put(configured, "LOCAL_WORKSPACE_ROOT", "")
    assert config.get("LOCAL_WORKSPACE_ROOT") == str(profile.current().data_dir / "local")


def test_no_editable_key_is_read_directly_outside_the_resolver():
    import re
    root = Path(__file__).parents[2] / "app"
    violations = []
    for file in root.rglob("*.py"):
        if file.name == "app_settings.py":
            continue
        for key in config.REGISTRY:
            if re.search(rf"\bsettings\.{key}\b", file.read_text(encoding="utf-8")):
                violations.append(f"{file.name}: {key}")
    assert violations == []
