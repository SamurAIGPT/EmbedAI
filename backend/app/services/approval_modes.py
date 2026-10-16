"""Approval modes: how much a conversation may do before it asks.

A mode is a name the person picks; the Claude CLI's own permission mode does
the enforcing, so this app never re-implements what the CLI already decides.

    ask          every call the CLI would ask about waits for Allow or Deny
    accept_edits file edits inside the workspace go through; the rest still asks
    plan         read-only: the CLI plans and edits nothing

There is deliberately no "full access" mode: no run skips permissions.

`mode=None` means "not chosen" and behaves exactly as before modes existed (ask).
"""

DEFAULT_MODE = "ask"
MODES = ("ask", "accept_edits", "plan")

# The CLI's --permission-mode value for each mode.
_CLI_MODE = {
    "ask": "default",
    "accept_edits": "acceptEdits",
    "plan": "plan",
}


class UnknownApprovalMode(ValueError):
    pass


def validate_mode(mode: str | None) -> str:
    """The mode, or the default for None. Raises UnknownApprovalMode."""
    if mode is None:
        return DEFAULT_MODE
    if mode not in MODES:
        raise UnknownApprovalMode(f"approval mode must be one of: {', '.join(MODES)}")
    return mode


def cli_permission_mode(mode: str | None) -> str:
    """The value for the CLI's `--permission-mode`."""
    return _CLI_MODE[validate_mode(mode)]
