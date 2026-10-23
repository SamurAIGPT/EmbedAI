"""Synthetic wire contracts; these fixtures do not claim vendor CLI behavior."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.acp_adapter import InvalidConfig
from app.services.acp_presets import CursorAdapter, GeminiAdapter, OpenCodeAdapter, cursor_model_id
from app.services.provider_adapter import TurnContext
from tests.services.test_acp_adapter import FakeAcp

FAKE = Path(__file__).with_name("fake_acp_agent.py")


@pytest.fixture
def fake(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_ACP_DIR", str(tmp_path))
    monkeypatch.setenv("OPEN_DOTS_ENGINE_ENV", "FAKE_*")
    return FakeAcp(tmp_path)


async def run(adapter, fake, **kwargs):
    adapter.config = {"command": f"{sys.executable} {FAKE}", **kwargs.pop("config", {})}
    async def history():
        return []
    ctx = TurnContext(1, 1, "hello", fake.folder, kwargs.pop("model", None), None, None, False, history, engine_config=adapter.config, **kwargs)
    sink = SimpleNamespace(lost=False)
    sink.emit = _emit
    sink.delta = lambda *args: None
    return await adapter.run_turn(ctx, sink, None)


async def _emit(*args):
    pass


async def test_cursor_key_skips_auth_and_confirms_parameterized_model(fake):
    fake.plan(auth_methods=[{"id": "cursor_login"}], session={"models": {"currentModelId": "other[]", "availableModels": [{"modelId": "default[]"}]}})
    result = await run(CursorAdapter(), fake, config={"api_key": "CURSOR_CANARY"}, model="auto")
    assert result.status == "succeeded" and result.model == "default[]"
    assert not fake.received("authenticate")
    assert fake.received("session/set_model")[0]["params"]["modelId"] == "default[]"
    names = next(e["env_names"] for e in fake.log() if e["event"] == "argv")
    assert "CURSOR_API_KEY" in names and "TOKEN_ENCRYPTION_KEYS" not in names


async def test_auth_failure_fails_before_prompt_without_ambient_login(fake, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setenv("HOME", str(fake.folder))
    fake.plan(auth_methods=[{"id": "oauth-personal"}], auth_error="not logged in")
    result = await run(GeminiAdapter(), fake)
    assert result.error_code == "engine_signed_out"
    assert not fake.received("session/prompt")


async def test_auth_failure_can_continue_with_saved_key(fake):
    fake.plan(auth_methods=[{"id": "gemini-api-key"}], auth_error="SAVED_CANARY invalid api key")
    result = await run(GeminiAdapter(), fake, config={"api_key": "SAVED_CANARY"}, model="gemini-test")
    assert result.status == "succeeded" and result.model == "gemini-test"
    assert "SAVED_CANARY" not in str(result)
    argv = next(e["argv"] for e in fake.log() if e["event"] == "argv")
    assert argv == ["--acp", "--approval-mode", "default", "-m", "gemini-test"]


@pytest.mark.parametrize("error", [-32601, -32602])
async def test_pinned_cursor_model_refusal_never_sends_prompt(fake, error):
    fake.plan(model_error=error, session={"models": {"availableModels": [{"modelId": "chosen[]"}]}})
    result = await run(CursorAdapter(), fake, model="chosen")
    assert result.error_code == "engine_model_unavailable" and not fake.received("session/prompt")


async def test_unoffered_opencode_model_fails_before_prompt(fake):
    fake.plan(session={"configOptions": [{"id": "model", "options": [{"value": "provider/good"}]}]})
    result = await run(OpenCodeAdapter(), fake, model="provider/bad")
    assert result.error_code == "engine_model_unavailable" and not fake.received("session/prompt")


async def test_opencode_model_requires_returned_confirmation(fake):
    fake.plan(session={"configOptions": [{"id": "model", "options": [{"value": "provider/good"}]}]})
    result = await run(OpenCodeAdapter(), fake, model="provider/good")
    assert result.status == "succeeded" and result.model == "provider/good"


async def test_gemini_oauth_fixture_expiry_and_refresh(fake, monkeypatch):
    monkeypatch.setenv("HOME", str(fake.folder))
    adapter = GeminiAdapter()
    folder = fake.folder / ".gemini"
    folder.mkdir()
    path = folder / "oauth_creds.json"
    path.write_text(json.dumps({"access_token": "expired", "expiry_date": 1}))
    assert await adapter.signed_in({"HOME": str(fake.folder)}, {}) is False
    path.write_text(json.dumps({"refresh_token": "refresh"}))
    assert await adapter.signed_in({"HOME": str(fake.folder)}, {}) is True


def test_opencode_permission_controls_override_host_and_drop_foreign_keys(monkeypatch):
    monkeypatch.setenv("OPEN_DOTS_ENGINE_ENV", "OPENCODE_*")
    monkeypatch.setenv("OPENCODE_PERMISSION", '{"*":"allow"}')
    monkeypatch.setenv("OPENCODE_CONFIG_CONTENT", '{"permission":{"*":"allow"}}')
    monkeypatch.setenv("OPENAI_API_KEY", "foreign")
    adapter = OpenCodeAdapter()
    env = adapter.child_env({}, SimpleNamespace(approval_mode="plan"))
    assert "OPENAI_API_KEY" not in env and "OPENCODE_CONFIG_CONTENT" not in env
    rules = json.loads(env["OPENCODE_PERMISSION"])
    assert "allow" not in rules.values() and rules.get("bash", rules["*"]) == "deny"
    assert env["OPENCODE_DISABLE_PROJECT_CONFIG"] == "1"


def test_config_is_write_only_and_cursor_mapping_is_unambiguous():
    adapter = CursorAdapter()
    saved = adapter.validate_config({"api_key": "CANARY", "command": "cursor-agent"}, None)
    assert "CANARY" not in str(adapter.public_config(saved))
    assert adapter.validate_config({"api_key": "", "command": ""}, saved)["api_key"] == "CANARY"
    assert cursor_model_id("chosen", [{"modelId": "chosen[fast=true]"}, {"modelId": "chosen[]"}]) == "chosen[]"
    with pytest.raises(InvalidConfig):
        adapter.validate_config({"api_key": "bad\nkey"}, None)


async def test_opencode_discovery_never_creates_a_session_and_keeps_last_good(fake):
    fake.plan(cli_models="provider/model-a\nprovider/model-b\ninvalid header\n")
    adapter = OpenCodeAdapter()
    adapter.config = {"command": f"{sys.executable} {Path(__file__).with_name('fake_agent_cli.py')}"}
    probe = await adapter.probe()
    assert probe.ready and probe.signed_in is None and probe.version == "1.2.3"
    assert [m.id for m in probe.models] == ["provider/model-a", "provider/model-b"]
    assert not fake.received("session/new")
    adapter._model_cache = (0, probe.models)
    fake.plan(cli_models="")
    assert [m.id for m in (await adapter.probe()).models] == ["provider/model-a", "provider/model-b"]


async def test_cursor_discovery_parses_synthetic_catalog_markers(fake):
    fake.plan(cli_models="auto - Auto (default)\nchosen - Chosen (current)\n", cli_status="Not logged in")
    adapter = CursorAdapter()
    adapter.config = {"command": f"{sys.executable} {Path(__file__).with_name('fake_agent_cli.py')}"}
    probe = await adapter.probe()
    assert not probe.ready and probe.signed_in is False
    assert [(m.id, m.label, m.default) for m in probe.models] == [("auto", "Auto", True), ("chosen", "Chosen", False)]
    assert not fake.received("session/new")


def test_opencode_default_off_and_explicit_preset_list(monkeypatch):
    from app.services.engine_registry import default_adapters
    monkeypatch.delenv("OPEN_DOTS_PRESET_ENGINES", raising=False)
    assert {"gemini", "cursor", "openrouter"} <= default_adapters().keys()
    assert "opencode" not in default_adapters()
    monkeypatch.setenv("OPEN_DOTS_PRESET_ENGINES", "opencode")
    assert "opencode" in default_adapters() and "gemini" not in default_adapters()
    monkeypatch.setenv("OPEN_DOTS_PRESET_ENGINES", "")
    assert not ({"gemini", "cursor", "opencode", "openrouter"} & default_adapters().keys())


async def test_prompt_errors_redact_saved_keys_before_output_clipping(fake):
    key = "LONG_CANARY_" + "a" * 2500
    fake.plan({"type": "error", "message": f"Upstream error {key}"})
    result = await run(CursorAdapter(), fake, config={"api_key": key})
    assert result.error_code == "acp_error"
    assert "[redacted]" in result.error and "LONG_CANARY" not in result.error


def test_saved_api_key_can_be_explicitly_cleared():
    adapter = CursorAdapter()
    result = adapter.validate_config({"clear_api_key": True}, {"api_key": "OLD_CANARY"})
    assert result["api_key"] is None and adapter.public_config(result)["has_api_key"] is False
