"""An agent's topic notes: `memory/<topic>.md` files beside its MEMORY.md
(docs/MEMORY.md).

MEMORY.md holds what the agent needs in every conversation and loads into
every turn. Topic notes hold longer notes about one subject (a person, a
project, a trip). They are never loaded whole: the system prompt lists them by
name (the topic index), the agent reads one with its file tools when it needs
it, and automatic recall (memory_recall.py) puts a matching passage in front
of the person's message.

The same safety as MEMORY.md: secrets are redacted when a note is saved and
again when any part of it is loaded, writes are atomic and readable by this
account only, a save carries the hash of the text it was based on, and a link
or anything but a regular file reads as empty, so a topic note can never be
used to read some other file.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from app.services import agent_memory
from app.services.agent_memory import MemoryConflict, MemoryTooLarge, redact_secrets, text_hash

TOPICS_DIR = "memory"
# One file name: letters, digits, dash and underscore, then ".md". No dots
# inside, no folders, nothing hidden.
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,59}\.md$")
TOPIC_MAX_BYTES = 64 * 1024
MAX_TOPICS = 100
# The topic index in the system prompt: at most this many topics and chars.
INDEX_MAX_TOPICS = 40
INDEX_MAX_CHARS = 2_000
# Only the start of a note is read for its title and aliases.
_HEAD_CHARS = 2_048


class InvalidTopicName(ValueError):
    pass


class TooManyTopics(ValueError):
    pass


@dataclass(frozen=True)
class TopicHeader:
    title: str | None
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class TopicInfo:
    name: str
    title: str | None
    aliases: tuple[str, ...]
    bytes: int
    modified_at: float


@dataclass(frozen=True)
class TopicDoc:
    name: str
    path: str
    text: str
    hash: str
    bytes: int
    exists: bool


def check_name(name: str) -> str:
    if not isinstance(name, str) or not _NAME.fullmatch(name) or name.lower() == agent_memory.MEMORY_FILE.lower():
        raise InvalidTopicName(
            "A topic is one file name ending in .md: letters, digits, dashes and underscores, "
            "at most 63 characters, for example travel.md."
        )
    return name


def topic_name_for(raw: str) -> str:
    """A free-form subject ("Asha's school") as a topic file name
    ("asha-s-school.md"). Raises InvalidTopicName when nothing is left."""
    base = re.sub(r"\.md$", "", (raw or "").strip(), flags=re.IGNORECASE).lower()
    base = re.sub(r"[^a-z0-9]+", "-", base).strip("-")[:59].strip("-")
    return check_name(f"{base}.md")


def topics_dir(agent_id: int) -> Path:
    return agent_memory.agent_home(agent_id) / TOPICS_DIR


def _folder(agent_id: int) -> Path | None:
    """The topics folder, or None when it is missing or is not a real folder
    inside the agent's own (a link to elsewhere reads as no topics)."""
    folder = topics_dir(agent_id)
    try:
        if folder.is_symlink() or not folder.is_dir():
            return None
    except OSError:
        return None
    return folder


def _read(path: Path) -> str:
    try:
        if path.is_symlink() or not path.is_file():
            return ""
        with path.open("rb") as handle:
            data = handle.read(TOPIC_MAX_BYTES + 1)
    except OSError:
        return ""
    return data[:TOPIC_MAX_BYTES].decode("utf-8", errors="replace")


def parse_header(text: str) -> TopicHeader:
    """`title` and `aliases` from a small front matter block (`---` lines at
    the top), else the first heading as the title."""
    head = text[:_HEAD_CHARS]
    title: str | None = None
    aliases: list[str] = []
    match = re.match(r"\s*---\r?\n(.*?)\r?\n---(?:\r?\n|$)", head, re.S)
    if match:
        lines = match.group(1).splitlines()
        i = 0
        while i < len(lines):
            field = re.match(r"^(title|aliases)\s*:\s*(.*)$", lines[i])
            i += 1
            if not field:
                continue
            key, value = field.group(1), field.group(2).strip()
            if key == "title":
                if value:
                    title = value.strip("'\"").strip()[:160] or None
                continue
            items: list[str] = []
            if value.startswith("["):
                items = value.strip("[]").split(",")
            elif value:
                items = value.split(",")
            else:
                while i < len(lines) and re.match(r"^\s*-\s+", lines[i]):
                    items.append(re.sub(r"^\s*-\s+", "", lines[i]))
                    i += 1
            for item in items:
                item = item.strip().strip("'\"").strip()[:40]
                if item and item not in aliases:
                    aliases.append(item)
        body = head[match.end():]
    else:
        body = head
    if title is None:
        heading = re.search(r"^#{1,6}[ \t]+(.+)$", body, re.M)
        if heading:
            title = heading.group(1).strip()[:160]
    return TopicHeader(title=title, aliases=tuple(aliases[:12]))


def body_of(text: str) -> str:
    """The note without its front matter."""
    return re.sub(r"^\s*---\r?\n.*?\r?\n---(?:\r?\n|$)", "", text, count=1, flags=re.S).strip()


def list_topics(agent_id: int) -> list[TopicInfo]:
    """Every topic note, newest change first."""
    folder = _folder(agent_id)
    if folder is None:
        return []
    out: list[TopicInfo] = []
    try:
        entries = list(folder.iterdir())
    except OSError:
        return []
    for entry in entries:
        if not _NAME.fullmatch(entry.name) or entry.name.lower() == agent_memory.MEMORY_FILE.lower():
            continue
        try:
            if entry.is_symlink() or not entry.is_file():
                continue
            info = entry.stat()
        except OSError:
            continue
        header = parse_header(_read(entry))
        out.append(
            TopicInfo(
                name=entry.name,
                title=header.title,
                aliases=header.aliases,
                bytes=info.st_size,
                modified_at=info.st_mtime,
            )
        )
    out.sort(key=lambda t: (-t.modified_at, t.name))
    return out


def read_topic(agent_id: int, name: str) -> TopicDoc:
    check_name(name)
    path = topics_dir(agent_id) / name
    text = _read(path) if _folder(agent_id) is not None else ""
    exists = _folder(agent_id) is not None and path.is_file() and not path.is_symlink()
    return TopicDoc(
        name=name, path=str(path), text=text, hash=text_hash(text), bytes=len(text.encode("utf-8")), exists=exists
    )


def write_topic(
    agent_id: int, name: str, text: str, *, expected_hash: str | None = None
) -> tuple[TopicDoc, int, str | None]:
    """Save a topic note. With `expected_hash`, refuse (MemoryConflict) when
    the note no longer has that content; a note not yet created has the hash of
    "". Returns the saved note, how many secrets were redacted, and the text it
    replaced (None for a new note). Raises InvalidTopicName, MemoryTooLarge,
    TooManyTopics."""
    check_name(name)
    if len(text.encode("utf-8")) > TOPIC_MAX_BYTES:
        raise MemoryTooLarge(f"A topic note must be at most {TOPIC_MAX_BYTES // 1024} KB.")
    folder = topics_dir(agent_id)
    if folder.is_symlink():
        raise InvalidTopicName("The agent's memory folder is a link; replace it with a folder first.")
    current = read_topic(agent_id, name)
    if expected_hash is not None and current.hash != expected_hash:
        raise MemoryConflict(current)
    if not current.exists and len(list_topics(agent_id)) >= MAX_TOPICS:
        raise TooManyTopics(f"An agent keeps at most {MAX_TOPICS} topic notes; delete one first.")
    clean, redacted = redact_secrets(text.replace("\r\n", "\n"))
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    agent_memory.write_atomic(folder / name, clean)
    return read_topic(agent_id, name), redacted, (current.text if current.exists else None)


def delete_topic(agent_id: int, name: str, *, expected_hash: str | None = None) -> str | None:
    """Remove a topic note; returns the text it had (None when there was
    none). Raises MemoryConflict on a stale `expected_hash`."""
    current = read_topic(agent_id, name)
    if expected_hash is not None and current.hash != expected_hash:
        raise MemoryConflict(current)
    if not current.exists:
        return None
    try:
        (topics_dir(agent_id) / name).unlink()
    except FileNotFoundError:
        return None
    return current.text


def remove_all(agent_id: int) -> None:
    """Delete every topic note (an agent being deleted). Only regular note
    files go; anything else the agent put in the folder stays."""
    for topic in list_topics(agent_id):
        try:
            (topics_dir(agent_id) / topic.name).unlink()
        except OSError:
            pass


def topic_index(agent_id: int) -> str:
    """The list of topic notes for the system prompt, alphabetical, bounded.
    Titles come from the files, so they are redacted too."""
    topics = sorted(list_topics(agent_id), key=lambda t: t.name)
    lines: list[str] = []
    used = 0
    omitted = 0
    for topic in topics:
        if len(lines) >= INDEX_MAX_TOPICS:
            omitted += 1
            continue
        about = f" — {topic.title}" if topic.title else ""
        also = f" (also: {', '.join(topic.aliases)})" if topic.aliases else ""
        line = " ".join(f"- {TOPICS_DIR}/{topic.name}{about}{also}".split())
        line, _ = redact_secrets(line)
        if used + len(line) + 1 > INDEX_MAX_CHARS:
            omitted += 1
            continue
        lines.append(line)
        used += len(line) + 1
    if omitted:
        lines.append(f"- …and {omitted} more in {TOPICS_DIR}/")
    return "\n".join(lines)


_LEAD = re.compile(r"^(?:\s*\r?\n|#{1,6}[ \t]+[^\r\n]*\r?\n)*")
_FRONT = re.compile(r"^---\r?\n(.*?)\r?\n---(?:\r?\n|$)", re.S)
ALIASES_MAX = 12


def _alias_list(aliases) -> str:
    return "[" + ", ".join(aliases) + "]"


def clean_aliases(aliases) -> list[str]:
    """Aliases as header values: no commas, brackets or line breaks, at most
    40 characters each, no repeats."""
    out: list[str] = []
    for alias in aliases or ():
        if not isinstance(alias, str):
            continue
        item = " ".join(re.sub(r"[\r\n,\[\]]+", " ", alias).split())[:40]
        if item and item.lower() not in {a.lower() for a in out}:
            out.append(item)
    return out[:ALIASES_MAX]


def merge_topic_text(existing: str | None, *, title: str, aliases, lines: list[str]) -> str:
    """The note with `lines` appended. A new note gets a header (`title`,
    `aliases`); an existing one keeps its header and gains only the aliases it
    does not have yet (up to 12), and a note with no header gets one."""
    aliases = clean_aliases(aliases)
    body = "\n".join(lines)
    if existing is None or not existing.strip():
        head = f"---\ntitle: {title}\n" + (f"aliases: {_alias_list(aliases)}\n" if aliases else "") + "---\n\n"
        return f"{head}{body}\n"
    lead = _LEAD.match(existing).group(0)
    rest = existing[len(lead):]
    front = _FRONT.match(rest)
    if front is None:
        known_title = parse_header(existing).title or title
        head = f"---\ntitle: {known_title}\n" + (f"aliases: {_alias_list(aliases)}\n" if aliases else "") + "---\n\n"
        text = head + existing
    else:
        have = list(parse_header(existing).aliases)
        lower = {a.lower() for a in have}
        added = [a for a in aliases if a.lower() not in lower]
        if not added:
            text = existing
        else:
            merged = (have + added)[:ALIASES_MAX]
            block = front.group(1).split("\n")
            at = next((k for k, line in enumerate(block) if re.match(r"^aliases\s*:", line)), None)
            if at is None:
                block.append(f"aliases: {_alias_list(merged)}")
            else:
                end = at + 1
                while end < len(block) and re.match(r"^\s*-\s+", block[end]):
                    end += 1
                block[at:end] = [f"aliases: {_alias_list(merged)}"]
            text = f"{lead}---\n" + "\n".join(block) + "\n---\n" + rest[front.end():]
    return text + ("" if text.endswith("\n") else "\n") + body + "\n"
