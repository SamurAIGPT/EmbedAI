"""Failed-attempt lockout for the sign-in and pairing endpoints.

In memory and per process: a restart forgets it, and several worker processes
each keep their own count (docs/AUTH.md, "Limits"). That is a slow-down for
online guessing, not the only defence: pairing codes also die after a few wrong
guesses in total, and the passphrase hash is slow.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field

MAX_TRACKED_SOURCES = 10_000


@dataclass
class _Entry:
    failures: list[float] = field(default_factory=list)
    locked_until: float = 0.0


class Lockout:
    def __init__(
        self,
        *,
        max_failures: int,
        window_seconds: float,
        lock_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self.lock_seconds = lock_seconds
        self.clock = clock
        self._entries: dict[str, _Entry] = {}

    def retry_after(self, source: str) -> int:
        """Seconds until `source` may try again; 0 when it may now."""
        entry = self._entries.get(source)
        if entry is None:
            return 0
        remaining = entry.locked_until - self.clock()
        return int(remaining) + 1 if remaining > 0 else 0

    def failure(self, source: str) -> None:
        now = self.clock()
        if len(self._entries) >= MAX_TRACKED_SOURCES and source not in self._entries:
            self._prune(now)
        entry = self._entries.setdefault(source, _Entry())
        entry.failures = [t for t in entry.failures if now - t < self.window_seconds]
        entry.failures.append(now)
        if len(entry.failures) >= self.max_failures:
            entry.locked_until = now + self.lock_seconds
            entry.failures.clear()

    def success(self, source: str) -> None:
        self._entries.pop(source, None)

    def reset(self) -> None:
        self._entries.clear()

    def _prune(self, now: float) -> None:
        for key in [k for k, e in self._entries.items()
                    if e.locked_until <= now and all(now - t >= self.window_seconds for t in e.failures)]:
            del self._entries[key]
        if len(self._entries) >= MAX_TRACKED_SOURCES:
            # Still full of live entries: forget the oldest rather than grow.
            for key in list(self._entries)[: len(self._entries) // 2]:
                del self._entries[key]


class RateLimit:
    """At most `limit` events per `window_seconds` per source, successes included."""

    def __init__(self, *, limit: int, window_seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self.clock = clock
        self._events: dict[str, list[float]] = {}

    def allow(self, source: str) -> bool:
        now = self.clock()
        if len(self._events) >= MAX_TRACKED_SOURCES and source not in self._events:
            self._events.clear()
        events = [t for t in self._events.get(source, []) if now - t < self.window_seconds]
        if len(events) >= self.limit:
            self._events[source] = events
            return False
        events.append(now)
        self._events[source] = events
        return True

    def reset(self) -> None:
        self._events.clear()


# Five wrong passphrases from one address lock it for fifteen minutes.
login_lockout = Lockout(max_failures=5, window_seconds=15 * 60, lock_seconds=15 * 60)
# Pairing codes are random, so this only has to slow a guesser down.
pair_lockout = Lockout(max_failures=10, window_seconds=10 * 60, lock_seconds=10 * 60)
# Any request to an auth endpoint that does work (scrypt, a code lookup).
auth_requests = RateLimit(limit=30, window_seconds=60)


def reset_all() -> None:
    login_lockout.reset()
    pair_lockout.reset()
    auth_requests.reset()
