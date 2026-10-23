"""A record of every call the helper model made (docs/MEMORY.md, "The helper
model"): which agent it wrote for, which job, the helper's engine and model, and
the tokens when the engine reported them. Kept beside the memory journal, outside
every agent's folder, and shown by the usage report (`GET /api/v1/usage`, the CSV).

Append-only; a damaged line is skipped, and the newest rows win when the file
gets long. Only metadata is kept, never a prompt or an answer.
"""

import json
import time
from datetime import datetime

from app.core.logging import get_logger
from app.services import memory_journal

logger = get_logger(__name__)

MAX_ROWS = 4_000
JOBS = {"compaction": "summary of a long conversation", "memory": "memory upkeep"}


def _path():
    return memory_journal.state_root() / "helper-usage.ndjson"


def _int(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def record(*, agent_id: int | None, agent_name: str | None, job: str, engine: str, model: str | None,
           usage: dict | None, ok: bool) -> None:
    """Never raises: a failed record must not fail the job."""
    usage = usage if isinstance(usage, dict) else {}
    row = {
        "at": time.time(), "agent_id": agent_id, "agent": (agent_name or "")[:80], "job": job,
        "engine": engine[:40], "model": (model or "")[:80], "ok": bool(ok),
        "input_tokens": _int(usage.get("input_tokens")), "output_tokens": _int(usage.get("output_tokens")),
    }
    try:
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row) + "\n")
        if path.stat().st_size > MAX_ROWS * 300:
            lines = path.read_text(encoding="utf-8").splitlines()[-MAX_ROWS // 2:]
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        logger.exception("could not record a helper model call")


def rows(start: datetime | None = None, end: datetime | None = None) -> list[dict]:
    """Rows in `[start, end)`, oldest first."""
    out: list[dict] = []
    try:
        text = _path().read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        try:
            row = json.loads(line)
            at = float(row["at"])
        except (ValueError, KeyError, TypeError):
            continue
        if not isinstance(row, dict):
            continue
        if start is not None and at < start.timestamp():
            continue
        if end is not None and at >= end.timestamp():
            continue
        out.append(row)
    return out
