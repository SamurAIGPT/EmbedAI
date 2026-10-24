"""Isolated ACP fixtures; real Google account compatibility is a manual check."""
import asyncio
import hashlib
import json
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode
from uuid import uuid4

import pytest

from app.services import antigravity_runtime as runtime
from app.services.antigravity_adapter import AntigravityAdapter, authenticated, authorization, callback_url, catalog, profile_dir
from app.services.acp_adapter import InvalidConfig
from app.services.provider_adapter import TurnContext
from app.services.stdio_rpc import StdioRpc
from tests.services.test_acp_adapter import FakeAcp

FAKE = Path(__file__).with_name("fake_acp_agent.py")
SESSION = {"configOptions": [{"id": "model", "type": "select", "currentValue": "gemini-account", "options": [
    {"group": "Google", "options": [{"value": "gemini-account", "name": "Account Gemini"}, {"value": "gemini-other", "name": "Other Gemini"}]},
]}]}

@pytest.fixture
def fixture(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime, "data_root", lambda: tmp_path / "data")
    monkeypatch.setattr(runtime, "installed", lambda: True)
    monkeypatch.setenv("FAKE_ACP_DIR", str(tmp_path))
    monkeypatch.setenv("OPEN_DOTS_ENGINE_ENV", "FAKE_*,GOOGLE_*,AGY_*,ANTIGRAVITY_*,GEMINI_*")
    fake = FakeAcp(tmp_path)
    fake.plan(load=True, agent_info={"name": "antigravity-acp", "version": runtime.VERSION}, auth_methods=[{"id": "oauth-personal"}], session=SESSION)
    adapter = AntigravityAdapter()
    adapter.config = {"profile": uuid4().hex}
    adapter.child_env(adapter.config)
    (profile_dir(adapter.config) / "antigravity-acp/acp_token.json").write_text(json.dumps({"client_id": "fixture", "client_secret": "SECRET_CANARY", "refresh_token": "TOKEN_CANARY"}))
    monkeypatch.setattr(adapter, "program", lambda config: [sys.executable, str(FAKE)])
    monkeypatch.setattr(adapter, "mode_args", lambda ctx, config: [])
    return adapter, fake

async def turn(adapter, fake, **kwargs):
    async def history(): return []
    async def emit(*args): pass
    ctx = TurnContext(1, 1, "hello", fake.folder, kwargs.pop("model", None), None, kwargs.pop("provider_state", None), kwargs.pop("resume", False), history,
                      engine_config=adapter.config, **kwargs)
    return await adapter.run_turn(ctx, SimpleNamespace(lost=False, emit=emit, delta=lambda *args: None), None)

async def test_model_permission_mode_and_resume(fixture):
    adapter, fake = fixture
    first = await turn(adapter, fake, model="gemini-other")
    assert first.status == "succeeded" and first.model == "gemini-other"
    assert [(m["params"]["configId"], m["params"]["value"]) for m in fake.received("session/set_config_option")] == [("mode", "default"), ("model", "gemini-other")]
    assert first.provider_state["antigravity_profile"] == adapter.config["profile"]
    second = await turn(adapter, fake, resume=True, provider_state=first.provider_state)
    assert second.status == "succeeded"
    assert fake.received("session/resume") and not fake.received("session/load")

@pytest.mark.parametrize("mode", ["ask", "accept_edits", "plan"])
async def test_never_enables_native_auto_approval(fixture, mode):
    adapter, fake = fixture
    assert (await turn(adapter, fake, approval_mode=mode)).status == "succeeded"
    assert fake.received("session/set_config_option")[0]["params"]["value"] == "default"

async def test_mode_refusal_blocks_prompt(fixture):
    adapter, fake = fixture
    plan = json.loads((fake.folder / "plan.json").read_text())
    fake.plan(**{**plan, "mode_result": "yolo"})
    assert (await turn(adapter, fake)).error_code == "engine_permission_mode_unavailable"
    assert not fake.received("session/prompt")

async def test_unknown_model_blocks_prompt(fixture):
    adapter, fake = fixture
    assert (await turn(adapter, fake, model="not-on-this-plan")).error_code == "engine_model_unavailable"
    assert not fake.received("session/prompt")

async def test_account_change_cannot_resume(fixture):
    adapter, fake = fixture
    result = await turn(adapter, fake, resume=True, provider_state={"acp_session_id": "old", "antigravity_profile": uuid4().hex})
    assert result.error_code == "session_resume_failed" and not fake.received("initialize")

async def test_signed_out_never_spawns(fixture):
    adapter, fake = fixture
    (profile_dir(adapter.config) / "antigravity-acp/acp_token.json").unlink()
    assert (await turn(adapter, fake)).error_code == "engine_signed_out"
    assert not fake.received("initialize")

def test_foreign_credentials_removed(fixture, monkeypatch):
    adapter, _ = fixture
    for name in ("GOOGLE_API_KEY", "GEMINI_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS", "AGY_ADC_AUTH", "AGY_ACP_DEFAULT_MODEL", "GOOGLE_GEMINI_BASE_URL", "ANTIGRAVITY_HARNESS_PATH"):
        monkeypatch.setenv(name, "FOREIGN_CANARY")
    env = adapter.child_env(adapter.config)
    assert "FOREIGN_CANARY" not in str(env)
    assert env["AGY_ACP_FORCE_FILE_STORAGE"] == "1"
    assert env["GEMINI_HOME"] == str(profile_dir(adapter.config))
    assert profile_dir(adapter.config).stat().st_mode & 0o777 == 0o700
    assert "TOKEN_CANARY" not in str(adapter.public_config(adapter.config))
    assert not authenticated({"profile": "../../outside"})

def test_catalog_groups_and_malformed_ids():
    assert [m.id for m in catalog(SESSION)] == ["gemini-account", "gemini-other"]
    assert catalog({"configOptions": [{"id": "model", "options": [{"value": "--bad"}, None, {"value": "has spaces"}]}]}) == []

def auth_url(**extra):
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({"state": "fixture-state", "redirect_uri": "http://127.0.0.1:45678/", "response_type": "code", **extra})

@pytest.mark.parametrize("raw", [auth_url().replace("accounts.google.com", "evil.test"), auth_url(redirect_uri="http://127.0.0.1:80/"), auth_url() + "&state=second", auth_url(response_type="token"), auth_url(redirect_uri="http://localhost:45678/")])
def test_invalid_auth_urls(raw):
    with pytest.raises(InvalidConfig): authorization(raw)

@pytest.mark.parametrize("raw", ["http://127.0.0.1:45679/?state=fixture-state&code=CANARY", "http://evil.test:45678/?state=fixture-state&code=CANARY", "http://127.0.0.1:45678/?state=wrong&code=CANARY", "http://127.0.0.1:45678/?state=fixture-state&code=a&code=b", "http://127.0.0.1:45678/?state=fixture-state&code=a&error=b", "http://127.0.0.1:45678/?state=fixture-state&code=a&iss=https://evil.test"])
def test_callback_port_and_state_bound(raw):
    _, redirect, state = authorization(auth_url())
    with pytest.raises(InvalidConfig) as exc: callback_url(raw, redirect, state)
    assert "CANARY" not in str(exc.value)

def test_valid_callback():
    _, redirect, state = authorization(auth_url())
    raw = redirect + "?state=fixture-state&code=CANARY"
    assert callback_url(raw, redirect, state) == raw

def archive(tmp_path, entries):
    path = tmp_path / "archive.zip"
    with zipfile.ZipFile(path, "w") as package:
        for name, data in entries: package.writestr(name, data)
    output = tmp_path / "out"
    output.mkdir()
    return path, output, hashlib.sha256(path.read_bytes()).hexdigest()

def test_verified_extraction(tmp_path):
    path, output, digest = archive(tmp_path, [(name, b"fixture") for name in runtime.MEMBERS])
    assert runtime.extract_archive(path, output, digest) == {name: 7 for name in runtime.MEMBERS}
    assert (output / runtime.MEMBERS[0]).stat().st_mode & 0o777 == 0o700

@pytest.mark.parametrize("entries", [[("../outside", b"bad")], [("agy_acp_server.par", b"a"), ("agy_acp_server.par", b"b")], [("agy_acp_server.par", b"a"), ("localharness_external", b"b"), ("extra", b"c")]])
def test_unexpected_archive_members(tmp_path, entries):
    path, output, digest = archive(tmp_path, entries)
    with pytest.raises(ValueError): runtime.extract_archive(path, output, digest)
    assert not list(output.iterdir())

def test_hash_failure(tmp_path):
    path, output, _ = archive(tmp_path, [(name, b"fixture") for name in runtime.MEMBERS])
    with pytest.raises(ValueError): runtime.extract_archive(path, output, "0" * 64)
    assert not list(output.iterdir())

async def test_cancel_closes_worker_and_hides_link(fixture):
    adapter, _ = fixture
    started, stopped = asyncio.Event(), asyncio.Event()
    async def work():
        started.set()
        try: await asyncio.Event().wait()
        finally: stopped.set()
    adapter.start_operation("sign_in", work)
    await started.wait()
    adapter._diagnostic("Open the following link to authenticate the ACP server: " + auth_url())
    assert adapter.operation["authorization_url"] == auth_url()
    with pytest.raises(InvalidConfig): adapter.start_operation("install", work)
    await adapter.close()
    assert stopped.is_set() and adapter.operation["phase"] == "cancelled"
    assert adapter.operation["authorization_url"] is None and adapter.auth_request is None

async def test_stderr_not_retained(tmp_path):
    rpc = await StdioRpc.spawn([sys.executable, "-c", "import sys;sys.stderr.write('OAUTH_CODE_CANARY\\n')"], cwd=str(tmp_path), max_bytes=1000, diagnostic=lambda line: None)
    await rpc.proc.wait()
    await rpc.close()
    assert rpc.stderr_tail == ""

@pytest.mark.parametrize("approval_mode,expected", [("ask", "once"), ("plan", "no")])
async def test_permission_gate_round_trip(fixture, monkeypatch, approval_mode, expected):
    from app.core.config import settings
    from app.services.provider_adapter import ALLOW
    adapter, fake = fixture
    monkeypatch.setattr(settings, "LOCAL_TOOLS_ENABLED", True)
    plan = json.loads((fake.folder / "plan.json").read_text())
    plan["steps"] = [{"type": "permission", "kind": "execute", "command": "ls"}]
    fake.plan(**plan)
    seen = []
    async def open_gate(**kwargs):
        seen.append(kwargs)
        return SimpleNamespace(tool=None)
    async def decide(handle): return SimpleNamespace(kind=ALLOW)
    async def emit(*args): pass
    async def history(): return []
    ctx = TurnContext(1, 1, "hello", fake.folder, None, None, None, False, history, approval_mode=approval_mode, engine_config=adapter.config)
    result = await adapter.run_turn(ctx, SimpleNamespace(lost=False, emit=emit, delta=lambda *args: None), SimpleNamespace(open=open_gate, decide=decide))
    assert result.status == "succeeded"
    assert fake.answers()[0]["result"]["outcome"]["optionId"] == expected
    assert bool(seen) == (approval_mode == "ask")

async def test_stop_cancels_native_session(fixture):
    adapter, fake = fixture
    plan = json.loads((fake.folder / "plan.json").read_text())
    fake.plan(**{**plan, "steps": [{"type": "slow"}]})
    async def emit(*args): pass
    async def history(): return []
    ctx = TurnContext(1, 1, "hello", fake.folder, None, None, None, False, history, engine_config=adapter.config)
    task = asyncio.create_task(adapter.run_turn(ctx, SimpleNamespace(lost=False, emit=emit, delta=lambda *args: None), None))
    for _ in range(100):
        if fake.received("session/prompt"): break
        await asyncio.sleep(.01)
    assert fake.received("session/prompt")
    ctx.abort()
    result = await asyncio.wait_for(task, 7)
    assert result.status != "succeeded" and fake.received("session/cancel")

async def test_cancel_before_worker_starts(fixture):
    adapter, _ = fixture
    async def work(): await asyncio.Event().wait()
    adapter.start_operation("sign_in", work)
    await adapter.close()
    assert adapter.operation["phase"] == "cancelled"


def test_runtime_identity_must_match_pin(fixture):
    adapter, _ = fixture
    for info in ({"name": "other", "version": runtime.VERSION}, {"name": "antigravity-acp", "version": "0.0.0"}):
        with pytest.raises(InvalidConfig):
            adapter.validate_initialize({"protocolVersion": 1, "agentInfo": info, "agentCapabilities": {"loadSession": True}, "authMethods": [{"id": "oauth-personal"}]})

@pytest.mark.parametrize("reject_validation", [False, True])
async def test_install_publishes_only_after_validation(tmp_path, monkeypatch, reject_validation):
    import httpx
    path, _, digest = archive(tmp_path, [(name, b"fixture") for name in runtime.MEMBERS])
    monkeypatch.setattr(runtime, "data_root", lambda: tmp_path / "data")
    monkeypatch.setattr(runtime, "asset", lambda: ("https://dl.google.com/fixture.zip", digest))
    monkeypatch.setattr(runtime.shutil, "disk_usage", lambda path: SimpleNamespace(free=10 * 1024**3))
    client_class = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=path.read_bytes()))
    monkeypatch.setattr(runtime.httpx, "AsyncClient", lambda **kwargs: client_class(transport=transport, **kwargs))
    validated = []
    async def validate(candidate):
        assert not runtime.runtime_dir().exists()
        assert (candidate / "agy_acp_server.par").read_bytes() == b"fixture"
        validated.append(candidate)
        if reject_validation: raise InvalidConfig("Wrong ACP identity")
    if reject_validation:
        with pytest.raises(InvalidConfig): await runtime.install(validate)
        assert not runtime.installed()
    else:
        await runtime.install(validate)
        assert runtime.installed()
        await runtime.install(validate)
    assert len(validated) == 1
    assert not list(runtime.runtime_dir().parent.glob(".install-*"))
