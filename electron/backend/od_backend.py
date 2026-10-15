"""Entry point for the packaged backend (desktop feasibility spike).

Starts the existing FastAPI app unchanged: applies the migrations, serves the
exported web page from the same address as the API, and shuts down cleanly when
the parent process closes our stdin (so a crashed or killed Electron never
leaves a backend behind).

Settings come from the environment that Electron sets; the working directory
is not searched for a .env file.
"""

import os
import sys
import threading
import time
from pathlib import Path


def bundle_dir() -> Path:
    """Where the bundled data files live: next to the executable when frozen."""
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2] / "backend"))


def apply_migrations() -> None:
    from alembic import command
    from alembic.config import Config

    config = Config()
    config.set_main_option("script_location", str(bundle_dir() / "alembic"))
    command.upgrade(config, "head")


def watch_parent(server) -> None:
    """Ask for a clean shutdown when stdin reaches end of file, then give up."""
    try:
        while sys.stdin.buffer.read(1024):
            pass
    except (OSError, ValueError):
        pass
    server.should_exit = True
    time.sleep(20)
    os._exit(1)


def main() -> None:
    import uvicorn

    port = int(os.environ["OD_PORT"])
    apply_migrations()

    from fastapi.staticfiles import StaticFiles

    from app.main import app

    ui_dir = os.environ.get("OD_UI_DIR")
    if ui_dir:
        # Mounted last, so every API route keeps priority.
        app.mount("/", StaticFiles(directory=ui_dir, html=True), name="ui")

    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info", log_config=None)
    )
    if os.environ.get("OD_WATCH_STDIN") == "1":
        threading.Thread(target=watch_parent, args=(server,), daemon=True).start()
    server.run()


if __name__ == "__main__":
    main()
