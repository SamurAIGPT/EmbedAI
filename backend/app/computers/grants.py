"""One-use permits for computer tool calls.

When the approval gate allows a computer tool call (a person's Allow, a saved
rule, or an automatic allow the gate records), the app writes a permit for
exactly that tool and those arguments into a private folder that belongs to
the running turn. The computer's MCP server runs a call only after it removed
a matching permit. So a call the gate never saw is refused even if the Claude
CLI were configured not to ask, and an approval is spent once.

The folder lives in the app's data folder, outside every workspace and never
mounted into a container, so an agent cannot write its own permits.

Standard library only.
"""

import hashlib
import json
import os
import secrets
import time
from pathlib import Path

# A permit not used within this long is ignored (and removed when seen).
TTL_SECONDS = 15 * 60


def canonical(arguments: object) -> str:
    return json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def key(tool: str, arguments: object) -> str:
    return hashlib.sha256(f"{tool}\n{canonical(arguments)}".encode("utf-8")).hexdigest()


def private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def issue(folder: Path, tool: str, arguments: object) -> Path:
    """Write one permit. `tool` is the computer tool's own name (`exec`)."""
    private_dir(folder)
    path = folder / f"{key(tool, arguments)}-{secrets.token_hex(8)}"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    return path


def consume(folder: Path, tool: str, arguments: object, *, now: float | None = None) -> bool:
    """Remove one matching, unexpired permit. True if this call took one."""
    prefix = key(tool, arguments) + "-"
    now = time.time() if now is None else now
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return False
    for name in names:
        if not name.startswith(prefix):
            continue
        path = folder / name
        try:
            fresh = now - path.stat().st_mtime <= TTL_SECONDS
            os.unlink(path)  # whoever unlinks it first owns it
        except OSError:
            continue
        if fresh:
            return True
    return False
