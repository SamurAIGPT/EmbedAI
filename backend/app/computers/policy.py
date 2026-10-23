"""What the approval gate settles on its own for a computer tool call.

Every computer call is recorded as an approval and logged; these are the ones
answered without a card, matching what the host flow does in each mode:

    read_file, list_files   allowed in every mode (reading inside the machine,
                            like the CLI's own reads in the workspace)
    write_file              allowed in accept_edits
    exec, write_file        refused in plan (plan changes nothing)

Everything else is put to the person (or a saved command rule, for `exec`).
"""

COMPUTER_TOOL_PREFIX = "mcp__computer__"
COMPUTER_EXEC_TOOL = COMPUTER_TOOL_PREFIX + "exec"
SCREEN_INPUT = frozenset(COMPUTER_TOOL_PREFIX + name for name in ("click", "double_click", "move", "drag", "type", "key", "scroll"))
SCREEN_READ = frozenset(COMPUTER_TOOL_PREFIX + name for name in ("screenshot", "wait"))


def automatic(tool: str, mode: str | None, screen_granted: bool = False) -> tuple[str, str] | None:
    """(status, source) for a call settled without a card, else None."""
    if not isinstance(tool, str) or not tool.startswith(COMPUTER_TOOL_PREFIX):
        return None
    name = tool[len(COMPUTER_TOOL_PREFIX):]
    if name in ("screenshot", "wait"):
        return "allowed", "computer_screen_read"
    if name in ("click", "double_click", "move", "drag", "type", "key", "scroll"):
        if mode == "plan":
            return "denied", "plan_mode"
        return ("allowed", "screen_turn_grant") if screen_granted else None
    if name in ("read_file", "list_files"):
        return "allowed", "computer_read"
    if mode == "plan" and name in ("exec", "write_file"):
        return "denied", "plan_mode"
    if mode == "accept_edits" and name == "write_file":
        return "allowed", "accept_edits"
    return None
