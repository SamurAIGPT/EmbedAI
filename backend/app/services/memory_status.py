"""What memory upkeep last did for one agent, kept beside the journal (outside
the agent's folder): the last capture, and the entries last moved out of
MEMORY.md to make room (so the agent can be told). docs/MEMORY.md."""

import json

from app.core.logging import get_logger
from app.services import agent_memory, memory_journal

logger = get_logger(__name__)


def _path(agent_id: int):
    return memory_journal.state_root() / "upkeep" / f"{int(agent_id)}.json"


def read(agent_id: int) -> dict:
    try:
        data = json.loads(_path(agent_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def update(agent_id: int, **fields) -> None:
    try:
        path = _path(agent_id)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        data = read(agent_id)
        data.update(fields)
        agent_memory.write_atomic(path, json.dumps(data))
    except OSError:
        logger.exception("could not save the memory upkeep status", agent_id=agent_id)


def remove(agent_id: int) -> None:
    try:
        _path(agent_id).unlink()
    except OSError:
        pass
