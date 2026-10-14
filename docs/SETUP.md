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
- To bring existing PostgreSQL data along, see [Moving existing data from PostgreSQL to SQLite](#moving-existing-data-from-postgresql-to-sqlite).
- Tested on Windows only. The code avoids OS-specific behaviour (paths use forward slashes in the database URL, POSIX file modes are applied only where they exist) but Linux and macOS runs are still to do.

### Moving existing data from PostgreSQL to SQLite

Copying and switching are two separate steps. The tool copies; you switch yourself afterwards.

1. **Stop the backend, the Telegram bot and the scheduler** (anything that writes to the database). The tool refuses if it sees active tasks, pending approvals or other connections on the database; `--allow-active` overrides that, but a copy taken while something is still writing can miss what it writes next.
2. From `backend/`, with `backend/.env` still pointing at PostgreSQL and the same `TOKEN_ENCRYPTION_KEYS` you used so far:
   ```
   uv run python -m app.db.copy_to_sqlite --dry-run     # checks and counts, creates nothing
   uv run python -m app.db.copy_to_sqlite               # copies to SQLITE_PATH (default ~/.open-dots/open-dots.db)
   ```
   `--target PATH` writes elsewhere. The target must not exist: the tool never overwrites a file (move or delete an old one yourself).
3. The tool reads PostgreSQL in one read-only snapshot, builds the SQLite file under a temporary name beside the target, copies every table keeping ids, then checks it: row counts, every row compared, every stored credential decrypted with your key, SQLite `integrity_check` and `foreign_key_check`. Only if everything passes is the file published. If anything fails, nothing is left behind. It prints table names and counts only, never row values.
4. To use SQLite, set `DATABASE_BACKEND=sqlite` in `backend/.env` (plus `SQLITE_PATH` if you used `--target`) and start the app. The tool does not change any setting.

**If something goes wrong**

- *"... already exists" (the target).* The app creates its SQLite file as soon as it starts with `DATABASE_BACKEND=sqlite`, and the tool's default target is that same file. Either run the copy before the app has ever started on SQLite, or use `--target` with a new name. The message says what is in the existing file (empty, an Open Dots database with N tasks, or something else). The tool never overwrites it; move or rename it yourself.
- *A `-wal` or `-shm` file is named.* SQLite keeps these next to a database while it is open. Move or rename them together with the `.db` file (same base name), and stop the backend first: they normally disappear when it stops cleanly. The tool refuses if only a side file remains, because it could be attached to a new database by mistake.
- *The page is empty after switching.* The app opened a different database file than the one you copied to. If you used `--target`, set `SQLITE_PATH` to it in `backend/.env`. The backend logs `Database in use: ...` at startup, so you can see which file it opened. Also use the same browser and address as before: the page remembers your user id in that browser.
- *Refused for active tasks or open connections.* Stop the backend, the bot and any database tool that is connected, then run it again.
- Inspecting an existing file to describe it never changes the database or its write-ahead log (SQLite's `-shm` index file may be touched, as by any reader).

**Rolling back:** set `DATABASE_BACKEND` back to `postgres` (or remove it). PostgreSQL still holds its data as it was when you copied; anything written in SQLite after you switched is not copied back, so rolling back loses it. Keep the PostgreSQL container and its volume until you are sure.

Tested on Windows with PostgreSQL 17 in Docker (`postgres:17-alpine`); the code avoids OS-specific behaviour (the no-overwrite publish uses a hard link, which fails atomically when the target exists, with a rename fallback for filesystems that have no hard links), but Linux and macOS have not been run yet.

## Local Claude (experimental)

Runs Claude through the `claude` CLI already installed and signed in on the machine that runs the backend, instead of a Boat sandbox. By default it is **chat-only**: tools, MCP servers and skills are all disabled, so Claude can answer but cannot read files or run commands. With `LOCAL_TOOLS_ENABLED=true` it can also use tools, each one waiting for your approval (see [Tools and approvals](#tools-and-approvals-local-claude)). The answer streams into the page as Claude writes it; there is no conversation history beyond a list of past answers yet.

1. Install Claude Code and sign in (`claude auth login`).
2. In `backend/.env` set `LOCAL_EXECUTION_ENABLED=true` and restart the backend. Optionally set `LOCAL_CLAUDE_CLI_PATH` to the full path of the real `claude` executable (on Windows the npm `claude.cmd` shim is resolved to the real `claude.exe` automatically) and `LOCAL_WORKSPACE_ROOT` (default `~/.open-dots/local`).
3. Open the web UI from this machine. A "Local Claude" card appears when the CLI is ready, or explains why it is not.

Who can use it: only requests that come from this machine, with a `localhost`, `127.0.0.1` or `[::1]` Host header, no proxy or tunnel headers, and a browser Origin listed in `FRONTEND_ORIGINS` (for example, opening the UI at `http://127.0.0.1:3000` requires adding that origin). Anything else, including schedules, Telegram and the older task endpoints, is refused. There is no password or token: anyone who can already run programs as you on this machine can use it. Do not enable it on a shared or publicly reachable server.

Settings: `LOCAL_EXECUTION_ENABLED` (default `false`), `LOCAL_TOOLS_ENABLED` (default `false`), `LOCAL_APPROVAL_TIMEOUT_SECONDS` (default 300), `LOCAL_ALLOWED_HOSTS`, `LOCAL_CLAUDE_CLI_PATH`, `LOCAL_WORKSPACE_ROOT`, `LOCAL_TASK_TIMEOUT_SECONDS` (default 300), `LOCAL_MAX_OUTPUT_BYTES` (default 1000000; the limit on the answer text, and the raw CLI stream may be up to 8 times larger).

Streaming: the page opens a server-sent-events stream (`GET /api/v1/local/tasks/{id}/events`) for each run and shows the text as it arrives; the final result and any error always come from the saved task. Closing or refreshing the tab does not stop a run (it ends or times out on its own), and reopening the page shows the answer so far. Streams are held in memory, so live streaming needs a single backend process; with several workers, or after a restart, the page falls back to polling the saved task. Nothing is replayed after a disconnect.

### Tools and approvals (local Claude)

Set `LOCAL_TOOLS_ENABLED=true` in `backend/.env` and restart the backend. Claude can then use six tools in its workspace folder (`LOCAL_WORKSPACE_ROOT`, default `~/.open-dots/local/workspace`): `Read`, `Write`, `Edit`, `Glob`, `Grep` and `Bash`. There is no web access, no sub-agents and no MCP servers.

How it works: the page shows each tool call as a step in a timeline. When the CLI would ask a person before running something, the run stops and the page shows a card with the tool and its exact input (the full command, the file path and the new contents) and **Allow** and **Deny** buttons. Nothing runs until you press Allow, and what runs is exactly what the card showed. Deny, an unanswered question (`LOCAL_APPROVAL_TIMEOUT_SECONDS`, default 300) or a run that ends all count as a refusal; Claude is told and carries on. There is no "always allow". A request too large to be shown in full (over 50000 characters) is refused rather than approved unseen. Time spent waiting for you does not count towards `LOCAL_TASK_TIMEOUT_SECONDS`.

What you must know before turning it on:

- Once you press Allow, a command runs **as you, on this computer, without a sandbox**. Read the command before approving. This is the same trust you give the Claude CLI in a terminal.
- The CLI, not this app, decides which calls ask. In its default mode it asks for writes, for anything outside the workspace folder, and for commands it does not recognise as read-only. A few read-only commands (for example `echo` and `ls`) and reads inside the workspace run without asking, and permission rules in your own Claude settings are not turned off by this app, so a rule there that allows something will let it run unasked. Tested with Claude Code 2.1.232: a write inside the workspace, a read of `C:\Windows\win.ini` and `python -c ...` each asked; denying each stopped it.
- Approvals and the timeline live in memory, like the answer stream (single backend process). The timeline is shown while you keep the page open and is not saved; the saved task keeps only the answer. If the backend restarts during a run, the run ends as interrupted.
- Each local task is a new conversation. Threads and resuming come later.
- Anyone who can already reach the local routes (see "Who can use it") can approve, because there is no password. Keep it off shared machines.

Tested on Windows with Claude Code 2.1.232. The code is written for Windows, macOS and Linux (OS-specific parts are guarded in `local_claude_runner.py`: the npm `claude.cmd` shim, `taskkill` versus `killpg`, console-window flags), but macOS and Linux have not been run yet.

This file will change when the local-first launcher (roadmap F09/F10) ships.
