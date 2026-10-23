"""Daily logs: one line per finished turn of an agent whose memory is on
(docs/MEMORY.md).

`memory/log/YYYY-MM-DD.md` in the agent's folder is the agent's diary of what it
did that day: the time, the conversation it was in, what it said last, the tools
it used, and whether the turn failed or was stopped. A log is never loaded into
a prompt and is never recalled automatically; the agent's past-conversation
search (session_search) finds it, and the person can read it and delete it.

The same safety as the notes: text is redacted, files are written atomically
and readable by this account only, a link where the folder or a file should be
is never followed, and a day's log stops growing at `LOG_MAX_BYTES`.
"""

import re
from datetime import datetime
from pathlib import Path

from app.core.logging import get_logger
from app.services import agent_memory, memory_entries, memory_topics

logger = get_logger(__name__)

LOG_DIR = "log"
LOG_MAX_BYTES = 64 * 1024
REPLY_CHARS = 240
MAX_TOOLS = 6
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def logs_dir(agent_id: int) -> Path:
    return memory_topics.topics_dir(agent_id) / LOG_DIR


def _folder_ok(agent_id: int) -> bool:
    """The `memory` and `log` folders, when both are real folders (not links)."""
    for folder in (memory_topics.topics_dir(agent_id), logs_dir(agent_id)):
        try:
            if folder.is_symlink() or not folder.is_dir():
                return False
        except OSError:
            return False
    return True


def _fold(text: str, limit: int) -> str:
    folded = " ".join((text or "").split())
    return folded if len(folded) <= limit else folded[: limit - 1] + "…"


def turn_line(status: str, reply: str | None, error_code: str | None, tools: list[str]) -> str | None:
    """What one finished turn becomes, or None when it said nothing and
    nothing went wrong."""
    head = _fold(reply or "", REPLY_CHARS)
    if not head and status == "succeeded":
        return None
    unique = [t for t in dict.fromkeys(tools) if t][:MAX_TOOLS]
    if status == "failed":
        mark = f"(turn failed{': ' + _fold(error_code, 80) if error_code else ''}) "
    elif status == "cancelled":
        mark = "(stopped) "
    else:
        mark = ""
    return f"{mark}{head or 'no reply'}" + (f" [tools: {', '.join(unique)}]" if unique else "")


def append(agent_id: int, line: str, *, source: str, now: datetime | None = None) -> bool:
    """Add one line to today's log. False when it was not written (a link in
    the way, or the day's log is full); never raises for those."""
    now = now or datetime.now()
    clean, _ = agent_memory.redact_secrets(_fold(line, 1_000))
    entry = f"- {now:%H:%M} · from {source} · {clean}"
    folder = logs_dir(agent_id)
    try:
        memory_topics.topics_dir(agent_id).mkdir(parents=True, exist_ok=True, mode=0o700)
        if memory_topics.topics_dir(agent_id).is_symlink():
            return False
        folder.mkdir(exist_ok=True, mode=0o700)
        if not _folder_ok(agent_id):
            return False
        path = folder / f"{now:%Y-%m-%d}.md"
        current = ""
        if path.is_symlink():
            return False
        if path.exists():
            current = path.read_text(encoding="utf-8", errors="replace")
        if len(current.encode("utf-8")) + len(entry) + 1 > LOG_MAX_BYTES:
            return False
        agent_memory.write_atomic(path, current + ("" if not current or current.endswith("\n") else "\n") + entry + "\n")
    except OSError:
        logger.exception("could not write the daily log", agent_id=agent_id)
        return False
    return True


def list_logs(agent_id: int) -> list[dict]:
    """The days that have a log, newest first."""
    if not _folder_ok(agent_id):
        return []
    out = []
    try:
        entries = list(logs_dir(agent_id).iterdir())
    except OSError:
        return []
    for entry in entries:
        day = entry.name.removesuffix(".md")
        try:
            if not entry.name.endswith(".md") or not memory_entries.is_day(day) or entry.is_symlink() or not entry.is_file():
                continue
            info = entry.stat()
        except OSError:
            continue
        out.append({"day": day, "bytes": info.st_size, "modified_at": info.st_mtime})
    out.sort(key=lambda d: d["day"], reverse=True)
    return out


def read_log(agent_id: int, day: str) -> str | None:
    if not memory_entries.is_day(day) or not _folder_ok(agent_id):
        return None
    path = logs_dir(agent_id) / f"{day}.md"
    try:
        if path.is_symlink() or not path.is_file():
            return None
        with path.open("rb") as handle:
            data = handle.read(LOG_MAX_BYTES + 1)
    except OSError:
        return None
    text, _ = agent_memory.redact_secrets(data[:LOG_MAX_BYTES].decode("utf-8", errors="replace"))
    return text


def delete_log(agent_id: int, day: str) -> bool:
    if not memory_entries.is_day(day) or not _folder_ok(agent_id):
        return False
    path = logs_dir(agent_id) / f"{day}.md"
    try:
        if path.is_symlink() or not path.is_file():
            return False
        path.unlink()
    except OSError:
        return False
    return True


def remove_all(agent_id: int) -> None:
    for item in list_logs(agent_id):
        delete_log(agent_id, item["day"])
