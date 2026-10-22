# Open Dots: Open-Source AI Coworkers You Run Yourself

**Open Dots is an open-source, self-hosted workspace for AI coworkers: named
agents that each have a job, instructions, memory, their own workspace and
isolated computer, a schedule, and a human approval step before they do anything
risky.** You chat with them in a browser, a desktop app or Telegram, hand them
recurring routines, and keep their data and credentials on your own machine or
server. It is an open alternative to paid AI agent products such as OpenAI Dots,
Meta Muse, Grok Bot, Instinct, Manus Cue, Claude Cowork, and ChatGPT agent, and
is not yet a feature-for-feature replacement for them.

An agent can be anything you would hand work to: a researcher, a support
triager, a reporting assistant, a developer. Coding is one thing agents do, not
what Open Dots is. Agents run on an engine you already have signed in (Claude,
Codex, any Agent Client Protocol CLI, or an OpenAI-compatible endpoint). Teams of
agents that delegate to each other, rooms, and connectors to other apps are
planned, not built; see [docs/ROADMAP.md](docs/ROADMAP.md).

<p align="center"><a href="https://youtu.be/b4ZTfs0KwR0" title="Watch the Open Dots demo on YouTube"><img src="https://i.ytimg.com/vi/b4ZTfs0KwR0/maxresdefault.jpg" alt="Open Dots demo video thumbnail — click to watch on YouTube" width="720"></a></p>
<p align="center"><a href="https://youtu.be/b4ZTfs0KwR0"><b>▶ Watch the Open Dots demo on YouTube</b></a></p>

> **Status:** Active development. See [Known limitations](#known-limitations) before running it for others.

## Contents

- [What it does](#what-it-does)
- [Why Open Dots](#why-open-dots)
- [Quick start](#quick-start)
- [Using Open Dots](#using-open-dots)
- [Telegram and Boat sandboxes (optional)](#telegram-and-boat-sandboxes-optional)
- [Architecture](#architecture)
- [Project layout](#project-layout)
- [Known limitations](#known-limitations)
- [Contributing](#contributing)

## What it does

- **Named agents** with a title, instructions, engine and model, workspace folder, approval mode and notes; export and import them as portable files.
- **Memory:** each agent keeps notes (`MEMORY.md` plus topic notes) that load into its conversations, with recall across conversations and undo.
- **Routines:** cron, weekday, interval, one-off and webhook schedules with timezones, run history, missed-run tracking, restart recovery and an in-app notification feed (see [docs/ROUTINES.md](docs/ROUTINES.md)).
- **Approvals you control:** ask-first, accept-edits and plan modes, saved rules, a decision log and a safety floor; risky actions wait for your Allow or Deny.
- **Their own computer:** give an agent an isolated Docker container so its commands and file edits run there, never on your machine (experimental, see [docs/COMPUTERS.md](docs/COMPUTERS.md)).
- **Durable conversations** with streaming answers, a tool-call timeline, search, attachments, Stop, and resume after a restart.
- **Several engines and limit-aware fallback:** Claude, Codex, ACP agents and OpenAI-compatible endpoints; usage limits are recognised and a turn can fall back to another engine (see [docs/ENGINES.md](docs/ENGINES.md)).
- **Run it where you want:** a desktop app from a source checkout or unsigned installers (experimental); or on a server you own, behind HTTPS, reachable from a browser or phone (single owner; experimental): `python -m app.server`, a Docker image and compose file, nginx and Caddy overlays, `doctor`, `backup` and `restore`. See [docs/SELF-HOSTING.md](docs/SELF-HOSTING.md).
- **Channels:** the web UI, the desktop app and a Telegram bot share one API.
- **Your data stays yours:** credentials are stored encrypted, in PostgreSQL (default) or SQLite (experimental, no Docker).
- **Boat sandboxes (optional):** the original Telegram and web task flow in hosted sandboxes, with scheduled prompts and GitHub push.

## Why Open Dots

Paid AI coworker products keep your agents, memory and credentials on someone
else's servers. Open Dots gives individuals and small teams the same idea in a
form they can inspect and run themselves, with visible approval steps, local
control over the app and its data, and no lock-in to one model provider. Read
[Known limitations](#known-limitations) before running it for others.

## Quick start

Pick how you want to run it:

| | Desktop app | Server you own | Development checkout |
|---|---|---|---|
| For | one person on a laptop or desktop | agents and routines that keep working with your laptop closed, reachable from a phone | working on Open Dots itself |
| Needs | Node.js 22+, [uv](https://docs.astral.sh/uv/), one signed-in engine | Linux, a domain, Docker or Node.js and uv, one signed-in engine | the same, plus Docker for PostgreSQL (or SQLite) |
| Status | experimental, from a source checkout or unsigned installers | experimental | |
| Guide | below | [docs/SELF-HOSTING.md](docs/SELF-HOSTING.md) | [Development setup](#development-setup-telegram-and-boat-optional) |

An **engine** is the model program your agents run on. You need at least one ready: a signed-in [Claude Code](https://docs.anthropic.com/en/docs/claude-code) or Codex CLI, an Agent Client Protocol CLI, or an OpenAI-compatible endpoint. Settings → Engines shows what is ready and why not.

### Desktop app

```bash
git clone https://github.com/Anil-matcha/open-dots
cd open-dots/desktop
npm run app
```

`npm run app` sets up the first time, builds the page when needed, and opens Open Dots. It needs no `.env`, keeps its own encryption key in the operating system's secret storage, and uses a local SQLite database. On first start a setup flow in Settings detects your engines, asks you to pick a workspace folder, explains the approval modes and can create a starter agent. Details and troubleshooting: [docs/SETUP.md](docs/SETUP.md#desktop-app-from-a-source-checkout-experimental). Tagged releases also build unsigned installers; they are unverified on a clean machine apart from a Windows smoke test, and there is no auto-update.

### On a server

One owner, one data folder, HTTPS through nginx or Caddy, and engines signed in on the server. `python -m app.server`, a Docker image and compose file, `doctor`, `backup` and `restore` are included. Follow [docs/SELF-HOSTING.md](docs/SELF-HOSTING.md), and read what has and has not been checked there before relying on it.

## Using Open Dots

A first session in the web app or the desktop app (the Chat page):

1. **Sign in.** The first start asks you to set the owner passphrase ([docs/AUTH.md](docs/AUTH.md)). Pair a phone or another browser from Settings → Devices.
2. **Check your engine.** Settings → Engines lists what is ready. Without a ready engine nothing can answer.
3. **Create an agent.** Chat → Agents → New agent. Give it a name, a title, instructions (its job, tone and rules), an engine and model, a workspace folder and an approval mode. Agents can be exported to a file and imported again.
4. **Talk to it.** Start a chat with the agent. Answers stream in, every tool call is a step you can open, and Stop works at any time. Conversations survive a restart and are searchable.
5. **Approve what matters.** A risky action shows a card with exactly what will run, and does not run until you press Allow. "Always allow this command" saves a rule you can review and undo in the Approvals panel. See [Approval modes](docs/SETUP.md#approval-modes-saved-rules-and-the-decision-log).
6. **Let it remember.** Each agent keeps notes (`MEMORY.md` and topic notes) that load into its conversations. Edit them under Agents → Notes. See [docs/MEMORY.md](docs/MEMORY.md).
7. **Give it a routine.** Chat → Routines runs a prompt as a new conversation of an agent on a schedule, by webhook or on demand, with run history, missed-run tracking and notifications. A routine never approves by itself. See [docs/ROUTINES.md](docs/ROUTINES.md).
8. **Give it a computer (optional).** With Docker installed, Agents → Computer gives an agent its own isolated container so its commands and file edits never run on your machine. See [docs/COMPUTERS.md](docs/COMPUTERS.md).

Agents need `LOCAL_EXECUTION_ENABLED=true` on a server or development setup (the desktop app sets it itself). File edits and commands also need `LOCAL_TOOLS_ENABLED=true`, and every command still asks you first.

## Telegram and Boat sandboxes (optional)

Open Dots started as a Telegram bot and web page that run tasks in hosted [Boat](https://docs.boat.dev/api-keys) sandboxes. That flow still works, is independent of agents, and needs the Boat key and Telegram setup described in [Development setup](#development-setup-telegram-and-boat-optional).

### Development setup, Telegram and Boat (optional)

#### Prerequisites

- [uv](https://docs.astral.sh/uv/) for Python dependency management
- [Node.js](https://nodejs.org/) (20.9+) and npm, for the web UI (required by Next.js 16)
- [Docker](https://docs.docker.com/get-docker/) (for Postgres; not needed with the experimental SQLite option, see `DATABASE_BACKEND` below)
- Bash (Git Bash on Windows works fine) to use `scripts/dev.sh`
- Optionally, a [Boat API key](https://docs.boat.dev/api-keys) for sandbox creation. If
  it belongs to a personal account that also has an org/team on a paid plan,
  you also need
  that org's id (see `BOAT_ORG_ID` below) — otherwise sandbox creation bills
  the personal account and returns `402 Payment Required`.
- A Telegram bot token from [@BotFather](https://t.me/BotFather) (only
  needed if you want to run the bot, or let people link the web UI to
  Telegram — the web UI's guest mode works without it)
- A public URL the sandbox can reach to call back into your backend for
  permission decisions (see [Permission prompts (Boat)](#permission-prompts-boat-sandboxes)) — in
  local dev this means tunneling your backend with
  [ngrok](https://ngrok.com/) or similar; not needed to run the app, only
  for any task that triggers a risky action (writing files, shell commands,
  etc.) to actually get a decision instead of hanging until it times out.

#### Configure the environment

`scripts/dev.sh` creates `backend/.env` from the example on first run, saves a
private encryption key and webhook token, and preserves existing secrets on
later runs. It also selects a trusted CA bundle for outbound HTTPS. Set
`SSL_CERT_FILE` in your shell or in `backend/.env` to use a custom PEM bundle;
certificate and hostname verification remain enabled. When Boat is configured,
the script checks its TLS certificate before starting services, without sending
credentials or creating a sandbox. You can start the API and web UI without a
Boat key; sandbox features then remain unavailable.

For manual setup, copy the example yourself:

```bash
cp backend/.env.example backend/.env
```

Fill in `backend/.env`:

| Var | Required | Notes |
|---|---|---|
| `BOAT_API_KEY` | no (optional provider) | From the Boat dashboard. Leave empty to run without Boat (for example with only local Claude); anything that needs a Boat sandbox then answers "Boat is not configured". Without Boat the app is chat-only unless you enable local tools (`LOCAL_TOOLS_ENABLED`, see [SETUP.md](docs/SETUP.md#tools-and-approvals-local-claude)); schedules, Telegram tasks and GitHub push need Boat. If set, `HOOK_TOKEN` and `PERMISSION_HOOK_BASE_URL` are also required. |
| `BOAT_BASE_URL` | no | Defaults to `https://boat.dev/api/v1`. |
| `BOAT_ORG_ID` | see note above | Team/org id (e.g. `team_...`) to attach to sandbox creation so it bills the org instead of your personal account. |
| `PARALLEL_SEARCH_ENABLED` | no | Set to `true` to add free Parallel web search and page fetching to Claude tasks. Defaults to `false`. See [Optional web search](#optional-web-search). |
| `LOCAL_EXECUTION_ENABLED` | for agents | Set to `true` to chat with agents and run routines on a local engine (Claude, Codex, ACP or an OpenAI-compatible endpoint). Off by default. `LOCAL_TOOLS_ENABLED=true` also lets agents read and write files and run commands, each call asking for your approval. |
| `TOKEN_ENCRYPTION_KEYS` | generated by `dev.sh` | Persistent Fernet key used to encrypt stored credentials. For manual setup, generate with `uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Comma-separate multiple keys to support rotation. |
| `TELEGRAM_BOT_TOKEN` | only for the bot / Telegram linking | From @BotFather. `dev.sh` skips the bot when this or the Boat key is missing. |
| `TELEGRAM_BOT_USERNAME` | no | Your bot's `@username` (no `@`), shown as a clickable link in the web UI. Cosmetic only. |
| `FRONTEND_ORIGINS` | no | Comma-separated origins allowed to call the API from a browser. Defaults to `http://localhost:3000`. |
| `HOOK_TOKEN` | generated by `dev.sh` | Shared secret the permission hook's caller (the sandbox) must present, so only your backend's own sandboxes can submit permission decisions. For manual setup, generate with `python -c "import secrets; print(secrets.token_urlsafe(32))"`. |
| `PERMISSION_HOOK_BASE_URL` | only with Boat | The public URL from the ngrok-or-similar prerequisite above, no trailing slash — e.g. `https://your-tunnel.ngrok-free.app`. Baked into each sandbox's `.claude/settings.json` at task start. |
| `POSTGRES_*` | no | Defaults match `backend/docker-compose.yml`. |
| `DATABASE_BACKEND` | no | Empty (the default) means PostgreSQL. Set `sqlite` to use a local SQLite file instead (experimental, no Docker). An existing setup never changes database unless you set this. See [docs/SETUP.md](docs/SETUP.md#database). |
| `SQLITE_PATH` | no | SQLite file when `DATABASE_BACKEND=sqlite`. Defaults to `~/.open-dots/open-dots.db`. |
| `SSL_CERT_FILE` | no | Trusted PEM bundle for outbound HTTPS. Detected by `dev.sh`; set explicitly to add a custom CA. |

The web UI (`frontend/`) has its own `.env.example` — `scripts/dev.sh` copies
it to `.env.local` automatically on first run. Copy it yourself if you're
running the frontend separately, and point `NEXT_PUBLIC_API_BASE_URL` at the
backend if you're not using the defaults.

#### Start the app

```bash
./scripts/dev.sh
```

From the repo root, this prepares local configuration, starts Postgres via
Docker when PostgreSQL is selected, waits for it to be healthy, installs
backend and frontend dependencies, runs migrations, and
starts the FastAPI app (`http://127.0.0.1:8000`, docs at `/docs`), the
Telegram bot when configured, and the web UI (`http://127.0.0.1:3000`). Without
a Boat key, the API and web UI still start, while sandbox tasks stay unavailable.
Ctrl+C stops everything it started (Postgres keeps running — `docker compose -f
backend/docker-compose.yml down` to stop it too).

Skip pieces you don't need:

```bash
./scripts/dev.sh --no-bot        # API + web UI only
./scripts/dev.sh --no-frontend   # API + bot only
```

##### Run the pieces manually

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


### Telegram and web task flow (Boat)

The original flow: tasks run in hosted Boat sandboxes, started from Telegram or the web page. Agents, routines and the Chat page below do not need it.

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
   tasks](#scheduling-tasks-boat) below.

The web UI covers the same ground from a browser:

1. **Get started** — either continue as a guest (an id the server makes, no
   Telegram needed) or link Telegram: the page generates a one-time code, you
   send `/link <code>` to the bot, and the server links your signed-in session
   to your real Telegram user id instead. (Sign in first: the web UI asks for
   the owner passphrase, see [docs/AUTH.md](docs/AUTH.md).)
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
   controls. See [Scheduling tasks](#scheduling-tasks-boat).


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
   tasks](#scheduling-tasks-boat).

In the web UI (`http://localhost:3000`), after setting the owner passphrase
on first start (or signing in):

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

### Optional web search (Boat tasks)

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

### Scheduling tasks (Boat)

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

### Permission prompts (Boat sandboxes)

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
Telegram bot ─┐                         ┌── PostgreSQL (default) or SQLite
Next.js web UI├── FastAPI backend ──────┤
Desktop app ──┘      │                  └── encrypted credentials
 (Electron,          ├── Boat sandboxes (optional): tasks, schedules, GitHub push
  local Claude)      ├── permission prompts and rules (git command classifier)
                     ├── task and schedule services (polling loops in the API process)
                     └── threads: local `claude` CLI turns, events stored then
                         streamed over SSE, approvals, resume after restart
```

Boat sandboxes are optional. Without a Boat key the app is chat-only unless
local tools are enabled, and schedules, Telegram tasks and GitHub push are
unavailable. The desktop app runs local Claude only. See
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the target design.

## Project layout

- [backend/](backend) — FastAPI app, Telegram bot, sandbox/auth/task
  services, Alembic migrations. See [backend/README.md](backend/README.md)
  for backend-specific details (REST endpoints, env vars, DB models).
- [frontend/](frontend) — Next.js web UI. See
  [frontend/README.md](frontend/README.md) for frontend-specific notes.
- [desktop/](desktop) — Electron desktop app (local Claude, SQLite, no `.env`).
- [scripts/dev.sh](scripts/dev.sh) — one-command dev environment for the backend, bot and web UI.
- [.github/workflows/](.github/workflows) — CI (backend on SQLite and PostgreSQL, frontend lint/build, desktop tests) and the tag-triggered installer build.

### Local Claude (experimental)

Optionally run prompts through your own signed-in `claude` CLI on the
machine running the backend, with no Boat sandbox. It is off by default and
only usable from that machine. The answer streams into the page as it is written.
It is chat-only unless you set `LOCAL_TOOLS_ENABLED=true`, which lets Claude
read and write files in its workspace folder and run commands, each call waiting
for your Allow or Deny in the page (an allowed command runs as you, with no
sandbox). See [docs/SETUP.md](docs/SETUP.md#local-claude-experimental).

A separate set of endpoints, `/api/v1/threads`, keeps conversations in the
database so they continue and survive a restart. The Chat page (`/chat`, linked
as "Open chat" on the home page) is their interface. See
[docs/SETUP.md](docs/SETUP.md#the-chat-page).

### Desktop app (from source, experimental)

An Electron window around the local backend, for local Claude only: from the
`desktop/` folder, `npm run app` (or the steps one at a time: `npm run setup`,
`npm run build`, `npm run start`). It
needs no `.env` file: it makes and keeps its own encryption key in the
operating system's secret storage, creates its SQLite database, and only its own
window can use the backend. `npm run app` does the setup and build on the first
run and then opens it. Tagged releases build unsigned installers (Windows NSIS
x64, macOS arm64 DMG) with GitHub Actions; neither has been verified on a
clean machine apart from a Windows smoke test, and there is no auto-update. See
[docs/SETUP.md](docs/SETUP.md#desktop-app-from-a-source-checkout-experimental).

## Known limitations

- Sandboxes are never automatically stopped/deleted — repeated `/connect`
  testing will accumulate them on the Boat account.
- Link codes expire after 10 minutes; there's no rate limiting on generating
  them.
- Authentication is single-owner: one passphrase per install, plus devices
  paired with one-time codes ([docs/AUTH.md](docs/AUTH.md)). There are no
  separate user accounts; every signed-in browser or paired device acts as the
  owner (a `chat` device only within conversations), and the `user_id` older
  routes take is not checked against the session. Sign-in lockouts are kept in
  memory per process. Use HTTPS beyond a trusted private network.
- Automated tests cover the permission engine and git classifier, schedules,
  local Claude (runner, stream, approvals, access policy), threads (store,
  runtime, stream, API), settings, the SQLite and PostgreSQL schemas and
  migrations, the desktop entry and launch token, and some task, sandbox and
  Telegram paths (`backend/tests/`, `desktop/test/`). Boat-sandbox task and
  sandbox services have only partial coverage, and there are no frontend
  tests. The GitHub Actions workflows have not yet run on GitHub, so treat
  them as untested until the first run.
- Engines are Claude, Codex, ACP agents and OpenAI-compatible endpoints; other
  engine presets are planned. Named agents and memory exist, but agents cannot
  delegate to each other yet, there are no rooms or teams, and connectors beyond
  an optional search MCP are not built.
- Desktop installers are unsigned; macOS and Linux have not been run.
- The `git` command classifier only covers the verbs it explicitly knows
  about (see `github_classifier.py`); an unrecognized verb defaults to the
  safer "mutation" bucket rather than being assumed read-only.
- Boat schedules (`/schedules`, Telegram `/schedule`) poll on a 30s
  interval, skip an occurrence missed by more than 15 minutes (recorded in
  `last_error`), and notify only Telegram-linked users (a numeric `user_id`)
  when `TELEGRAM_BOT_TOKEN` is set. Local routines
  ([docs/ROUTINES.md](docs/ROUTINES.md)) do not need Boat: they poll every
  5s, record missed and skipped occurrences, notify in the app (and
  optionally one Telegram chat via `ROUTINE_TELEGRAM_CHAT_ID`), but only run
  while the backend is running, do not retry failed or interrupted runs, and
  do not de-duplicate webhook deliveries.
- Durable threads (`/api/v1/threads`) and the Chat page are tested on SQLite only, and store transcripts (including
  what Claude read, wrote and ran) in the database. A conversation can be deleted
  from there (not a secure erase), but the Claude CLI's own copy in your `.claude`
  folder is never touched.

## Contributing

Issues and pull requests are welcome. Keep the README aligned with the
backend and frontend behavior, and include focused tests when changing
services or API behavior.
