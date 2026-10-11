import json
import shlex
import uuid
from datetime import UTC, datetime, timedelta

from boat_sdk.exceptions import ApiException
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models.task import Tasks
from app.db.session import AsyncSessionLocal
from app.services import user_credential_service
from app.services.box_operations import ascii_box_service
from app.services.user_credential_service import CredentialNotFoundError

logger = get_logger(__name__)

RUNNABLE_BOX_STATES = {"ready", "running", "idle"}

# queued: persisted, not yet launched. starting: claimed by one caller and
# being launched. pending: legacy pre-launch status from before the split.
# waiting_approval: launched, but blocked inside a PermissionRequest hook
# call until a human answers it (see permission_ask_service).
ACTIVE_STATUSES = {"queued", "starting", "pending", "running", "waiting_approval"}
LAUNCHED_STATUSES = {"pending", "running"}
FINISHED_STATUSES = {"succeeded", "failed"}

MAX_TASK_SECONDS = 600

# A task still "starting" after this long means its launcher died mid-launch.
# It is failed rather than retried because the command may already be running.
STUCK_STARTING_SECONDS = 600
# Queued tasks are normally started right after being created; ones older than
# this were orphaned by a crash and are picked up by the background sync.
QUEUED_RECOVERY_SECONDS = 60

PROVIDER_COMMANDS = {
    "claude": "claude -p {prompt} --output-format json",
}

PERMISSION_HOOK_SETTINGS_PATH = ".claude/settings.json"


class TaskNotFoundError(Exception):
    pass


class ParentTaskActiveError(Exception):
    pass


class UnsupportedProviderError(Exception):
    pass


class BoxCommandError(Exception):
    pass


START_ERRORS = (UnsupportedProviderError, BoxCommandError, CredentialNotFoundError)


async def _ensure_box_running(box_id: str) -> None:
    try:
        result = await run_in_threadpool(ascii_box_service.get_box, box_id)
    except ApiException as exc:
        raise BoxCommandError(
            f"Could not reach box {box_id}: {exc.status} {exc.reason}"
        ) from exc

    if result.sandbox.state not in RUNNABLE_BOX_STATES:
        try:
            await run_in_threadpool(ascii_box_service.resume_box, box_id)
        except ApiException as exc:
            raise BoxCommandError(
                f"Could not resume box {box_id}: {exc.status} {exc.reason}"
            ) from exc


async def _write_permission_hook_config(box_id: str, user_id: str, task_id: int) -> None:
    """Tell Claude Code to send every permission decision to our backend
    instead of skipping permissions or asking a human at a terminal."""
    hook_url = (
        f"{settings.PERMISSION_HOOK_BASE_URL}/api/v1/permissions/check/{user_id}/{task_id}"
    )
    config = {
        "hooks": {
            "PermissionRequest": [
                {
                    "matcher": "*",
                    "hooks": [
                        {
                            "type": "http",
                            "url": hook_url,
                            "timeout": 120,
                            "headers": {
                                "Authorization": f"Bearer {settings.HOOK_TOKEN}"
                            },
                        }
                    ],
                }
            ]
        }
    }
    await run_in_threadpool(
        ascii_box_service.write_file,
        box_id,
        PERMISSION_HOOK_SETTINGS_PATH,
        json.dumps(config),
    )


def _build_command(
    provider: str, prompt_text: str, *, session_id: str, resume: bool
) -> str:
    template = PROVIDER_COMMANDS.get(provider)
    if template is None:
        raise UnsupportedProviderError(f"Unsupported provider: {provider}")
    base = template.format(prompt=shlex.quote(prompt_text))
    if provider == "claude" and settings.PARALLEL_SEARCH_ENABLED:
        # Per-invocation config keeps saved MCP servers and credentials intact.
        mcp_config = {
            "mcpServers": {
                "open-dots-parallel-search": {
                    "type": "http",
                    "url": "https://search.parallel.ai/mcp",
                    "headers": {
                        "User-Agent": "OpenDots/0.1 (+https://github.com/Anil-matcha/open-dots)"
                    },
                }
            }
        }
        base += f" --mcp-config {shlex.quote(json.dumps(mcp_config))}"
    flag = "--resume" if resume else "--session-id"
    return f"{base} {flag} {shlex.quote(session_id)}"


def _finished_values(stdout: str, stderr: str) -> dict:
    try:
        payload = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        return {"status": "failed", "error": stderr or stdout}

    if not isinstance(payload, dict):
        return {"status": "failed", "error": stderr or stdout}

    if payload.get("is_error"):
        return {"status": "failed", "error": payload.get("result") or stderr}
    return {"status": "succeeded", "result": payload.get("result")}


async def _reload(db: AsyncSession, task_id: int) -> Tasks:
    result = await db.execute(
        select(Tasks)
        .where(Tasks.id == task_id)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one()


async def _is_resume(db: AsyncSession, task: Tasks) -> bool:
    # Use a recorded process id as evidence of an earlier launch. A task
    # that failed during setup has no session to resume, so its reply still
    # needs --session-id.
    result = await db.execute(
        select(
            exists().where(
                Tasks.session_id == task.session_id,
                Tasks.id < task.id,
                Tasks.prompt_id.is_not(None),
            )
        )
    )
    return bool(result.scalar())


async def create_task(
    db: AsyncSession,
    *,
    user_id: str,
    provider: str,
    box_id: str,
    prompt_text: str,
    parent_task_id: int | None = None,
    schedule_id: int | None = None,
    scheduled_for: datetime | None = None,
) -> Tasks:
    """Insert a queued task without committing, so callers can commit it
    atomically with their own changes."""
    if provider not in PROVIDER_COMMANDS:
        raise UnsupportedProviderError(f"Unsupported provider: {provider}")

    if parent_task_id is None:
        session_id = str(uuid.uuid4())
    else:
        parent = await get_task(db, parent_task_id)
        if parent is None or parent.user_id != user_id:
            raise TaskNotFoundError(f"Parent task {parent_task_id} not found")
        if not parent.session_id:
            raise TaskNotFoundError(
                f"Task {parent_task_id} predates conversation replies and can't be resumed"
            )
        if parent.status in ACTIVE_STATUSES:
            raise ParentTaskActiveError(
                f"Task {parent_task_id} is still {parent.status} — wait for it to finish first"
            )
        session_id = parent.session_id

        # Replies through different finished ancestors still continue the
        # same conversation. Lock its first task until the queued reply is
        # committed, so separate API workers cannot both admit a reply.
        await db.execute(
            select(Tasks.id)
            .where(Tasks.session_id == session_id)
            .order_by(Tasks.id)
            .limit(1)
            .with_for_update()
        )
        active = (
            await db.execute(
                select(Tasks.id, Tasks.status)
                .where(
                    Tasks.session_id == session_id,
                    Tasks.status.in_(ACTIVE_STATUSES),
                )
                .limit(1)
            )
        ).first()
        if active is not None:
            raise ParentTaskActiveError(
                f"Task {active.id} is still {active.status} — wait for it to finish first"
            )

    task = Tasks(
        user_id=user_id,
        provider=provider,
        box_id=box_id,
        prompt_text=prompt_text,
        status="queued",
        session_id=session_id,
        schedule_id=schedule_id,
        scheduled_for=scheduled_for,
    )
    db.add(task)
    await db.flush()
    return task


async def start_task(db: AsyncSession, task_id: int) -> Tasks:
    """Launch a queued task. Only the caller that wins the queued→starting
    transition launches it; anyone else gets the task back unchanged."""
    claimed = await db.execute(
        update(Tasks)
        .where(Tasks.id == task_id, Tasks.status == "queued")
        .values(status="starting")
        .returning(Tasks.id)
    )
    won = claimed.scalar_one_or_none() is not None
    await db.commit()
    task = await _reload(db, task_id)
    if not won:
        return task

    assert task.session_id is not None  # create_task always sets it
    try:
        command = _build_command(
            task.provider,
            task.prompt_text,
            session_id=task.session_id,
            resume=await _is_resume(db, task),
        )
        await _ensure_box_running(task.box_id)
        await user_credential_service.inject_credential_into_box(
            db, box_id=task.box_id, user_id=task.user_id, provider=task.provider
        )
        try:
            await user_credential_service.inject_credential_into_box(
                db, box_id=task.box_id, user_id=task.user_id, provider="github"
            )
        except CredentialNotFoundError:
            pass
        await _write_permission_hook_config(task.box_id, task.user_id, task.id)
    except ApiException as exc:
        error = BoxCommandError(
            f"Could not prepare box {task.box_id}: {exc.status} {exc.reason}"
        )
        task.status = "failed"
        task.error = str(error)
        await db.commit()
        raise error from exc
    except START_ERRORS as exc:
        task.status = "failed"
        task.error = str(exc)
        await db.commit()
        raise

    try:
        started = await run_in_threadpool(
            ascii_box_service.run_command,
            task.box_id,
            command,
            timeout_seconds=MAX_TASK_SECONDS,
            detached=True,
        )
    except ApiException as exc:
        task.status = "failed"
        task.error = f"{exc.status} {exc.reason}"
        await db.commit()
        await db.refresh(task)
        return task

    task.status = "running"
    task.prompt_id = str(started.actual_instance.process_id)
    await db.commit()
    await db.refresh(task)
    return task


async def execute_task(
    db: AsyncSession,
    *,
    user_id: str,
    provider: str,
    box_id: str,
    prompt_text: str,
    parent_task_id: int | None = None,
) -> Tasks:
    task = await create_task(
        db,
        user_id=user_id,
        provider=provider,
        box_id=box_id,
        prompt_text=prompt_text,
        parent_task_id=parent_task_id,
    )
    await db.commit()
    return await start_task(db, task.id)


async def get_task(db: AsyncSession, task_id: int) -> Tasks | None:
    result = await db.execute(select(Tasks).where(Tasks.id == task_id))
    return result.scalar_one_or_none()


async def list_tasks_for_user(db: AsyncSession, user_id: str) -> list[Tasks]:
    result = await db.execute(
        select(Tasks).where(Tasks.user_id == user_id).order_by(Tasks.created_at.desc())
    )
    return list(result.scalars().all())


async def sync_task_status(db: AsyncSession, task: Tasks) -> Tasks:
    if task.status == "waiting_approval":
        # Re-read a decision made by a different request before polling.
        # A command may have exited while its ask remained pending.
        task = await _reload(db, task.id)

    if (
        task.status not in LAUNCHED_STATUSES
        and task.status != "waiting_approval"
    ) or not task.prompt_id:
        return task

    try:
        run_status = await run_in_threadpool(
            ascii_box_service.command_status, task.box_id, int(task.prompt_id)
        )
        stdout = run_status.stdout
        if (
            not run_status.running
            and run_status.stdout_truncated
            and run_status.log_path
        ):
            output = await run_in_threadpool(
                ascii_box_service.read_file, task.box_id, run_status.log_path
            )
            stdout = output.content
    except ApiException as exc:
        values = {"status": "failed", "error": f"{exc.status} {exc.reason}"}
    else:
        if run_status.running:
            if task.status in {"running", "waiting_approval"}:
                return task
            values = {"status": "running"}
        else:
            values = _finished_values(stdout, run_status.stderr)

    # Conditional on the status we read, so concurrent syncers (API requests,
    # the Telegram bot, background sync on other instances) apply each
    # transition once.
    applied = await db.execute(
        update(Tasks)
        .where(Tasks.id == task.id, Tasks.status == task.status)
        .values(**values)
    )
    await db.commit()

    # The CLI may have renewed its login during the run; keep that renewal
    # even if the box is replaced before the next task.
    if applied.rowcount and values["status"] in FINISHED_STATUSES:
        try:
            await user_credential_service.pull_renewed_credential_from_box(
                db, box_id=task.box_id, user_id=task.user_id, provider=task.provider
            )
        except Exception:
            await db.rollback()
            logger.exception("failed to pull renewed credential", task_id=task.id)

    return await _reload(db, task.id)


async def sync_active_tasks() -> None:
    now = datetime.now(UTC)
    async with AsyncSessionLocal() as db:
        await db.execute(
            update(Tasks)
            .where(
                Tasks.status == "starting",
                Tasks.updated_at < now - timedelta(seconds=STUCK_STARTING_SECONDS),
            )
            .values(
                status="failed",
                error="Task never finished launching (the server stopped while starting it)",
            )
        )
        await db.commit()

        queued = await db.execute(
            select(Tasks.id).where(
                Tasks.status == "queued",
                Tasks.created_at < now - timedelta(seconds=QUEUED_RECOVERY_SECONDS),
            )
        )
        for task_id in queued.scalars().all():
            try:
                await start_task(db, task_id)
            except START_ERRORS:
                pass
            except Exception:
                await db.rollback()
                logger.exception("failed to start queued task", task_id=task_id)

        launched = await db.execute(
            select(Tasks).where(
                Tasks.status.in_(LAUNCHED_STATUSES), Tasks.prompt_id.is_not(None)
            )
        )
        for task in launched.scalars().all():
            try:
                await sync_task_status(db, task)
            except Exception:
                await db.rollback()
                logger.exception("failed to sync task status", task_id=task.id)
