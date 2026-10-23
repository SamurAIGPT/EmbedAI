"""Every automatic tool decision passes through the durable approval gate."""
from app.computers import policy as computers
from app.team import policy as teams


def automatic(tool, mode, run, arguments):
    if tool.startswith('mcp__memory__'):
        # Read-only search of the agent's own history (memory_bridge.py).
        return ('allowed', 'memory_read') if tool in ('mcp__memory__session_search', 'mcp__memory__session_read') else ('denied', 'unknown_memory_tool')
    if tool.startswith('mcp__team__'):
        return teams.automatic(tool, mode, run.ctx.team, arguments)
    # Screen grants are turn scoped and are never persisted as command rules.
    if tool in computers.SCREEN_INPUT or tool in computers.SCREEN_READ:
        return computers.automatic(tool, mode, screen_granted=getattr(run, "screen_granted", False))
    return computers.automatic(tool, mode)
