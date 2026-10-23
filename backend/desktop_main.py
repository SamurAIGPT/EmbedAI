"""Start the backend for the desktop app. Run by the launcher, not by hand:
it waits for the launcher's message on stdin (see app/desktop/entry.py)."""

import os
import sys

from app.desktop.entry import main

if __name__ == "__main__":
    if sys.argv[1:2] == ["--computer-mcp"]:
        # The packaged app is one executable; an agent computer's MCP server
        # (app/computers/mcp_server.py) is started through it.
        from app.computers.mcp_server import main as computer_mcp

        sys.exit(computer_mcp(sys.argv[2:]))
    if sys.argv[1:2] == ["--team-mcp"]:
        from app.team.mcp_server import main as team_mcp
        sys.argv = [sys.argv[0], *sys.argv[2:]]
        sys.exit(team_mcp())
    if sys.argv[1:2] == ["--memory-mcp"]:
        from app.memory_mcp_server import main as memory_mcp
        sys.argv = [sys.argv[0], *sys.argv[2:]]
        sys.exit(memory_mcp())
    code = main()
    # Leave without interpreter shutdown. A thread may still be blocked reading
    # stdin (the launcher's message, or the lifeline); Python aborts with a fatal
    # error, and a garbage exit code, when it finalizes while one holds stdin's
    # buffer lock. Everything that matters (server shutdown, the database) has
    # finished by now, and the exit code is part of the contract with the launcher.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except (OSError, ValueError):
            pass
    os._exit(code)
