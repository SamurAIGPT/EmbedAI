import asyncio
import json
import shlex
import uuid
from datetime import UTC, datetime, timedelta

from boat_sdk.exceptions import ApiException
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core import app_settings
from app.core.logging import get_logger
from app.db.models.task import Tasks
from app.db.session import AsyncSessionLocal
from app.db.transactions import begin_write, release_snapshot
from app.services import local_claude_runner, user_credential_service
from app.services.box_operations import ascii_box_service
from app.services.local_access_policy import LocalAccessGrant
from app.services.local_approval_broker import broker as approval_broker
from app.services.local_claude_stream import StreamNormalizer, ToolEvent
from app.services.local_run_hub import (
    TEXT_DELTA,
    TOOL_COMPLETED,
    TOOL_STARTED,
    TURN_COMPLETED,
    TURN_FAILED,
    TURN_STARTED,
)
from app.services.local_run_hub import hub as run_hub
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

# Local runs share the task table but never go through the Boat code paths:
# the provider is deliberately absent from PROVIDER_COMMANDS, so schedules,
# Telegram and POST /tasks cannot select it. Only execute_local_task can create
# one, and it requires a LocalAccessGrant.
LOCAL_PROVIDER = "claude-local"
LOCAL_BOX_ID = "local"
# A local run still "running" this long after its deadline lost its server.
LOCAL_STALE_GRACE_SECONDS = 60
LOCAL_MAX_ERROR_CHARS = 2000

_local_runs: set[asyncio.Task] = set()


class TaskNotFoundError(Exception):
    pass


class ParentTaskActiveError(Exception):
    pass


class UnsupportedProviderError(Exception):
    pass


class BoxCommandError(Exception):
    pass


class LocalRuntimeUnavailableError(Exception):
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
        # SQLite has no row locks, so take the write lock before the checks
        # below: a second reply then waits and sees this one (a clean 409).
        await begin_write(db)
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
        await release_snapshot(db)  # the box calls below are slow
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
        await release_snapshot(db)
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

    await release_snapshot(db)
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


async def get_task(
    db: AsyncSession, task_id: int, *, access: LocalAccessGrant | None = None
) -> Tasks | None:
    """Local tasks are invisible here unless the caller holds a grant, so the
    guest-id endpoints and the Telegram bot cannot read their output."""
    result = await db.execute(select(Tasks).where(Tasks.id == task_id))
    task = result.scalar_one_or_none()
    if task is not None and task.provider == LOCAL_PROVIDER and access is None:
        return None
    return task


async def list_tasks_for_user(db: AsyncSession, user_id: str) -> list[Tasks]:
    result = await db.execute(
        select(Tasks)
        .where(Tasks.user_id == user_id, Tasks.provider != LOCAL_PROVIDER)
        .order_by(Tasks.created_at.desc())
    )
    return list(result.scalars().all())


async def list_local_tasks_for_user(
    db: AsyncSession, user_id: str, *, access: LocalAccessGrant
) -> list[Tasks]:
    result = await db.execute(
        select(Tasks)
        .where(Tasks.user_id == user_id, Tasks.provider == LOCAL_PROVIDER)
        .order_by(Tasks.created_at.desc())
    )
    return list(result.scalars().all())


async def execute_local_task(
    db: AsyncSession,
    *,
    access: LocalAccessGrant,
    user_id: str,
    prompt_text: str,
) -> Tasks:
    """Start a run on the user's own Claude CLI (chat-only, or with tools that
    wait for approval when LOCAL_TOOLS_ENABLED) and return it while it is still
    running; callers poll the task like any other."""
    if not isinstance(access, LocalAccessGrant):
        raise PermissionError("Local execution requires an access grant")

    snapshot = app_settings.capture()
    with app_settings.use(snapshot):
        cli = await local_claude_runner.get_status()
    if not cli.installed or cli.signed_in is not True:
        raise LocalRuntimeUnavailableError(
            cli.error or "Claude CLI is not ready for local use."
        )

    task = Tasks(
        user_id=user_id,
        provider=LOCAL_PROVIDER,
        box_id=LOCAL_BOX_ID,
        prompt_text=prompt_text,
        status="running",
        session_id=str(uuid.uuid4()),
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)

    # Registered before the run starts so a stream opened right after this
    # call returns already finds it.
    run_hub.start(task.id)
    run = asyncio.create_task(_run_local_task(task.id, snapshot))
    _local_runs.add(run)
    run.add_done_callback(_local_runs.discard)
    return task


def _clip_error(message: str) -> str:
    message = message.strip()
    if len(message) > LOCAL_MAX_ERROR_CHARS:
        return message[:LOCAL_MAX_ERROR_CHARS] + "…"
    return message


def _local_run_values(
    normalizer: StreamNormalizer,
    output: local_claude_runner.RunOutput,
    *,
    tools: bool = False,
) -> dict:
    """What to record for a finished local run. Whatever text Claude had
    streamed is kept even when the run failed, so a partial answer survives.

    With tools on, a refused tool call is not a failure: the person said no (or
    did not answer) and Claude carried on, so the run's own result decides."""
    streamed = normalizer.text
    final = normalizer.final
    stderr = output.stderr.strip()

    error: str | None = None
    if output.timed_out:
        error = f"Timed out after {app_settings.get('LOCAL_TASK_TIMEOUT_SECONDS')} seconds"
    elif output.output_truncated or normalizer.over_limit:
        error = "Claude produced more output than allowed"
    elif final is None:
        if stderr:
            error = stderr
        elif output.exit_code not in (0, None):
            error = f"Claude exited with code {output.exit_code}"
        else:
            error = "Claude ended without a result"
    elif final.is_error:
        error = final.text or stderr or "Claude reported an error"
    elif final.permission_denied and not tools:
        error = "Claude tried to use a tool, which is turned off for local chat"

    if error is not None:
        values: dict = {"status": "failed", "error": _clip_error(error)}
        if streamed:
            values["result"] = streamed
        return values

    # Prefer the CLI's own final text; if it sent none, keep what was streamed
    # rather than replacing it with nothing. An empty answer stays empty.
    if final is not None and final.text and final.text.strip():
        text = final.text
    else:
        text = streamed
    return {"status": "succeeded", "result": text}


def terminal_event_for(task: Tasks) -> tuple[str, dict]:
    """The terminal event that matches what the database actually records."""
    if task.status == "succeeded":
        return TURN_COMPLETED, {"status": "succeeded", "result": task.result or ""}
    return TURN_FAILED, {
        "status": task.status if task.status in FINISHED_STATUSES else "failed",
        "error": task.error or "The run ended unexpectedly",
        "result": task.result or "",
    }


def _handle_tool_event(
    task_id: int, event: ToolEvent, control: local_claude_runner.RunControl
) -> None:
    """Runs on the event loop. Publishes tool activity, and turns the CLI's
    questions into approvals a person answers in the UI."""
    data = event.data
    if event.kind == "tool_started":
        run_hub.publish(task_id, TOOL_STARTED, data)
    elif event.kind == "tool_completed":
        run_hub.publish(task_id, TOOL_COMPLETED, data)
    elif event.kind == "approval_requested":
        approval_broker.request(
            task_id,
            data["request_id"],
            data["tool"],
            data["input"],
            lambda payload: control.send(json.dumps(payload, ensure_ascii=False)),
            on_open=control.pause_deadline,
            on_close=control.resume_deadline,
        )
    elif event.kind == "approval_cancelled":
        approval_broker.cancel_request(task_id, data["request_id"])
    elif event.kind == "control_unsupported":
        # A question this app does not know how to ask a person: say no.
        control.send(
            json.dumps(
                {
                    "type": "control_response",
                    "response": {
                        "subtype": "error",
                        "request_id": data["request_id"],
                        "error": "Not supported by this app.",
                    },
                }
            )
        )


async def _run_local_task(task_id: int, snapshot: dict | None = None) -> None:
    with app_settings.use(snapshot if snapshot is not None else app_settings.capture()):
        await _run_local_task_with_settings(task_id)


async def _run_local_task_with_settings(task_id: int) -> None:
    loop = asyncio.get_running_loop()
    run_hub.start(task_id)
    normalizer = StreamNormalizer(max_text_bytes=app_settings.get("LOCAL_MAX_OUTPUT_BYTES"))
    tools = app_settings.get("LOCAL_TOOLS_ENABLED")
    control = local_claude_runner.RunControl() if tools else None

    def on_line(line: bytes) -> bool:
        # Runs on the runner's reader thread; the hub is only touched on the
        # event loop, in order.
        for chunk in normalizer.feed(line):
            loop.call_soon_threadsafe(
                run_hub.publish, task_id, TEXT_DELTA, {"text": chunk, "mode": "append"}
            )
        if control is not None:
            for event in normalizer.take_tool_events():
                loop.call_soon_threadsafe(_handle_tool_event, task_id, event, control)
            if normalizer.final is not None:
                # The turn is over; closing stdin lets the CLI exit.
                control.close_stdin()
        return normalizer.over_limit

    terminal: tuple[str, dict] | None = None
    try:
        async with AsyncSessionLocal() as db:
            task = await _reload(db, task_id)
            # The run can take minutes: hold no transaction (and so no stale
            # snapshot) while it works; the result is written afterwards.
            await release_snapshot(db)
            run_hub.publish(task_id, TURN_STARTED)
            try:
                extra = {"control": control} if control is not None else {}
                output = await local_claude_runner.run_prompt(
                    task.prompt_text, task.session_id, on_line, **extra
                )
                values = _local_run_values(normalizer, output, tools=tools)
            except local_claude_runner.ClaudeCliUnavailable as exc:
                values = {"status": "failed", "error": str(exc)}
            except Exception:
                logger.exception("local task crashed", task_id=task_id)
                values = {"status": "failed", "error": "Local run failed unexpectedly"}
                if normalizer.text:
                    values["result"] = normalizer.text

            # Conditional so a task already failed by the stale sweep stays so.
            await db.execute(
                update(Tasks)
                .where(Tasks.id == task_id, Tasks.status == "running")
                .values(**values)
            )
            await db.commit()
            # Announce what the database now says, never what we meant to write.
            terminal = terminal_event_for(await _reload(db, task_id))
    except Exception:
        logger.exception("could not record local task result", task_id=task_id)
    finally:
        # Questions still open when the run ends can no longer matter.
        approval_broker.cancel_run(task_id)
        if terminal is None:
            terminal = (
                TURN_FAILED,
                {
                    "status": "failed",
                    "error": "Could not record the result of this run",
                    "result": normalizer.text,
                },
            )
        run_hub.publish(task_id, *terminal)


async def sync_task_status(db: AsyncSession, task: Tasks) -> Tasks:
    # Callers often keep one session across polls; drop any older snapshot so
    # the reads below see current data and the writes cannot hit a stale one.
    await release_snapshot(db)

    if task.provider == LOCAL_PROVIDER:
        # Run in this process and recorded by _run_local_task; no box to ask.
        return await _reload(db, task.id)

    if task.status == "waiting_approval":
        # Blocked inside a permission hook call, answered by a different
        # request (web or Telegram) — nothing to ask the sandbox here, just
        # re-read in case it was just answered.
        return await _reload(db, task.id)

    if task.status not in LAUNCHED_STATUSES or not task.prompt_id:
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
            if task.status == "running":
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

        # A run this process is still driving is not stale, however long it has
        # waited for a person to answer an approval.
        stale = await db.execute(
            select(Tasks.id).where(
                Tasks.provider == LOCAL_PROVIDER,
                Tasks.status == "running",
                Tasks.updated_at
                < now
                - timedelta(
                    seconds=app_settings.get("LOCAL_TASK_TIMEOUT_SECONDS")
                    + LOCAL_STALE_GRACE_SECONDS
                ),
            )
        )
        stale_ids = [i for i in stale.scalars().all() if not run_hub.is_live(i)]
        await db.execute(
            update(Tasks)
            .where(Tasks.id.in_(stale_ids), Tasks.status == "running")
            .values(
                status="failed",
                error="Local run was interrupted (the server stopped while it was running)",
            )
        )
        await db.commit()

        queued = await db.execute(
            select(Tasks.id).where(
                Tasks.provider != LOCAL_PROVIDER,
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
            select(Tasks.id).where(
                Tasks.provider != LOCAL_PROVIDER,
                Tasks.status.in_(LAUNCHED_STATUSES),
                Tasks.prompt_id.is_not(None),
            )
        )
        # Ids only: a rollback after one failure expires every loaded row, and
        # each task is read fresh anyway after the previous one's slow calls.
        for task_id in launched.scalars().all():
            try:
                await sync_task_status(db, await _reload(db, task_id))
            except Exception:
                await db.rollback()
                logger.exception("failed to sync task status", task_id=task_id)
