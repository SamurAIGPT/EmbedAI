# Setup

The authoritative quick start is the [README](../README.md#quick-start). Summary:

**Prerequisites:** `uv`, Node.js 20.9+ with npm, Docker (for Postgres; not needed with the experimental SQLite option), Bash (Git Bash on Windows), a Boat API key, and optionally a Telegram bot token.

**Run:** configure `backend/.env` from `backend/.env.example` and `frontend/.env` from `frontend/.env.example`, then start the stack with `scripts/dev.sh`.

Component-specific details: [backend/README.md](../backend/README.md), [frontend/README.md](../frontend/README.md).

## Database

**PostgreSQL is the default** and is what the Docker setup above starts. Nothing changes for an existing installation.

**SQLite (experimental, opt-in):** one local file, no Docker. Set `DATABASE_BACKEND=sqlite` in `backend/.env` (optionally `SQLITE_PATH`; the default is `~/.open-dots/open-dots.db`), then run `uv run alembic upgrade head` (or `scripts/dev.sh`, which skips Docker for SQLite). A new SQLite file is created from a frozen baseline of the current schema and stamped at the current revision; an existing file is upgraded like any other. A SQLite file that is not one this app created (tables but no version table, an empty version table, missing app tables, or not a SQLite file at all) is refused and left byte-for-byte unchanged.

What to know before using SQLite:

- The database holds encrypted credentials. The file is created readable only by you on Linux and macOS; on Windows it lives in your profile folder. Keep it out of synced or shared folders. Back it up while the app is stopped, or with SQLite's backup command; do not copy the file while the app is running.
- It runs in WAL mode with foreign keys on and a 5 second busy timeout. Writers take turns. The places that relied on PostgreSQL row locks or on a transaction staying open were reworked so they behave the same on both databases: the permission-approval wait, conflicting answers to one approval, reply admission, schedule claiming, and the code that talks to a sandbox or the Claude CLI (it no longer holds a database snapshot while it waits). The test suite checks these with several simultaneous connections on both databases.
- Still unproven on SQLite: the API, the Telegram bot and the scheduler running as separate operating-system processes on one file, and real Boat sandboxes end to end. Keep PostgreSQL for those until they are exercised; SQLite is suitable for trying the local Claude path and the web UI.
- There is no tool yet to copy existing PostgreSQL data into SQLite. A trial copy of a development database kept all rows, ids and timestamps, but a supported migration tool with backup and rollback is planned.
- Tested on Windows only. The code avoids OS-specific behaviour (paths use forward slashes in the database URL, POSIX file modes are applied only where they exist) but Linux and macOS runs are still to do.

## Local Claude (experimental)

Runs Claude through the `claude` CLI already installed and signed in on the machine that runs the backend, instead of a Boat sandbox. It is **chat-only**: tools, MCP servers and skills are all disabled, so Claude can answer but cannot read files or run commands. The answer streams into the page as Claude writes it; there is no conversation history beyond a list of past answers yet.

1. Install Claude Code and sign in (`claude auth login`).
2. In `backend/.env` set `LOCAL_EXECUTION_ENABLED=true` and restart the backend. Optionally set `LOCAL_CLAUDE_CLI_PATH` to the full path of the real `claude` executable (on Windows the npm `claude.cmd` shim is resolved to the real `claude.exe` automatically) and `LOCAL_WORKSPACE_ROOT` (default `~/.open-dots/local`).
3. Open the web UI from this machine. A "Local Claude" card appears when the CLI is ready, or explains why it is not.

Who can use it: only requests that come from this machine, with a `localhost`, `127.0.0.1` or `[::1]` Host header, no proxy or tunnel headers, and a browser Origin listed in `FRONTEND_ORIGINS` (for example, opening the UI at `http://127.0.0.1:3000` requires adding that origin). Anything else, including schedules, Telegram and the older task endpoints, is refused. There is no password or token: anyone who can already run programs as you on this machine can use it. Do not enable it on a shared or publicly reachable server.

Settings: `LOCAL_EXECUTION_ENABLED` (default `false`), `LOCAL_ALLOWED_HOSTS`, `LOCAL_CLAUDE_CLI_PATH`, `LOCAL_WORKSPACE_ROOT`, `LOCAL_TASK_TIMEOUT_SECONDS` (default 300), `LOCAL_MAX_OUTPUT_BYTES` (default 1000000; the limit on the answer text, and the raw CLI stream may be up to 8 times larger).

Streaming: the page opens a server-sent-events stream (`GET /api/v1/local/tasks/{id}/events`) for each run and shows the text as it arrives; the final result and any error always come from the saved task. Closing or refreshing the tab does not stop a run (it ends or times out on its own), and reopening the page shows the answer so far. Streams are held in memory, so live streaming needs a single backend process; with several workers, or after a restart, the page falls back to polling the saved task. Nothing is replayed after a disconnect.

Tested on Windows with Claude Code 2.1.232. The code is written for Windows, macOS and Linux (OS-specific parts are guarded in `local_claude_runner.py`: the npm `claude.cmd` shim, `taskkill` versus `killpg`, console-window flags), but macOS and Linux have not been run yet.

This file will change when the local-first launcher (roadmap F09/F10) ships.
