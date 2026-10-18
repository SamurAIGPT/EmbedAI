"""Authentication for every request (docs/AUTH.md).

Pure ASGI, like LaunchAuthMiddleware, so a route added later is covered without
anyone remembering a dependency, and a refused request for a path that does not
exist looks the same as one that does.

Order of checks:
1. The desktop profile: LaunchAuthMiddleware already demanded the launch token,
   so this middleware steps aside and the request acts as the owner.
2. Host must be in TRUSTED_HOSTS (DNS rebinding), and Origin, when present,
   must be the frontend's or this server's own.
3. Public routes pass (health, sign-in, pairing exchange, the Boat hook).
4. A presented credential decides: a bearer token for a paired device, the
   session cookie for a browser. An invalid one is refused; it never falls back
   to loopback trust. A cookie write needs the CSRF header.
5. No credential: loopback trust, only when LOOPBACK_TRUST allows it.
"""

import hmac
import json

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.auth import credentials, request_info, store
from app.auth.policy import Principal, is_public, required_scope
from app.core.config import settings
from app.desktop import profile

COOKIE_NAME = "od_session"
CSRF_HEADER = b"x-csrf-token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

DESKTOP_PRINCIPAL = Principal(kind="desktop")
LOOPBACK_PRINCIPAL = Principal(kind="loopback")


def _cookie(scope: Scope, name: str) -> str | None:
    found = None
    for raw in request_info.header_values(scope, b"cookie"):
        for part in raw.split(";"):
            key, sep, value = part.strip().partition("=")
            if sep and key == name:
                if found is not None:
                    return None  # two cookies of our name: refuse to guess
                found = value.strip()
    return found


def _bearer(scope: Scope) -> str | None:
    values = request_info.header_values(scope, b"authorization")
    if len(values) != 1:
        return None
    kind, _, token = values[0].strip().partition(" ")
    return token.strip() if kind.lower() == "bearer" and token.strip() else None


def session_cookie(scope: Scope) -> str | None:
    return _cookie(scope, COOKIE_NAME)


def loopback_trusted(scope: Scope) -> bool:
    mode = settings.LOOPBACK_TRUST
    if mode == "off" or not request_info.loopback_eligible(scope):
        return False
    return mode == "all" or not request_info.from_browser(scope)


def set_principal(scope: Scope, principal: Principal) -> None:
    scope.setdefault("state", {})["auth"] = principal


def principal_of(scope: Scope) -> Principal | None:
    state = scope.get("state") or {}
    return state.get("auth")


async def _refuse(send: Send, status: int, code: str, message: str, *, clear_cookie: bool = False) -> None:
    body = json.dumps({"detail": {"error_code": code, "message": message}}).encode()
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
    if status == 401:
        headers.append((b"www-authenticate", b'Bearer realm="open-dots"'))
    if clear_cookie:
        headers.append((b"set-cookie", f"{COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0".encode()))
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


async def _close_socket(receive: Receive, send: Send) -> None:
    message: Message = await receive()
    if str(message.get("type", "")).endswith(".connect"):
        await send({"type": "websocket.close", "code": 1008})


class AuthMiddleware:
    def __init__(self, app: ASGIApp, session_factory: async_sessionmaker[AsyncSession] | None = None) -> None:
        self.app = app
        self._session_factory = session_factory

    def sessions(self) -> async_sessionmaker[AsyncSession]:
        if self._session_factory is None:
            from app.db.session import AsyncSessionLocal

            self._session_factory = AsyncSessionLocal
        return self._session_factory

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        if profile.active():
            set_principal(scope, DESKTOP_PRINCIPAL)
            await self.app(scope, receive, send)
            return

        refusal = await self._authorize(scope)
        if refusal is None:
            await self.app(scope, receive, send)
        elif scope["type"] == "websocket":
            await _close_socket(receive, send)
        else:
            status, code, message, clear = refusal
            await _refuse(send, status, code, message, clear_cookie=clear)

    async def _authorize(self, scope: Scope) -> tuple[int, str, str, bool] | None:
        hostname = request_info.request_hostname(scope)
        if hostname is None or not request_info.host_is_trusted(hostname):
            return (421, "untrusted_host",
                    "This server does not answer to that host name. Add it to TRUSTED_HOSTS.", False)
        origin = request_info.header(scope, b"origin")
        if origin is not None and (
            len(request_info.header_values(scope, b"origin")) != 1
            or not request_info.origin_is_trusted(scope, origin)
        ):
            return (403, "untrusted_origin", "Requests from this page's address are not allowed.", False)

        method = scope.get("method", "GET").upper()
        path = scope.get("path", "")
        if is_public(method, path):
            return None

        bearer = _bearer(scope)
        cookie = session_cookie(scope) if bearer is None else None
        if bearer is not None or cookie is not None:
            async with self.sessions()() as db:
                session = await store.authenticate(
                    db, bearer if bearer is not None else cookie, kind="device" if bearer is not None else "browser"
                )
            if session is None:
                return (401, "session_invalid",
                        "This sign-in has expired or was revoked. Sign in or pair this device again.",
                        cookie is not None)
            if cookie is not None and method not in SAFE_METHODS:
                presented = request_info.header_values(scope, CSRF_HEADER)
                expected = credentials.csrf_token_for(cookie)
                if len(presented) != 1 or not hmac.compare_digest(presented[0].encode(), expected.encode()):
                    return (403, "csrf_failed", "Missing or wrong CSRF token. Reload the page.", False)
            needed = required_scope(method, path)
            if needed not in session.scopes:
                return (403, "scope_denied", f"This device needs the {needed} scope for that.", False)
            set_principal(scope, Principal(kind=session.kind, scopes=session.scopes,
                                           session_id=session.id, label=session.label))
            return None

        if loopback_trusted(scope):
            set_principal(scope, LOOPBACK_PRINCIPAL)
            return None
        return (401, "auth_required", "Sign in to use this server.", False)
