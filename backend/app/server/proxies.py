"""Which peers' forwarded headers the server believes (`TRUSTED_PROXIES`).

A reverse proxy tells the server who the visitor is and whether the visit was HTTPS through
`X-Forwarded-For` and `X-Forwarded-Proto`. Anyone who can reach the port can send the same
headers, so they are believed only from a peer on this list, and only Uvicorn acts on them: it
rewrites the request's client address and scheme before the application sees the request, and
the application reads only those (app/auth/request_info.py). One list, one place.

The list always holds exactly the loopback addresses (127.0.0.1 and ::1); `TRUSTED_PROXIES`
adds to it, for a proxy in another container or on another machine of a private network.
Unset, nothing changes. `FORWARDED_ALLOW_IPS`, which Uvicorn would otherwise read from the
environment, is ignored, so a container image's default cannot widen the list unseen.
"""

import ipaddress
from collections.abc import Mapping

LOOPBACK = ("127.0.0.1", "::1")
ENV_NAME = "TRUSTED_PROXIES"
IGNORED_ENV_NAME = "FORWARDED_ALLOW_IPS"


class ProxyListError(ValueError):
    """The list cannot be used; the message names the entry and what to write instead."""


def _entry(raw: str) -> str:
    text = raw.strip()
    if not text:
        raise ProxyListError(f"{ENV_NAME} has an empty entry. Separate addresses with single commas, for example 172.18.0.0/16,10.0.0.5.")
    if text == "*":
        raise ProxyListError(f"{ENV_NAME} cannot be *: that would believe forwarded headers from anyone who reaches the port. "
                             "List the proxy's address or its network, for example 172.18.0.0/16.")
    if "/" in text:
        try:
            network = ipaddress.ip_network(text, strict=True)
        except ValueError as exc:
            reason = "write the network address, for example 172.18.0.0/16" if "host bits" in str(exc) else "it is not an address range"
            raise ProxyListError(f"{ENV_NAME} entry {text!r} is not usable: {reason}.") from None
        if network.prefixlen == 0:
            raise ProxyListError(f"{ENV_NAME} entry {text!r} covers every address, which is the same as trusting everyone. "
                                 "Use the proxy's own network, for example 172.18.0.0/16.")
        return str(network)
    try:
        return str(ipaddress.ip_address(text))
    except ValueError:
        raise ProxyListError(f"{ENV_NAME} entry {text!r} is not an IP address or network (host names and ports are not accepted).") from None


def parse(value: str | None) -> tuple[str, ...]:
    """The loopback addresses, then each listed address or network once, written canonically."""
    result = list(LOOPBACK)
    if value is not None and value.strip():
        for raw in value.split(","):
            entry = _entry(raw)
            if entry not in result:
                result.append(entry)
    return tuple(result)


def environment_override(environ: Mapping[str, str], trusted: tuple[str, ...]) -> str | None:
    """A warning when the environment sets Uvicorn's own list to something other than ours."""
    value = environ.get(IGNORED_ENV_NAME)
    if value is None or value.strip() == "":
        return None
    try:
        theirs = {part.strip() for part in value.split(",") if part.strip()}
    except AttributeError:  # pragma: no cover - environ values are strings
        return None
    if theirs == set(trusted):
        return None
    return (f"{IGNORED_ENV_NAME} is set to {value!r} but is ignored: the addresses whose forwarded headers are believed "
            f"come only from {ENV_NAME} (now: {', '.join(trusted)}).")
