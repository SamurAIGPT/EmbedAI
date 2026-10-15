# Contributing

Issues and pull requests are welcome.

## Workflow

1. Check [ROADMAP.md](ROADMAP.md) and pick one feature or fix; keep PRs small and focused.
2. Branch from `main`; use Conventional Commit prefixes (`feat:`, `fix:`, `test:`, `docs:`, `chore:`).
3. Add focused tests for service or API changes; run backend tests and the frontend lint/build.
4. Keep the README and `docs/` aligned with behavior; note limitations honestly.
5. Do not merge with failing checks.

## Database rules (PostgreSQL and SQLite)

The backend runs on PostgreSQL and, experimentally, on SQLite, and tests run on both (`DATABASE_BACKEND=sqlite uv run pytest` uses a temporary file). Code that touches the database must work on both:

- A transaction that does a read and then waits on something slow (a sandbox or CLI call, a sleep, a Telegram message) must end first: call `release_snapshot(db)` from `app/db/transactions.py` after the read. SQLite keeps the snapshot of a transaction's first read, so a later read would not see other writers' commits and a later write would fail with `database is locked`.
- A read followed by a write that must not interleave with another request (check-then-insert, claim-then-update) starts with `begin_write(db)`, or uses one conditional `UPDATE ... WHERE status = ...` and checks the row count. Do not rely on `SELECT ... FOR UPDATE`: SQLite ignores it.
- Use the types in `app/db/types.py` for identity keys and datetimes, and add SQLite-safe migrations (`op.batch_alter_table` for anything that alters constraints). The PostgreSQL schema must not change as a side effect; `tests/db/test_postgres_schema_unchanged.py` checks it.
- Thread data (`thread`, `turn`, `event`, `approval`) changes only through `app/services/thread_store.py`. Each state change there is one conditional `UPDATE` (a heartbeat, finishing or interrupting a turn, an approval answer), so racing requests and processes cannot both win; an ad-hoc query that skips them can quietly break that.
- A table added after the original schema has its own reviewed PostgreSQL snapshot (for example `tests/db/postgres_schema_f04.sql`); changing the table means deliberately editing that file.

## Security expectations

- Authenticate and validate every route that mutates state or runs code.
- Never commit secrets, tokens, raw provider transcripts or unsanitized captures.
- Do not silently weaken sandboxing or bypass approvals.
- Features exposing host execution, subprocesses or files need a security review in the PR.

## Frontend note

The frontend uses a recent Next.js with breaking changes; read the guides in `frontend/node_modules/next/dist/docs/` before editing it.
