"""Serve the exported web pages in front of the API.

The pages are public by design: the same files anyone can download, no secrets,
and `/login` and `/pair` have to load before there is a session. So they are
answered here, ahead of the API's sign-in check, and only for what is plainly a
page request: GET or HEAD on a path that is not the API, the webhook route or
the API's own documentation. Everything else, including every other method, goes
to the application unchanged and keeps its sign-in rules.

Nothing here knows about a particular reverse proxy. Redirects are relative, so
they stay correct whatever scheme and host the visitor used to get here.
"""

from urllib.parse import urlsplit

from starlette.exceptions import HTTPException
from starlette.responses import PlainTextResponse
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Paths the application answers, never the pages.
APP_PREFIXES = ("/api/", "/hooks/")
APP_PATHS = frozenset({"/api", "/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"})

PAGE_METHODS = frozenset({"GET", "HEAD"})

# A page must never be framed (a hidden frame could act on a sign-in or pairing
# page unseen) and need not tell the next site where the visitor came from.
PAGE_HEADERS = (
    (b"content-security-policy", b"frame-ancestors 'none'"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"cache-control", b"no-cache"),
)
# Next.js content-hashes everything under /_next/static/, so those URLs never change meaning.
IMMUTABLE = (b"cache-control", b"public, max-age=31536000, immutable")


def is_page_request(scope: Scope) -> bool:
    if scope["type"] != "http" or scope["method"] not in PAGE_METHODS:
        return False
    path = scope["path"]
    return path not in APP_PATHS and not path.startswith(APP_PREFIXES)


def _relative(location: str) -> str:
    """`http://host/chat/?x=1` becomes `/chat/?x=1`: the visitor's own address
    is the only one that is right, and the proxy in front may have changed it."""
    parts = urlsplit(location)
    return parts.path + (f"?{parts.query}" if parts.query else "")


def serve_ui(app: ASGIApp, ui_dir) -> ASGIApp:
    """`app` with the pages in `ui_dir` answered before it. Raises if the folder is missing."""
    pages = StaticFiles(directory=ui_dir, html=True)

    async def asgi(scope: Scope, receive: Receive, send: Send) -> None:
        if not is_page_request(scope):
            await app(scope, receive, send)
            return

        immutable = scope["path"].startswith("/_next/static/")

        async def tidy(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                status = message["status"]
                if 300 <= status < 400:
                    headers = [
                        (k, _relative(v.decode("latin-1")).encode("latin-1")) if k.lower() == b"location" else (k, v)
                        for k, v in headers
                    ]
                else:
                    html = any(k.lower() == b"content-type" and v.startswith(b"text/html") for k, v in headers)
                    if html:
                        replaced = {k for k, _ in PAGE_HEADERS}
                        headers = [(k, v) for k, v in headers if k.lower() not in replaced] + list(PAGE_HEADERS)
                    elif immutable and status == 200:
                        headers = [(k, v) for k, v in headers if k.lower() != IMMUTABLE[0]] + [IMMUTABLE]
                    headers.append((b"x-content-type-options", b"nosniff"))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await pages(scope, receive, tidy)
        except HTTPException as exc:
            # StaticFiles leaves this to the framework's error handling, which is not
            # here: without a 404.html in the build, a missing page would be a 500.
            await PlainTextResponse(exc.detail, status_code=exc.status_code)(scope, receive, tidy)

    return asgi
