"""`python -m app.server service`: a systemd unit for this installation, made from its real paths.

    python -m app.server service [--user NAME] [--env-file PATH] [--name open-dots]
    python -m app.server service --write [PATH]        # needs root; refuses to replace an existing file

The unit runs the same Python that runs this command, from the backend folder, with the
settings in an environment file. Before each start it runs `migrate` (creates or updates the
database; a SQLite file is copied aside first), so an update is: pull, rebuild, restart. It
restarts the server if it dies, and stops it with SIGTERM, giving conversations time to end.

The text can be made on any system; installing it needs Linux with systemd.
"""

import argparse
import getpass
import re
import sys
from collections.abc import Sequence
from pathlib import Path

DEFAULT_ENV_FILE = "/etc/open-dots/open-dots.env"
DEFAULT_UNIT_PATH = "/etc/systemd/system/{name}.service"
UNIT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@-]{0,60}$")
ACCOUNT = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
STOP_SECONDS = 30
# systemd expands %specifiers and $variables and splits on whitespace and quotes: a path with any of
# these would not mean what it says, so such a path is refused instead of guessed at.
UNSAFE_PATH = re.compile(r"[\s%$\"'\\;]")


class ServiceError(Exception):
    """The settings cannot make a safe unit; the message says what to change."""


def backend_dir() -> Path:
    return Path(__file__).resolve().parents[2]


def _check_path(label: str, path: str) -> str:
    if not path.startswith("/"):
        raise ServiceError(f"{label} must be an absolute path (got {path!r}).")
    if UNSAFE_PATH.search(path):
        raise ServiceError(f"{label} {path!r} has a space or a character systemd treats specially. Use a plain path.")
    return path


def render(*, user: str, env_file: str = DEFAULT_ENV_FILE, python: str | None = None, backend: Path | None = None,
           description: str = "Open Duet") -> str:
    """The unit text."""
    if not ACCOUNT.match(user):
        raise ServiceError(f"{user!r} is not a usable account name. Give the Linux account that runs the server with --user.")
    python_path = _check_path("The Python path", python or sys.executable)
    work = _check_path("The backend folder", (backend or backend_dir()).as_posix())
    env = _check_path("The settings file", env_file)
    return f"""# Open Duet, made by `python -m app.server service`.
# The settings are in {env} (mode 0600, owned by {user}); see the self-hosting notes for what to put there.

[Unit]
Description={description}
After=network-online.target
Wants=network-online.target

[Service]
User={user}
WorkingDirectory={work}
EnvironmentFile={env}
# Prepare the database before serving: creates it, or updates it after an upgrade (a SQLite file is
# copied aside first). The server will not start on a database that is not ready, so a failed update
# stops here and shows in `journalctl`.
ExecStartPre={python_path} -m app.server migrate
ExecStart={python_path} -m app.server
Restart=on-failure
RestartSec=5
# A stop (SIGTERM) ends conversations cleanly; give it time before systemd gives up.
TimeoutStopSec={STOP_SECONDS}
# Agents run commands as this account. This does not stop that; it keeps a compromised process from
# gaining privileges it does not already have.
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
"""


def instructions(name: str, unit_path: str, env_file: str, user: str) -> str:
    return f"""Next, as root:

  1. Make the settings file at {env_file} if it is not there yet, then edit it (an existing file is left alone):
       install -d -m 0750 -o {user} "$(dirname {env_file})"
       test -e {env_file} || install -m 0600 -o {user} /dev/null {env_file}
  2. Install and start the service:
       systemctl daemon-reload
       systemctl enable --now {name}
  3. Watch it: journalctl -u {name} -f      and check the setup: python -m app.server doctor

The unit is at {unit_path}."""


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.server service", description="Make a systemd unit for this installation.")
    parser.add_argument("--user", default=None, help="the Linux account that runs the server (default: the current one)")
    parser.add_argument("--env-file", default=DEFAULT_ENV_FILE, help=f"the settings file (default {DEFAULT_ENV_FILE})")
    parser.add_argument("--name", default="open-dots", help="the service name (default open-dots)")
    parser.add_argument("--write", nargs="?", const="", metavar="PATH", help="write the unit instead of printing it (default path /etc/systemd/system/NAME.service)")
    try:
        args = parser.parse_args(list(argv))
    except SystemExit as exc:
        return 2 if exc.code else 0
    if not UNIT_NAME.match(args.name):
        print(f"Open Duet server: {args.name!r} is not a usable service name.", file=sys.stderr)
        return 2
    user = args.user or getpass.getuser()
    try:
        text = render(user=user, env_file=args.env_file)
    except ServiceError as exc:
        print(f"Open Duet server: {exc}", file=sys.stderr)
        return 2
    if args.write is None:
        print(text)
        return 0
    if not sys.platform.startswith("linux"):
        print("Open Duet server: systemd is Linux only. The unit text can be printed here (leave out --write) and copied to the server.", file=sys.stderr)
        return 2
    target = Path(args.write or DEFAULT_UNIT_PATH.format(name=args.name))
    if target.exists():
        print(f"Open Duet server: {target} already exists and was not replaced. Remove it or choose another --write PATH.", file=sys.stderr)
        return 4
    try:
        target.write_text(text, encoding="utf-8")
        target.chmod(0o644)
    except OSError as exc:
        print(f"Open Duet server: could not write {target} ({exc.strerror or exc}). Run it as root, or print the unit and copy it.", file=sys.stderr)
        return 4
    print(f"Wrote {target}.\n")
    print(instructions(args.name, str(target), args.env_file, user))
    return 0
