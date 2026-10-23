"""The shape of a dated line in MEMORY.md and in topic notes (docs/MEMORY.md).

A line the app or the agent adds is one bullet:

    - 2026-10-23 · from chat "Plans" (noticed) · The person is vegetarian · until 2026-11-01

A person's hand-written bullet without a date is just as valid; it is not an
"entry" and nothing in the app ever rewrites or moves it. A replaced fact is
struck through rather than deleted:

    - 2026-10-01 · ~~The office is in Pune~~ · superseded 2026-10-23

Only the parts the app needs are read: the date, an optional `until` date, and
whether the line is struck through. Continuation lines (indented, or the body of
a code block) belong to the entry above them.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime

_DATED = re.compile(r"^(- (\d{4}-\d{2}-\d{2}) · (?:from [^·\n]* · )?)(.*)$")
_UNTIL = re.compile(r" · until (\d{4}-\d{2}-\d{2})(?= · |$)")
_TRAILING = re.compile(r"(?: · (?:updated|confirmed|until|expired|superseded|moved) \d{4}-\d{2}-\d{2})+$")
_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")

SOURCE_MAX = 60


@dataclass(frozen=True)
class Entry:
    start: int  # first line (0-based)
    end: int  # one past the last line
    raw: str  # the first line
    prefix: str  # "- 2026-10-23 · from … · "
    body: str  # the fact, without marks
    day: str
    until: str | None
    struck: bool


def is_day(value) -> bool:
    if not isinstance(value, str) or not _DAY.match(value):
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def today(now: datetime | None = None) -> str:
    return (now or datetime.now()).strftime("%Y-%m-%d")


def _opens_fence(line: str) -> bool:
    return line.count("```") % 2 == 1


def parse(text: str) -> list[Entry]:
    """The dated entries, in order. Undated lines are not entries."""
    lines = text.split("\n")
    out: list[Entry] = []
    i = 0
    while i < len(lines):
        match = _DATED.match(lines[i])
        if not match:
            i += 1
            continue
        open_fence = _opens_fence(lines[i])
        end = i + 1
        j = i + 1
        while j < len(lines) and not _DATED.match(lines[j]):
            if not open_fence and not re.match(r"^[ \t]", lines[j]) and not lines[j].startswith("```"):
                break
            if _opens_fence(lines[j]):
                open_fence = not open_fence
            if not open_fence:
                end = j + 1
            j += 1
        rest = match.group(3)
        struck = rest.startswith("~~")
        until = _UNTIL.search(rest)
        body = _TRAILING.sub("", rest).removeprefix("~~").removesuffix("~~").strip()
        out.append(Entry(i, end, lines[i], match.group(1), body, match.group(2), until.group(1) if until else None, struck))
        i = end
    return out


def is_expired(entry: Entry, on: str) -> bool:
    return not entry.struck and entry.until is not None and entry.until < on


def identity(text: str) -> str:
    """Two facts are the same when they differ only in a bullet, spacing and a
    final full stop. Deliberately narrow: -10 and 10 stay different facts."""
    text = _BULLET.sub("", text.replace("\r", ""))
    return " ".join(text.split()).rstrip(".").strip()


def line_identity(line: str) -> str:
    found = parse(line)
    if found:
        return "" if found[0].struck else identity(found[0].body)
    return identity(line.replace("~~", ""))


def identities(text: str) -> set[str]:
    """Every live fact in a file, by identity (for "already there")."""
    return {k for k in (line_identity(line) for line in text.split("\n")) if k}


def source_label(title: str | None, thread_id: int, *, kind: str = "chat") -> str:
    clean = " ".join((title or f"conversation {thread_id}").replace("·", " ").replace('"', "'").split())
    return f'{kind} "{clean[:SOURCE_MAX]}"'


def entry_line(text: str, *, source: str, day: str, until: str | None = None, noticed: bool = False) -> str:
    """One dated bullet. `source` is a `source_label`."""
    fact = " ".join(text.split())
    mark = f"{source} (noticed)" if noticed else source
    tail = f" · until {until}" if until else ""
    return f"- {day} · from {mark} · {fact}{tail}"
