"""Launcher-issued authentication for the desktop profile (D-012 decision 5).

The desktop launcher makes a random token for each launch and hands it to the
backend before the backend listens. Every request must then carry it in the
`X-Open-Dots-Token` header. Nothing is public: the page's own files need it too,
because the launcher's browser window attaches the header to every request it
makes to this server.

This is pure ASGI middleware rather than a dependency on each router, so a
route added later is covered without anyone remembering to ask, and so a request
for a path that does not exist is refused the same way as one that does (an
unauthenticated caller learns nothing about the routes). It also covers
WebSocket connections, which this app does not use today.

The middleware only acts while the desktop profile is active, so server and
headless installs behave exactly as before. While the profile is active there
is no way to turn it off: if no token was installed, everything is refused.
"""

import hmac
import re

from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from app.desktop import profile

HEADER = "x-open-dots-token"
_HEADER_BYTES = HEADER.encode("ascii")

MIN_TOKEN_LENGTH = 32
_TOKEN_PATTERN = re.compile(r"[\x21-\x7e]+")  # visible ASCII: safe to carry in a header

_token: bytes | None = None


class InvalidLaunchToken(ValueError):
    pass


def check_token(token: str) -> None:
    if len(token) < MIN_TOKEN_LENGTH or not _TOKEN_PATTERN.fullmatch(token):
        raise InvalidLaunchToken(
            f"The launch token must be at least {MIN_TOKEN_LENGTH} visible ASCII characters."
        )


def install(token: str) -> None:
    """Keep the token in memory for the life of the process. It is never written
    anywhere, put in the environment or logged."""
    global _token
    if _token is not None:
        raise RuntimeError("A launch token is already installed.")
    check_token(token)
    _token = token.encode("ascii")


def proof_for_port(port: int) -> str:
    """What the backend tells the launcher about the port it bound: an HMAC of
    the port keyed by the token. Only a process that holds the token can make it,
    so the launcher can tell this backend's ready line from any other text on the
    same stream before it sends the token anywhere."""
    if _token is None:
        raise RuntimeError("No launch token is installed.")
    return hmac.new(_token, f"ready:{port}".encode("ascii"), "sha256").hexdigest()


def _authorized(scope: Scope) -> bool:
    if _token is None:
        return False
    presented = [value for name, value in scope["headers"] if name == _HEADER_BYTES]
    # Exactly one header: a request that repeats it is not one the launcher made.
    return len(presented) == 1 and hmac.compare_digest(presented[0], _token)


_REFUSAL_PAGE = (
    "<!doctype html><meta charset=utf-8><title>Open Duet</title>"
    "<body style=\"font:16px system-ui,sans-serif;max-width:32rem;margin:4rem auto;padding:0 1rem\">"
    "<h1 style=\"font-size:1.2rem\">Open this from the Open Duet app</h1>"
    "<p>This address only works inside the Open Duet window, which keeps your conversations "
    "private to it. Use that window instead of a browser tab.</p>"
)


def _wants_a_page(scope: Scope) -> bool:
    """A browser navigation (not a request the page itself makes) asks for HTML."""
    if scope["method"] not in ("GET", "HEAD"):
        return False
    accept = next((value for name, value in scope["headers"] if name == b"accept"), b"")
    return b"text/html" in accept


def _refusal(scope: Scope) -> Response:
    if _token is not None and _wants_a_page(scope):
        return HTMLResponse(_REFUSAL_PAGE, status_code=401)
    if _token is None:
        return JSONResponse(
            status_code=503,
            content={
                "detail": {
                    "error_code": "launch_token_unavailable",
                    "message": "This backend has no launch token, so it refuses every request.",
                }
            },
        )
    return JSONResponse(
        status_code=401,
        content={
            "detail": {
                "error_code": "launch_token_required",
                "message": "This address only works from inside the Open Duet app.",
            }
        },
    )


class LaunchAuthMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # The lifespan scope is the server talking to the app, not a client.
        if scope["type"] == "lifespan" or not profile.active():
            await self.app(scope, receive, send)
            return
        if scope["type"] == "http":
            if _authorized(scope):
                await self.app(scope, receive, send)
            else:
                await _refusal(scope)(scope, receive, send)
            return
        if scope["type"] == "websocket":
            if _authorized(scope):
                await self.app(scope, receive, send)
            else:
                await send({"type": "websocket.close", "code": 1008})
            return
        # An unknown scope type is refused rather than passed on.
