"""The engine registry: which engines this app can run turns on, what each can
do, and whether each is usable right now (docs/ENGINES.md).

It reads the runtime's adapters live (tests swap them), so it owns no engines
of its own. Every read of an adapter's self-description has a safe default, so
an adapter written against the smaller contract still works: no capabilities,
any model, and a probe derived from `status()`.

Nothing here runs at startup. A probe runs only when someone asks for the
engines (GET /api/v1/engines) and is bounded by a timeout; an adapter that
cannot be built, a probe that raises and a probe that hangs all come out as an
engine that is "unavailable", with the reason, never as an error.
"""

import asyncio
import os
import re
from dataclasses import dataclass, field

from app.core.logging import get_logger
from app.services.provider_adapter import NO_CAPABILITIES, Capabilities, EngineProbe, ModelOption

logger = get_logger(__name__)

PROBE_TIMEOUT_SECONDS = 12.0
PROBE_CACHE_SECONDS = 15.0

# A model id is an argument value for a CLI or a field in a request, never a
# flag or shell text: one line, no spaces, not starting with "-".
_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+\-\[\]]{0,199}$")


class UnknownEngine(Exception):
    pass


class InvalidModel(ValueError):
    pass


class UnavailableAdapter:
    """Stands in for an adapter that could not be built, so the rest of the
    engines still load and this one shows why it is missing."""

    kind = "cli"
    capabilities = NO_CAPABILITIES
    models: list[ModelOption] = []
    custom_models = False

    def __init__(self, key: str, label: str, reason: str) -> None:
        self.key = key
        self.label = label
        self.reason = reason

    def new_session_id(self) -> None:
        return None

    async def status(self):
        from app.services.provider_adapter import ProviderStatus

        return ProviderStatus(ready=False, error=self.reason)

    async def probe(self) -> EngineProbe:
        return EngineProbe(installed=False, ready=False, reason=self.reason)

    async def run_turn(self, ctx, sink, gate):
        from app.services.provider_adapter import TurnOutcome

        return TurnOutcome("failed", error=self.reason, error_code="engine_unavailable")


def default_adapters() -> dict:
    """Every built-in engine. One that cannot be built is kept as unavailable."""
    from app.services.acp_adapter import AcpAdapter
    from app.services.acp_presets import GeminiAdapter, CursorAdapter, OpenCodeAdapter
    from app.services.claude_adapter import ClaudeAdapter
    from app.services.codex_adapter import CodexAdapter
    from app.services.openai_chat_adapter import OpenAIChatAdapter, OpenRouterAdapter

    adapters: dict = {}
    for key, label, factory in (
        ("claude", "Claude", ClaudeAdapter),
        ("codex", "Codex", CodexAdapter),
        ("cursor", "Cursor", CursorAdapter),
        ("gemini", "Gemini CLI", GeminiAdapter),
        ("opencode", "OpenCode (experimental)", OpenCodeAdapter),
        ("openrouter", "OpenRouter", OpenRouterAdapter),
        ("acp", "ACP agent", AcpAdapter),
        ("openai_compat", "OpenAI-compatible", OpenAIChatAdapter),
    ):
        selected = os.environ.get("OPEN_DOTS_PRESET_ENGINES")
        enabled = {name.strip() for name in selected.split(",")} if selected is not None else {"gemini", "cursor", "openrouter"}
        if key in {"gemini", "cursor", "opencode", "openrouter"} and key not in enabled:
            continue
        try:
            adapters[key] = factory()
        except Exception as exc:  # noqa: BLE001
            logger.exception("could not set up an engine", engine=key)
            adapters[key] = UnavailableAdapter(key, label, f"This engine could not be set up ({exc.__class__.__name__}).")
    return adapters


def label_of(adapter, key: str) -> str:
    label = getattr(adapter, "label", None)
    return label if isinstance(label, str) and label else key


def capabilities_of(adapter) -> Capabilities:
    caps = getattr(adapter, "capabilities", None)
    return caps if isinstance(caps, Capabilities) else NO_CAPABILITIES


def static_models(adapter) -> list[ModelOption]:
    models = getattr(adapter, "models", None)
    return [m for m in models if isinstance(m, ModelOption)] if isinstance(models, list) else []


def accepts_custom_models(adapter) -> bool:
    # An adapter that says nothing accepts any model, as before the registry.
    value = getattr(adapter, "custom_models", None)
    return True if value is None else bool(value)


def check_model_id(model: str) -> None:
    if not _MODEL_ID.match(model):
        raise InvalidModel(
            "A model id is one word of letters, digits and . _ : / @ + - [ ], "
            "and does not start with '-'."
        )


async def run_probe(adapter, timeout: float) -> EngineProbe:
    """The adapter's probe (or one derived from `status()`), never raising and
    never taking longer than `timeout`."""

    async def probe() -> EngineProbe:
        own = getattr(adapter, "probe", None)
        if callable(own):
            return await own()
        status = await adapter.status()
        installed = getattr(status, "installed", None)
        return EngineProbe(
            installed=installed if installed is not None else (status.ready or status.version is not None),
            ready=status.ready,
            version=status.version,
            signed_in=getattr(status, "signed_in", None),
            reason=None if status.ready else status.error,
        )

    try:
        result = await asyncio.wait_for(probe(), timeout)
    except asyncio.TimeoutError:
        return EngineProbe(
            installed=False,
            ready=False,
            reason=f"This engine did not report its status within {timeout:g} seconds. "
            "Check that its command starts in a terminal.",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("engine probe failed", engine=getattr(adapter, "key", "?"), error=exc.__class__.__name__)
        return EngineProbe(installed=False, ready=False, reason=f"The status check failed ({exc.__class__.__name__}).")
    if not isinstance(result, EngineProbe):
        return EngineProbe(installed=False, ready=False, reason="The status check returned nothing usable.")
    return result


@dataclass
class EngineRegistry:
    runtime: object
    probe_timeout: float = PROBE_TIMEOUT_SECONDS
    cache_seconds: float = PROBE_CACHE_SECONDS
    _cache: dict = field(default_factory=dict)

    @property
    def adapters(self) -> dict:
        return self.runtime.adapters

    def ids(self) -> list[str]:
        return list(self.adapters)

    def get(self, engine_id: str):
        adapter = self.adapters.get(engine_id)
        if adapter is None:
            raise UnknownEngine(engine_id)
        return adapter

    def capabilities(self, engine_id: str) -> Capabilities:
        return capabilities_of(self.get(engine_id))

    def check_model(self, engine_id: str, model: str | None) -> None:
        """Raise UnknownEngine or InvalidModel. None (the engine's default) is
        always fine. An engine without custom models takes only ids from its
        catalog, including a live one a probe found."""
        adapter = self.get(engine_id)
        if model is None:
            return
        check_model_id(model)
        if accepts_custom_models(adapter):
            return
        known = {m.id for m in static_models(adapter)}
        cached = self._cache.get(engine_id)
        if cached is not None and cached[1].models:
            known |= {m.id for m in cached[1].models}
        if model not in known:
            label = label_of(adapter, engine_id)
            raise InvalidModel(
                f"{label} has no model named {model!r}."
                + (" It runs the agent's own default model only." if not known else "")
            )

    def forget(self, engine_id: str | None = None) -> None:
        if engine_id is None:
            self._cache.clear()
        else:
            self._cache.pop(engine_id, None)

    async def _probe(self, engine_id: str, adapter, refresh: bool) -> EngineProbe:
        loop = asyncio.get_running_loop()
        cached = self._cache.get(engine_id)
        if not refresh and cached is not None and loop.time() - cached[0] < self.cache_seconds and cached[2] is adapter:
            return cached[1]
        await self.runtime.prepare_adapter(engine_id, adapter)
        result = await run_probe(adapter, self.probe_timeout)
        self._cache[engine_id] = (loop.time(), result, adapter)
        return result

    async def describe(self, *, refresh: bool = False, only: str | None = None) -> list[dict]:
        items = [(k, a) for k, a in self.adapters.items() if only is None or k == only]
        probes = await asyncio.gather(*(self._probe(k, a, refresh) for k, a in items))
        out = []
        for (engine_id, adapter), probe in zip(items, probes):
            out.append(await self._entry(engine_id, adapter, probe))
        return out

    async def _entry(self, engine_id: str, adapter, probe: EngineProbe) -> dict:
        models = probe.models if probe.models else static_models(adapter)
        default = next((m.id for m in models if m.default), None)
        config = None
        public = getattr(adapter, "public_config", None)
        if getattr(adapter, "needs_config", False) and callable(public):
            try:
                config = public(getattr(adapter, "config", None))
            except Exception:  # noqa: BLE001
                config = None
        return {
            "id": engine_id,
            "label": label_of(adapter, engine_id),
            "kind": getattr(adapter, "kind", "cli"),
            "installed": bool(probe.installed),
            "version": probe.version,
            "signed_in": probe.signed_in,
            "available": bool(probe.ready),
            "reason": None if probe.ready else (probe.reason or "This engine is not ready."),
            "models": [{"id": m.id, "label": m.label, "default": m.default} for m in models],
            "default_model": default,
            "custom_models": accepts_custom_models(adapter),
            "capabilities": capabilities_of(adapter).as_dict(),
            "configurable": bool(getattr(adapter, "needs_config", False)),
            "config": config,
            "preset": bool(getattr(adapter, "preset", False)),
            "setup": getattr(adapter, "setup", None),
            "config_fields": getattr(adapter, "config_fields", []),
            "limit": self.runtime.engine_limit(engine_id) if callable(getattr(self.runtime, "engine_limit", None)) else None,
        }
