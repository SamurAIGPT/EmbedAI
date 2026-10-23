"""Who writes a small background text for an agent (docs/MEMORY.md).

Two jobs write text with a quick model call: the model-written summary of a
folded conversation (`compaction`) and the model steps of memory upkeep
(`memory`: noticing facts, organizing, the tidy-up).

**Rules.** An agent whose own engine can write the job always writes it itself.
Only when its engine has no quick text call (Codex and ACP agents here) can the
**helper** write it: one connected engine, optionally one of its models, that the
person set (`HELPER_ENGINE` / `HELPER_MODEL`, or the Memory settings). It is off
unless set, an agent can be switched to never use it (`use_helper`), the text it
reads is redacted again before it leaves, every call is recorded
(helper_usage.py), and a helper that is missing, failing or slow breaks nothing:
the job falls back as it does with no helper.
"""

import functools
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.core.config import settings
from app.core.logging import get_logger
from app.services import helper_usage, memory_settings
from app.services.agent_memory import redact_secrets

logger = get_logger(__name__)

COMPACTION = "compaction"
MEMORY = "memory"
# What the helper uses on an engine when the person named no model for it.
DEFAULT_HELPER_MODELS = {"claude": "haiku"}
_MODEL = re.compile(r"^[A-Za-z0-9][\x21-\x7e]*$")
_said: dict[str, str | None] = {"line": None}


@dataclass
class TextSource:
    """`generate(prompt, timeout=...)` returns the text or raises."""

    via: str  # "engine" | "helper"
    generate: Callable[..., Awaitable[str]]
    engine: str = ""
    model: str | None = None


@dataclass(frozen=True)
class Helper:
    engine: str
    model: str | None = None
    source: str = "settings"  # "environment" | "settings"


def configured() -> Helper | None:
    """The helper the person set, or None. The environment wins over the saved
    setting; an empty `HELPER_ENGINE` switches a saved helper off. A model name
    that could be taken for an option switches the helper off rather than write
    with a model nobody named."""
    saved = memory_settings.helper()
    env_engine = settings.HELPER_ENGINE
    engine = env_engine.strip() if env_engine is not None else (saved or {}).get("engine", "")
    if not engine:
        return None
    env_model = settings.HELPER_MODEL
    model = env_model.strip() if env_model is not None else (saved or {}).get("model", "")
    if model and not _MODEL.match(model):
        return None
    return Helper(engine, model or None, "environment" if env_engine is not None else "settings")


def _say(line: str | None) -> None:
    """Logged once per change, not once per call."""
    if line and line != _said["line"]:
        logger.warning("helper model: " + line)
    _said["line"] = line


def can_write(adapter) -> bool:
    """True when the engine has a quick text call of its own."""
    return getattr(adapter, "generate_text", None) is not None


def engine_source(adapter, *, config: dict | None, model: str | None) -> TextSource | None:
    """The engine's own quick text call, or None when it has none."""
    if not can_write(adapter):
        return None
    call = adapter.generate_text
    return TextSource("engine", functools.partial(call, config=config, model=model), engine=getattr(adapter, "key", ""), model=model)


async def helper_source(rt, agent, job: str) -> TextSource | None:
    """The helper's text call for `agent`'s job, or None (no helper set, the agent
    never uses it, its engine cannot write, or it is not usable now)."""
    if agent is not None and not getattr(agent, "use_helper", True):
        return None
    helper = configured()
    if helper is None:
        _say(None)
        return None
    adapter = rt.adapters.get(helper.engine)
    call = getattr(adapter, "generate_text", None)
    if not can_write(adapter):
        _say(f"the helper engine {helper.engine!r} is not connected or cannot make a quick text call, so agents whose engine has none keep their fallback")
        return None
    try:
        config = await rt.prepare_adapter(helper.engine, adapter)
    except Exception:  # noqa: BLE001  (a helper never fails a job)
        _say("the helper engine could not be set up")
        return None
    model = helper.model or DEFAULT_HELPER_MODELS.get(helper.engine) or (config or {}).get("model")
    _say(f"jobs for agents whose engine has no quick text call are written by {helper.engine}" + (f" ({model})" if model else ""))

    async def generate(prompt: str, *, timeout: float = 60) -> str:
        used: dict = {}
        ok = False
        try:
            # Redacted again here: what leaves the agent's own provider carries no secret.
            text = await call(redact_secrets(prompt)[0], config=config, model=model, timeout=timeout, on_usage=used.update)
            ok = True
            return text
        finally:
            helper_usage.record(
                agent_id=getattr(agent, "id", None), agent_name=getattr(agent, "name", None), job=job,
                engine=helper.engine, model=model, usage=used, ok=ok,
            )

    return TextSource("helper", generate, engine=helper.engine, model=model)


async def source_for(rt, agent, job: str, adapter, *, config: dict | None, model: str | None) -> TextSource | None:
    """Who writes `job` for `agent`: its own engine when it can, else the helper."""
    return engine_source(adapter, config=config, model=model) or await helper_source(rt, agent, job)
