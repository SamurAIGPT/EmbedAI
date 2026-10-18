"""Token and cost totals from what engines reported (docs/EVERYDAY.md).

The turn table is the ledger: each finished turn keeps the `usage` its engine
reported (input/output/cache tokens, cost_usd) or nothing. Totals never
estimate: a cost is the sum of reported costs, or None when no turn in the
group reported one.
"""

import csv
import io
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Thread, Turn

TOKEN_KEYS = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")


def _int(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _cost(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if value >= 0 and value == value and value != float("inf") else None


def reported(usage: dict | None) -> dict:
    """Only the well-formed numbers of a stored usage dict."""
    usage = usage if isinstance(usage, dict) else {}
    out = {key: _int(usage.get(key)) for key in TOKEN_KEYS}
    out["cost_usd"] = _cost(usage.get("cost_usd"))
    return out


@dataclass
class Totals:
    key: str
    label: str
    turns: int = 0
    reported_turns: int = 0
    unreported_turns: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cost_usd: float | None = None
    extra: dict = field(default_factory=dict)

    def add(self, numbers: dict) -> None:
        self.turns += 1
        if all(numbers[key] is None for key in TOKEN_KEYS) and numbers["cost_usd"] is None:
            self.unreported_turns += 1
            return
        self.reported_turns += 1
        for key in TOKEN_KEYS:
            setattr(self, key, getattr(self, key) + (numbers[key] or 0))
        if numbers["cost_usd"] is not None:
            self.cost_usd = (self.cost_usd or 0.0) + numbers["cost_usd"]

    def as_dict(self) -> dict:
        data = {k: v for k, v in vars(self).items() if k != "extra"}
        if self.cost_usd is not None:
            data["cost_usd"] = round(self.cost_usd, 6)
        return {**data, **self.extra}


def month_range(month: str | None, now: datetime | None = None) -> tuple[str, datetime, datetime]:
    now = now or datetime.now(UTC)
    if month:
        try:
            first = date.fromisoformat(f"{month}-01")
        except ValueError:
            raise ValueError("month must look like 2026-10") from None
    else:
        first = date(now.year, now.month, 1)
    start = datetime(first.year, first.month, 1, tzinfo=UTC)
    end = datetime(first.year + (first.month == 12), first.month % 12 + 1, 1, tzinfo=UTC)
    return f"{first.year:04d}-{first.month:02d}", start, end


async def _rows(db: AsyncSession, owner_id: str, start: datetime, end: datetime):
    query = (
        select(Turn, Thread)
        .join(Thread, Thread.id == Turn.thread_id)
        .where(
            Thread.owner_id == owner_id,
            Turn.status != "running",
            Turn.started_at >= start,
            Turn.started_at < end,
        )
        .order_by(Turn.started_at, Turn.id)
    )
    return (await db.execute(query)).all()


async def month_summary(db: AsyncSession, owner_id: str, month: str | None) -> dict:
    label, start, end = month_range(month)
    total = Totals("total", "Total")
    threads: dict[int, Totals] = {}
    engines: dict[str, Totals] = {}
    days: dict[str, Totals] = {}
    for turn, thread in await _rows(db, owner_id, start, end):
        numbers = reported(turn.usage)
        total.add(numbers)
        if thread.id not in threads:
            threads[thread.id] = Totals(
                f"thread:{thread.id}",
                thread.title or f"Conversation {thread.id}",
                extra={"thread_id": thread.id, "agent_id": thread.agent_id},
            )
        threads[thread.id].add(numbers)
        model = turn.model or thread.model or "default"
        key = f"{thread.provider}/{model}"
        engines.setdefault(key, Totals(key, key, extra={"provider": thread.provider, "model": model})).add(numbers)
        day = turn.started_at.astimezone(UTC).date().isoformat()
        days.setdefault(day, Totals(day, day)).add(numbers)

    def ordered(groups: dict) -> list[dict]:
        return [
            g.as_dict()
            for g in sorted(
                groups.values(),
                key=lambda g: (-(g.cost_usd or 0), -(g.input_tokens + g.output_tokens), g.label),
            )
        ]

    return {
        "month": label,
        "total": total.as_dict(),
        "by_thread": ordered(threads),
        "by_engine": ordered(engines),
        "by_day": [days[k].as_dict() for k in sorted(days)],
    }


def _cell(value) -> str:
    if value is None:
        return ""
    text = str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


async def month_csv(db: AsyncSession, owner_id: str, month: str | None) -> tuple[str, str]:
    label, start, end = month_range(month)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(
        ["started_at", "thread_id", "conversation", "engine", "model", "status", *TOKEN_KEYS, "cost_usd"]
    )
    for turn, thread in await _rows(db, owner_id, start, end):
        numbers = reported(turn.usage)
        writer.writerow(
            [
                _cell(value)
                for value in (
                    turn.started_at.astimezone(UTC).isoformat(),
                    thread.id,
                    thread.title or "",
                    thread.provider,
                    turn.model or thread.model or "",
                    turn.status,
                    *(numbers[key] for key in TOKEN_KEYS),
                    numbers["cost_usd"],
                )
            ]
        )
    return label, buffer.getvalue()
