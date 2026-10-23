"""An agent as one portable file, to keep or to give to someone else.

The file is an allowlist, not a dump of the row. It can carry only:

- the agent's name, title, description and instructions;
- optionally its notes (MEMORY.md), as starter notes for the copy.

It has no field for a model, a provider, a folder, an approval mode, a pin,
conversation history, or anything about this computer. Whatever a file says
beyond the allowlist is dropped on import, so a hand-edited file can never
widen what an imported agent may do: it always starts on `ask`, with the
default model and its own new folder.

Every text part passes through the notes' secret redaction on the way out and
again on the way in; export reports which parts were changed.
"""

import json
from datetime import UTC, datetime
from typing import Any

from app.schemas.agent import INSTRUCTIONS_MAX_BYTES
from app.services import memory_topics
from app.services.agent_memory import FILE_MAX_BYTES, redact_secrets

FORMAT = "open-dots.agent"
VERSION = 1
# The whole file, as JSON text.
PACKAGE_MAX_BYTES = 1_000_000

_LIMITS = {"name": 80, "title": 200, "description": 4000}


class PackageError(ValueError):
    pass


def _clean(value: str | None) -> tuple[str | None, bool]:
    if value is None:
        return None, False
    text, count = redact_secrets(value)
    return text, count > 0


def export_package(agent, notes: str | None, topics: dict[str, str] | None = None) -> tuple[dict, list[str]]:
    """The file for `agent`, and the names of the parts that had something
    redacted. `notes` None leaves the notes out; `topics` (file name -> text)
    are the topic notes that travel with them."""
    redacted: list[str] = []
    body: dict[str, Any] = {"name": agent.name}
    for field in ("title", "description", "instructions"):
        value, changed = _clean(getattr(agent, field))
        if value:
            body[field] = value
        if changed:
            redacted.append(field)
    document: dict[str, Any] = {
        "format": FORMAT,
        "version": VERSION,
        "exported_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "agent": body,
    }
    if notes is not None and notes.strip():
        text, changed = _clean(notes)
        document["notes"] = {"MEMORY.md": text}
        if changed:
            redacted.append("notes")
    if notes is not None and topics:
        clean: dict[str, str] = {}
        for name, body_text in sorted(topics.items()):
            cleaned, changed = _clean(body_text)
            if cleaned.strip():
                clean[name] = cleaned
            if changed and "topic notes" not in redacted:
                redacted.append("topic notes")
        if clean:
            document.setdefault("notes", {})["topics"] = clean
    return document, redacted


def file_name(agent_name: str) -> str:
    """A safe file name for the download, from the agent's name."""
    stem = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in agent_name.strip().lower())
    stem = "-".join(part for part in stem.split("-") if part)[:60] or "agent"
    return f"{stem}.agent.json"


def _text(container: dict, field: str, *, required: bool = False) -> str | None:
    value = container.get(field)
    if value is None or (isinstance(value, str) and not value.strip() and not required):
        if required:
            raise PackageError(f"agent.{field} is required")
        return None
    if not isinstance(value, str):
        raise PackageError(f"agent.{field} must be text")
    return value


def parse_package(document: Any) -> dict:
    """What an import may use from `document`:
    `{name, title, description, instructions, notes}`. Raises PackageError."""
    if not isinstance(document, dict):
        raise PackageError("This is not an agent file.")
    if document.get("format") != FORMAT:
        raise PackageError("This is not an agent file.")
    if document.get("version") != VERSION:
        raise PackageError(f"This agent file is version {document.get('version')!r}; this app reads version {VERSION}.")
    if len(json.dumps(document, ensure_ascii=False).encode("utf-8")) > PACKAGE_MAX_BYTES:
        raise PackageError("The agent file is larger than 1 MB.")
    body = document.get("agent")
    if not isinstance(body, dict):
        raise PackageError("The agent file has no agent in it.")

    name = _text(body, "name", required=True).strip()
    if not name:
        raise PackageError("agent.name is required")
    if "\n" in name or "\r" in name:
        raise PackageError("agent.name must fit on one line")
    title = _text(body, "title")
    if title is not None:
        title = title.strip()
        if "\n" in title or "\r" in title:
            raise PackageError("agent.title must fit on one line")
    description = _text(body, "description")
    instructions = _text(body, "instructions") or ""
    for field, value in (("name", name), ("title", title), ("description", description)):
        if value is not None and len(value) > _LIMITS[field]:
            raise PackageError(f"agent.{field} must be at most {_LIMITS[field]} characters")
    if len(instructions.encode("utf-8")) > INSTRUCTIONS_MAX_BYTES:
        raise PackageError(f"agent.instructions must be at most {INSTRUCTIONS_MAX_BYTES} bytes")

    notes = None
    topics: dict[str, str] = {}
    raw_notes = document.get("notes")
    if raw_notes is not None:
        if not isinstance(raw_notes, dict) or not isinstance(raw_notes.get("MEMORY.md", ""), str):
            raise PackageError("notes must be {\"MEMORY.md\": text}")
        notes = raw_notes.get("MEMORY.md") or None
        if notes is not None and len(notes.encode("utf-8")) > FILE_MAX_BYTES:
            raise PackageError(f"The notes must be at most {FILE_MAX_BYTES // 1024} KB.")
        raw_topics = raw_notes.get("topics")
        if raw_topics is not None:
            if not isinstance(raw_topics, dict) or len(raw_topics) > memory_topics.MAX_TOPICS:
                raise PackageError(f"notes.topics must be at most {memory_topics.MAX_TOPICS} topic notes")
            for name, value in raw_topics.items():
                try:
                    memory_topics.check_name(name)
                except memory_topics.InvalidTopicName:
                    raise PackageError(f"{name!r} is not a topic note name") from None
                if name == memory_topics.ARCHIVE_NAME or not isinstance(value, str):
                    raise PackageError(f"topic note {name} must be text")
                if len(value.encode("utf-8")) > memory_topics.TOPIC_MAX_BYTES:
                    raise PackageError(f"topic note {name} must be at most {memory_topics.TOPIC_MAX_BYTES // 1024} KB")
            topics = {n: _clean(v)[0] for n, v in raw_topics.items() if v.strip()}

    return {
        "name": name,
        "title": title or None,
        "description": _clean(description)[0] or None,
        "instructions": _clean(instructions)[0] or "",
        "notes": _clean(notes)[0] if notes else None,
        "topics": topics,
    }
