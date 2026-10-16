"""An append-only record of every decision about a tool call.

One NDJSON file per month (`decisions-YYYY-MM.ndjson`), mode 0600 in a 0700
folder. It records what was asked and how it was settled, never the full input
of a call: the summary is cut short and has anything that looks like a secret
removed. A write that fails is logged and ignored; an approval never fails
because its record could not be written.

Events: shown, allowed, denied, auto_allowed, expired, cancelled.
`source` says who or what decided (user, saved_rule, timeout, provider) and
`rule` which rule did (a saved command, or the safety-floor rule that made a
call ask instead of being auto-allowed). Months older than the retention window
(`DECISION_LOG_RETENTION_DAYS`, default 180) are deleted, at most once an hour.
"""

import json
import os
import re
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import settings
from app.core.logging import get_logger
from app.desktop import profile

logger = get_logger(__name__)

EVENTS = ("shown", "allowed", "denied", "auto_allowed", "expired", "cancelled")
FILE_PREFIX = "decisions-"
FILE_SUFFIX = ".ndjson"
MAX_SUMMARY_CHARS = 300
MAX_PAGE = 200

# Order matters: whole tokens first, then `NAME=value` assignments.
_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)", re.S),
    re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/\-]{12,}=*"),
    re.compile(
        r"(?i)([A-Za-z0-9_.\-]*(?:key|token|secret|passw(?:or)?d|credential|auth)[A-Za-z0-9_.\-]*"
        r"\s*[=:]\s*)(?:\"[^\"]*\"|'[^']*'|\S+)"
    ),
    re.compile(r"\b[A-Za-z0-9+/_\-]{40,}={0,2}"),
)
_REDACTED = "[redacted]"

_lock = threading.Lock()
_counter = 0


def redact(text: str) -> str:
    for pattern in _SECRET_PATTERNS:
        if pattern.groups:
            text = pattern.sub(lambda m: m.group(1) + _REDACTED, text)
        else:
            text = pattern.sub(_REDACTED, text)
    return text


def log_dir() -> Path:
    desktop = profile.current()
    if desktop is not None:
        return desktop.data_dir / "decisions"
    if settings.DECISION_LOG_DIR.strip():
        return Path(settings.DECISION_LOG_DIR).expanduser()
    return Path.home() / ".open-dots" / "decisions"


def _next_id() -> str:
    """Sortable and unique within this process: nanoseconds, then a counter."""
    global _counter
    _counter = (_counter + 1) % 10_000
    return f"{time.time_ns():020d}-{_counter:04d}"


def _owner_only_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


def record(
    event: str,
    *,
    thread_id: int | None = None,
    turn_id: int | None = None,
    approval_id: str | None = None,
    tool: str | None = None,
    kind: str | None = None,
    summary: str | None = None,
    source: str | None = None,
    rule: str | None = None,
    remembered: bool = False,
) -> None:
    """Append one decision. Never raises."""
    if event not in EVENTS:
        raise ValueError(f"unknown decision event: {event}")
    try:
        now = datetime.now(UTC)
        entry = {
            "id": _next_id(),
            "at": now.isoformat(),
            "event": event,
            "thread_id": thread_id,
            "turn_id": turn_id,
            "approval_id": approval_id,
            "tool": tool,
            "kind": kind,
            "summary": redact(summary)[:MAX_SUMMARY_CHARS] if summary else None,
            "source": source,
            "rule": redact(rule)[:MAX_SUMMARY_CHARS] if rule else None,
            "remembered": remembered or None,
        }
        line = (json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8")
        directory = log_dir()
        with _lock:
            _owner_only_dir(directory)
            path = directory / f"{FILE_PREFIX}{now:%Y-%m}{FILE_SUFFIX}"
            fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                os.write(fd, line)
            finally:
                os.close(fd)
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
            _prune_if_due(directory, now)
    except Exception:  # noqa: BLE001  (a missing record must never break an approval)
        logger.warning("could not write a decision record", decision_event=event)


_last_prune = 0.0


def retention_days() -> int:
    days = settings.DECISION_LOG_RETENTION_DAYS
    return days if 1 <= days <= 3650 else 180


def prune(directory: Path, days: int, now: datetime | None = None) -> list[str]:
    """Delete month files that end before the window starts. Never raises."""
    now = now or datetime.now(UTC)
    removed: list[str] = []
    try:
        names = [p.name for p in directory.iterdir()]
    except OSError:
        return removed
    for name in names:
        match = re.fullmatch(rf"{FILE_PREFIX}(\d{{4}})-(\d{{2}}){re.escape(FILE_SUFFIX)}", name)
        if not match:
            continue
        year, month = int(match.group(1)), int(match.group(2))
        end = datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=UTC)  # first moment after the month
        if (now - end).days >= days:
            try:
                (directory / name).unlink()
                removed.append(name)
            except OSError:
                pass
    return removed


def _prune_if_due(directory: Path, now: datetime) -> None:
    global _last_prune
    if time.monotonic() - _last_prune >= 3600:
        _last_prune = time.monotonic()
        prune(directory, retention_days(), now)


def read_page(
    *,
    limit: int = 50,
    before: str | None = None,
    event: str | None = None,
    thread_id: int | None = None,
) -> tuple[list[dict], str | None]:
    """Newest first. `before` is the id of the last entry of the previous page;
    the returned cursor is None on the last page. Entries are never altered, so
    a cursor stays valid while new ones are appended. Unreadable lines are
    skipped."""
    limit = max(1, min(limit, MAX_PAGE))
    directory = log_dir()
    if not directory.is_dir():
        return [], None
    files = sorted(
        (p for p in directory.iterdir() if p.name.startswith(FILE_PREFIX) and p.name.endswith(FILE_SUFFIX)),
        reverse=True,
    )
    items: list[dict] = []
    more = False
    for path in files:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for raw in reversed(lines):
            try:
                entry = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
                continue
            if before is not None and entry["id"] >= before:
                continue
            if event is not None and entry.get("event") != event:
                continue
            if thread_id is not None and entry.get("thread_id") != thread_id:
                continue
            if len(items) == limit:
                more = True
                break
            items.append(entry)
        if more:
            break
    return items, (items[-1]["id"] if more and items else None)
