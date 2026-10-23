"""Who writes a small background text for an agent (docs/MEMORY.md).

Three jobs write text with a quick model call: the model-written summary of a
folded conversation (`compaction`), and the model steps of memory upkeep
(`memory`: noticing facts, organizing, the tidy-up). Each is written by the
agent's own engine whenever that engine can write it. An agent whose engine
can write a job always writes it itself.
"""

import functools
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

COMPACTION = "compaction"
MEMORY = "memory"


@dataclass
class TextSource:
    """`generate(prompt, timeout=...)` returns the text or raises."""

    via: str  # "engine"
    generate: Callable[..., Awaitable[str]]


def engine_source(adapter, *, config: dict | None, model: str | None) -> TextSource | None:
    """The engine's own quick text call, or None when it has none."""
    call = getattr(adapter, "generate_text", None)
    if call is None:
        return None
    return TextSource("engine", functools.partial(call, config=config, model=model))
