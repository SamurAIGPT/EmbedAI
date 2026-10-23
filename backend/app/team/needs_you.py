"""The "Needs you" card: one server-written card a day in the lead's chat that
lists what only the person can fix.

Everything is computed from local data on demand: approvals waiting on the
person, routines whose latest run failed or was missed, engines that are signed
out or limited, and faults that keep repeating (Sentinel). An item leaves when
something later proves it fixed (the approval is decided, the routine ran
again, the engine is signed in or reset), never because time passed.

The card is an ordinary event (`card.needs_you`) in the lead's conversation.
Events are append-only, so "edited in place" means a newer event with the same
`card_id`; the client shows only the latest one. It is written only when its
content changed, only when something needs the person (never an empty card on a
fresh day), and never while the conversation has a turn running.
"""
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select

from app.db.models import Approval, NeedsYouState, Routine, RoutineRun, Team, Thread, Turn
from app.db.transactions import begin_write
from app.services import notification_feed, sentinel
from app.services import thread_store as store

logger = logging.getLogger(__name__)

CARD_EVENT = "card.needs_you"
CARD_ROWS = 6
WINDOW = timedelta(days=7)
AWAY = timedelta(hours=3)
NOTIFIED_KEPT = 200
REPEATED_FAILURES_SHOWN = 3
EMPTY_TEXT = "Nothing needs you right now."

# Most blocking first: nothing runs while an engine is signed out or out of credit.
KIND_ORDER = {
    "signed_out": 0,
    "out_of_credit": 1,
    "usage_limit": 2,
    "approval": 3,
    "routine": 4,
    "repeated_failure": 5,
}
_BAD_RUN_STATES = frozenset({"failed", "missed"})
_SETTLED_RUN_STATES = frozenset({"completed", "failed", "missed", "interrupted"})


@dataclass(frozen=True)
class Item:
    kind: str
    key: str
    title: str
    detail: str | None
    thread_id: int | None
    at: datetime

    def public(self) -> dict:
        return {"kind": self.kind, "key": self.key, "title": self.title,
                "detail": self.detail, "thread_id": self.thread_id}


def utcnow() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def zone_of(name: str | None) -> ZoneInfo:
    """The zone named, or UTC when it is missing or unknown."""
    if name:
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError, OSError):
            pass
    return ZoneInfo("UTC")


def valid_zone(name: object) -> str:
    """An IANA zone name the host knows, or ValueError."""
    if not isinstance(name, str) or not name or len(name) > 64 or name.startswith(("/", ".")):
        raise ValueError("Send a time zone name such as Europe/Paris.")
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        raise ValueError("That time zone is not known on this computer.") from None
    return name


def local_day(now: datetime, zone: ZoneInfo) -> str:
    return _aware(now).astimezone(zone).date().isoformat()


def _clip(text: str | None, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


# --- what needs the person ---------------------------------------------------------


async def collect(db, owner_id: str, *, now: datetime, engines: list[dict],
                  issues: list[dict]) -> list[Item]:
    """Every item, most blocking first. `engines` are the engine entries
    (`GET /engines`); `issues` are Sentinel's public issues."""
    since = now - WINDOW
    items: list[Item] = []

    for engine in engines:
        label = engine.get("label") or engine["id"]
        limit = engine.get("limit")
        if engine.get("installed") and engine.get("signed_in") is False:
            items.append(Item("signed_out", f"signed_out:{engine['id']}", f"Sign in to {label}",
                              "Nothing can run on this engine until you do.", None, now))
        if limit:
            credits = limit.get("kind") == "credits"
            when = f" Resets {limit['resets_at']}." if limit.get("resets_at") else ""
            items.append(Item(
                "out_of_credit" if credits else "usage_limit", f"{'credit' if credits else 'limit'}:{engine['id']}",
                f"{label} is out of credit" if credits else f"{label} hit its limit",
                _clip((limit.get("message") or "") + when, 160) or None, None, now))

    pending = (await db.execute(
        select(Approval, Thread.title)
        .join(Thread, Thread.id == Approval.thread_id)
        .join(Turn, Turn.id == Approval.turn_id)
        .where(Thread.owner_id == owner_id, Approval.status == "pending", Turn.status == "running")
        .order_by(Approval.created_at, Approval.id)
    )).all()
    for approval, title in pending:
        items.append(Item("approval", f"approval:{approval.id}",
                          _clip(approval.summary or f"Approve {approval.tool}", 120),
                          _clip(title, 80) or None, approval.thread_id,
                          _aware(approval.created_at)))

    routines = {r.id: r for r in (await db.execute(
        select(Routine).where(Routine.owner_id == owner_id))).scalars()}
    runs = (await db.execute(
        select(RoutineRun).where(RoutineRun.routine_id.in_(list(routines) or [0]),
                                 RoutineRun.created_at >= since).order_by(RoutineRun.id)
    )).scalars()
    latest: dict[int, RoutineRun] = {}
    for run in runs:
        if run.state in _SETTLED_RUN_STATES:
            latest[run.routine_id] = run
    for routine_id, run in latest.items():
        if run.state in _BAD_RUN_STATES:
            what = "missed its time" if run.state == "missed" else "failed"
            items.append(Item("routine", f"routine:{routine_id}", f"{routines[routine_id].name} {what}",
                              _clip(run.error, 160) or None, run.thread_id,
                              _aware(run.finished_at or run.created_at)))
    for routine in routines.values():
        if routine.paused and routine.paused_reason:
            items.append(Item("routine", f"routine_paused:{routine.id}", f"{routine.name} is paused",
                              _clip(routine.paused_reason, 160), None, now))

    repeated = [i for i in issues if i.get("count", 0) >= sentinel.RETRY_LOOP_MIN
                or i.get("kind") == "repeated_denial"]
    repeated.sort(key=lambda i: (sentinel._SEVERITY_ORDER.get(i.get("severity"), 3), -i.get("count", 0)))
    for issue in repeated[:REPEATED_FAILURES_SHOWN]:
        items.append(Item("repeated_failure", f"repeated_failure:{issue['sig']}",
                          _clip(issue["title"], 120), f"{issue['count']} times in the last day",
                          issue.get("thread_id"), _aware(issue["last_at"])))

    items.sort(key=lambda i: (KIND_ORDER[i.kind], -i.at.timestamp()))
    return items


def build_card(items: list[Item], *, day: str, now: datetime, zone: ZoneInfo) -> dict:
    local = _aware(now).astimezone(zone)
    shown = items[:CARD_ROWS]
    return {
        "card_id": f"needs-you:{day}",
        "day": day,
        "title": "Needs you",
        "subtitle": f"{local:%A}, {local.day} {local:%B}",
        "items": [i.public() for i in shown],
        "more": max(0, len(items) - len(shown)),
        "empty_text": EMPTY_TEXT,
    }


def card_hash(card: dict) -> str:
    return hashlib.sha256(json.dumps(card, sort_keys=True).encode()).hexdigest()


# --- the card's life ---------------------------------------------------------------


class NeedsYou:
    """Writes the day's card for one runtime owner. `engines` and `issues` are
    injected so tests need no engine; by default they come from the runtime and
    from Sentinel."""

    def __init__(self, runtime, clock=utcnow, *, engines=None, issues=None) -> None:
        self.runtime = runtime
        self.clock = clock
        self._engines = engines
        self._issues = issues
        self._dirty = False
        self._rendering = False

    @property
    def owner_id(self) -> str:
        return self.runtime.owner_id

    async def _engine_entries(self) -> list[dict]:
        if self._engines is not None:
            return await self._engines()
        return await self.runtime.engines.describe()

    async def _issue_entries(self, now: datetime) -> list[dict]:
        if self._issues is not None:
            return await self._issues()
        report = await sentinel.scan(self.runtime.session, self.owner_id, now=now)
        return [issue.public() for issue in report["issues"]]

    async def _state(self, db) -> NeedsYouState:
        state = await db.get(NeedsYouState, self.owner_id)
        if state is None:
            state = NeedsYouState(owner_id=self.owner_id, notified=[], card_revision=0)
            db.add(state)
        return state

    async def read(self) -> dict:
        """The card as it would be written now, with no side effects."""
        now = self.clock()
        async with self.runtime.session() as db:
            state = await db.get(NeedsYouState, self.owner_id)
            zone = zone_of(state.time_zone if state else None)
            items = await collect(db, self.owner_id, now=now, engines=await self._engine_entries(),
                                  issues=await self._issue_entries(now))
        return build_card(items, day=local_day(now, zone), now=now, zone=zone)

    async def presence(self, time_zone: str) -> dict:
        """The person opened or used the app. Records the zone and the time;
        only a return after 3 hours away, or the first visit of the local day,
        writes the card, and the answer says where it is only if it has
        something to show."""
        zone_name = valid_zone(time_zone)
        now = self.clock()
        async with self.runtime.session() as db:
            await begin_write(db)
            state = await self._state(db)
            previous = _aware(state.last_active_at) if state.last_active_at else None
            old_zone = zone_of(state.time_zone)
            state.time_zone = zone_name
            state.last_active_at = now
            await db.commit()
        skewed = previous is not None and previous - now > timedelta(minutes=5)
        returned = previous is None or (not skewed and now - previous >= AWAY)
        first_today = previous is None or (
            not skewed and local_day(previous, old_zone) != local_day(now, zone_of(zone_name)))
        opened = None
        if returned or first_today:
            opened = await self.render(create=True)
        return {"returned": returned, "first_today": first_today, "open": opened}

    async def tick(self) -> None:
        """The background check: updates today's card, and starts one only for
        something that needs the person. Does nothing until they have been here."""
        async with self.runtime.session() as db:
            state = await db.get(NeedsYouState, self.owner_id)
        if state is None or state.last_active_at is None:
            return
        if self._dirty:
            self._dirty = False
        await self.render(create=False)

    async def _home(self, db, state: NeedsYouState, lead_agent_id: int) -> int | None:
        """The lead's conversation the card lives in: the one it has used today,
        else the lead's newest chat, else None."""
        if state.card_thread_id is not None:
            thread = await db.get(Thread, state.card_thread_id)
            if thread is not None and thread.archived_at is None and thread.agent_id == lead_agent_id:
                return thread.id
        return (await db.execute(
            select(Thread.id).where(Thread.owner_id == self.owner_id, Thread.agent_id == lead_agent_id,
                                    Thread.kind == "chat", Thread.archived_at.is_(None))
            .order_by(Thread.updated_at.desc(), Thread.id.desc()).limit(1)
        )).scalar_one_or_none()

    async def render(self, *, create: bool) -> dict | None:
        """Write or refresh today's card. Returns `{agent_id, thread_id}` when
        the card has something worth opening, else None. `create` says the
        person just arrived; without it a new card starts only for an item."""
        if self._rendering:
            self._dirty = True
            return None
        self._rendering = True
        try:
            return await self._render(create)
        finally:
            self._rendering = False

    async def _render(self, create: bool) -> dict | None:
        now = self.clock()
        async with self.runtime.session() as db:
            team = (await db.execute(select(Team).where(Team.owner_id == self.owner_id))).scalar_one_or_none()
            lead_id = team.lead_agent_id if team else None
            state = await db.get(NeedsYouState, self.owner_id)
        if lead_id is None:
            return None
        zone = zone_of(state.time_zone if state else None)
        day = local_day(now, zone)
        async with self.runtime.session() as db:
            items = await collect(db, self.owner_id, now=now, engines=await self._engine_entries(),
                                  issues=await self._issue_entries(now))
        card = build_card(items, day=day, now=now, zone=zone)
        has_card = state is not None and state.card_day == day and state.card_thread_id is not None
        if not has_card and not items:
            # Never posted empty. `create` makes no difference: with nothing
            # to say there is nothing to open.
            return None
        digest = card_hash(card)
        async with self.runtime.session() as db:
            await begin_write(db)
            state = await self._state(db)
            thread_id = state.card_thread_id if (state.card_day == day and state.card_thread_id) else None
            if thread_id is None:
                thread_id = await self._home(db, state, lead_id)
            if thread_id is None:
                await db.rollback()
                thread = await self.runtime.create_thread(agent_id=lead_id, title="Needs you")
                thread_id = thread.id
                await begin_write(db)
                state = await self._state(db)
            if await store.running_turn_id(db, thread_id) is not None:
                # Never write into a running turn; the next tick tries again.
                await db.rollback()
                self._dirty = True
                return None
            if state.card_day == day and state.card_thread_id == thread_id and state.card_hash == digest:
                await db.rollback()
                return {"agent_id": lead_id, "thread_id": thread_id} if items else None
            card["revision"] = (state.card_revision + 1) if state.card_day == day else 1
            await store._add_event(db, thread_id, CARD_EVENT, card, None)
            seen = set(state.notified or [])
            fresh = [i for i in items if i.key not in seen]
            if fresh:
                kinds = len(items)
                notification_feed.add(
                    db, owner_id=self.owner_id, kind="needs_you",
                    title=f"{kinds} thing{'s' if kinds != 1 else ''} need{'' if kinds != 1 else 's'} you",
                    body="\n".join(i.title for i in items[:CARD_ROWS]), thread_id=thread_id)
            state.notified = ([*state.notified, *(i.key for i in fresh)])[-NOTIFIED_KEPT:]
            state.card_day, state.card_thread_id, state.card_hash = day, thread_id, digest
            state.card_revision = card["revision"]
            await db.commit()
        self.runtime.hub.notify(thread_id)
        return {"agent_id": lead_id, "thread_id": thread_id} if items else None
