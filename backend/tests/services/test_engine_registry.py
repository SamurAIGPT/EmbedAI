"""The engine registry and its probes: safe defaults for small adapters, and a
probe that raises or hangs is an unavailable engine, never an error."""

import asyncio
import sys
import time

import pytest

from app.services import cli_probe, engine_registry
from app.services.claude_adapter import ClaudeAdapter
from app.services.engine_registry import (
    EngineRegistry,
    InvalidModel,
    UnavailableAdapter,
    UnknownEngine,
    check_model_id,
    default_adapters,
)
from app.services.provider_adapter import Capabilities, EngineProbe, ModelOption, ProviderStatus


class Runtime:
    def __init__(self, adapters):
        self.adapters = adapters
        self.prepared = []

    async def prepare_adapter(self, engine_id, adapter):
        self.prepared.append(engine_id)
        return None


class Minimal:
    """An adapter written against the old, smaller contract."""

    key = "minimal"

    def __init__(self, ready=True):
        self.ready = ready
        self.calls = 0

    def new_session_id(self):
        return None

    async def status(self):
        self.calls += 1
        return ProviderStatus(ready=self.ready, version="1.0" if self.ready else None, error=None if self.ready else "not set up")

    async def run_turn(self, ctx, sink, gate):
        raise AssertionError("not used")


class Hanging(Minimal):
    async def probe(self):
        await asyncio.sleep(30)


class Raising(Minimal):
    async def probe(self):
        raise RuntimeError("secret detail that is not shown")


class Strict(Minimal):
    label = "Strict"
    capabilities = Capabilities(tools=True)
    models = [ModelOption("a", "A", default=True), ModelOption("b", "B")]
    custom_models = False

    async def probe(self):
        return EngineProbe(installed=True, ready=True, models=[ModelOption("live", "Live")])


async def test_a_small_adapter_gets_safe_defaults():
    registry = EngineRegistry(Runtime({"minimal": Minimal()}))
    [entry] = await registry.describe()
    assert entry["available"] and entry["installed"] and entry["version"] == "1.0"
    assert entry["label"] == "minimal" and entry["kind"] == "cli"
    assert entry["capabilities"] == {k: False for k in ("tools", "approvals", "mcp", "images", "resume", "computers")}
    assert entry["custom_models"] is True and entry["models"] == []
    assert entry["reason"] is None
    assert entry["preset"] is False and entry["setup"] is None and entry["config_fields"] == []


async def test_an_unready_adapter_says_why():
    registry = EngineRegistry(Runtime({"minimal": Minimal(ready=False)}))
    [entry] = await registry.describe()
    assert not entry["available"] and entry["reason"] == "not set up"


async def test_a_hanging_probe_is_unavailable_in_bounded_time():
    registry = EngineRegistry(Runtime({"hang": Hanging(), "ok": Minimal()}), probe_timeout=0.3)
    started = time.monotonic()
    entries = {e["id"]: e for e in await registry.describe()}
    assert time.monotonic() - started < 3
    assert not entries["hang"]["available"] and "did not report its status" in entries["hang"]["reason"]
    assert entries["ok"]["available"]


async def test_a_raising_probe_is_unavailable_without_its_message():
    registry = EngineRegistry(Runtime({"bad": Raising()}))
    [entry] = await registry.describe()
    assert not entry["available"]
    assert "RuntimeError" in entry["reason"] and "secret" not in entry["reason"]


async def test_probes_are_cached_until_refresh():
    adapter = Minimal()
    registry = EngineRegistry(Runtime({"minimal": adapter}))
    await registry.describe()
    await registry.describe()
    assert adapter.calls == 1
    await registry.describe(refresh=True)
    assert adapter.calls == 2


async def test_live_models_replace_the_static_catalog_and_count_for_validation():
    registry = EngineRegistry(Runtime({"strict": Strict()}))
    registry.check_model("strict", "a")
    with pytest.raises(InvalidModel):
        registry.check_model("strict", "live")
    [entry] = await registry.describe()
    assert [m["id"] for m in entry["models"]] == ["live"]
    registry.check_model("strict", "live")
    with pytest.raises(InvalidModel):
        registry.check_model("strict", "other")


def test_model_checks():
    registry = EngineRegistry(Runtime({"minimal": Minimal(), "strict": Strict()}))
    registry.check_model("minimal", None)
    registry.check_model("minimal", "anything-goes:1.5[1m]")
    with pytest.raises(UnknownEngine):
        registry.check_model("nope", None)
    for bad in ("-rf", "two words", "", "a\nb", "x" * 201):
        with pytest.raises(InvalidModel):
            check_model_id(bad)
    with pytest.raises(InvalidModel):
        registry.check_model("strict", "c")


def test_an_engine_that_cannot_be_built_is_kept_as_unavailable(monkeypatch):
    def broken():
        raise OSError("boom")

    monkeypatch.setattr("app.services.claude_adapter.ClaudeAdapter", broken)
    adapters = default_adapters()
    assert isinstance(adapters["claude"], UnavailableAdapter)
    assert "OSError" in adapters["claude"].reason


async def test_the_unavailable_stand_in_describes_itself():
    registry = EngineRegistry(Runtime({"claude": UnavailableAdapter("claude", "Claude", "could not be set up")}))
    [entry] = await registry.describe()
    assert (entry["available"], entry["installed"], entry["reason"]) == (False, False, "could not be set up")


async def test_claude_reports_installed_and_signed_in_through_status(monkeypatch):
    from app.services import local_claude_runner

    async def signed_out():
        return local_claude_runner.CliStatus(installed=True, version="2.1.302", signed_in=False, error="Claude is not signed in.")

    monkeypatch.setattr(local_claude_runner, "get_status", signed_out)
    registry = EngineRegistry(Runtime({"claude": ClaudeAdapter()}))
    [entry] = await registry.describe()
    assert (entry["installed"], entry["signed_in"], entry["available"]) == (True, False, False)
    assert entry["version"] == "2.1.302"
    assert entry["capabilities"]["computers"] and entry["custom_models"]
    assert {m["id"] for m in entry["models"]} == {"sonnet", "opus", "haiku"}


async def test_cli_probe_kills_a_command_that_hangs():
    started = time.monotonic()
    result = await cli_probe.run([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.5)
    assert result.timed_out and not result.ok
    assert time.monotonic() - started < 5


async def test_cli_probe_reports_a_missing_program():
    result = await cli_probe.run(["/nonexistent/engine-cli", "--version"], timeout=2)
    assert result.failed_to_start and not result.ok


async def test_cli_probe_reads_output():
    result = await cli_probe.run([sys.executable, "-c", "print('v1.2.3')"], timeout=10)
    assert result.ok and result.stdout.strip() == "v1.2.3"


def test_the_contract_flags_of_the_built_in_engines():
    adapters = default_adapters()
    assert set(adapters) == {"claude", "codex", "acp", "openai_compat", "openrouter"}
    caps = {k: engine_registry.capabilities_of(a) for k, a in adapters.items()}
    assert caps["claude"].computers and not caps["codex"].computers
    assert not caps["openai_compat"].tools and not caps["openai_compat"].computers
    assert caps["openrouter"] == caps["openai_compat"] and adapters["openrouter"].replays_history
    assert caps["acp"].approvals and caps["codex"].approvals
