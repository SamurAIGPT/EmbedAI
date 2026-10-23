"""The computer tools, as a stdio MCP server the Claude CLI starts for a turn.

It exposes four tools bound to one container: `exec` (a shell command),
`read_file`, `write_file` and `list_files`. Each call runs only after a
one-use permit written by the app's approval gate is found and removed
(grants.py); without one the call is refused and nothing runs.

Line-delimited JSON-RPC 2.0 on stdin/stdout, standard library only. Started as

    python -m app.computers.mcp_server --container NAME --user UID:GID \
        --grants DIR --state DIR [--docker PATH]

(or, in the packaged desktop app, the app's own executable with
`--computer-mcp` in front of the same arguments).
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

from app.computers import docker_cli, grants, screen

PROTOCOL_VERSION = "2025-06-18"
LAST_COMMAND_FILE = "last-command.json"
NOT_APPROVED = (
    "This call was not approved in open-dots, so it was not run. "
    "Ask again; every computer call needs an approval."
)


def _schema(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


TOOLS = [
    {
        "name": "exec",
        "description": (
            "Run a shell command on your computer (a Linux container). The working directory is "
            f"{docker_cli.GUEST_WORKSPACE}, which is the agent's workspace folder. Returns the exit "
            "code and output."
        ),
        "inputSchema": _schema(
            {
                "command": {"type": "string", "minLength": 1, "maxLength": docker_cli.MAX_COMMAND_CHARS},
                "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": docker_cli.EXEC_MAX_SECONDS},
                "cwd": {"type": "string", "maxLength": 4096},
            },
            ["command"],
        ),
        "annotations": {"title": "Run a command in the computer", "destructiveHint": True, "openWorldHint": True},
    },
    {
        "name": "read_file",
        "description": f"Read a text file on your computer. Relative paths are under {docker_cli.GUEST_WORKSPACE}.",
        "inputSchema": _schema({"path": {"type": "string", "minLength": 1, "maxLength": 4096}}, ["path"]),
        "annotations": {"title": "Read a file in the computer", "readOnlyHint": True},
    },
    {
        "name": "write_file",
        "description": (
            "Create or replace a text file on your computer, making its folders. Relative paths are "
            f"under {docker_cli.GUEST_WORKSPACE}."
        ),
        "inputSchema": _schema(
            {
                "path": {"type": "string", "minLength": 1, "maxLength": 4096},
                "content": {"type": "string", "maxLength": docker_cli.MAX_FILE_BYTES},
            },
            ["path", "content"],
        ),
        "annotations": {"title": "Write a file in the computer", "destructiveHint": True},
    },
    {
        "name": "list_files",
        "description": f"List a folder on your computer. Relative paths are under {docker_cli.GUEST_WORKSPACE}.",
        "inputSchema": _schema({"path": {"type": "string", "minLength": 1, "maxLength": 4096}}, []),
        "annotations": {"title": "List a folder in the computer", "readOnlyHint": True},
    },
]
_TOOL_NAMES = {tool["name"] for tool in TOOLS}


class Server:
    def __init__(self, container: str, user: str, grants_dir: Path, state_dir: Path, docker: str | None, kind: str = "shell", turn_id: str = "", max_actions: int = 300) -> None:
        self.container = docker_cli.check_name(container)
        self.user = user
        self.grants_dir = grants_dir
        self.state_dir = state_dir
        self.docker = docker
        self.kind = kind
        self.screen = screen.Screen(self, turn_id or str(grants_dir), max_actions)

    # -- protocol ----------------------------------------------------------------------

    def handle(self, message: dict) -> dict | None:
        method = message.get("method")
        msg_id = message.get("id")
        params = message.get("params") or {}
        if msg_id is None:
            return None  # a notification (initialized, cancelled): nothing to answer
        try:
            if method == "initialize":
                result = {
                    "protocolVersion": params.get("protocolVersion") or PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "open-dots-computer", "version": "1"},
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": TOOLS + (screen.TOOLS if self.kind == "desktop" else [])}
            elif method == "tools/call":
                result = self.call(params.get("name"), params.get("arguments") or {})
            else:
                return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": f"Unknown method: {method}"}}
        except Exception as exc:  # noqa: BLE001  (a tool failure must not end the server)
            result = _text(f"The computer tool failed: {exc}", error=True)
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    # -- tools -------------------------------------------------------------------------

    def call(self, name: object, arguments: object) -> dict:
        if name not in _TOOL_NAMES and not (self.kind == "desktop" and name in screen.DEFINITIONS):
            return _text("Unknown computer tool.", error=True)
        if not isinstance(arguments, dict):
            return _text("Tool arguments must be an object.", error=True)
        problem = screen.validate(name, arguments) if name in screen.DEFINITIONS else _argument_problem(name, arguments)
        if problem:
            return _text(problem, error=True)
        if not grants.consume(self.grants_dir, name, arguments):
            return _text(NOT_APPROVED, error=True)
        if name not in ("read_file", "list_files") and screen.held(self.state_dir):
            return _text(screen.CONTROL_REFUSAL, error=True)
        if name in screen.DEFINITIONS:
            return self.screen.run(name, arguments)
        return getattr(self, f"_{name}")(arguments)

    def _exec(self, args: dict) -> dict:
        seconds = docker_cli.exec_seconds(args.get("timeout_seconds"))
        cwd = args.get("cwd")
        if cwd is not None:
            docker_cli.check_guest_path(cwd)
        command = docker_cli.exec_args(self.container, args["command"], seconds, self.user, cwd=cwd)
        if self.kind == "desktop":
            command[command.index(f"HOME={docker_cli.GUEST_HOME}")] = "HOME=/home/agent"
            command[1:1] = ["-e", "DISPLAY=:1"]
        done = docker_cli.run(
            command,
            timeout=seconds + 20,
            docker=self.docker,
        )
        timed_out = done.timed_out or done.code in (124, 137)
        code = done.code
        stdout, stderr = docker_cli.clip_output(done.stdout), docker_cli.clip_output(done.stderr)
        lines = [f"exit code: {code if code is not None else 'none'}" + (" (timed out)" if timed_out else "")]
        if stdout:
            lines += ["--- stdout ---", stdout.rstrip("\n")]
        if stderr:
            lines += ["--- stderr ---", stderr.rstrip("\n")]
        self._remember("exec", args["command"], code, timed_out, (stdout + stderr))
        return _text("\n".join(lines), error=code != 0)

    def _read_file(self, args: dict) -> dict:
        done = docker_cli.run(docker_cli.read_args(self.container, args["path"], self.user), timeout=60, docker=self.docker)
        self._remember("read_file", args["path"], done.code, done.timed_out, "")
        if done.code != 0:
            return _text(done.stderr.strip() or "The file could not be read.", error=True)
        data = done.raw
        if len(data) > docker_cli.MAX_FILE_BYTES:
            return _text(f"The file is larger than {docker_cli.MAX_FILE_BYTES} bytes; read a part with exec (head, sed -n).", error=True)
        if b"\x00" in data:
            return _text("This looks like a binary file; inspect it with exec instead.", error=True)
        return _text(data.decode("utf-8", errors="replace"))

    def _write_file(self, args: dict) -> dict:
        content = args["content"].encode("utf-8")
        done = docker_cli.run(
            docker_cli.write_args(self.container, args["path"], self.user),
            timeout=60, input_bytes=content, docker=self.docker,
        )
        self._remember("write_file", args["path"], done.code, done.timed_out, done.stderr)
        if done.code != 0:
            return _text(done.stderr.strip() or "The file could not be written.", error=True)
        return _text(f"Wrote {len(content)} bytes to {args['path']}.")

    def _list_files(self, args: dict) -> dict:
        path = args.get("path") or "."
        done = docker_cli.run(docker_cli.list_args(self.container, path, self.user), timeout=60, docker=self.docker)
        self._remember("list_files", path, done.code, done.timed_out, "")
        if done.code != 0:
            return _text(done.stderr.strip() or "The folder could not be listed.", error=True)
        return _text(docker_cli.clip_output(done.stdout))

    def _remember(self, tool: str, subject: str, code: int | None, timed_out: bool, output: str) -> None:
        """What ran last, for the Computer panel. Best effort."""
        record = {
            "tool": tool,
            "subject": subject[:2000],
            "exit_code": code,
            "timed_out": timed_out,
            "output": docker_cli.clip_output(output, 4000),
            "at": time.time(),
        }
        try:
            grants.private_dir(self.state_dir)
            tmp = self.state_dir / f".{LAST_COMMAND_FILE}.{os.getpid()}"
            tmp.write_text(json.dumps(record), encoding="utf-8")
            os.replace(tmp, self.state_dir / LAST_COMMAND_FILE)
        except OSError:
            pass


def _argument_problem(name: str, arguments: dict) -> str | None:
    schema = next(tool["inputSchema"] for tool in TOOLS if tool["name"] == name)
    allowed = schema["properties"]
    for field in schema["required"]:
        if field not in arguments:
            return f"`{field}` is required."
    for field, value in arguments.items():
        if field not in allowed:
            return f"Unknown argument `{field}`."
        expected = allowed[field]["type"]
        if expected == "string" and not isinstance(value, str):
            return f"`{field}` must be a string."
        if expected == "integer" and (isinstance(value, bool) or not isinstance(value, int)):
            return f"`{field}` must be an integer."
    return None


def _text(text: str, *, error: bool = False) -> dict:
    result: dict = {"content": [{"type": "text", "text": text}]}
    if error:
        result["isError"] = True
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="open-dots-computer-mcp")
    parser.add_argument("--container", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--grants", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--docker", default=None)
    parser.add_argument("--kind", choices=docker_cli.COMPUTER_KINDS, default="shell")
    parser.add_argument("--turn-id", default="")
    parser.add_argument("--max-screen-actions", type=int, default=300)
    options = parser.parse_args(argv)
    try:
        server = Server(options.container, options.user, Path(options.grants), Path(options.state), options.docker, options.kind, options.turn_id, options.max_screen_actions)
    except docker_cli.InvalidSpec as exc:
        sys.stderr.write(f"invalid computer connection: {exc}\n")
        return 2
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        else:
            reply = server.handle(message) if isinstance(message, dict) else None
        if reply is not None:
            sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
