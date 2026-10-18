"""A turn's time limit that stops while a person decides: waiting for an
approval is not the engine running long (the wait has its own limit)."""

import time


class TurnClock:
    def __init__(self, seconds: float) -> None:
        self._left = float(seconds)
        self._since: float | None = time.monotonic()
        self._pauses = 0

    def pause(self) -> None:
        self._pauses += 1
        if self._pauses == 1 and self._since is not None:
            self._left -= time.monotonic() - self._since
            self._since = None

    def resume(self) -> None:
        if self._pauses == 0:
            return
        self._pauses -= 1
        if self._pauses == 0:
            self._since = time.monotonic()

    def remaining(self) -> float | None:
        """Seconds left, or None while paused (no limit runs)."""
        if self._since is None:
            return None
        return self._left - (time.monotonic() - self._since)

    @property
    def expired(self) -> bool:
        left = self.remaining()
        return left is not None and left <= 0
