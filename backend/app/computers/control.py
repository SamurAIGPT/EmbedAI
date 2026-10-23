"""Ephemeral, view-bound control leases; expired owners cannot drive again."""
import secrets
import time
from dataclasses import dataclass, field


@dataclass(repr=False)
class Hold:
    lease: str
    since: float
    last_beat: float
    viewers: set[str] = field(default_factory=set)
    joined: bool = False
    last_left: float | None = None


class ControlRegistry:
    def __init__(self, clock=time.time):
        self.clock = clock
        self._holds: dict[int, Hold] = {}

    def snapshot(self, computer_id: int) -> dict:
        h = self._holds.get(computer_id)
        return {"held": h is not None, "since": h.since if h else None, "viewers": len(h.viewers) if h else 0}

    def take(self, computer_id: int, lease: str | None = None):
        self.expire()
        h = self._holds.get(computer_id)
        if h:
            return self.snapshot(computer_id), bool(lease and secrets.compare_digest(h.lease, lease)), False, None
        now = self.clock()
        token = secrets.token_hex(32)
        self._holds[computer_id] = Hold(token, now, now)
        return self.snapshot(computer_id), True, True, token

    def owns(self, computer_id, lease):
        self.expire()
        h = self._holds.get(computer_id)
        return bool(h and isinstance(lease, str) and secrets.compare_digest(h.lease, lease))

    def heartbeat(self, computer_id, lease):
        if not self.owns(computer_id, lease): return False
        self._holds[computer_id].last_beat = self.clock()
        return True

    def release(self, computer_id, lease=None):
        h = self._holds.get(computer_id)
        if not h or (lease is not None and not secrets.compare_digest(h.lease, lease)):
            return False
        del self._holds[computer_id]
        return True

    def viewer_opened(self, computer_id, viewer):
        h = self._holds.get(computer_id)
        if h:
            h.viewers.add(viewer); h.joined = True; h.last_left = None

    def viewer_closed(self, computer_id, viewer):
        h = self._holds.get(computer_id)
        if h:
            h.viewers.discard(viewer)
            if not h.viewers: h.last_left = self.clock()

    def expire(self):
        now = self.clock()
        expired = [cid for cid, h in self._holds.items() if now-h.last_beat >= 30 or (not h.joined and now-h.since >= 45) or (h.last_left is not None and now-h.last_left >= 20)]
        for cid in expired: del self._holds[cid]
        return expired
