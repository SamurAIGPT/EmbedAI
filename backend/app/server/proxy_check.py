"""Tell the person running the server when a reverse proxy is not sending what the
server relies on. It never blocks or changes a request.

Any proxy works (Caddy, nginx, Traefik, a tunnel): the server reads only standard
forwarded headers. Two of them matter for a visit to a public name:

- `X-Forwarded-For`: without it every visitor looks like the proxy's own address,
  so they all share one sign-in lockout and one person's wrong guesses lock the
  owner out too.
- `X-Forwarded-Proto`: without it the server cannot tell an HTTPS visit from a plain
  one, so the sign-in cookie is not marked Secure.

The standard `Forwarded` header is not read by the server (it reads only the two headers
above), so it does not count: a proxy that sends only `Forwarded` is reported, with a hint that
says so. A visit by the machine's own names (localhost, 127.0.0.1) is local use and is never checked, and neither
is a name the server does not answer to.
Each problem is reported once per run, as a warning in the log.
"""

from starlette.types import ASGIApp, Receive, Scope, Send

from app.auth import request_info
from app.core.logging import get_logger

logger = get_logger(__name__)

EXPLANATIONS = {
    "X-Forwarded-For": (
        "every visitor looks like the proxy's own address, so they share one sign-in lockout "
        "and one person's wrong guesses can lock the owner out"
    ),
    "X-Forwarded-Proto": (
        "the server cannot tell an HTTPS visit from a plain one, so the sign-in cookie is not marked Secure"
    ),
}


def warn(message: str) -> None:
    logger.warning(message)


def missing_headers(scope: Scope) -> list[str]:
    """The forwarded headers a visit to a public name should carry and does not."""
    if scope["type"] != "http":
        return []
    hostname = request_info.request_hostname(scope)
    if not hostname or request_info.is_loopback_hostname(hostname):
        return []
    if not request_info.host_is_trusted(hostname):
        return []  # refused anyway; a stranger's Host header is never copied into the log
    present = {key for key, _ in scope.get("headers", [])}
    missing = []
    if b"x-forwarded-for" not in present:
        missing.append("X-Forwarded-For")
    if b"x-forwarded-proto" not in present:
        missing.append("X-Forwarded-Proto")
    return missing


def check_forwarded_headers(app: ASGIApp) -> ASGIApp:
    reported: set[str] = set()

    async def asgi(scope: Scope, receive: Receive, send: Send) -> None:
        for name in missing_headers(scope):
            if name not in reported:
                reported.add(name)
                host = request_info.request_hostname(scope)
                hint = (
                    " The request does carry a standard `Forwarded` header, but this server does not read it."
                    if any(key == b"forwarded" for key, _ in scope.get("headers", []))
                    else ""
                )
                warn(
                    f"A request for {host} arrived without {name}: {EXPLANATIONS[name]}.{hint} "
                    f"Set {name} in your reverse proxy (see the proxy rules in the self-hosting guide). "
                    "If you are not using a reverse proxy, put one in front: the server is meant to listen on loopback."
                )
        await app(scope, receive, send)

    return asgi
