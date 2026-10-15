"""Freeze the backend with PyInstaller (onedir).

Run from the electron/ folder, with the backend's own environment plus PyInstaller
added for this run only (nothing is added to the project):

    uv run --project ../backend --with pyinstaller python backend/build.py
"""

import os
import shutil
import time
from pathlib import Path

import PyInstaller.__main__

HERE = Path(__file__).resolve().parent
ELECTRON = HERE.parent
BACKEND = ELECTRON.parent / "backend"
OUT = ELECTRON / "build"

HIDDEN = [
    # Loaded by name at run time, so the import scan cannot see them.
    "aiosqlite",
    "sqlalchemy.dialects.sqlite.aiosqlite",
    "sqlalchemy.dialects.sqlite.pysqlite",
    "sqlalchemy.dialects.postgresql.asyncpg",
    "sqlalchemy.dialects.postgresql.psycopg2",
    "psycopg2",
    "asyncpg",
    # Imported only by alembic/env.py, which is read as a data file.
    "app.db.base",
    "app.db.engine",
    "app.db.models",
    "app.db.sqlite_baseline",
]


def main() -> None:
    shutil.rmtree(OUT, ignore_errors=True)
    args = [
        str(HERE / "od_backend.py"),
        "--name", "od-backend",
        "--noconfirm",
        "--onedir",
        "--console",
        "--distpath", str(OUT / "dist"),
        "--workpath", str(OUT / "work"),
        "--specpath", str(OUT),
        "--paths", str(BACKEND),
        "--add-data", f"{BACKEND / 'alembic'}{os.pathsep}alembic",
        "--collect-submodules", "uvicorn",
        "--collect-data", "tzdata",
    ]
    for name in HIDDEN:
        args += ["--hidden-import", name]
    started = time.monotonic()
    PyInstaller.__main__.run(args)
    print(f"built in {time.monotonic() - started:.0f} s")


if __name__ == "__main__":
    main()
