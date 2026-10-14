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

- **Decision:** Local Claude runs are allowed only for requests from the same machine (loopback client, trusted Host, trusted Origin, no proxy headers), behind `LOCAL_EXECUTION_ENABLED` (default off). The check lives in one module, `local_access_policy`, which issues a grant that the execution service requires. Local tasks use their own provider value and are hidden from the guest-id endpoints. Runs are chat-only (all tools disabled) until approvals exist.
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
- **Evidence:** A trial run of the existing test suite on SQLite passed once these two type differences were handled, and a copy of a development PostgreSQL database into SQLite preserved every row, id and timestamp. Concurrency, not schema, is the remaining risk: SQLite has no row locks, so the permission-approval polling, reply admission and schedule claiming need dedicated work before SQLite can be recommended.
- **Rejected:** inferring SQLite from missing `POSTGRES_*` values (those have defaults that match docker-compose, so an existing setup may have none and would silently move to an empty database); making SQLite the default now (the Boat permission flow is not safe on it yet); removing PostgreSQL (existing data, Boat and Telegram deployments).
- **Consequence:** Two databases to test; tests run on either (`DATABASE_BACKEND=sqlite uv run pytest` uses a temporary file, never the user's database). The default can be revisited, with notice, once concurrency is hardened and a tested data-migration tool exists.
- **Date:** 2026-10-14
