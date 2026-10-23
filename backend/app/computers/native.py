"""Desktop-only native connection. Electron owns the driver and all host input.

The launch capability never reaches an engine. Each turn receives a separate
capability, revoked on release; Electron also consumes the existing one-use
approval before forwarding any tool. No environment variable activates this.
"""
import asyncio
import http.client
import json
import os
import secrets
import shutil
import sys
from pathlib import Path

from app.computers import grants
from app.computers.provider import McpLaunch
from app.desktop import profile


def durable_input(tool: str, value: dict) -> dict:
    if tool.startswith("mcp__computer__native_"):
        # Tool arguments may contain typed secrets or accessibility labels.
        return {key: (item if key in ("pid", "window_id", "x", "y", "direction", "button", "count") else "[redacted]") for key, item in value.items()}
    return value


def request(connection: dict, body: dict) -> dict:
    conn = http.client.HTTPConnection("127.0.0.1", connection["port"], timeout=25)
    try:
        conn.request("POST", "/", json.dumps(body), {"Authorization": "Bearer " + connection["token"], "Content-Type": "application/json"})
        response = conn.getresponse()
        data = response.read(16 * 1024 * 1024 + 1)
        if response.status != 200 or len(data) > 16 * 1024 * 1024:
            raise RuntimeError("Native desktop unavailable or action refused. Check permissions and control in the desktop app.")
        result = json.loads(data)
        if not isinstance(result, dict):
            raise RuntimeError("Native desktop returned an invalid response.")
        return result
    finally:
        conn.close()


async def selected(agent_id: int | None) -> bool:
    desktop = profile.current()
    if agent_id is None or desktop is None or desktop.native_control is None:
        return False
    try:
        result = await asyncio.to_thread(request, desktop.native_control, {"method": "selected", "agent": agent_id})
        return result["selected"] is True
    except Exception:
        # An unavailable bridge must never silently select Docker or host tools.
        from app.computers.service import ComputerUnavailable
        raise ComputerUnavailable("The native desktop service is disconnected. Restart the desktop app.") from None


async def attach(agent_id: int, turn_id: int, root: Path):
    from app.computers.service import ComputerUnavailable, TurnComputer

    desktop = profile.current()
    folder = grants.private_dir((root / "grants" / f"native-{turn_id}-{secrets.token_hex(8)}").resolve())
    try:
        result = await asyncio.to_thread(request, desktop.native_control, {"method": "acquire", "agent": agent_id, "turn": turn_id, "grants": str(folder)})
        connection = {"port": desktop.native_control["port"], "token": result["capability"]}
        file = folder / "connection.json"
        fd = os.open(file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(connection, stream)
        tail = ["--connection", str(file)]
        if getattr(sys, "frozen", False):
            launch = McpLaunch(sys.executable, ["--native-mcp", *tail])
        else:
            launch = McpLaunch(sys.executable, ["-m", "app.computers.native", *tail], {"PYTHONPATH": str(Path(__file__).resolve().parents[2]), "PYTHONDONTWRITEBYTECODE": "1"})
        return TurnComputer(computer_id=-agent_id, name="This Mac", image="native", provider="native", kind="native", launch=launch, grants_dir=folder)
    except Exception:
        # An interrupted acquire could already own the desktop.
        try:
            await asyncio.to_thread(request, desktop.native_control, {"method": "release", "agent": agent_id, "turn": turn_id})
        except Exception:
            pass
        shutil.rmtree(folder, ignore_errors=True)
        raise ComputerUnavailable("Native desktop unavailable. Check owner opt-in, permissions, or another active agent in the desktop app.") from None


async def release(binding, turn_id: int) -> None:
    desktop = profile.current()
    try:
        if desktop and desktop.native_control:
            await asyncio.to_thread(request, desktop.native_control, {"method": "release", "agent": -binding.computer_id, "turn": turn_id})
    finally:
        shutil.rmtree(binding.grants_dir, ignore_errors=True)


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--connection", type=Path, required=True)
    connection = json.loads(parser.parse_args(argv).connection.read_text())
    for line in sys.stdin:
        message = None
        try:
            message = json.loads(line)
            if not isinstance(message, dict) or "id" not in message:
                continue
            method, params = message.get("method"), message.get("params") or {}
            if method == "initialize":
                result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}, "serverInfo": {"name": "open-duet-native", "version": "1"}}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = request(connection, {"method": method})
            elif method == "tools/call":
                result = request(connection, {"method": method, "name": params.get("name"), "arguments": params.get("arguments", {})})
            else:
                raise RuntimeError("Unknown native method.")
        except Exception:
            result = {"isError": True, "content": [{"type": "text", "text": "Native desktop unavailable or call refused. No action was retried. Check permissions, control and approvals in the desktop app."}]}
        if isinstance(message, dict) and "id" in message:
            print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
