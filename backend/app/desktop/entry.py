"""A desktop launch of the backend, from the launcher's first line to its last (D-012).

1. Read the launcher's one-line message on stdin (token, key, data folder).
2. Bind 127.0.0.1 on a port the operating system picks, and switch the desktop
   profile on with the page's own origin. From here on settings come from the
   profile alone.
3. Get the database ready (judge, check the key, back up, migrate).
4. Serve the API and the built page, behind the launch token, on that socket.
5. Tell the launcher the port, with proof, on stdout.
6. Stop when the launcher's end of stdin closes, however the launcher ended.

Exit codes are part of the contract with the launcher, which turns them into
messages: 2 handshake, 3 stored credentials the key cannot open, 4 database
refused, update failed or already in use by another backend, 5 the page's
files are missing.
"""

import asyncio
import json
import os
import socket
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

from app.desktop import handshake, launch_auth, profile

EXIT_HANDSHAKE = 2
EXIT_KEY_MISMATCH = 3
EXIT_DATABASE = 4
EXIT_UI_MISSING = 5

READY_PREFIX = "OPEN_DOTS_READY "

# After the launcher has gone, the server gets this long to finish cleanly.
SHUTDOWN_BACKSTOP_SECONDS = 20
# The most it waits for connected clients (an open event stream, say) once told to
# stop; the only client is the launcher's own window, which is closing.
GRACEFUL_SHUTDOWN_SECONDS = 5


def log(message: str) -> None:
    print(f"[desktop] {message}", file=sys.stderr, flush=True)


def bind_loopback() -> socket.socket:
    """Bind first, then tell everyone the port: nothing can take it in between.
    The socket is not inherited by child processes (the default on every OS)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
    except BaseException:
        sock.close()
        raise
    return sock


class Lifeline:
    """The launcher's end of our stdin. When it closes (the launcher quit, or was
    killed) the server is asked to stop; if it does not, the process ends anyway."""

    def __init__(self, stream, backstop_seconds: float = SHUTDOWN_BACKSTOP_SECONDS) -> None:
        self._stream = stream
        self._backstop = backstop_seconds
        self.gone = threading.Event()
        self._stop: Callable[[], None] | None = None

    def start(self) -> None:
        threading.Thread(target=self._watch, name="lifeline", daemon=True).start()

    def on_gone(self, stop: Callable[[], None]) -> None:
        self._stop = stop
        if self.gone.is_set():
            stop()

    def _watch(self) -> None:
        try:
            while self._stream.read(1024):
                pass
        except (OSError, ValueError):
            pass
        self.gone.set()
        if self._stop is not None:
            self._stop()
        time.sleep(self._backstop)
        os._exit(1)


def serve(server, sock: socket.socket) -> None:
    """Run the server until it is told to stop, like `server.run`, but on a loop of
    our own. `asyncio.run` finishes by waiting for every worker thread, for up to
    five minutes, and a request can still be waiting on one (a Claude status check
    runs the CLI in a thread); the launcher would wait that long to quit."""
    factory = server.config.get_loop_factory()
    loop = factory() if factory is not None else asyncio.new_event_loop()
    try:
        loop.run_until_complete(server.serve(sockets=[sock]))
    finally:
        loop.close()  # does not wait for threads


def _announce(server, port: int, lifeline: Lifeline) -> None:
    """Print the ready line once the server accepts requests. The proof lets the
    launcher tell this line from any other text on the same stream."""
    while not lifeline.gone.is_set() and not server.should_exit:
        if server.started:
            line = {"port": port, "pid": os.getpid(), "proof": launch_auth.proof_for_port(port)}
            print(READY_PREFIX + json.dumps(line), flush=True)
            return
        time.sleep(0.05)


def main(stdin=None, handshake_timeout: float = handshake.DEFAULT_TIMEOUT_SECONDS) -> int:
    stdin = stdin if stdin is not None else sys.stdin.buffer
    try:
        message = handshake.read(stdin, handshake_timeout)
    except handshake.HandshakeError as exc:
        log(f"The launcher's message was refused: {exc}")
        return EXIT_HANDSHAKE

    lifeline = Lifeline(stdin)
    lifeline.start()

    sock = bind_loopback()
    port = sock.getsockname()[1]
    desktop = profile.DesktopProfile(
        data_dir=message.data_dir,
        origin=f"http://127.0.0.1:{port}",
        encryption_key=message.encryption_key,
        claude_cli_path=message.claude_cli_path,
    )
    profile.activate(desktop)
    launch_auth.install(message.token)

    # Only now: importing these builds the settings, which need the profile.
    from app.core import data_lease
    from app.desktop import database

    # Before the database is judged or migrated: a second launch on the same
    # data folder must not touch a file another backend is serving.
    try:
        lease = data_lease.acquire(desktop.database_file)
    except data_lease.DataDirInUse as exc:
        log(str(exc))
        return EXIT_DATABASE
    try:
        return _prepared_and_served(desktop, message, lifeline, sock, port)
    finally:
        lease.release()


def _prepared_and_served(desktop, message, lifeline, sock, port) -> int:
    from app.desktop import database

    try:
        backup = database.prepare(desktop)
    except database.KeyMismatch as exc:
        log(str(exc))
        return EXIT_KEY_MISMATCH
    except database.DatabaseRefused as exc:
        log(str(exc))
        return EXIT_DATABASE
    if backup:
        log(f"The database was updated; the copy made before is {backup.name}.")

    if message.ui_dir is not None and not (message.ui_dir / "index.html").is_file():
        log(f"The page's files are missing from {message.ui_dir}. Build them with `npm run build`.")
        return EXIT_UI_MISSING
    if lifeline.gone.is_set():
        return 0

    import uvicorn
    from fastapi.staticfiles import StaticFiles

    from app.main import app

    if message.ui_dir is not None:
        # Mounted last, so every API route keeps priority.
        app.mount("/", StaticFiles(directory=message.ui_dir, html=True), name="ui")

    server = uvicorn.Server(
        uvicorn.Config(
            app,
            log_config=None,
            log_level="info",
            access_log=False,
            timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_SECONDS,
        )
    )
    from app.core import shutdown

    def stop() -> None:
        shutdown.begin()  # event streams end, so the window's connections close
        server.should_exit = True

    lifeline.on_gone(stop)
    threading.Thread(target=_announce, args=(server, port, lifeline), name="announce", daemon=True).start()
    log(f"Serving on {desktop.origin} (process {os.getpid()}); data in {Path(desktop.data_dir)}")
    serve(server, sock)
    database.checkpoint(desktop.database_file)
    return 0 if server.started else 1
