import asyncio

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.ext.asyncio import AsyncSession

from boat_sdk.exceptions import ApiException
from app.core.logging import get_logger
import json

logger = get_logger(__name__)

from app.db.session import get_db, AsyncSessionLocal
from app.db.transactions import release_snapshot

from app.schemas.sandbox import (
    SandboxCreateRequest,
    SandboxResponse,
    AuthLoginStartResponse,
    AuthLoginSubmitRequest,
    AuthLoginSubmitResponse,
    AuthStatusResponse,
    DeviceLoginStartResponse,
)

from app.services.box_operations import ascii_box_service
from app.services import user_sandbox_service, user_credential_service, auth_flow_service
from app.services.auth_flow_service import (
    UnsupportedProviderError,
    LoginTimeoutError,
    LoginFailedError,
    BoxNotRunningError,
    BoxCommandError,
)


router = APIRouter(
    prefix="/sandboxes",
    tags=["Sandboxes"],
)


@router.post("", response_model=SandboxResponse)
async def create_sandbox(
    payload: SandboxCreateRequest,
    db: AsyncSession = Depends(get_db),
):
    try:
        result = await run_in_threadpool(
            ascii_box_service.create_box,
            ttl_seconds=payload.ttl_seconds,
        )

        box = result

        record = await user_sandbox_service.create(
            db,
            user_id=payload.user_id,
            box_id=box.id,
            state=box.state,
            machine_type=box.type,
            ttl_seconds=payload.ttl_seconds,
        )

        return SandboxResponse(
            box_id=box.id,
            state=box.state,
            machine_type=box.type,
            user_id=record.user_id,
            ttl_seconds=record.ttl_seconds,
            expires_at=record.expires_at,
            is_active=record.is_active,
        )

    except ApiException as exc:
        raise HTTPException(
            status_code=502,
            detail=f"ASCII error: {exc.reason}",
        )


@router.post("/ensure", response_model=SandboxResponse)
async def ensure_sandbox(
    payload: SandboxCreateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Returns the caller's existing active sandbox, creating one if they
    don't have one yet. Lets the web UI and Telegram bot share a sandbox
    for the same (linked) user instead of each spinning up their own.
    """
    try:
        record, box = await user_sandbox_service.ensure(
            db, user_id=payload.user_id, ttl_seconds=payload.ttl_seconds
        )
    except ApiException as exc:
        raise HTTPException(status_code=502, detail=f"ASCII error: {exc.reason}") from exc

    return SandboxResponse(
        box_id=box.id,
        state=box.state,
        machine_type=box.type,
        user_id=record.user_id if record else None,
        ttl_seconds=record.ttl_seconds if record else None,
        expires_at=record.expires_at if record else None,
        is_active=record.is_active if record else None,
    )


@router.get("/health")
def health_check():
    return {"status": "healthy"}


@router.get("/{box_id}", response_model=SandboxResponse)
async def get_sandbox(box_id: str, db: AsyncSession = Depends(get_db)):
    try:
        result = await run_in_threadpool(
            ascii_box_service.get_box,
            box_id,
        )

        box = result.sandbox

        record = await user_sandbox_service.update_state(
            db, box_id, state=box.state
        )

        return SandboxResponse(
            box_id=box.id,
            state=box.state,
            machine_type=box.type,
            user_id=record.user_id if record else None,
            ttl_seconds=record.ttl_seconds if record else None,
            expires_at=record.expires_at if record else None,
            is_active=record.is_active if record else None,
        )

    except ApiException as exc:
        raise HTTPException(
            status_code=502,
            detail=f"ASCII error: {exc.reason}",
        )


@router.post("/{box_id}/stop")
async def stop_sandbox(box_id: str, db: AsyncSession = Depends(get_db)):
    try:
        result = await run_in_threadpool(
            ascii_box_service.stop_box,
            box_id,
        )

        await user_sandbox_service.update_state(
            db, box_id, state=result.status
        )

        return {
            "box_id": box_id,
            "status": result.status,
        }

    except ApiException as exc:
        raise HTTPException(
            status_code=502,
            detail=f"ASCII error: {exc.reason}",
        )


@router.post("/{box_id}/resume", response_model=SandboxResponse)
async def resume_sandbox(box_id: str, db: AsyncSession = Depends(get_db)):
    try:
        result = await run_in_threadpool(
            ascii_box_service.resume_box,
            box_id,
        )

        box = result

        record = await user_sandbox_service.update_state(
            db, box_id, state=box.state
        )

        return SandboxResponse(
            box_id=box.id,
            state=box.state,
            machine_type=box.type,
            user_id=record.user_id if record else None,
            ttl_seconds=record.ttl_seconds if record else None,
            expires_at=record.expires_at if record else None,
            is_active=record.is_active if record else None,
        )

    except ApiException as exc:
        raise HTTPException(
            status_code=502,
            detail=f"ASCII error: {exc.reason}",
        )

@router.post("/{box_id}/test-command")
async def test_command(box_id: str):
    result = await run_in_threadpool(
        ascii_box_service.run_command,
        box_id,
        'claude auth status',
        #'tmux new-session -d -s claude_auth -x 500 -y 50 "claude auth login"'
    )

    logger.info(f"Command result: {result.to_json()}")
    parsed_result = json.loads(result.to_json())

    return {
        "stdout": parsed_result.get("stdout"),
        "stderr": parsed_result.get("stderr"),
        "exit_code": parsed_result.get("exit_code"),
        "success": parsed_result.get("success"),
    }


@router.post("/{box_id}/test-write-file")
async def test_write_file(box_id: str, payload: dict):
    result = await run_in_threadpool(
        ascii_box_service.write_file,
        box_id,
        payload["path"],
        payload["content"],
    )

    return result.to_dict()


@router.post("/{box_id}/auth/{provider}/start", response_model=AuthLoginStartResponse)
async def start_auth_login(box_id: str, provider: str):
    try:
        login_url = await run_in_threadpool(
            auth_flow_service.start_login, box_id, provider
        )
        return AuthLoginStartResponse(login_url=login_url)

    except UnsupportedProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except BoxNotRunningError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except BoxCommandError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except LoginTimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc))


@router.post(
    "/{box_id}/auth/{provider}/device/start", response_model=DeviceLoginStartResponse
)
async def start_device_auth_login(box_id: str, provider: str):
    """Starts a device-flow login (GitHub) — returns a URL + one-time code
    to show the user, then finishes saving the credential in the background
    once they approve it. Poll .../status to find out when that happens.
    """
    try:
        login = await run_in_threadpool(
            auth_flow_service.start_device_login, box_id, provider
        )
    except UnsupportedProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except BoxNotRunningError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except BoxCommandError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except LoginTimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc))

    asyncio.create_task(_finish_device_auth_login(box_id, provider))
    return DeviceLoginStartResponse(url=login.url, code=login.code)


async def _finish_device_auth_login(box_id: str, provider: str) -> None:
    try:
        credential_content = await run_in_threadpool(
            auth_flow_service.await_device_login, box_id, provider
        )
    except (LoginFailedError, LoginTimeoutError, BoxCommandError) as exc:
        logger.info(f"Device login for {provider} on {box_id} did not complete: {exc}")
        return

    async with AsyncSessionLocal() as db:
        sandbox_record = await user_sandbox_service.get_by_box_id(db, box_id)
        if sandbox_record is None:
            return

        await user_credential_service.save_token(
            db,
            user_id=sandbox_record.user_id,
            provider=provider,
            file_path=auth_flow_service.PROVIDERS[provider]["credential_path"],
            plaintext=credential_content,
        )


@router.get("/{box_id}/auth/{provider}/status", response_model=AuthStatusResponse)
async def get_auth_status(box_id: str, provider: str, db: AsyncSession = Depends(get_db)):
    sandbox_record = await user_sandbox_service.get_by_box_id(db, box_id)
    if sandbox_record is None:
        raise HTTPException(status_code=404, detail="Sandbox not found")

    decrypted = await user_credential_service.get_decrypted(
        db, sandbox_record.user_id, provider
    )
    return AuthStatusResponse(connected=decrypted is not None)


@router.post(
    "/{box_id}/auth/{provider}/submit", response_model=AuthLoginSubmitResponse
)
async def submit_auth_login(
    box_id: str,
    provider: str,
    payload: AuthLoginSubmitRequest,
    db: AsyncSession = Depends(get_db),
):
    sandbox_record = await user_sandbox_service.get_by_box_id(db, box_id)
    if sandbox_record is None:
        raise HTTPException(status_code=404, detail="Sandbox not found")
    await release_snapshot(db)  # the login below can take tens of seconds

    try:
        credential_content = await run_in_threadpool(
            auth_flow_service.submit_code, box_id, provider, payload.code
        )

        await user_credential_service.save_token(
            db,
            user_id=sandbox_record.user_id,
            provider=provider,
            file_path=auth_flow_service.PROVIDERS[provider]["credential_path"],
            plaintext=credential_content,
        )

        return AuthLoginSubmitResponse(status="connected")

    except UnsupportedProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except BoxNotRunningError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except BoxCommandError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except LoginFailedError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except LoginTimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc))


@router.delete("/{box_id}")
async def delete_sandbox(box_id: str, db: AsyncSession = Depends(get_db)):
    try:
        await run_in_threadpool(
            ascii_box_service.delete_box,
            box_id,
        )

        await user_sandbox_service.deactivate(db, box_id)

        return {
            "box_id": box_id,
            "deleted": True,
        }

    except ApiException as exc:
        raise HTTPException(
            status_code=502,
            detail=f"ASCII error: {exc.reason}",
        )
