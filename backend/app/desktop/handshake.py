"""What the desktop launcher tells the backend, and how (D-012 decision 3).

One JSON line on the backend's standard input, before the backend listens.
Nothing secret goes through the environment, the command line, a file or a URL.
Errors name the field that is wrong and never repeat a value, because the line
holds the launch token and the encryption key.
"""

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

from cryptography.fernet import Fernet

from app.desktop import launch_auth

VERSION = 1
MAX_LINE_BYTES = 16 * 1024
DEFAULT_TIMEOUT_SECONDS = 30.0

_FIELDS = {"version", "token", "encryption_key", "data_dir", "ui_dir", "claude_cli_path", "native_control"}


class HandshakeError(Exception):
    pass


@dataclass(frozen=True)
class Handshake:
    token: str = field(repr=False)
    encryption_key: str = field(repr=False)
    data_dir: Path
    ui_dir: Path | None
    claude_cli_path: str
    native_control: dict | None = field(default=None, repr=False)


def _text(message: dict, name: str, *, required: bool) -> str | None:
    value = message.get(name)
    if value is None:
        if required:
            raise HandshakeError(f"The launcher's message has no {name}.")
        return None
    if not isinstance(value, str) or not value.strip():
        raise HandshakeError(f"The launcher's {name} must be a non-empty string.")
    return value


def _absolute(name: str, value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise HandshakeError(f"The launcher's {name} must be an absolute path.")
    return path


def parse(line: bytes | str) -> Handshake:
    try:
        message = json.loads(line)
    except (ValueError, UnicodeDecodeError):
        raise HandshakeError("The launcher's message is not valid JSON.") from None
    if not isinstance(message, dict):
        raise HandshakeError("The launcher's message must be a JSON object.")
    unknown = sorted(set(message) - _FIELDS)
    if unknown:
        raise HandshakeError(f"The launcher's message has unexpected fields: {', '.join(unknown)}.")
    if message.get("version") != VERSION:
        raise HandshakeError(f"The launcher speaks protocol {message.get('version')!r}, not {VERSION}.")

    token = _text(message, "token", required=True)
    try:
        launch_auth.check_token(token)
    except launch_auth.InvalidLaunchToken as exc:
        raise HandshakeError(str(exc)) from None

    key = _text(message, "encryption_key", required=True)
    try:
        for part in key.split(","):  # the same list format TOKEN_ENCRYPTION_KEYS has always had
            Fernet(part.strip().encode())
    except ValueError:
        raise HandshakeError("The launcher's encryption_key is not a valid key.") from None

    data_dir = _absolute("data_dir", _text(message, "data_dir", required=True))
    if data_dir.exists() and not data_dir.is_dir():
        raise HandshakeError("The launcher's data_dir exists and is not a folder.")
    ui = _text(message, "ui_dir", required=False)
    native = message.get("native_control")
    if native is not None:
        if (not isinstance(native, dict) or set(native) != {"port", "token"}
                or type(native["port"]) is not int or not 1 <= native["port"] <= 65535
                or not isinstance(native["token"], str) or len(native["token"]) != 64
                or any(c not in "0123456789abcdef" for c in native["token"])):
            raise HandshakeError("The launcher's native_control is invalid.")
    return Handshake(
        token=token,
        encryption_key=key,
        data_dir=data_dir,
        ui_dir=_absolute("ui_dir", ui) if ui else None,
        claude_cli_path=_text(message, "claude_cli_path", required=False) or "",
        native_control=native,
    )


def read(stream: BinaryIO, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> Handshake:
    """Read and check the launcher's one line. Gives up after `timeout` seconds
    (the reading thread is a daemon: a stream that never answers cannot hold the
    process open) and when the stream ends or the line is too long."""
    outcome: list[bytes | BaseException] = []
    done = threading.Event()

    def fetch() -> None:
        try:
            outcome.append(stream.readline(MAX_LINE_BYTES + 1))
        except (OSError, ValueError) as exc:
            outcome.append(exc)
        finally:
            done.set()

    threading.Thread(target=fetch, name="handshake-read", daemon=True).start()
    if not done.wait(timeout):
        raise HandshakeError(f"The launcher sent nothing within {timeout:g} seconds.")
    line = outcome[0]
    if isinstance(line, BaseException):
        raise HandshakeError("The launcher's message could not be read.")
    if not line.strip():
        raise HandshakeError("The launcher closed the connection without sending its message.")
    if len(line) > MAX_LINE_BYTES or not line.endswith(b"\n"):
        raise HandshakeError("The launcher's message is too long or has no end.")
    return parse(line)
