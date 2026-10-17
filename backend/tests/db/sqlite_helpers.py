import os
import subprocess
import sys
from pathlib import Path

from app.core.config import settings

BACKEND_DIR = Path(__file__).parents[2]


def run_alembic(
    sqlite_file: Path, *args: str, ini: Path | None = None
) -> subprocess.CompletedProcess:
    """Run Alembic against `sqlite_file` in its own process, the way a user
    would (it reads the app's settings, with SQLite selected). `ini` points at
    another alembic.ini, used to test migrations that do not exist yet. The
    decision log folder is the test's own (tests/conftest.py), never the real one."""
    environment = {
        **os.environ,
        "DATABASE_BACKEND": "sqlite",
        "SQLITE_PATH": str(sqlite_file),
        "PYTHONUTF8": "1",
        "DECISION_LOG_DIR": settings.DECISION_LOG_DIR,
    }
    config = ["-c", str(ini)] if ini else []
    return subprocess.run(
        [sys.executable, "-m", "alembic", *config, *args],
        cwd=BACKEND_DIR, env=environment, capture_output=True, text=True, timeout=120,
    )
