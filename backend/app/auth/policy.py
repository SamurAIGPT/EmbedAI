"""Which requests need no sign-in, and what a chat-only device may do.

Both lists are allowlists: a route added later is admin-only and signed-in
until someone deliberately lists it here.
"""

import re
from dataclasses import dataclass, field

API = "/api/v1"

_ID = r"[0-9]+"

# Reachable without a session. Host and Origin checks still apply.
PUBLIC_ROUTES: tuple[tuple[frozenset[str], re.Pattern[str]], ...] = tuple(
    (frozenset(methods), re.compile(f"^{API}{path}$"))
    for methods, path in (
        ({"GET", "HEAD"}, "/health"),
        ({"GET", "HEAD"}, "/sandboxes/health"),
        ({"GET", "HEAD"}, "/permissions/health"),
        # Boat sandboxes call this with their own bearer (HOOK_TOKEN), checked by the route.
        ({"POST"}, r"/permissions/check/[^/]+/[0-9]+"),
        ({"GET"}, "/auth/status"),
        ({"POST"}, "/auth/setup"),
        ({"POST"}, "/auth/login"),
        ({"POST"}, "/auth/logout"),
        ({"POST"}, "/auth/pair"),
    )
) + (
    # Routine webhooks: each delivery carries the routine's own secret, checked by the route.
    (frozenset({"POST"}), re.compile(r"^/hooks/[^/]+$")),
)

# What the `chat` scope reaches: conversations and their approvals, and the
# reads the chat page needs. No settings, rules, computers, devices or pairing.
CHAT_ROUTES: tuple[tuple[frozenset[str], re.Pattern[str]], ...] = tuple(
    (frozenset(methods), re.compile(f"^{API}{path}$"))
    for methods, path in (
        ({"GET"}, "/auth/session"),
        ({"GET"}, "/team"),
        ({"GET"}, "/team/jobs"),
        ({"GET"}, f"/team/jobs/{_ID}"),
        ({"GET"}, "/rooms"),
        ({"GET"}, f"/rooms/{_ID}"),
        ({"GET"}, f"/rooms/{_ID}/messages"),
        ({"POST"}, f"/rooms/{_ID}/messages"),
        ({"POST"}, f"/rooms/{_ID}/read"),
        ({"POST"}, f"/rooms/{_ID}/stop"),
        ({"GET"}, f"/rooms/{_ID}/approvals"),
        ({"GET"}, f"/rooms/{_ID}/export"),
        ({"GET"}, "/threads"),
        ({"POST"}, "/threads"),
        ({"GET", "PATCH"}, f"/threads/{_ID}"),
        ({"POST"}, f"/threads/{_ID}/turns"),
        ({"POST"}, f"/threads/{_ID}/stop"),
        ({"GET"}, f"/threads/{_ID}/events"),
        ({"GET"}, f"/threads/{_ID}/events/stream"),
        ({"GET"}, f"/threads/{_ID}/approvals"),
        ({"POST"}, f"/threads/{_ID}/approvals/[A-Za-z0-9_-]+"),
        # Search and attachments are part of chatting. Workspace files
        # (/threads/{id}/files...) and usage (/usage) stay admin-only.
        ({"GET"}, "/search"),
        ({"POST"}, f"/threads/{_ID}/attachments"),
        ({"GET"}, f"/threads/{_ID}/attachments/[0-9a-f]{{32}}"),
        ({"GET"}, "/agents"),
        ({"GET"}, f"/agents/{_ID}"),
        ({"GET"}, "/engines"),
        ({"GET"}, "/engines/[A-Za-z0-9_-]+"),
        ({"GET"}, "/providers"),
        ({"GET"}, "/local/status"),
    )
)


def _matches(rules, method: str, path: str) -> bool:
    return any(method in methods and pattern.match(path) for methods, pattern in rules)


def is_public(method: str, path: str) -> bool:
    return _matches(PUBLIC_ROUTES, method, path)


def required_scope(method: str, path: str) -> str:
    return "chat" if _matches(CHAT_ROUTES, method, path) else "admin"


@dataclass(frozen=True)
class Principal:
    """Who a request acts as. `kind`: desktop (launch token), loopback (trusted
    local request), browser (owner cookie session) or device (paired bearer)."""

    kind: str
    scopes: tuple[str, ...] = field(default=("admin", "chat"))
    session_id: int | None = None
    label: str | None = None

    @property
    def is_admin(self) -> bool:
        return "admin" in self.scopes
