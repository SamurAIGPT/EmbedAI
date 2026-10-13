"""Who may start and read local (host) Claude runs.

Local execution runs a process on the machine hosting this API, so it must
not be reachable by other websites, tunnels or remote clients. All of that is
decided here and nowhere else: callers hold a `LocalAccessGrant`, which only a
policy can issue. To change the mechanism (for example a launcher-issued
token for a desktop build), implement `LocalAccessPolicy` and swap
`get_local_access_policy`; the runner and services stay the same.
"""

import ipaddress
from typing import Protocol

from fastapi import Depends, HTTPException, Request

from app.core.config import settings

_ISSUER = object()

# Present when a reverse proxy or tunnel forwarded the request. Such a request
# can arrive from a loopback address, so it is refused outright.
_FORWARDING_HEADERS = ("forwarded", "x-forwarded-for", "x-forwarded-host", "x-real-ip")


class LocalAccessDenied(Exception):
    def __init__(self, reason: str, status_code: int = 403) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code


class LocalAccessGrant:
    """Proof that a policy approved the current request. Not constructible
    outside this module."""

    __slots__ = ()

    def __init__(self, _issuer: object = None) -> None:
        if _issuer is not _ISSUER:
            raise TypeError("LocalAccessGrant can only be issued by a LocalAccessPolicy")


def _issue_grant() -> LocalAccessGrant:
    return LocalAccessGrant(_ISSUER)


class LocalAccessPolicy(Protocol):
    def authorize(self, request: Request) -> LocalAccessGrant:
        """Return a grant or raise LocalAccessDenied."""
        ...


def _hostname(value: str) -> str:
    """Host header / client address without its port, lowercased, with IPv6
    brackets kept so it compares equal to the configured entries."""
    value = value.strip().lower()
    if value.startswith("["):
        end = value.find("]")
        return value[: end + 1] if end != -1 else value
    return value.split(":", 1)[0]


def _is_loopback_address(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


class LoopbackPolicy:
    """The web build: only the machine's own browser may use local execution."""

    def authorize(self, request: Request) -> LocalAccessGrant:
        if not settings.LOCAL_EXECUTION_ENABLED:
            raise LocalAccessDenied(
                "Local execution is disabled. Set LOCAL_EXECUTION_ENABLED=true on the "
                "machine running the backend to enable it.",
                status_code=403,
            )

        client = request.client
        if client is None or not _is_loopback_address(client.host):
            raise LocalAccessDenied("Local execution is only available from this machine.")

        if any(header in request.headers for header in _FORWARDING_HEADERS):
            raise LocalAccessDenied("Local execution cannot be used through a proxy or tunnel.")

        host_header = request.headers.get("host")
        if host_header is None or _hostname(host_header) not in settings.LOCAL_ALLOWED_HOST_LIST:
            raise LocalAccessDenied("Local execution request has an untrusted Host header.")

        # Browsers send Origin on cross-site requests; same-origin GETs and
        # non-browser clients may omit it.
        origin = request.headers.get("origin")
        if origin is not None and origin not in settings.FRONTEND_ORIGIN_LIST:
            raise LocalAccessDenied("Local execution request has an untrusted Origin.")

        return _issue_grant()


def get_local_access_policy() -> LocalAccessPolicy:
    return LoopbackPolicy()


def require_local_access(
    request: Request,
    policy: LocalAccessPolicy = Depends(get_local_access_policy),
) -> LocalAccessGrant:
    try:
        return policy.authorize(request)
    except LocalAccessDenied as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.reason) from exc
