"""Engine failures that mean "this account or endpoint is limited right now"
(docs/ENGINES.md): a subscription's usage limit, a rate limit, or no credits.

Adapters report what their engine said (`TurnOutcome.error`, and a reset time
when the engine gave one in a structured form, `limit_resets_at`). The runtime
reads it here, once, for every engine: a limit becomes `error_code`
`engine_limited` with a plain message, the engine is marked limited until its
reset (shown by GET /engines and the pickers), and an agent with a fallback
engine may move a turn the engine never accepted there (thread_runtime.py).

Only failures the engine itself reported are read (`ENGINE_REPORTED`), so a
timeout or a crash whose text happens to mention a limit is never taken for
one.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

ENGINE_LIMITED = "engine_limited"

# error_code values that carry the engine's own words.
ENGINE_REPORTED = frozenset({
    "claude_error", "cli_error", "codex_error", "acp_error", "endpoint_error", "endpoint_refused",
})

USAGE_LIMIT = "usage_limit"
RATE_LIMIT = "rate_limit"
CREDITS = "credits"

# A subscription's usage running out: Claude Code says "Claude AI usage limit
# reached|<epoch>", "You've hit your weekly limit · resets 3pm", "...your
# session limit"; Codex says "You've hit your usage limit".
_USAGE = re.compile(
    r"\busage limit\b|\bhit your (?:(?!rate\b)[\w'’-]+ ){0,3}(?:limit|budget)\b"
    r"|\b(?:weekly|daily|monthly|subscription|usage|session|5-hour) limit reached\b"
    r"|\bout of (?:usage(?: credits)?|extra usage)\b",
    re.IGNORECASE,
)
_CREDITS = re.compile(
    r"\b402\b|\bpayment required\b|\binsufficient[_ ](?:quota|funds|balance|credits?)\b"
    r"|\b(?:credit|account) balance is too low\b|\bout of credits?\b|\bexceeded your current quota\b",
    re.IGNORECASE,
)
_RATE = re.compile(r"\b429\b|\brate[ _-]?limit(?:ed)?\b|\btoo many requests\b", re.IGNORECASE)
# "Claude AI usage limit reached|1760000000"
_EPOCH = re.compile(r"\|(\d{10})\b")
# "try again in 20s", "Try again in 3 days 2 hours", "retry after 90 seconds"
_IN = re.compile(r"\b(?:try again|retry|resets?)\s+(?:in|after)\s+((?:\d+(?:\.\d+)?\s*[a-z]+[\s,]*(?:and\s+)?)+)", re.IGNORECASE)
_UNIT = re.compile(r"(\d+(?:\.\d+)?)\s*(d|day|days|h|hr|hrs|hour|hours|m|min|mins|minute|minutes|s|sec|secs|second|seconds|ms)\b", re.IGNORECASE)
_SECONDS = {"d": 86400, "h": 3600, "m": 60, "s": 1}


@dataclass(frozen=True)
class EngineLimit:
    kind: str  # usage_limit | rate_limit | credits
    resets_at: datetime | None
    said: str

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "resets_at": self.resets_at.isoformat() if self.resets_at else None,
        }


def _duration(text: str) -> timedelta | None:
    total = 0.0
    for amount, unit in _UNIT.findall(text):
        unit = unit.lower()
        if unit == "ms":
            total += float(amount) / 1000
        else:
            total += float(amount) * _SECONDS[unit[0]]
    return timedelta(seconds=total) if total > 0 else None


def reset_time(text: str, now: datetime | None = None) -> datetime | None:
    """A reset time the engine's words give, or None."""
    now = now or datetime.now(UTC)
    epoch = _EPOCH.search(text)
    if epoch:
        when = datetime.fromtimestamp(int(epoch.group(1)), UTC)
        return when if when > now else None
    match = _IN.search(text)
    if match:
        span = _duration(match.group(1))
        if span is not None:
            return now + span
    return None


def classify(text: str | None, *, resets_at: float | None = None, now: datetime | None = None) -> EngineLimit | None:
    """The limit `text` (an engine's own error) describes, or None."""
    if not text:
        return None
    if _CREDITS.search(text):
        kind = CREDITS
    elif _USAGE.search(text):
        kind = USAGE_LIMIT
    elif _RATE.search(text):
        kind = RATE_LIMIT
    else:
        return None
    now = now or datetime.now(UTC)
    when = None
    if resets_at:
        when = datetime.fromtimestamp(resets_at, UTC)
        if when <= now:
            when = None
    if when is None:
        when = reset_time(text, now)
    said = _EPOCH.sub("", " ".join(text.split()))[:300]
    return EngineLimit(kind=kind, resets_at=when, said=said)


def describe(label: str, limit: EngineLimit) -> str:
    """The limit in plain words, with when it resets when known."""
    when = f" It resets at {limit.resets_at.strftime('%Y-%m-%d %H:%M UTC')}." if limit.resets_at else ""
    said = f' ({label} says: "{limit.said.rstrip(". ")}")' if limit.said else ""
    if limit.kind == CREDITS:
        return f"{label} is out of credits{said}. Add credits, or switch this agent to another engine."
    if limit.kind == USAGE_LIMIT:
        return f"{label} has reached its usage limit{said}.{when} Until then, switch this agent to another engine or give it a fallback engine."
    return f"{label} hit a rate limit{said}.{when or ' Wait a minute, then send it again.'}"
