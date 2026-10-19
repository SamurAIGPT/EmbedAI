"""Start the backend as a server that also serves the web pages.

    python -m app.server migrate               # create or update the database first (copies SQLite aside)
    python -m app.server                       # reads the settings below from the environment
    python -m app.server --ui-dir ../frontend/out --port 8000

Settings (bootstrap class, D-008: needed before the database is opened):
    OPEN_DOTS_HOST   address to listen on, default 127.0.0.1
    OPEN_DOTS_PORT   port, default 8000
    OPEN_DOTS_UI_DIR folder holding the exported pages (a build with a relative API path)
    OPEN_DOTS_ALLOW_PUBLIC_BIND=1   needed to listen on anything but this machine's own address

The server is meant to sit behind a reverse proxy on the same machine (any proxy;
it only reads standard forwarded headers) and to listen on loopback. Listening
on another address, as a container needs, is an explicit opt-in.

Local execution is switched on here unless the environment or `.env` sets it either way: a
server exists to run agents, and the desktop does the same through its profile.
Tools and everything else keep their settings.
"""

import argparse
import ipaddress
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

EXIT_USAGE = 2
EXIT_KEY_MISMATCH = 3  # same numbers as the desktop launch
EXIT_DATABASE = 4
GRACEFUL_SHUTDOWN_SECONDS = 5
LOOPBACK_NAMES = frozenset({"localhost"})


class ServerConfigError(Exception):
    """The settings cannot start a server; the message says what to change."""


@dataclass(frozen=True)
class ServerOptions:
    host: str
    port: int
    ui_dir: Path


def _is_loopback(host: str) -> bool:
    if host.lower() in LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def read_options(argv: Sequence[str], environ: Mapping[str, str]) -> ServerOptions:
    parser = argparse.ArgumentParser(prog="python -m app.server", description="Run the backend and serve the web pages.")
    parser.add_argument("--host", default=environ.get("OPEN_DOTS_HOST", "127.0.0.1"))
    parser.add_argument("--port", default=environ.get("OPEN_DOTS_PORT", "8000"))
    parser.add_argument("--ui-dir", default=environ.get("OPEN_DOTS_UI_DIR", ""))
    try:
        args = parser.parse_args(list(argv))
    except SystemExit as exc:  # argparse already printed the usage
        raise ServerConfigError("Check the options above.") from exc

    try:
        port = int(args.port)
    except ValueError:
        raise ServerConfigError(f"The port must be a number, not {args.port!r}.") from None
    if not 1 <= port <= 65535:
        raise ServerConfigError(f"The port must be between 1 and 65535, not {port}.")

    host = args.host.strip()
    if not host:
        raise ServerConfigError("The host is empty. Use 127.0.0.1 behind a reverse proxy on this machine.")
    if not _is_loopback(host) and environ.get("OPEN_DOTS_ALLOW_PUBLIC_BIND") != "1":
        raise ServerConfigError(
            f"Refusing to listen on {host}: the server is meant to sit behind a reverse proxy on this machine. "
            "Use 127.0.0.1, or set OPEN_DOTS_ALLOW_PUBLIC_BIND=1 if a container or a private network needs it."
        )

    if not args.ui_dir:
        raise ServerConfigError(
            "No web pages to serve. Set OPEN_DOTS_UI_DIR (or --ui-dir) to the folder made by the web build."
        )
    ui_dir = Path(args.ui_dir).expanduser().resolve()
    if not (ui_dir / "index.html").is_file():
        raise ServerConfigError(
            f"{ui_dir} has no index.html. Build the pages first: set NEXT_PUBLIC_API_BASE_URL=/api/v1 and "
            "OD_STATIC_EXPORT=1, run `npm run build` in frontend/, and point OPEN_DOTS_UI_DIR at the `out` folder."
        )
    return ServerOptions(host=host, port=port, ui_dir=ui_dir)


def build_app(options: ServerOptions):
    """The application with the pages in front of it. Local execution defaults on
    here unless the environment or `.env` sets it either way."""
    from app.core.config import settings

    if "LOCAL_EXECUTION_ENABLED" not in settings.model_fields_set:
        settings.LOCAL_EXECUTION_ENABLED = True
    from app.main import app
    from app.server.static_ui import serve_ui

    return serve_ui(app, options.ui_dir)


def migrate() -> int:
    from app.server.database import DatabaseRefused, KeyMismatch, prepare_database

    try:
        report = prepare_database()
    except KeyMismatch as exc:
        print(f"Open Dots server: {exc}", file=sys.stderr)
        return EXIT_KEY_MISMATCH
    except DatabaseRefused as exc:
        print(f"Open Dots server: {exc}", file=sys.stderr)
        return EXIT_DATABASE
    if report.backup:
        print(f"Copied the database to {report.backup} before updating it.")
    if report.changed:
        print(f"Database ready at revision {report.after} ({report.database}).")
    else:
        print(f"Database already up to date ({report.database}).")
    return 0


def check_schema() -> str | None:
    """None when the database can be served; otherwise what to tell the person."""
    from app.server.database import DatabaseRefused, schema_state

    try:
        state = schema_state()
    except DatabaseRefused as exc:
        return str(exc)
    except Exception as exc:  # noqa: BLE001 - an unreachable database is reported, not a traceback
        return f"Could not read the database ({type(exc).__name__}: {exc})."
    if state != "current":
        what = "has no tables yet" if state == "new" else "is from an older version"
        return f"The database {what}. Run `python -m app.server migrate` first, then start the server again."
    return None


def main(argv: Sequence[str] | None = None, environ: Mapping[str, str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments[:1] == ["migrate"]:
        return migrate()
    try:
        options = read_options(arguments, os.environ if environ is None else environ)
    except ServerConfigError as exc:
        print(f"Open Dots server: {exc}", file=sys.stderr)
        return EXIT_USAGE

    problem = check_schema()
    if problem:
        print(f"Open Dots server: {problem}", file=sys.stderr)
        return EXIT_DATABASE

    import uvicorn

    asgi = build_app(options)
    uvicorn.run(
        asgi,
        host=options.host,
        port=options.port,
        log_config=None,
        log_level="info",
        access_log=False,
        timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_SECONDS,
    )
    return 0
