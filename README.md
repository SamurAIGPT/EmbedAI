# Open Dots: Open-Source Alternative to Paid AI Agents

**Open Dots is an open-source, self-hosted alternative for people exploring
paid AI agent products such as OpenAI Dots, Meta Muse, Grok Bot, Instinct,
Manus Cue, Claude Cowork, and ChatGPT agent.** The current prototype focuses
on coding-agent workflows: run Claude Code tasks from Telegram or the web,
review risky actions, connect GitHub, and schedule recurring prompts. Its
features differ from those services; it is not a feature-for-feature
replacement.

Both interfaces use the same API, sandbox, and credentials for each user.

<p align="center"><a href="https://youtu.be/b4ZTfs0KwR0" title="Watch the Open Dots demo on YouTube"><img src="https://i.ytimg.com/vi/b4ZTfs0KwR0/maxresdefault.jpg" alt="Open Dots demo video thumbnail — click to watch on YouTube" width="720"></a></p>
<p align="center"><a href="https://youtu.be/b4ZTfs0KwR0"><b>▶ Watch the Open Dots demo on YouTube</b></a></p>

> **Status:** Prototype / active development. See [Known limitations](#known-limitations) before running it for others.

## Contents

- [What it does](#what-it-does)
- [Why Open Dots](#why-open-dots)
- [Quick start](#quick-start)
- [Using Open Dots](#using-open-dots)
- [Scheduling tasks](#scheduling-tasks)
- [Permission prompts](#permission-prompts)
- [Architecture](#architecture)
- [Project layout](#project-layout)
- [Known limitations](#known-limitations)
- [Contributing](#contributing)

## What it does

- Run coding-agent tasks in disposable sandboxes from Telegram or a browser.
- Connect Claude through its OAuth login flow and optionally connect a user's GitHub account.
- Ask for approval before risky actions such as file writes, shell commands, and Git pushes.
- Continue completed tasks in the same Claude conversation from the web UI.
- Schedule recurring prompts and receive run results in Telegram.
- Store user credentials encrypted and manage sandbox, task, permission, and schedule records in PostgreSQL.

## Why Open Dots

Open Dots gives individuals and developers an inspectable workspace they can run themselves, with coding tasks, visible approval steps, and local control over the app and its data. The web UI supports guest use without Telegram and can also link to a Telegram identity.

## Quick start

### Prerequisites

- [uv](https://docs.astral.sh/uv/) for Python dependency management
- [Node.js](https://nodejs.org/) (20.9+) and npm, for the web UI (required by Next.js 16)
- [Docker](https://docs.docker.com/get-docker/) (for Postgres)
- Bash (Git Bash on Windows works fine) to use `scripts/dev.sh`
- A [Boat API key](https://docs.boat.dev/api-keys) for sandbox creation. If
  it belongs to a personal account that also has an org/team on a paid plan,
  you also need
  that org's id (see `BOAT_ORG_ID` below) — otherwise sandbox creation bills
  the personal account and returns `402 Payment Required`.
- A Telegram bot token from [@BotFather](https://t.me/BotFather) (only
  needed if you want to run the bot, or let people link the web UI to
  Telegram — the web UI's guest mode works without it)
- A public URL the sandbox can reach to call back into your backend for
  permission decisions (see [Permission prompts](#permission-prompts)) — in
  local dev this means tunneling your backend with
  [ngrok](https://ngrok.com/) or similar; not needed to run the app, only
  for any task that triggers a risky action (writing files, shell commands,
  etc.) to actually get a decision instead of hanging until it times out.

### Configure the environment

```bash
cp backend/.env.example backend/.env
```

Fill in `backend/.env`:

| Var | Required | Notes |
|---|---|---|
| `BOAT_API_KEY` | yes | From the Boat dashboard. |
| `BOAT_BASE_URL` | no | Defaults to `https://boat.dev/api/v1`. |
| `BOAT_ORG_ID` | see note above | Team/org id (e.g. `team_...`) to attach to sandbox creation so it bills the org instead of your personal account. |
| `PARALLEL_SEARCH_ENABLED` | no | Set to `true` to add free Parallel web search and page fetching to Claude tasks. Defaults to `false`. See [Optional web search](#optional-web-search). |
| `TOKEN_ENCRYPTION_KEYS` | yes | Fernet key used to encrypt stored credentials. Generate with `uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Comma-separate multiple keys to support rotation. |
| `TELEGRAM_BOT_TOKEN` | only for the bot / Telegram linking | From @BotFather. |
| `TELEGRAM_BOT_USERNAME` | no | Your bot's `@username` (no `@`), shown as a clickable link in the web UI. Cosmetic only. |
| `FRONTEND_ORIGINS` | no | Comma-separated origins allowed to call the API from a browser. Defaults to `http://localhost:3000`. |
| `HOOK_TOKEN` | yes | Shared secret the permission hook's caller (the sandbox) must present, so only your backend's own sandboxes can submit permission decisions. Generate with `python -c "import secrets; print(secrets.token_urlsafe(32))"`. |
| `PERMISSION_HOOK_BASE_URL` | yes | The public URL from the ngrok-or-similar prerequisite above, no trailing slash — e.g. `https://your-tunnel.ngrok-free.app`. Baked into each sandbox's `.claude/settings.json` at task start. |
| `POSTGRES_*` | no | Defaults match `backend/docker-compose.yml`. |

The web UI (`frontend/`) has its own `.env.example` — `scripts/dev.sh` copies
it to `.env.local` automatically on first run. Copy it yourself if you're
running the frontend separately, and point `NEXT_PUBLIC_API_BASE_URL` at the
backend if you're not using the defaults.

### Start the app

```bash
./scripts/dev.sh
```

From the repo root, this starts Postgres via Docker, waits for it to be
healthy, installs backend and frontend dependencies, runs migrations, and
starts the FastAPI app (`http://127.0.0.1:8000`, docs at `/docs`), the
Telegram bot, and the web UI (`http://127.0.0.1:3000`). Ctrl+C stops
everything it started (Postgres keeps running — `docker compose -f
backend/docker-compose.yml down` to stop it too).

Skip pieces you don't need:

```bash
./scripts/dev.sh --no-bot        # API + web UI only
./scripts/dev.sh --no-frontend   # API + bot only
```

#### Run the pieces manually

```bash
cd backend
docker compose up -d                      # Postgres
uv sync                                   # install deps
uv run alembic upgrade head               # migrations
uv run fastapi dev app/main.py            # API (terminal 1)
uv run python -m app.telegram_bot         # bot (terminal 2)

cd ../frontend
npm install && npm run dev                # web UI (terminal 3)
```

## Using Open Dots

The Telegram bot walks a user through:

1. `/connect` — creates a sandbox and starts an OAuth login for the agent
   provider (currently Claude) inside it.
2. Pasting the OAuth code — the bot reads the resulting credential file out
   of the sandbox, encrypts it, and stores it.
3. `/connect_github` (optional) — links the user's own GitHub account via
   device-flow login, so tasks that `git push` do so as that user rather
   than a shared/anonymous identity. No token is ever typed into the chat:
   the bot posts a one-time code and a `github.com/login/device` link, the
   user approves it in their browser, and the bot picks up the result on
   its own.
4. Any plain text message — runs it as a task in the user's sandbox and
   replies with the result once it finishes. If the user has connected
   GitHub, that credential is restored into the sandbox before the task
   runs too.
5. `/schedule` — repeats a prompt on a cron schedule (hourly/daily/weekdays/
   weekly, always in UTC) instead of running it once. `/schedules` lists
   them, `/pause`/`/resume`/`/unschedule` manage one. Each run is reported
   back in the chat — the result on success, the error on failure — and a
   schedule that can't succeed (e.g. an expired login) is paused
   automatically instead of retrying forever. See [Scheduling
   tasks](#scheduling-tasks) below.

The web UI covers the same ground from a browser:

1. **Get started** — either continue as a guest (a local id, no Telegram
   needed) or link Telegram: the page generates a one-time code, you send
   `/link <code>` to the bot, and that links the browser to your real
   Telegram user id instead.
2. **Connect Claude** — same OAuth flow as `/connect`, driven by REST
   endpoints instead of chat messages (get-or-create the sandbox, start
   login, paste the code back into the page).
3. **Connect GitHub** (optional) — same device-flow login as
   `/connect_github`: the page shows a one-time code and a
   `github.com/login/device` link, and picks up the result on its own once
   you approve it — no code to paste back.
4. **Submit a task** — a prompt box that submits to the same
   `POST /api/v1/tasks` endpoint, polls for the result, and lists past tasks
   for that user. Finished tasks can be replied to in place to continue that
   same Claude conversation, instead of starting a new unrelated one.
   Checking "Repeat this on a schedule" turns the same prompt into a
   recurring schedule instead (`POST /api/v1/schedules`) — pick a frequency
   (hourly/daily/weekdays/weekly) and a time, in the browser's own timezone.
   Schedules are listed below the composer with pause/resume/delete
   controls. See [Scheduling tasks](#scheduling-tasks).


In Telegram:

1. `/start`
2. `/connect` — creates a sandbox (can take a bit) and replies with a login
   URL. Open it, complete the OAuth flow, and paste the code back into the
   chat.
3. `/connect_github` (optional) — reply has a code and a link; open the link,
   enter the code, approve it. No need to send anything back in the chat.
4. Send any message, e.g. `create a webpage that says hello` — the bot runs
   it in your sandbox and replies with the result (or the error).
5. `/schedule daily 08:00 generate my report` — repeats that prompt every
   day at 08:00 UTC. `/schedules` shows it; `/pause 1`, `/resume 1`,
   `/unschedule 1` manage it by the id the bot gave you. The API process
   must be running for it to actually fire — see [Scheduling
   tasks](#scheduling-tasks).

In the web UI (`http://localhost:3000`):

1. **Get started** — click "Continue without Telegram" for a standalone
   guest session, or "Link Telegram instead" and send `/link <code>` to the
   bot (the page picks this up automatically).
2. **Connect Claude** — click "Start login", open the link, and paste the
   code back into the page.
3. **Connect GitHub** (optional) — click "Connect GitHub", open the link,
   enter the code shown. No need to come back to the page — it updates once
   you approve it.
4. **Submit a task** — type a prompt and submit; the page polls for the
   result and lists past tasks below. Click a finished task to expand it and
   reply in that same conversation.
5. Check **"Repeat this on a schedule"**, pick Daily and a time a couple of
   minutes out, and click "Create schedule". It appears in the schedules
   list below with its next run time; pause/resume/delete it from there.

### Optional web search

Set `PARALLEL_SEARCH_ENABLED=true` in `backend/.env` and restart the backend
and bot processes to make [Parallel Search MCP](https://docs.parallel.ai/integrations/mcp/search-mcp)
available to new Claude task launches, including replies and scheduled runs.
It connects over HTTP to `https://search.parallel.ai/mcp` and provides
`web_search` and `web_fetch` without a Parallel account or API key. Anonymous
search uses Fast mode and is intended for light use, with rate limits.

For example, submit: “Use Parallel to find the latest Python release, then
fetch its release page and summarize the changes.” Approve the MCP tool calls
through the existing permission prompts when asked. Claude still needs its
usual connected account. The setting is off by default and leaves saved MCP
configuration in place.

## Scheduling tasks

A schedule (`schedule` table) is a saved prompt plus a cron expression and
timezone. Three background loops in the **API process** — not the bot
process — drive it (`app/core/background.py`, `app/services/
schedule_service.py`, all on a 30s interval):

- **`poll_due_schedules`** — claims schedules whose `next_run_at` has
  passed and creates a `Tasks` row for each occurrence (`FOR UPDATE SKIP
  LOCKED`, safe with more than one API instance), then starts it.
- **`sync_active_tasks`** — finishes tasks that have no one polling them,
  the way a scheduled run does (nobody's watching a `GET /tasks/{id}` for
  it). Also recovers a task orphaned by a crash mid-launch.
- **`report_finished_runs`** — for each finished scheduled run, reported
  exactly once: sends the result on success, the error on failure, and
  pauses the schedule if it can't succeed on its own (an expired login,
  a missing credential) or after 3 failures in a row. Delivered over
  Telegram, to users identified by a numeric (Telegram-linked) `user_id`
  only — a web-only guest sees the outcome in the schedules list instead.

Because these loops live in the API process, **the API must be running for
a schedule to actually fire** — creating one from Telegram doesn't require
the bot process to also run the schedule (it only writes the row), but
nothing fires without `uv run fastapi dev app/main.py` (or `./scripts/
dev.sh`) up.

A schedule created from the web UI uses the browser's own timezone,
captured at creation time. One created from Telegram is always UTC, since
Telegram gives the bot no way to read the user's timezone — the bot's
replies always spell out times in UTC.

## Permission prompts

Every risky action the AI attempts inside a sandbox — writing a file,
running a shell command, a `git push`, etc. — is intercepted by a
`PermissionRequest` hook before it runs, and checked against a per-user
rule table (`permission_rule`). No matching rule means the task pauses
(status `waiting_approval`) and you're asked, in whichever channel you're
using:

- **Telegram**: a message with **Allow once** / **Always allow** / **Deny**
  buttons.
- **Web UI**: the same three choices appear inline on the task once it's
  expanded, no page navigation needed.

"Always allow" writes a standing rule so the same action doesn't ask again;
"Allow once"/"Deny" apply only to that one attempt. An unanswered prompt
times out (110s) to a safe default of deny rather than hanging forever.

Shell (`Bash`) commands get a further pass: a small classifier
(`app/services/github_classifier.py`) recognizes `git` subcommands and
scopes the rule to the specific operation (`git status` read-only,
`git commit` a local change, `git push` a publish) instead of treating every
shell command the same — so "always allow committing" doesn't also silently
allow pushing. Everything else run via `Bash`, and any tool without its own
classifier, falls back to one coarse bucket per tool.

Extending classification to other connectors (beyond `git`) is still open;
contributions welcome.

## Architecture

```text
Telegram bot ─┐
              ├── FastAPI backend ── PostgreSQL
Next.js web UI┘          ├── disposable sandboxes (Boat provider)
                         ├── permission prompts and rules
                         └── task and schedule services
```

## Project layout

- [backend/](backend) — FastAPI app, Telegram bot, sandbox/auth/task
  services, Alembic migrations. See [backend/README.md](backend/README.md)
  for backend-specific details (REST endpoints, env vars, DB models).
- [frontend/](frontend) — Next.js web UI. See
  [frontend/README.md](frontend/README.md) for frontend-specific notes.
- [scripts/dev.sh](scripts/dev.sh) — one-command dev environment for both.

## Known limitations

- Sandboxes are never automatically stopped/deleted — repeated `/connect`
  testing will accumulate them on the Boat account.
- Link codes expire after 10 minutes; there's no rate limiting on generating
  them.
- The web UI has no real authentication — a browser's identity is whatever
  id (guest or Telegram) it last linked, persisted in `localStorage`. This
  also means the permission engine's accumulated rules are only as safe as
  that identity.
- Automated tests exist for the permission engine
  (`backend/tests/services/`) but not yet for the rest of the app
  (sandbox/task/schedule services, routers).
- The `git` command classifier only covers the verbs it explicitly knows
  about (see `github_classifier.py`); an unrecognized verb defaults to the
  safer "mutation" bucket rather than being assumed read-only.
- Schedules poll on a 30s interval, so a run can start up to ~30s after its
  exact due time.
- A schedule missed by more than 15 minutes (e.g. the API was down) is
  skipped rather than run late; the schedule's `last_error` records that it
  happened.
- Scheduled-run notifications only reach Telegram-linked users (a numeric
  `user_id`) and only if `TELEGRAM_BOT_TOKEN` is set — a web-only guest has
  no chat to message and only sees outcomes in the UI's schedules list.

## Contributing

Issues and pull requests are welcome. Keep the README aligned with the
backend and frontend behavior, and include focused tests when changing
services or API behavior.
