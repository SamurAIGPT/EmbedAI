"""Make the engines findable when the server runs as a service.

The server finds an engine's program (`claude`, `codex`) through the PATH it was
started with, and hands the same PATH to the engines it starts (which may need
`node` or other tools from there). A service manager gives a program a minimal PATH,
so an engine installed in the service account's own home (an npm global prefix, a
user-level install) is not found even though it works in that account's shell.

At start the server adds the usual per-user and local program folders to the END of
the PATH, so nothing already on it is overridden, and `OPEN_DOTS_EXTRA_PATH` can name
more folders. Only folders that exist are added.
"""

import os
from collections.abc import MutableMapping
from pathlib import Path

# Where per-user installs of the engines usually put their programs.
USER_DIRS = (".local/bin", ".npm-global/bin", ".claude/local")
SYSTEM_DIRS = ("/usr/local/bin",)
EXTRA_SETTING = "OPEN_DOTS_EXTRA_PATH"


def _same(a: str, b: str) -> bool:
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def candidate_dirs(home: Path, extra: str = "") -> list[str]:
    """Folders worth having on the PATH, in order: the account's own, the system's, then the ones named in `extra`."""
    named = [Path(part).expanduser() for part in extra.split(os.pathsep) if part.strip()] if extra else []
    return [str(path) for path in [*(home / name for name in USER_DIRS), *(Path(d) for d in SYSTEM_DIRS), *named]]


def augment_path(environ: MutableMapping[str, str] | None = None, home: Path | None = None) -> list[str]:
    """Append the candidate folders that exist and are not on the PATH yet. Returns what was added."""
    env = os.environ if environ is None else environ
    current = [part for part in env.get("PATH", "").split(os.pathsep) if part]
    added: list[str] = []
    for folder in candidate_dirs(home or Path.home(), env.get(EXTRA_SETTING, "")):
        if not os.path.isdir(folder):
            continue
        if any(_same(folder, existing) for existing in [*current, *added]):
            continue
        added.append(folder)
    if added:
        env["PATH"] = os.pathsep.join([*current, *added])
    return added
