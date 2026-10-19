"""The address people use to reach this server from outside (OPEN_DOTS_PUBLIC_URL).

One value, set once for a server install: `https://dots.example.com`. It is the
host name the server answers to, and the address printed in pairing links. It is
an origin only (scheme, host, optional port), never a path, so a reverse proxy
may serve the app at the root of its own name and nothing else is assumed about
the proxy.
"""

from urllib.parse import urlsplit

EXAMPLE = "https://dots.example.com"


def normalize(value: str) -> str:
    """The origin without a trailing slash, lowercase host; "" when unset.
    Raises ValueError, saying what to change, for anything that is not an origin."""
    text = value.strip()
    if not text:
        return ""
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        raise ValueError(f"OPEN_DOTS_PUBLIC_URL is not an address. Use something like {EXAMPLE}.") from None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(f"OPEN_DOTS_PUBLIC_URL must start with http:// or https:// and name a host, like {EXAMPLE}.")
    if parts.username or parts.password:
        raise ValueError("OPEN_DOTS_PUBLIC_URL must not contain a user name or password.")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise ValueError(f"OPEN_DOTS_PUBLIC_URL must be only the address, with no path, like {EXAMPLE}.")
    host = parts.hostname.lower()
    if ":" in host:  # IPv6 keeps its brackets in a URL
        host = f"[{host}]"
    return f"{parts.scheme}://{host}" + (f":{port}" if port else "")


def hostname(origin: str) -> str:
    """The host name of a normalized origin, as a Host header names it without the port
    ("" when there is none)."""
    if not origin:
        return ""
    host = urlsplit(origin).hostname or ""
    return f"[{host}]" if ":" in host else host


def pairing_link(origin: str, display_code: str) -> str:
    """Where to open a pairing code: the page reads the code from the fragment, which
    never reaches a server log or a proxy."""
    return f"{origin}/pair#code={display_code}"
