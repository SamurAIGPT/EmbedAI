"""Bounded desktop input. All commands use argv and the fixed display space."""
import base64
import json
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path

from app.computers import docker_cli, grants

WIDTH, HEIGHT = 1280, 800
INPUT_NAMES = frozenset({"click", "double_click", "move", "drag", "type", "key", "scroll"})
_KEY = re.compile(r"^[A-Za-z0-9_+\-]{1,60}( [A-Za-z0-9_+\-]{1,60}){0,9}$")
CONTROL_REFUSAL = (
    "The person has taken control of this computer, so this call was not run. "
    "Do not retry now. Stop and tell them you are waiting, then take a fresh screenshot "
    "before your next action once they hand control back."
)


def prop(kind, **kw):
    return {"type": kind, **kw}


X, Y = prop("integer", minimum=0, maximum=WIDTH-1), prop("integer", minimum=0, maximum=HEIGHT-1)
XY = {"x": X, "y": Y}
DEFINITIONS = {
    "screenshot": ({**XY, "width": prop("integer", minimum=1, maximum=WIDTH), "height": prop("integer", minimum=1, maximum=HEIGHT)}, []),
    "wait": ({"seconds": prop("integer", minimum=1, maximum=10)}, ["seconds"]),
    "click": ({**XY, "button": prop("string", enum=["left", "middle", "right"]), "count": prop("integer", minimum=1, maximum=3)}, ["x", "y"]),
    "double_click": (XY, ["x", "y"]),
    "move": (XY, ["x", "y"]),
    "drag": ({"from_x": X, "from_y": Y, "to_x": X, "to_y": Y}, ["from_x", "from_y", "to_x", "to_y"]),
    "type": ({"text": prop("string", maxLength=2000)}, ["text"]),
    "key": ({"keys": prop("string", maxLength=609)}, ["keys"]),
    "scroll": ({**XY, "direction": prop("string", enum=["up", "down", "left", "right"]), "amount": prop("integer", minimum=1, maximum=30)}, ["x", "y", "direction"]),
}
TOOLS = [{"name": name, "description": f"{name.replace('_', ' ').capitalize()} on the computer's {WIDTH}x{HEIGHT} screen. Screen input requires permission for this turn.", "inputSchema": {"type": "object", "properties": fields, "required": required, "additionalProperties": False}, "annotations": {"readOnlyHint": name not in INPUT_NAMES}} for name, (fields, required) in DEFINITIONS.items()]


def validate(name: str, args: dict) -> str | None:
    fields, required = DEFINITIONS[name]
    if any(k not in args for k in required) or any(k not in fields for k in args):
        return "Missing or unknown screen argument."
    for key, value in args.items():
        schema = fields[key]
        if schema["type"] == "integer":
            if isinstance(value, bool) or not isinstance(value, int) or not schema["minimum"] <= value <= schema["maximum"]:
                return f"{key} must be an integer in {schema['minimum']}..{schema['maximum']} (screen {WIDTH}x{HEIGHT})."
        elif not isinstance(value, str) or "\x00" in value or len(value) > schema.get("maxLength", 2000):
            return f"Invalid {key}."
        if "enum" in schema and value not in schema["enum"]:
            return f"Invalid {key}."
    if name == "key" and not _KEY.fullmatch(args["keys"]):
        return "Invalid key combination."
    if name == "screenshot" and args:
        if set(args) != {"x", "y", "width", "height"}:
            return "A crop requires x, y, width and height."
        if args["x"] + args["width"] > WIDTH or args["y"] + args["height"] > HEIGHT:
            return "Crop exceeds the 1280x800 screen."
    return None


def read_state(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def held(state_dir: Path) -> bool:
    until = read_state(state_dir / "control.json").get("until", 0)
    return isinstance(until, (float, int)) and until > time.time()


@contextmanager
def driver_lock(state_dir: Path):
    """Cross-process lock around owner checks and desktop actions."""
    grants.private_dir(state_dir)
    with (state_dir / "screen.lock").open("a+b") as lock:
        if os.name == "nt":
            import msvcrt
            lock.seek(0); lock.write(b"0"); lock.flush(); lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                lock.seek(0); msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock, fcntl.LOCK_UN)


def write_state(path: Path, value: dict):
    grants.private_dir(path.parent)
    temp = path.with_name(f".{path.name}.{os.getpid()}")
    temp.write_text(json.dumps(value)); temp.chmod(0o600)
    os.replace(temp, path)


class Screen:
    def __init__(self, server, turn_id: str, max_actions=300):
        self.server, self.turn_id, self.max_actions = server, turn_id, max_actions
        self.actions, self.last_input, self.last_capture = 0, 0.0, 0.0

    def run(self, name: str, args: dict) -> dict:
        from app.computers.mcp_server import _text
        s = self.server
        with driver_lock(s.state_dir):
            if held(s.state_dir):
                return _text(CONTROL_REFUSAL, error=True)
            if name in INPUT_NAMES:
                if self.actions >= self.max_actions:
                    return _text("This turn reached its screen action limit.", error=True)
                owner_path = s.state_dir / "screen-owner.json"
                owner = read_state(owner_path)
                if owner.get("until", 0) > time.time() and owner.get("turn_id") != self.turn_id:
                    return _text("Another conversation is using this screen. Wait until it finishes.", error=True)
                write_state(owner_path, {"turn_id": self.turn_id, "until": time.time()+60})
                time.sleep(max(0, .15-(time.monotonic()-self.last_input)))
                self.actions += 1
                self.last_input = time.monotonic()
            if name == "wait":
                time.sleep(args["seconds"])
                return _text(f"Waited {args['seconds']} seconds.")
            if name == "screenshot":
                time.sleep(max(0, .25-(time.monotonic()-self.last_capture)))
                self.last_capture = time.monotonic()
                cmd = ["od-shot"]
                if args:
                    cmd += ["--crop", f"{args['width']}x{args['height']}+{args['x']}+{args['y']}"]
            else:
                cmd = ["xdotool", *input_args(name, args)]
            done = docker_cli.run(docker_cli.desktop_args(s.container, s.user, *cmd), timeout=45, docker=s.docker, max_bytes=2_000_000)
            if done.code != 0 or done.timed_out:
                return _text("The screen action failed: " + docker_cli.clip_output(done.stderr, 500), error=True)
            subject = f"{len(args['text'])} characters" if name == "type" else json.dumps(args)
            s._remember(name, subject, done.code, False, "")
            if name == "screenshot":
                if not done.raw.startswith(b"\x89PNG\r\n\x1a\n"):
                    return _text("The screen returned an invalid image.", error=True)
                return {"content": [{"type": "text", "text": f"screen {WIDTH}x{HEIGHT}"}, {"type": "image", "mimeType": "image/png", "data": base64.b64encode(done.raw).decode()}]}
            return _text(f"{name}: {subject}")


def input_args(name, a):
    move = ["mousemove", "--sync", str(a.get("x", 0)), str(a.get("y", 0))]
    if name == "move": return move
    if name in {"click", "double_click"}:
        return move + ["click", "--repeat", str(2 if name == "double_click" else a.get("count", 1)), "--delay", "100", str({"left": 1, "middle": 2, "right": 3}[a.get("button", "left")])]
    if name == "type": return ["type", "--clearmodifiers", "--delay", "12", "--", a["text"]]
    if name == "key": return ["key", "--clearmodifiers", *a["keys"].split()]
    if name == "scroll": return move + ["click", "--repeat", str(a.get("amount", 1)), str({"up": 4, "down": 5, "left": 6, "right": 7}[a["direction"]])]
    return ["mousemove", "--sync", str(a["from_x"]), str(a["from_y"]), "mousedown", "1", "mousemove", "--sync", str(a["to_x"]), str(a["to_y"]), "mouseup", "1"]
