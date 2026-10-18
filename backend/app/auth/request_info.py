"""What a request says about where it came from, read from the ASGI scope.

Shared by the middleware and the auth routes so both judge a request the same
way. Nothing here trusts a header more than the socket: forwarding headers are
only ever a reason to trust a request less.
"""

import ipaddress
from urllib.parse import urlsplit

from starlette.types import Scope

from app.core.config import settings

# Any of these means a proxy or tunnel handled the request, so its loopback
# socket says nothing about the real client.
PROXY_HEADERS = (b"forwarded", b"x-forwarded-for", b"x-forwarded-host", b"x-forwarded-proto", b"x-real-ip")

# A browser sends these on every request to this server and a page cannot
# remove them; curl, scripts and httpx send none.
_BROWSER_HEADERS = (b"sec-fetch-site", b"sec-fetch-dest", b"origin")


def header_values(scope: Scope, name: bytes) -> list[str]:
    return [value.decode("latin-1") for key, value in scope.get("headers", []) if key == name]


def header(scope: Scope, name: bytes) -> str | None:
    values = header_values(scope, name)
    return values[0] if values else None


def hostname_of(host: str) -> str:
    """A Host header value (or URL netloc) without its port, lowercased; IPv6
    keeps its brackets so it compares equal to "[::1]" in settings."""
    value = host.strip().lower()
    if value.startswith("["):
        end = value.find("]")
        return value[: end + 1] if end != -1 else ""
    if value.count(":") > 1:  # bare IPv6 is not a valid Host header
        return ""
    return value.split(":", 1)[0].rstrip(".")


def is_loopback_hostname(hostname: str) -> bool:
    if hostname == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname.strip("[]")).is_loopback
    except ValueError:
        return False


def host_is_trusted(hostname: str, trusted: list[str] | None = None) -> bool:
    if not hostname:
        return False
    for entry in trusted if trusted is not None else settings.TRUSTED_HOST_LIST:
        if entry.startswith("*."):
            suffix = entry[1:]
            if hostname.endswith(suffix) and len(hostname) > len(suffix):
                return True
        elif hostname == entry:
            return True
    return False


def request_hostname(scope: Scope) -> str | None:
    """None when the Host header is missing or repeated."""
    values = header_values(scope, b"host")
    if len(values) != 1:
        return None
    return hostname_of(values[0])


def has_proxy_headers(scope: Scope) -> bool:
    return any(key in PROXY_HEADERS for key, _ in scope.get("headers", []))


def peer_address(scope: Scope) -> str | None:
    client = scope.get("client")
    return client[0] if client else None


def peer_is_loopback(scope: Scope) -> bool:
    peer = peer_address(scope)
    return peer is not None and is_loopback_hostname(peer)


def from_browser(scope: Scope) -> bool:
    return any(key in _BROWSER_HEADERS for key, _ in scope.get("headers", []))


def is_https(scope: Scope) -> bool:
    if scope.get("scheme") in ("https", "wss"):
        return True
    # Believed only from a proxy on this machine (the usual TLS front).
    if peer_is_loopback(scope):
        proto = (header(scope, b"x-forwarded-proto") or "").split(",")[0].strip().lower()
        return proto == "https"
    return False


def own_origin(scope: Scope) -> str | None:
    host = header(scope, b"host")
    if not host:
        return None
    return f"{'https' if is_https(scope) else 'http'}://{host.strip().lower()}"


def _normalize_origin(origin: str) -> str | None:
    try:
        parts = urlsplit(origin.strip())
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.netloc or parts.path not in ("", "/"):
        return None
    return f"{parts.scheme}://{parts.netloc.lower()}"


def origin_is_trusted(scope: Scope, origin: str) -> bool:
    """The frontend's configured origins, or this server's own origin."""
    normalized = _normalize_origin(origin)
    if normalized is None:
        return False
    allowed = {o for o in (_normalize_origin(x) for x in settings.FRONTEND_ORIGIN_LIST) if o}
    own = own_origin(scope)
    if own:
        allowed.add(own)
    if normalized not in allowed:
        return False
    return host_is_trusted(hostname_of(urlsplit(normalized).netloc))


def loopback_eligible(scope: Scope) -> bool:
    """A request made on this machine directly to this server: loopback socket,
    loopback Host, no proxy headers, and no Origin from anywhere else."""
    if not peer_is_loopback(scope) or has_proxy_headers(scope):
        return False
    hostname = request_hostname(scope)
    if hostname is None or not is_loopback_hostname(hostname):
        return False
    origin = header(scope, b"origin")
    if origin is not None:
        normalized = _normalize_origin(origin)
        if normalized is None or not is_loopback_hostname(hostname_of(urlsplit(normalized).netloc)):
            return False
    return True


def client_source(scope: Scope) -> str:
    """Who to count failed attempts against. Behind a proxy on this machine the
    last X-Forwarded-For hop is the one that proxy wrote; anything earlier came
    from the client and is ignored."""
    peer = peer_address(scope) or "unknown"
    if is_loopback_hostname(peer):
        forwarded = ",".join(header_values(scope, b"x-forwarded-for"))
        hops = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
        if hops:
            peer = hops[-1]
    clean = "".join(ch for ch in peer if ch.isalnum() or ch in ".:[]-%")
    return (clean or "unknown")[:64]


def label_from_user_agent(user_agent: str | None) -> str:
    ua = user_agent or ""
    if not ua:
        return "Unnamed device"
    browser = next(
        (name for marker, name in (("Edg/", "Edge"), ("OPR/", "Opera"), ("Chrome/", "Chrome"),
                                   ("Firefox/", "Firefox"), ("Safari/", "Safari"), ("curl/", "curl"),
                                   ("python-httpx", "Script")) if marker in ua),
        "Client",
    )
    system = next(
        (name for marker, name in (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"),
                                   ("Mac OS X", "Mac"), ("Windows", "Windows"), ("Linux", "Linux")) if marker in ua),
        "",
    )
    return f"{browser} on {system}" if system else browser
