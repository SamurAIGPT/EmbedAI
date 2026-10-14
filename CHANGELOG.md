# Changelog

## Unreleased

### Changed
- Boat is now an optional provider. `BOAT_API_KEY`, `HOOK_TOKEN` and `PERMISSION_HOOK_BASE_URL` are no longer required to start the backend; `scripts/dev.sh` only requires the hook settings when a Boat key is set. Work that needs a Boat sandbox answers 503 "Boat is not configured" when the key is empty, and the permission hook endpoint rejects every request when `HOOK_TOKEN` is empty. Existing setups with a key are unchanged. Without a Boat key the app is chat-only: local Claude answers questions but cannot use tools, files, schedules, Telegram tasks or GitHub push yet, so keep Boat configured if you need those.

### Added
- Experimental SQLite storage (opt-in, no Docker): set `DATABASE_BACKEND=sqlite`. PostgreSQL remains the default and nothing changes for existing setups. New SQLite files start from a frozen baseline of the current schema; models use dialect-aware types that leave the PostgreSQL schema unchanged. Concurrency was reworked for SQLite (permission-approval wait, conflicting approval answers, reply admission, schedule claiming, no database snapshot held across slow sandbox/CLI calls); PostgreSQL behaviour is unchanged. Keep PostgreSQL for real Boat sandboxes and multi-process setups until those are exercised on SQLite; see [docs/SETUP.md](docs/SETUP.md#database) and [D-005](docs/DECISIONS.md).
- Local Claude answers now stream into the web UI as Claude writes them (server-sent events, `GET /api/v1/local/tasks/{id}/events`). The saved task still decides the final outcome, a partial answer is kept if a run fails, and an empty answer is shown as such. The existing polling endpoints still work. Events live in memory for now (single backend process); see [D-004](docs/DECISIONS.md).
- Local Claude (experimental, off by default): run chat-only prompts through your own signed-in `claude` CLI from the web UI. Only usable from the machine running the backend. See [docs/SETUP.md](docs/SETUP.md#local-claude-experimental).
