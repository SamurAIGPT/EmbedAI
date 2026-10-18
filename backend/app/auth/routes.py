"""Owner setup, sign-in, pairing and devices (docs/AUTH.md).

The middleware has already checked Host, Origin, the session and its scope
for everything here except the public routes, which do their own checks.
"""

import re
import uuid
from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import credentials, limiter, request_info, store
from app.auth.middleware import (
    COOKIE_NAME,
    principal_of,
    session_cookie,
)
from app.auth.policy import Principal
from app.db.session import get_db
from app.desktop import profile
from app.services import link_service

router = APIRouter(prefix="/auth", tags=["Auth"])

# A Telegram user id, or the `web_` guest id the web UI used to make.
_LEGACY_IDENTITY = re.compile(r"^(web_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9]{1,20})$")
LINK_CODE_CLAIM_WINDOW = timedelta(hours=1)


def _error(status: int, code: str, message: str, headers: dict[str, str] | None = None) -> HTTPException:
    return HTTPException(status, {"error_code": code, "message": message}, headers=headers)


def require_json(request: Request) -> None:
    """A cross-site form cannot send JSON without a CORS preflight, so this
    closes login CSRF on the routes that run before any session exists."""
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type != "application/json":
        raise _error(415, "json_required", "Send this as application/json.")


def require_rate(request: Request) -> str:
    source = request_info.client_source(request.scope)
    if not limiter.auth_requests.allow(source):
        raise _error(429, "rate_limited", "Too many requests. Wait a minute.", {"Retry-After": "60"})
    return source


def current_principal(request: Request) -> Principal:
    principal = principal_of(request.scope)
    if principal is None:  # only reachable if the middleware is not installed
        raise _error(401, "auth_required", "Sign in to use this server.")
    return principal


def require_admin(principal: Principal = Depends(current_principal)) -> Principal:
    if not principal.is_admin:
        raise _error(403, "scope_denied", "This device needs the admin scope for that.")
    return principal


def _set_session_cookie(request: Request, response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=int(store.SESSION_MAX_AGE.total_seconds()),
        path="/",
        httponly=True,
        samesite="lax",
        secure=request_info.is_https(request.scope),
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, samesite="lax")


class SessionInfo(BaseModel):
    kind: str
    scopes: list[str]
    label: str | None = None
    session_id: int | None = None
    user_id: str | None = None
    csrf_token: str | None = None
    owner_configured: bool


class StatusResponse(BaseModel):
    owner_configured: bool
    setup_allowed: bool
    desktop: bool
    signed_in: bool


class PassphraseBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    passphrase: str = Field(max_length=credentials.MAX_PASSPHRASE_LENGTH)


async def _session_info(db: AsyncSession, principal: Principal, cookie: str | None) -> SessionInfo:
    return SessionInfo(
        kind=principal.kind,
        scopes=list(principal.scopes),
        label=principal.label,
        session_id=principal.session_id,
        user_id=await store.get_identity(db),
        csrf_token=credentials.csrf_token_for(cookie) if cookie and principal.kind == "browser" else None,
        owner_configured=await store.owner_configured(db),
    )


async def _browser_session(
    request: Request, response: Response, db: AsyncSession, *, label: str, scopes: str
) -> SessionInfo:
    # Signing in again replaces this browser's previous session rather than piling them up.
    previous = session_cookie(request.scope)
    if previous:
        await store.revoke_token(db, previous)
    token, record = await store.create_session(db, kind="browser", label=label, scopes=scopes)
    _set_session_cookie(request, response, token)
    principal = Principal(kind="browser", scopes=store.parse_scopes(record.scopes),
                          session_id=record.id, label=record.label)
    return await _session_info(db, principal, token)


@router.get("/status", response_model=StatusResponse)
async def auth_status(request: Request, db: AsyncSession = Depends(get_db)):
    configured = await store.owner_configured(db)
    signed_in = False
    cookie = session_cookie(request.scope)
    if cookie:
        signed_in = await store.authenticate(db, cookie, kind="browser") is not None
    return StatusResponse(
        owner_configured=configured,
        setup_allowed=not configured and not profile.active() and request_info.loopback_eligible(request.scope),
        desktop=profile.active(),
        signed_in=signed_in,
    )


@router.post("/setup", response_model=SessionInfo, dependencies=[Depends(require_json)])
async def setup_owner(
    body: PassphraseBody, request: Request, response: Response,
    db: AsyncSession = Depends(get_db), source: str = Depends(require_rate),
):
    """First run only, and only from this machine: anyone who could reach the
    server remotely could otherwise claim it. Remote installs use the CLI."""
    if not request_info.loopback_eligible(request.scope):
        raise _error(403, "setup_local_only",
                     "Set the owner passphrase on the server itself, or with the command "
                     "`python -m app.auth.cli set-passphrase`.")
    try:
        await store.set_passphrase(db, body.passphrase, first_time_only=True)
    except credentials.WeakPassphrase as exc:
        raise _error(422, "weak_passphrase", str(exc)) from exc
    except store.OwnerAlreadyConfigured as exc:
        raise _error(409, "owner_exists", "An owner passphrase is already set. Sign in instead.") from exc
    label = request_info.label_from_user_agent(request.headers.get("user-agent"))
    return await _browser_session(request, response, db, label=label, scopes=store.scopes_for("admin"))


@router.post("/login", response_model=SessionInfo, dependencies=[Depends(require_json)])
async def login(
    body: PassphraseBody, request: Request, response: Response,
    db: AsyncSession = Depends(get_db), source: str = Depends(require_rate),
):
    wait = limiter.login_lockout.retry_after(source)
    if wait:
        raise _error(429, "locked_out", f"Too many wrong passphrases. Try again in {wait} seconds.",
                     {"Retry-After": str(wait)})
    if not await store.owner_configured(db):
        raise _error(409, "owner_not_configured", "No owner passphrase is set yet.")
    if not await store.verify_owner(db, body.passphrase):
        limiter.login_lockout.failure(source)
        raise _error(401, "wrong_passphrase", "That passphrase is not right.")
    limiter.login_lockout.success(source)
    label = request_info.label_from_user_agent(request.headers.get("user-agent"))
    return await _browser_session(request, response, db, label=label, scopes=store.scopes_for("admin"))


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    cookie = session_cookie(request.scope)
    if cookie:
        await store.revoke_token(db, cookie)
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        await store.revoke_token(db, authorization[7:].strip())
    _clear_session_cookie(response)
    response.status_code = 204
    return response


@router.get("/session", response_model=SessionInfo)
async def get_session(
    request: Request, db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(current_principal),
):
    return await _session_info(db, principal, session_cookie(request.scope))


# --- pairing ----------------------------------------------------------------


class PairingCodeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(default="", max_length=200)
    scope: Literal["admin", "chat"] = "admin"


class PairingCodeResponse(BaseModel):
    id: int
    code: str
    display_code: str
    scope: str
    label: str
    expires_at: datetime


@router.post("/pairing-codes", response_model=PairingCodeResponse, status_code=201)
async def create_pairing_code(
    body: PairingCodeBody, db: AsyncSession = Depends(get_db),
    _: Principal = Depends(require_admin), __: str = Depends(require_rate),
):
    try:
        code, record = await store.create_pairing_code(db, label=body.label, scope=body.scope)
    except store.TooManyOpenCodes as exc:
        raise _error(429, "too_many_codes", "Several pairing codes are already open. Use one or wait for them to expire.") from exc
    return PairingCodeResponse(
        id=record.id, code=code, display_code=credentials.format_pairing_code(code),
        scope=body.scope, label=record.label, expires_at=record.expires_at,
    )


class PairBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(max_length=64)
    label: str = Field(default="", max_length=200)
    # A browser takes the session as its cookie; any other client gets a token once.
    cookie: bool = False


class PairResponse(BaseModel):
    token: str | None = None
    session: SessionInfo


@router.post("/pair", response_model=PairResponse, dependencies=[Depends(require_json)])
async def pair(
    body: PairBody, request: Request, response: Response,
    db: AsyncSession = Depends(get_db), source: str = Depends(require_rate),
):
    wait = limiter.pair_lockout.retry_after(source)
    if wait:
        raise _error(429, "locked_out", f"Too many wrong codes. Try again in {wait} seconds.",
                     {"Retry-After": str(wait)})
    if await store.count_active_sessions(db) >= store.MAX_ACTIVE_SESSIONS:
        raise _error(409, "too_many_devices", "Too many devices are signed in. Revoke one first.")
    record = await store.redeem_pairing_code(db, body.code)
    if record is None:
        limiter.pair_lockout.failure(source)
        raise _error(401, "pairing_code_invalid", "That code is wrong, expired or already used. Make a new one.")
    limiter.pair_lockout.success(source)
    label = store.clean_label(body.label or record.label,
                              fallback=request_info.label_from_user_agent(request.headers.get("user-agent")))
    if body.cookie:
        info = await _browser_session(request, response, db, label=label, scopes=record.scopes)
        return PairResponse(session=info)
    token, session = await store.create_session(db, kind="device", label=label, scopes=record.scopes)
    principal = Principal(kind="device", scopes=store.parse_scopes(session.scopes),
                          session_id=session.id, label=session.label)
    return PairResponse(token=token, session=await _session_info(db, principal, None))


# --- devices ----------------------------------------------------------------


class DeviceResponse(BaseModel):
    id: int
    kind: str
    label: str
    scopes: list[str]
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    current: bool


@router.get("/devices", response_model=list[DeviceResponse])
async def list_devices(db: AsyncSession = Depends(get_db), principal: Principal = Depends(require_admin)):
    return [
        DeviceResponse(
            id=s.id, kind=s.kind, label=s.label, scopes=list(s.scopes), created_at=s.created_at,
            last_seen_at=s.last_seen_at, expires_at=s.expires_at, current=s.id == principal.session_id,
        )
        for s in await store.list_active_sessions(db)
    ]


@router.delete("/devices/{device_id}", status_code=204)
async def revoke_device(
    device_id: int, response: Response, db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_admin),
):
    if not await store.revoke_session(db, device_id):
        raise _error(404, "device_not_found", "No such device, or it was already revoked.")
    if device_id == principal.session_id and principal.kind == "browser":
        _clear_session_cookie(response)
    response.status_code = 204
    return response


# --- web identity -----------------------------------------------------------


class IdentityBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Exactly one of these.
    link_code: str | None = Field(default=None, max_length=32)
    guest: bool = False
    legacy_user_id: str | None = Field(default=None, max_length=64)


class IdentityResponse(BaseModel):
    user_id: str | None


@router.put("/identity", response_model=IdentityResponse)
async def set_identity(body: IdentityBody, db: AsyncSession = Depends(get_db), _: Principal = Depends(require_admin)):
    """Who the web UI acts as. Set from a Telegram link code this server saw
    claimed, a new guest id, or (once, while unset) the id an older version of
    the web UI kept in the browser."""
    chosen = [body.link_code is not None, body.guest, body.legacy_user_id is not None]
    if sum(chosen) != 1:
        raise _error(422, "identity_choice", "Send exactly one of link_code, guest or legacy_user_id.")
    if body.link_code is not None:
        record = await link_service.get_code(db, body.link_code.strip().upper())
        if (
            record is None or record.claimed_at is None or not record.user_id
            or store.utcnow() - record.claimed_at > LINK_CODE_CLAIM_WINDOW
        ):
            raise _error(409, "link_code_not_claimed", "That Telegram link code has not been claimed.")
        return IdentityResponse(user_id=await store.set_identity(db, record.user_id))
    if body.guest:
        return IdentityResponse(user_id=await store.set_identity(db, f"web_{uuid.uuid4()}"))
    legacy = (body.legacy_user_id or "").strip()
    if not _LEGACY_IDENTITY.fullmatch(legacy):
        raise _error(422, "legacy_identity_invalid", "That is not an id the web UI made.")
    return IdentityResponse(user_id=await store.set_identity(db, legacy, only_if_unset=True))


@router.delete("/identity", response_model=IdentityResponse)
async def clear_identity(db: AsyncSession = Depends(get_db), _: Principal = Depends(require_admin)):
    return IdentityResponse(user_id=await store.set_identity(db, None))
