from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db
from app.schemas.local import LocalStatusResponse, LocalTaskCreateRequest
from app.schemas.task import TaskResponse
from app.services import local_claude_runner, task_execution_service
from app.services.local_access_policy import (
    LocalAccessDenied,
    LocalAccessGrant,
    LocalAccessPolicy,
    get_local_access_policy,
    require_local_access,
)
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
