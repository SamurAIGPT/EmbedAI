"""Agent computers: an isolated machine an agent runs its commands and file
edits in, instead of the host. See docs/COMPUTERS.md.

`docker_cli`, `grants` and `mcp_server` use the standard library only: the MCP
server runs as its own small process started by the Claude CLI.
"""
