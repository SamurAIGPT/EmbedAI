import re
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.triggers.combining import OrTrigger
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import exists, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models.schedule import Schedule
from app.db.models.task import Tasks
from app.db.session import AsyncSessionLocal
from app.db.transactions import begin_write
from app.services import notification_service, task_execution_service
from app.services.task_execution_service import (
    ACTIVE_STATUSES,
    FINISHED_STATUSES,
    PROVIDER_COMMANDS,
    START_ERRORS,
    UnsupportedProviderError,
)

logger = get_logger(__name__)

# An occurrence found more than this late (server was down) is skipped rather
# than run, so a stale report doesn't fire hours after it was due.
MISFIRE_GRACE_SECONDS = 900
POLL_BATCH_SIZE = 20
REPORT_BATCH_SIZE = 20

# A failure containing one of these needs the user to act (reconnect a login,
# fix the schedule) — retrying on the next occurrence can't succeed, so the
# schedule is paused on the first one instead of after MAX_CONSECUTIVE_FAILURES.
NEEDS_USER_ACTION_MARKERS = (
    "Failed to authenticate",  # Claude CLI: saved login expired and couldn't renew
    "No credential found for user",  # CredentialNotFoundError
    "Unsupported provider",  # UnsupportedProviderError
)
MAX_CONSECUTIVE_FAILURES = 3

UPDATABLE_FIELDS = {"provider", "box_id", "prompt_text", "cron_expression", "timezone"}


class ScheduleNotFoundError(Exception):
    pass


class InvalidScheduleError(Exception):
    pass


class ScheduleInactiveError(Exception):
    pass


def _crontab_trigger(expression: str, timezone: ZoneInfo) -> CronTrigger | OrTrigger:
    # Validate with APScheduler first, retaining the existing accepted syntax.
    CronTrigger.from_crontab(expression, timezone=timezone)
    minute, hour, day, month, weekday = expression.split()

    # Standard crontab and our UI/Telegram presets use Sunday=0, whereas
    # APScheduler 3 uses Monday=0. Expand numeric ranges before translating:
    # rotating range endpoints would turn 0-6 into the invalid range sun-sat.
    names = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")
    translated = []
    for part in weekday.split(","):
        match = re.fullmatch(r"(\*|\d+)(?:-(\d+))?(?:/(\d+))?", part)
        if part == "*" or match is None:
            translated.append(part)
            continue
        first, last, step = match.groups()
        start = 0 if first == "*" else int(first)
        end = int(last) if last is not None else (6 if step or first == "*" else start)
        translated.extend(names[value] for value in range(start, end + 1, int(step or 1)))

    weekday_expression = ",".join(translated)
    if not day.startswith("*") and not weekday.startswith("*"):
        # Crontab runs when either restricted day field matches. APScheduler
        # intersects all fields, so keep these two alternatives separate.
        return OrTrigger([
            CronTrigger.from_crontab(f"{minute} {hour} {day} {month} *", timezone=timezone),
            CronTrigger.from_crontab(f"{minute} {hour} * {month} {weekday_expression}", timezone=timezone),
        ])
    return CronTrigger.from_crontab(
        f"{minute} {hour} {day} {month} {weekday_expression}", timezone=timezone
    )


def compute_next_run(cron_expression: str, timezone: str, after: datetime) -> datetime:
    try:
        tz = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise InvalidScheduleError(f"Unknown timezone: {timezone}") from exc
    try:
        trigger = _crontab_trigger(cron_expression, tz)
    except ValueError as exc:
        raise InvalidScheduleError(
            f"Invalid cron expression {cron_expression!r}: {exc}"
        ) from exc

    # get_next_fire_time is inclusive; nudge past `after` so an occurrence
    # landing exactly on it isn't returned again.
    next_run = trigger.get_next_fire_time(None, after + timedelta(microseconds=1))
    if next_run is None:
        raise InvalidScheduleError(f"Cron expression {cron_expression!r} never fires")
    return next_run.astimezone(UTC)


def _validate_provider(provider: str) -> None:
    if provider not in PROVIDER_COMMANDS:
        raise InvalidScheduleError(f"Unsupported provider: {provider}")


async def create_schedule(
    db: AsyncSession,
    *,
    user_id: str,
    provider: str,
    box_id: str,
    prompt_text: str,
    cron_expression: str,
    timezone: str,
) -> Schedule:
    _validate_provider(provider)
    schedule = Schedule(
        user_id=user_id,
        provider=provider,
        box_id=box_id,
        prompt_text=prompt_text,
        cron_expression=cron_expression,
        timezone=timezone,
        is_active=True,
        next_run_at=compute_next_run(cron_expression, timezone, datetime.now(UTC)),
    )
    db.add(schedule)
    await db.commit()
    await db.refresh(schedule)
    return schedule


async def get_schedule(db: AsyncSession, schedule_id: int, user_id: str) -> Schedule:
    result = await db.execute(
        select(Schedule).where(
            Schedule.id == schedule_id,
            Schedule.user_id == user_id,
            Schedule.deleted_at.is_(None),
        )
    )
    schedule = result.scalar_one_or_none()
    if schedule is None:
        raise ScheduleNotFoundError(f"Schedule {schedule_id} not found")
    return schedule


async def list_schedules(db: AsyncSession, user_id: str) -> list[Schedule]:
    result = await db.execute(
        select(Schedule)
        .where(Schedule.user_id == user_id, Schedule.deleted_at.is_(None))
        .order_by(Schedule.created_at.desc())
    )
    return list(result.scalars().all())


async def update_schedule(
    db: AsyncSession, schedule_id: int, user_id: str, changes: dict
) -> Schedule:
    await begin_write(db)
    schedule = await get_schedule(db, schedule_id, user_id)
    changes = {
        k: v for k, v in changes.items() if k in UPDATABLE_FIELDS and v is not None
    }

    if "provider" in changes:
        _validate_provider(changes["provider"])
    if "cron_expression" in changes or "timezone" in changes:
        schedule.next_run_at = compute_next_run(
            changes.get("cron_expression", schedule.cron_expression),
            changes.get("timezone", schedule.timezone),
            datetime.now(UTC),
        )
    for field, value in changes.items():
        setattr(schedule, field, value)

    await db.commit()
    await db.refresh(schedule)
    return schedule


async def pause_schedule(db: AsyncSession, schedule_id: int, user_id: str) -> Schedule:
    await begin_write(db)
    schedule = await get_schedule(db, schedule_id, user_id)
    schedule.is_active = False
    await db.commit()
    await db.refresh(schedule)
    return schedule


async def resume_schedule(db: AsyncSession, schedule_id: int, user_id: str) -> Schedule:
    await begin_write(db)
    schedule = await get_schedule(db, schedule_id, user_id)
    # Computed from now so occurrences missed while paused don't fire.
    schedule.next_run_at = compute_next_run(
        schedule.cron_expression, schedule.timezone, datetime.now(UTC)
    )
    schedule.is_active = True
    schedule.consecutive_failures = 0
    schedule.paused_reason = None
    await db.commit()
    await db.refresh(schedule)
    return schedule


async def run_schedule_now(db: AsyncSession, schedule_id: int, user_id: str) -> Schedule:
    await begin_write(db)
    schedule = await get_schedule(db, schedule_id, user_id)
    if not schedule.is_active:
        raise ScheduleInactiveError(f"Schedule {schedule_id} is paused — resume it first")
    schedule.next_run_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(schedule)
    return schedule


async def delete_schedule(db: AsyncSession, schedule_id: int, user_id: str) -> None:
    await begin_write(db)
    schedule = await get_schedule(db, schedule_id, user_id)
    schedule.is_active = False
    schedule.deleted_at = datetime.now(UTC)
    await db.commit()


async def list_schedule_runs(
    db: AsyncSession, schedule_id: int, user_id: str
) -> list[Tasks]:
    await get_schedule(db, schedule_id, user_id)
    result = await db.execute(
        select(Tasks)
        .where(Tasks.schedule_id == schedule_id)
        .order_by(Tasks.created_at.desc())
    )
    return list(result.scalars().all())


async def _has_active_run(db: AsyncSession, schedule_id: int) -> bool:
    result = await db.execute(
        select(
            exists().where(
                Tasks.schedule_id == schedule_id, Tasks.status.in_(ACTIVE_STATUSES)
            )
        )
    )
    return bool(result.scalar())


async def _enqueue_occurrence(
    db: AsyncSession, schedule: Schedule, now: datetime
) -> int | None:
    due_at = schedule.next_run_at
    # Advanced from now, not from due_at, so a backlog of missed occurrences
    # collapses into this single one.
    schedule.next_run_at = compute_next_run(
        schedule.cron_expression, schedule.timezone, now
    )

    skip_prefix = f"Skipped run due at {due_at.isoformat()}"
    if now - due_at > timedelta(seconds=MISFIRE_GRACE_SECONDS):
        schedule.last_error = (
            f"{skip_prefix}: server was down for more than "
            f"{MISFIRE_GRACE_SECONDS // 60} minutes"
        )
        return None
    if await _has_active_run(db, schedule.id):
        schedule.last_error = f"{skip_prefix}: previous run is still active"
        return None

    try:
        async with db.begin_nested():
            task = await task_execution_service.create_task(
                db,
                user_id=schedule.user_id,
                provider=schedule.provider,
                box_id=schedule.box_id,
                prompt_text=schedule.prompt_text,
                schedule_id=schedule.id,
                scheduled_for=due_at,
            )
    except IntegrityError:
        schedule.last_error = f"{skip_prefix}: this occurrence already ran"
        return None
    except UnsupportedProviderError as exc:
        schedule.last_error = f"{skip_prefix}: {exc}"
        return None

    schedule.last_run_at = now
    schedule.last_error = None
    return task.id


async def poll_due_schedules() -> None:
    now = datetime.now(UTC)
    task_ids: list[int] = []

    # The queued task and the advanced next_run_at commit together, so a crash
    # can't consume an occurrence without leaving a task row for it; queued
    # tasks orphaned after this commit are recovered by sync_active_tasks.
    async with AsyncSessionLocal() as db:
        # SQLite has no SKIP LOCKED: the whole claim runs as one write
        # transaction, so a second poller waits, then finds nothing due.
        await begin_write(db)
        result = await db.execute(
            select(Schedule)
            .where(
                Schedule.is_active.is_(True),
                Schedule.deleted_at.is_(None),
                Schedule.next_run_at <= now,
            )
            .order_by(Schedule.next_run_at)
            .limit(POLL_BATCH_SIZE)
            .with_for_update(skip_locked=True)
        )
        for schedule in result.scalars().all():
            task_id = await _enqueue_occurrence(db, schedule, now)
            if task_id is not None:
                task_ids.append(task_id)
        await db.commit()

    for task_id in task_ids:
        async with AsyncSessionLocal() as db:
            try:
                await task_execution_service.start_task(db, task_id)
            except START_ERRORS:
                pass
            except Exception:
                logger.exception("failed to start scheduled task", task_id=task_id)


def _needs_user_action(error: str | None) -> bool:
    return any(marker in (error or "") for marker in NEEDS_USER_ACTION_MARKERS)


def _truncate(text: str, limit: int = 3500) -> str:
    # Telegram messages cap at 4096 characters; leave room for the preamble.
    return text if len(text) <= limit else text[:limit] + "…"


async def _pause(db: AsyncSession, schedule_id: int, reason: str) -> None:
    await db.execute(
        update(Schedule)
        .where(Schedule.id == schedule_id)
        .values(is_active=False, paused_reason=reason)
    )


async def _report_task(db: AsyncSession, task: Tasks) -> None:
    await begin_write(db)
    schedule = await db.get(Schedule, task.schedule_id)
    if schedule is None or schedule.deleted_at is not None:
        return

    if task.status == "succeeded":
        if schedule.consecutive_failures or schedule.paused_reason:
            await db.execute(
                update(Schedule)
                .where(Schedule.id == schedule.id)
                .values(consecutive_failures=0, paused_reason=None)
            )
        message = (
            f"✅ Scheduled task succeeded ({schedule.prompt_text[:80]!r})\n\n"
            f"{_truncate(task.result or '(no output)')}"
        )
    else:
        failures = (
            await db.execute(
                update(Schedule)
                .where(Schedule.id == schedule.id)
                .values(consecutive_failures=Schedule.consecutive_failures + 1)
                .returning(Schedule.consecutive_failures)
            )
        ).scalar_one()

        needs_action = _needs_user_action(task.error)
        if needs_action or failures >= MAX_CONSECUTIVE_FAILURES:
            reason = (
                task.error
                if needs_action
                else f"{failures} consecutive failures. Last error: {task.error}"
            )
            await _pause(db, schedule.id, reason)
            message = (
                f"⏸️ Schedule paused ({schedule.prompt_text[:80]!r})\n\n"
                f"{_truncate(reason or 'unknown error')}\n\n"
                "Fix the issue, then resume the schedule to start it again."
            )
        else:
            message = (
                f"❌ Scheduled task failed ({schedule.prompt_text[:80]!r}), "
                f"will retry next occurrence ({failures}/{MAX_CONSECUTIVE_FAILURES})\n\n"
                f"{_truncate(task.error or 'unknown error')}"
            )

    chat_id = notification_service.telegram_chat_id(schedule.user_id)
    if chat_id is not None:
        # Save the counters first: do not hold the write lock (SQLite) across
        # the Telegram call.
        await db.commit()
        await notification_service.send_telegram_message(chat_id, message)


async def report_finished_runs() -> None:
    async with AsyncSessionLocal() as db:
        due = await db.execute(
            select(Tasks.id)
            .where(
                Tasks.schedule_id.is_not(None),
                Tasks.status.in_(FINISHED_STATUSES),
                Tasks.reported_at.is_(None),
            )
            .order_by(Tasks.created_at)
            .limit(REPORT_BATCH_SIZE)
        )
        for task_id in due.scalars().all():
            # Claim this task before acting, so a crash mid-report can't
            # notify twice, and two instances can't both claim it.
            claimed = await db.execute(
                update(Tasks)
                .where(Tasks.id == task_id, Tasks.reported_at.is_(None))
                .values(reported_at=datetime.now(UTC))
                .returning(Tasks.id)
            )
            if claimed.scalar_one_or_none() is None:
                continue
            await db.commit()

            task = await db.get(Tasks, task_id)
            try:
                await _report_task(db, task)
                await db.commit()
            except Exception:
                await db.rollback()
                logger.exception("failed to report finished run", task_id=task_id)
