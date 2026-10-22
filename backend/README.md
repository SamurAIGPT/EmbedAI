# Open Duet — Backend

FastAPI service behind Open Duet's AI coworkers: named agents, durable
conversations on several engines, routines, approvals, memory and agent
computers, plus an optional Telegram bot and the original task flow in
disposable sandboxes provided by [Boat](https://docs.boat.dev).

For prerequisites, `.env` setup, and how to run the whole stack (this API,
the Telegram bot, and the [web UI](../frontend)) with one command, see the
[repo root README](../README.md). This file covers backend-specific details.

## Layout

- `app/routers/` — REST API: `sandboxes` (sandbox lifecycle + Claude/GitHub
  auth), `tasks` (create/get/list), `link` (web ↔ Telegram identity linking),
  `schedules` (create/list/get/update/pause/resume/run-now/delete, and
  `/{id}/runs` for its task history).
- `app/services/` — `box_operations` (Boat SDK wrapper), `auth_flow_service`
  (OAuth/device-flow login inside a sandbox), `user_credential_service`
  (encrypted credential storage; also pulls a renewed login back out of the
  box instead of overwriting it), `user_sandbox_service`, `link_service`,
  `task_execution_service` (`create_task`/`start_task` split so a task row
  always exists before anything is launched, plus `sync_active_tasks`),
  `schedule_service` (cron scheduling — `poll_due_schedules`,
  `report_finished_runs`), `notification_service` (Telegram delivery,
  independent of whether the bot process is running).
- `app/core/background.py` — `run_every`, the plain `asyncio` loop the three
  background jobs above run on; started in `app/main.py`'s FastAPI lifespan.
- `app/telegram_bot.py` — the bot, built on the same services as the REST
  API. Includes `/schedule`, `/schedules`, `/pause`, `/resume`,
  `/unschedule` — these only read/write the `schedule` table; the bot
  process does not itself run schedules (see [Scheduling](#scheduling)).
- `alembic/` — migrations. Run `uv run alembic upgrade head` after pulling
  changes that touch `app/db/models/`.

## Running just the backend

To run the whole app on a server (pages and API in one process, with its own database commands), see [docs/SELF-HOSTING.md](../docs/SELF-HOSTING.md); the steps below are for developing the backend.

```bash
docker compose up -d                # Postgres
uv sync                             # install deps
uv run alembic upgrade head         # migrations
uv run fastapi dev app/main.py      # API — http://127.0.0.1:8000, docs at /docs
uv run python -m app.telegram_bot   # bot, in a separate terminal
```

## Scheduling

`app/main.py`'s FastAPI lifespan starts three `asyncio` loops (30s interval,
`app/core/background.py`) that poll, launch, and report scheduled runs — see
the [repo root README](../README.md#scheduling-tasks) for how they fit
together. They only run inside the API process: running the bot without the
API lets users create/list/pause/resume/delete schedules (it just writes to
the `schedule` table), but nothing will actually fire until
`uv run fastapi dev app/main.py` is also running.

Scheduled-run notifications go out over Telegram via `notification_service`,
using `telegram.Bot` directly — not through `app/telegram_bot.py`'s running
`Application` — so they're sent by the API process even if the bot process
isn't running. They require `TELEGRAM_BOT_TOKEN` and only reach users whose
`user_id` is numeric (Telegram-linked); a web-only guest's `user_id` (e.g.
`web_...`) has no matching chat, so notifications are silently skipped for
them — they still see outcomes in the web UI.

## Known limitations

- Sandboxes are never automatically stopped/deleted — repeated `/connect`
  testing will accumulate them on the Boat account.
- The bot's own polling loop (5 min) is shorter than the task command
  timeout (10 min), so a long-running task can appear to "fail" in Telegram
  before the sandbox actually gives up on it.
- Link codes expire after 10 minutes; there's no rate limiting on generating
  them.
- Schedules poll on a 30s interval (so a run can start up to ~30s late) and
  skip an occurrence missed by more than 15 minutes rather than run it late.
- No automated tests yet.
