"""Workspace identity shared by thread creation, migration and the CLI runner."""

import os
from pathlib import Path


def canonical_workspace(root: str = "") -> Path:
    base = Path(root).expanduser() if root else Path.home() / ".open-dots" / "local"
    # resolve() uses the filesystem's spelling on Windows. Lowercasing the
    # entire string would change the cwd spelling used by existing CLI sessions.
    return (base / "workspace").resolve()


def require_workspace(path: Path) -> Path:
    # Existing conversations must never silently recreate or switch folders.
    if not path.is_dir() or not os.access(path, os.R_OK | os.W_OK | os.X_OK):
        raise OSError("The conversation's workspace is missing or inaccessible. Restore access to its original folder to continue.")
    return path
