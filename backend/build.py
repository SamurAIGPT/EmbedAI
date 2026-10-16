"""Freeze the desktop backend with PyInstaller (`--onedir`, D-009).

Run from the backend folder:  uv run --with pyinstaller python build.py

Output: backend/dist/open-dots-backend/open-dots-backend[.exe] (the folder the
desktop packaging copies into the app). The entry is desktop_main.py, the same
script the source checkout runs.

Alembic reads `alembic/env.py` and the migration files as data, so PyInstaller
cannot see what they import. The whole `app` package is collected instead of a
hand-kept list, so a new module or migration import cannot be forgotten.
"""

import shutil
import sys
from pathlib import Path

import PyInstaller.__main__

HERE = Path(__file__).resolve().parent
NAME = "open-dots-backend"

# Imported by name at run time (servers, drivers, plugins), which a static scan misses.
COLLECT_SUBMODULES = [
    "app",
    "alembic",
    "uvicorn",
    "sqlalchemy.dialects.sqlite",
    "apscheduler",
]
HIDDEN_IMPORTS = ["aiosqlite", "tzdata"]
COLLECT_DATA = ["tzdata"]


def main() -> int:
    for folder in ("build", "dist"):
        shutil.rmtree(HERE / folder, ignore_errors=True)

    args = [
        str(HERE / "desktop_main.py"),
        "--name", NAME,
        "--onedir",
        "--noconfirm",
        "--console",  # the launcher reads the ready line from stdout
        "--paths", str(HERE),
        "--distpath", str(HERE / "dist"),
        "--workpath", str(HERE / "build"),
        "--specpath", str(HERE / "build"),
        # Read by app/desktop/database.py as <package root>/alembic.
        "--add-data", f"{HERE / 'alembic'}{';' if sys.platform == 'win32' else ':'}alembic",
    ]
    for module in COLLECT_SUBMODULES:
        args += ["--collect-submodules", module]
    for module in HIDDEN_IMPORTS:
        args += ["--hidden-import", module]
    for package in COLLECT_DATA:
        args += ["--collect-data", package]

    PyInstaller.__main__.run(args)

    exe = HERE / "dist" / NAME / (NAME + (".exe" if sys.platform == "win32" else ""))
    if not exe.is_file():
        print(f"The build finished but {exe} is missing.", file=sys.stderr)
        return 1
    print(f"Built {exe}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
