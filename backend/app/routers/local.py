from collections.abc import AsyncIterator
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core import app_settings
from app.db.session import get_db
from app.schemas.local import (
    LocalApprovalAnswer,
    LocalApprovalResponse,
    LocalStatusResponse,
    LocalTaskCreateRequest,
)
from app.schemas.task import TaskResponse
from app.services import local_claude_runner, task_execution_service
from app.services.local_approval_broker import ApprovalNotFound
from app.services.local_approval_broker import broker as approval_broker
from app.services.local_access_policy import (
    LocalAccessDenied,
    LocalAccessGrant,
    LocalAccessPolicy,
    get_local_access_policy,
    require_local_access,
)
from app.services.local_run_hub import RUNTIME_STATUS, RunEvent, format_sse, stream_frames
from app.services.local_run_hub import hub as run_hub
from app.services.task_execution_service import LocalRuntimeUnavailableError

router = APIRouter(
    prefix="/local",
    tags=["Local execution"],
)


@router.get("/status", response_model=LocalStatusResponse)
async def local_status(
    request: Request,
    policy: LocalAccessPolicy = Depends(get_local_access_policy),
):
    """Tells the UI whether to offer local execution. Never runs the CLI for a
    caller the policy refuses."""
    if not settings.LOCAL_EXECUTION_ENABLED:
        return LocalStatusResponse(enabled=False, available=False)

    try:
        policy.authorize(request)
    except LocalAccessDenied as exc:
        return LocalStatusResponse(enabled=True, available=False, reason=exc.reason)

    cli = await local_claude_runner.get_status()
    ready = cli.installed and cli.signed_in is True
    return LocalStatusResponse(
        enabled=True,
        available=ready,
        reason=None if ready else cli.error,
        cli_version=cli.version,
        tools_enabled=app_settings.get("LOCAL_TOOLS_ENABLED"),
    )


@router.get("/tasks", response_model=list[TaskResponse])
async def list_local_tasks(
    user_id: str,
    access: LocalAccessGrant = Depends(require_local_access),
    db: AsyncSession = Depends(get_db),
):
    return await task_execution_service.list_local_tasks_for_user(
        db, user_id, access=access
    )


@router.post("/tasks", response_model=TaskResponse)
async def create_local_task(
    payload: LocalTaskCreateRequest,
    access: LocalAccessGrant = Depends(require_local_access),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await task_execution_service.execute_local_task(
            db,
            access=access,
            user_id=payload.user_id,
            prompt_text=payload.prompt_text,
        )
    except LocalRuntimeUnavailableError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.get("/tasks/{task_id}", response_model=TaskResponse)
async def get_local_task(
    task_id: int,
    access: LocalAccessGrant = Depends(require_local_access),
    db: AsyncSession = Depends(get_db),
):
    task = await task_execution_service.get_task(db, task_id, access=access)
    if task is None or task.provider != task_execution_service.LOCAL_PROVIDER:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


async def _local_task_or_404(db: AsyncSession, task_id: int, access: LocalAccessGrant):
    task = await task_execution_service.get_task(db, task_id, access=access)
    if task is None or task.provider != task_execution_service.LOCAL_PROVIDER:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.get("/tasks/{task_id}/approvals", response_model=list[LocalApprovalResponse])
async def list_local_approvals(
    task_id: int,
    access: LocalAccessGrant = Depends(require_local_access),
    db: AsyncSession = Depends(get_db),
):
    """Tool calls this run is waiting for a person to allow or deny."""
    await _local_task_or_404(db, task_id, access)
    return approval_broker.list_pending(task_id)


@router.post("/tasks/{task_id}/approvals/{approval_id}")
async def answer_local_approval(
    task_id: int,
    approval_id: str,
    payload: LocalApprovalAnswer,
    access: LocalAccessGrant = Depends(require_local_access),
    db: AsyncSession = Depends(get_db),
):
    """Allow or deny one tool call. The first answer wins; the tool runs with
    exactly the input that `list_local_approvals` showed."""
    await _local_task_or_404(db, task_id, access)
    try:
        approval_broker.answer(task_id, approval_id, payload.decision)
    except ApprovalNotFound:
        raise HTTPException(
            status_code=404, detail="That request is no longer waiting for an answer"
        )
    return {"status": "recorded", "decision": payload.decision}


async def _frames_from_task(task) -> AsyncIterator[str]:
    """Answer for a run this process is not tracking (finished a while ago,
    started before a restart, or on another worker): the task row says what
    happened, and a run that is still marked running is reported as not live
    so the client polls instead of waiting."""
    now = datetime.now(UTC).isoformat()
    if task.status in task_execution_service.FINISHED_STATUSES:
        event_type, data = task_execution_service.terminal_event_for(task)
        yield format_sse(RunEvent(task.id, 1, event_type, data, now))
    else:
        yield format_sse(RunEvent(task.id, 0, RUNTIME_STATUS, {"state": "not_live"}, now))


@router.get("/tasks/{task_id}/events")
async def stream_local_task_events(
    task_id: int,
    access: LocalAccessGrant = Depends(require_local_access),
    db: AsyncSession = Depends(get_db),
):
    """Server-sent events for one local run. Streams are live only: nothing is
    stored or replayed, so after a disconnect read GET /local/tasks/{id}.
    Closing the stream never stops the run."""
    task = await task_execution_service.get_task(db, task_id, access=access)
    if task is None or task.provider != task_execution_service.LOCAL_PROVIDER:
        raise HTTPException(status_code=404, detail="Task not found")

    subscription = run_hub.subscribe(task_id)
    frames = _frames_from_task(task) if subscription is None else stream_frames(subscription)
    return StreamingResponse(
        frames,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )
