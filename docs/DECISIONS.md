# Architecture Decisions

Format: decision, evidence, rejected alternative, consequence, date.

## D-001 — Separate public `docs/` from local `.internal/`

- **Decision:** `docs/` is tracked and holds contributor/user documentation; `.internal/` is Git-ignored and holds agent handoff notes and raw investigations.
- **Evidence:** `.gitignore` previously ignored `docs/`; it now ignores `.internal/`.
- **Rejected:** keeping all planning in one ignored `docs/` (nothing shareable), or tracking handoff logs (risk of leaking machine-specific data).
- **Consequence:** A fresh clone has no `.internal/`; status must be derived from code, tests, commits and public docs.
- **Date:** 2026-10-13

## D-002 — Native launcher for host CLI execution

- **Decision:** Early beta runs natively on loopback; Docker isolation comes later as a computer provider.
- **Evidence:** A containerized server cannot see host CLI logins or processes.
- **Rejected:** copying consumer login tokens into images.
- **Date:** 2026-10-13

## D-003 — Local execution is gated by a loopback access policy, not a token

- **Decision:** Local Claude runs are allowed only for requests from the same machine (loopback client, trusted Host, trusted Origin, no proxy headers), behind `LOCAL_EXECUTION_ENABLED` (default off). The check lives in one module, `local_access_policy`, which issues a grant that the execution service requires. Local tasks use their own provider value and are hidden from the guest-id endpoints. Runs are chat-only (all tools disabled) unless `LOCAL_TOOLS_ENABLED` is set, which became possible once approvals existed (D-006).
- **Evidence:** Peer projects rely on loopback-only for local execution; a user-entered token would add friction to the first run and would not protect the older unauthenticated endpoints anyway.
- **Rejected:** a mandatory owner token for every route (breaks existing guest and Telegram users); no check at all (any website or tunnel could start a process on the machine).
- **Consequence:** Another program or user on the same machine can use the feature. The policy sits behind an interface so a launcher-issued token can replace it for a desktop build.
- **Date:** 2026-10-13


## D-004 — Local run events are in memory; storage and replay come later

- **Decision:** F02 streams a local run's text to the browser as server-sent events. The events (`turn.started`, `text.delta`, `turn.completed`, `turn.failed`, `runtime.status`) use one envelope, `{run_id, seq, type, data, ts}`, with `seq` counting from 1 per run. They are kept only in memory while a run is live, plus a short time after it ends. There is no event table, no migration and no `Last-Event-ID` replay. A listener that connects late first receives the text so far as one `text.delta` with `mode: "replace"`, then live `mode: "append"` deltas. The task row (`task.result`, `task.error`, status) stays the record of the outcome and is saved before the terminal event is sent.
- **Evidence:** Replay and durable events need the thread/turn model (F04) and cancellation (F05) to be designed first; adding a table now would fix a schema before those exist. The existing task endpoint already returns the latest status and final answer after a reconnect.
- **Rejected:** persisting every event now (premature schema); relying on the stream for the outcome (a dropped connection could then look like success).
- **Consequence:** After a server restart, or when a different worker serves the request, a stream can only report the saved task state, and the UI falls back to polling the task. Live streaming therefore supports one backend process. The envelope already carries `run_id` and `seq`, so F04 can store events and add replay without changing what clients receive.
- **Date:** 2026-10-13


## D-005 — SQLite is an opt-in second database; PostgreSQL stays the default

- **Decision:** The backend can use a local SQLite file (`DATABASE_BACKEND=sqlite`, file `SQLITE_PATH`, default `~/.open-dots/open-dots.db`) alongside PostgreSQL. With `DATABASE_BACKEND` empty it uses PostgreSQL exactly as before. A new SQLite file is created from a frozen schema baseline stamped at the current Alembic revision, because the existing PostgreSQL migration chain alters constraints and uses `now()` defaults that SQLite cannot run; later migrations run on both. Models use dialect-aware types (integer identity key and a UTC datetime type on SQLite) that compile to the same PostgreSQL DDL as before, which a snapshot test checks.
- **Evidence:** A trial run of the existing test suite on SQLite passed once these two type differences were handled, and a copy of a development PostgreSQL database into SQLite preserved every row, id and timestamp. Concurrency, not schema, was the main risk: SQLite has no row locks and a transaction keeps the snapshot of its first read. The permission-approval wait, reply admission, schedule claiming and every place that held a transaction open across a slow sandbox or CLI call were reworked (a `begin_write` helper that starts a write transaction up front on SQLite, a `release_snapshot` helper before slow waits, and conditional UPDATEs for state transitions), with tests that run several simultaneous connections on both databases. Separate OS processes sharing one file and real Boat sandboxes on SQLite are not yet exercised.
- **Rejected:** inferring SQLite from missing `POSTGRES_*` values (those have defaults that match docker-compose, so an existing setup may have none and would silently move to an empty database); making SQLite the default now (the Boat permission flow is not safe on it yet); removing PostgreSQL (existing data, Boat and Telegram deployments).
- **Data migration:** `python -m app.db.copy_to_sqlite` copies PostgreSQL into a new SQLite file. It only reads PostgreSQL (one read-only REPEATABLE READ snapshot, with writers required to be stopped), verifies before publishing, never overwrites a file, and never changes `DATABASE_BACKEND`: copying and switching are separate steps. Rolling back to PostgreSQL restores its state at copy time, not later SQLite writes.
- **Consequence:** Two databases to test; tests run on either (`DATABASE_BACKEND=sqlite uv run pytest` uses a temporary file, never the user's database). The default can be revisited, with notice, once concurrency is hardened and a tested data-migration tool exists.
- **Date:** 2026-10-14


## D-006 — Local tools ask a person through the CLI's own permission protocol

- **Decision:** With `LOCAL_TOOLS_ENABLED` (default off), a local run starts the CLI in bidirectional stream-json mode with `--permission-mode default` and `--permission-prompt-tool stdio`. Each time the CLI would prompt a person it sends a `can_use_tool` control request on stdout and waits for a `control_response` on stdin. An in-memory approval broker holds the question until the web UI answers it (Allow or Deny), and replies with the original input unchanged. Anything else (no answer within `LOCAL_APPROVAL_TIMEOUT_SECONDS`, the CLI withdrawing the call, the run ending, an input too large to display, a control question the app does not know) is a refusal. The tool set is fixed (`Read`, `Write`, `Edit`, `Glob`, `Grep`, `Bash`). The run's time limit is paused while a question is open. Chat-only runs are launched exactly as before.
- **Evidence:** Verified against Claude Code 2.1.232 with real runs: a write, a read outside the workspace and a non-trivial shell command each produced a control request; allowing ran the tool with the shown input, denying stopped it and Claude explained; stdin was closed after the `result` event and the process exited with code 0. Permission mode `default` was chosen over `acceptEdits`, `dontAsk` and `bypassPermissions`, none of which ask.
- **Rejected:** a `PermissionRequest` hook over HTTP, as Boat uses (needs a reachable URL and token, and a second approval path for no benefit locally); an app-maintained allow-list (the CLI already knows which calls are safe, and a second list would drift from it); "always allow" rules (a durable grant needs the F04 data model and a review UI first); persisting approvals now (they belong to threads and events, F04).
- **Consequence:** The CLI, not this app, decides which calls ask, so some read-only commands run unasked and the user's own Claude permission rules still apply; the docs say so. An approved `Bash` call is unsandboxed. Approvals are in memory, so live approval supports one backend process, like D-004. Replacing the broker's store with a table is the only change F04 needs.
- **Date:** 2026-10-14
