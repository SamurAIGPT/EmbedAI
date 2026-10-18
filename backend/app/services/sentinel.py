"""Sentinel: a read-only health scan of stored conversations.

It reads thread events (failed tool calls, failed turns) and the decision log
(denials) from a recent window, groups what went wrong, and reports it. It is
plain code with no model calls, and it changes nothing: it only runs SELECTs
and reads the log files.

    tool_error       a failed tool call, grouped by tool and normalized error
    retry_loop       the same tool failing the same way 3+ times in one turn
    repeated_denial  the same call denied 2+ times in one conversation, so
                     Claude kept asking for what the person refused
    turn_failure     turns that failed, grouped by error code (stops the
                     person chose and app shutdowns are left out)

Privacy: an issue never holds raw tool output. Its `title` is the error with
quoted text, URLs, email addresses, paths, ids and numbers replaced, cut at
160 characters; a failed shell command is grouped only by exit code and error
class, because its output names the files of the work in hand. `evidence`
points back at the event or decision, for someone allowed to read it.

    uv run python -m app.services.sentinel [--hours 24] [--json]
"""

import argparse
import asyncio
import hashlib
import json
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Event, Thread
from app.services import decision_log

MAX_TITLE = 160
MAX_TEXT = 240
DEFAULT_HOURS = 24
MAX_HOURS = 24 * 90
RETRY_LOOP_MIN = 3
REPEATED_DENIAL_MIN = 2
SHELL_TOOLS = frozenset({"bash", "shell", "exec", "run_command", "terminal"})
# Turn endings that are not a fault: the person stopped it, or the app closed.
_NOT_A_FAULT = frozenset({"cancelled", "stopped", "shutdown"})

_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def normalize(text: str) -> str:
    """The error with anything specific to this work replaced, so two
    occurrences of one fault read the same."""
    text = re.sub(r"\"[^\"\n]*\"|(?<![\w])'[^'\n]*'(?![\w])|`[^`\n]*`", "<q>", text)
    text = re.sub(r"\b[a-z][a-z0-9+.-]*://\S+", "<url>", text, flags=re.I)
    text = re.sub(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b", "<email>", text)
    text = re.sub(r"\b[A-Za-z]:\\[^\s:]+", "<path>", text)
    text = re.sub(r"(?:~|\.{1,2})?(?:/[^\s/:'\",)]+)+/?", "<path>", text)
    text = re.sub(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", "<id>", text, flags=re.I)
    text = re.sub(r"\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{8,}\b", "<id>", text, flags=re.I)
    text = re.sub(r"\b\d+(?:[.,:]\d+)*\b", "<n>", text)
    return re.sub(r"\s+", " ", text).strip()[:MAX_TEXT]


_SHELL_CLASSES = (
    (re.compile(r"\btimed?[ -]?out\b|\btimeout\b", re.I), "timed out"),
    (re.compile(r"command not found|not recognized as an internal or external command", re.I), "command not found"),
    (re.compile(r"syntax error|unexpected token|unexpected EOF|parse error", re.I), "syntax error"),
    (re.compile(r"no such file or directory|\bENOENT\b", re.I), "no such file"),
    (re.compile(r"permission denied|\bEACCES\b", re.I), "permission denied"),
    (re.compile(r"operation not permitted|\bEPERM\b", re.I), "operation not permitted"),
    (re.compile(r"connection refused|could not resolve host|network is unreachable|\bECONNREFUSED\b", re.I), "network error"),
    (re.compile(r"out of memory|cannot allocate memory|\bENOMEM\b", re.I), "out of memory"),
    (re.compile(r"\bkilled\b|\bSIGKILL\b|\bSIGTERM\b", re.I), "killed"),
    (re.compile(r"user (?:rejected|denied|doesn't want)|was denied|not allowed", re.I), "denied"),
)
_EXIT_CODE = re.compile(r"\bexit(?:ed with)?(?: code| status)?\s*[:=]?\s*(-?\d+)", re.I)


def failure_key(tool: str, text: str) -> tuple[str, str]:
    """(what groups it, the title a person reads)."""
    if tool.lower() in SHELL_TOOLS:
        error_class = next((name for pattern, name in _SHELL_CLASSES if pattern.search(text)), None)
        code_match = None if error_class == "timed out" else _EXIT_CODE.search(text)
        code = code_match.group(1) if code_match else None
        parts = [f"exit {code}" if code is not None else None, error_class or (None if code else "failed")]
        return f"{code or ''}\n{error_class or ''}", ": ".join(p for p in parts if p)[:MAX_TITLE]
    normalized = normalize(text)
    return normalized, (normalized[:MAX_TITLE] or "failed")


def signature(kind: str, *parts: str | None) -> str:
    digest = hashlib.sha256("\n".join(p or "" for p in parts).encode("utf-8")).hexdigest()
    return f"{kind}:{digest[:16]}"


# --- records: what a scan reads, held in memory only ---------------------------------


@dataclass(frozen=True)
class ToolFailure:
    event_id: int
    thread_id: int
    turn_id: int | None
    tool: str
    text: str  # signatures only; never reported
    at: datetime


@dataclass(frozen=True)
class TurnFailure:
    event_id: int
    thread_id: int
    turn_id: int | None
    error_code: str
    at: datetime


@dataclass(frozen=True)
class Denial:
    decision_id: str
    thread_id: int | None
    tool: str
    summary: str
    at: datetime


@dataclass
class Issue:
    sig: str
    kind: str
    title: str
    severity: str
    count: int
    first_at: datetime
    last_at: datetime
    evidence: dict
    tool: str | None = None
    thread_id: int | None = None
    turn_id: int | None = None
    _seen: set = field(default_factory=set, repr=False)

    def public(self) -> dict:
        return {
            "sig": self.sig, "kind": self.kind, "title": self.title, "severity": self.severity,
            "tool": self.tool, "thread_id": self.thread_id, "turn_id": self.turn_id,
            "count": self.count, "first_at": self.first_at, "last_at": self.last_at,
            "evidence": self.evidence,
        }


def _add(issues: dict[str, Issue], sig: str, at: datetime, evidence: dict, **fields) -> None:
    issue = issues.get(sig)
    if issue is None:
        issues[sig] = Issue(sig=sig, count=1, first_at=at, last_at=at, evidence=evidence, **fields)
        return
    issue.count += 1
    issue.first_at = min(issue.first_at, at)
    if at >= issue.last_at:
        issue.last_at, issue.evidence = at, evidence


# --- detectors: pure functions from records to issues ------------------------------


def tool_errors(failures: Iterable[ToolFailure]) -> list[Issue]:
    issues: dict[str, Issue] = {}
    for f in failures:
        key, title = failure_key(f.tool, f.text)
        _add(
            issues, signature("tool_error", f.tool, key), f.at, {"source": "event", "id": f.event_id},
            kind="tool_error", title=title, severity="low", tool=f.tool,
        )
    for issue in issues.values():
        if issue.count >= 5:
            issue.severity = "medium"
    return list(issues.values())


def retry_loops(failures: Iterable[ToolFailure], minimum: int = RETRY_LOOP_MIN) -> list[Issue]:
    groups: dict[tuple, list[ToolFailure]] = {}
    for f in failures:
        if f.turn_id is None:
            continue
        key, title = failure_key(f.tool, f.text)
        groups.setdefault((f.turn_id, f.tool, key, title), []).append(f)
    issues = []
    for (turn_id, tool, key, title), calls in groups.items():
        if len(calls) < minimum:
            continue
        last = max(calls, key=lambda c: c.at)
        issues.append(Issue(
            sig=signature("retry_loop", tool, key), kind="retry_loop",
            title=f"repeated in one turn: {title}"[:MAX_TITLE], severity="high", tool=tool,
            thread_id=last.thread_id, turn_id=turn_id, count=len(calls),
            first_at=min(c.at for c in calls), last_at=last.at,
            evidence={"source": "event", "id": last.event_id},
        ))
    return issues


def repeated_denials(denials: Iterable[Denial], minimum: int = REPEATED_DENIAL_MIN) -> list[Issue]:
    groups: dict[tuple, list[Denial]] = {}
    for d in denials:
        if d.thread_id is None:
            continue
        groups.setdefault((d.thread_id, d.tool, normalize(d.summary)), []).append(d)
    issues = []
    for (thread_id, tool, summary), rows in groups.items():
        if len(rows) < minimum:
            continue
        last = max(rows, key=lambda r: r.at)
        issues.append(Issue(
            sig=signature("repeated_denial", tool, summary), kind="repeated_denial",
            title=f"denied {len(rows)} times: {tool} {summary}".strip()[:MAX_TITLE], severity="medium",
            tool=tool, thread_id=thread_id, count=len(rows),
            first_at=min(r.at for r in rows), last_at=last.at,
            evidence={"source": "decision", "id": last.decision_id},
        ))
    return issues


def turn_failures(failures: Iterable[TurnFailure]) -> list[Issue]:
    issues: dict[str, Issue] = {}
    for f in failures:
        if f.error_code in _NOT_A_FAULT:
            continue
        code = normalize(f.error_code) or "unknown"
        _add(
            issues, signature("turn_failure", code), f.at, {"source": "event", "id": f.event_id},
            kind="turn_failure", title=f"turn failed: {code}"[:MAX_TITLE], severity="medium",
        )
    return list(issues.values())


def scan_records(
    tool_failures: list[ToolFailure], failed_turns: list[TurnFailure], denials: list[Denial]
) -> list[dict]:
    """Every detector over the records, worst first."""
    found = [
        *retry_loops(tool_failures),
        *repeated_denials(denials),
        *turn_failures(failed_turns),
        *tool_errors(tool_failures),
    ]
    found.sort(key=lambda i: (_SEVERITY_ORDER[i.severity], -i.count, -i.last_at.timestamp()))
    return [i.public() for i in found]


# --- sources --------------------------------------------------------------------------


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


async def read_events(
    db: AsyncSession, owner_id: str, since: datetime
) -> tuple[list[ToolFailure], list[TurnFailure], int]:
    """Failed tool calls and failed turns in the owner's threads since `since`."""
    owned = select(Thread.id).where(Thread.owner_id == owner_id)
    rows = (
        await db.execute(
            select(Event)
            .where(
                Event.thread_id.in_(owned),
                Event.created_at >= since,
                Event.type.in_(("tool.completed", "turn.failed")),
            )
            .order_by(Event.id)
        )
    ).scalars().all()
    failed_calls = [e for e in rows if e.type == "tool.completed" and (e.data or {}).get("ok") is False]
    # A completion names only its call; the call's start names the tool, and
    # may be older than the window.
    names: dict[tuple[int | None, str], str] = {}
    turns = {e.turn_id for e in failed_calls if e.turn_id is not None}
    if turns:
        started = (
            await db.execute(select(Event).where(Event.turn_id.in_(turns), Event.type == "tool.started"))
        ).scalars().all()
        for e in started:
            data = e.data or {}
            if isinstance(data.get("call_id"), str) and isinstance(data.get("name"), str):
                names[(e.turn_id, data["call_id"])] = data["name"]
    tools = [
        ToolFailure(
            event_id=e.id, thread_id=e.thread_id, turn_id=e.turn_id,
            tool=names.get((e.turn_id, str((e.data or {}).get("call_id"))), "unknown"),
            text=str((e.data or {}).get("output") or ""), at=_aware(e.created_at),
        )
        for e in failed_calls
    ]
    turns_failed = [
        TurnFailure(
            event_id=e.id, thread_id=e.thread_id, turn_id=e.turn_id,
            error_code=str((e.data or {}).get("error_code") or "unknown"), at=_aware(e.created_at),
        )
        for e in rows
        if e.type == "turn.failed"
    ]
    return tools, turns_failed, len(rows)


def read_denials(since: datetime, thread_ids: set[int] | None = None) -> list[Denial]:
    """Denials in the decision log since `since`, newest first. With
    `thread_ids`, only those conversations."""
    found = []
    for entry in decision_log.iter_entries(event="denied"):
        try:
            at = _aware(datetime.fromisoformat(entry["at"]))
        except (KeyError, TypeError, ValueError):
            continue
        if at < since:
            break  # the log is newest first
        thread_id = entry.get("thread_id")
        if thread_ids is not None and thread_id not in thread_ids:
            continue
        found.append(Denial(
            decision_id=entry["id"], thread_id=thread_id if isinstance(thread_id, int) else None,
            tool=str(entry.get("tool") or "unknown"), summary=str(entry.get("summary") or ""), at=at,
        ))
    return found


async def scan(session_factory, owner_id: str, hours: int = DEFAULT_HOURS, now: datetime | None = None) -> dict:
    """One scan of the last `hours`. Reads only."""
    hours = max(1, min(hours, MAX_HOURS))
    since = (now or datetime.now(UTC)) - timedelta(hours=hours)
    async with session_factory() as db:
        tools, turns, scanned = await read_events(db, owner_id, since)
        owned = set((await db.execute(select(Thread.id).where(Thread.owner_id == owner_id))).scalars())
    denials = read_denials(since, owned)
    return {
        "since": since,
        "scanned_events": scanned,
        "scanned_decisions": len(denials),
        "issues": scan_records(tools, turns, denials),
    }


def summary_text(report: dict) -> str:
    issues = report["issues"]
    if not issues:
        return f"No issues since {report['since']:%Y-%m-%d %H:%M} UTC."
    lines = [f"{len(issues)} issue(s) since {report['since']:%Y-%m-%d %H:%M} UTC:"]
    for issue in issues:
        where = f" thread {issue['thread_id']}" if issue["thread_id"] is not None else ""
        lines.append(f"  [{issue['severity']}] {issue['kind']} x{issue['count']}{where}: {issue['title']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sentinel", description="Read-only health scan of stored conversations.")
    parser.add_argument("--hours", type=int, default=DEFAULT_HOURS, help="how far back to look (default 24)")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)

    from app.db.session import AsyncSessionLocal
    from app.services.thread_runtime import LOCAL_OWNER_ID

    report = asyncio.run(scan(AsyncSessionLocal, LOCAL_OWNER_ID, args.hours))
    if args.json:
        print(json.dumps(report, default=str, indent=2))
    else:
        print(summary_text(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
