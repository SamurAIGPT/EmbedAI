# Routines

A routine runs a prompt on one of your **local agents** on a schedule, when a
webhook calls it, or when you press **Run now**. Each run is an ordinary
conversation of that agent: the agent's engine, model, instructions, workspace
and computer (if it has one) are used, through the same thread runtime and
approval gate as a chat you type yourself. Nothing here needs Boat.

The older Boat **schedules** (`/schedules`, Telegram `/schedule`) are unchanged
and keep running Boat sandboxes. Routines are the local counterpart.

## What a routine is

| Field | Meaning |
| --- | --- |
| `agent_id` | The agent that runs it. A deleted or archived agent fails the run and pauses the routine. |
| `name`, `prompt` | What it is called, and what the agent is told to do. |
| `schedule_kind`, `schedule_spec`, `timezone` | When it runs (below). |
| `paused` | A paused routine never starts on its own. Webhook calls to it are refused; Run now still works. |
| `run_limit_minutes` | 1 to 240 (default 30). Active time only: time spent waiting for your approval does not count. A run that reaches it is stopped and recorded as failed. |
| `approval_mode` | `ask` (default), `plan` or `accept_edits`. A routine run uses the **stricter** of this and the agent's own mode, so a routine can never loosen what its agent may do. |
| `approval_timeout_minutes` | 1 to 1440 (default 30). How long a run waits for your answer before the request is denied. |
| `catch_up_minutes` | 0 to 1440 (default 60). How late a missed occurrence may still run (below). |
| webhook | Off until you enable it. The secret is shown once; only its SHA-256 hash is stored. |

## Schedule kinds

All times are evaluated in the routine's IANA `timezone` (for example
`Europe/Berlin`; default `UTC`). The finest resolution is one minute.

| Kind | `schedule_spec` | Example summary |
| --- | --- | --- |
| `cron` | `{"expression": "0 9 * * 1-5"}` | five fields: minute hour day-of-month month day-of-week (Sunday is 0). As in standard cron, when both day fields are restricted, either may match. |
| `weekdays` | `{"days": [1,2,3,4,5], "time": "09:00"}` | "Mon–Fri at 09:00". Days use Sunday = 0. |
| `interval` | `{"every_minutes": 90, "anchor": "2026-10-17T08:00:00Z"}` | "Every 90 minutes". Elapsed time from the anchor (default: when saved), 5 to 10080 minutes, unaffected by clock changes. |
| `once` | `{"at": "2026-10-18T09:00"}` | "Once on Sun 18 Oct 2026 09:00". A time without an offset is read in the routine's timezone. After it fires the routine has no next run. |
| `manual` | `{}` | Runs only by Run now or webhook. |

The editor shows a plain-language summary and the next three dates for any
draft (`POST /routines/preview`), computed by the same code the scheduler uses.

**Clock changes.** A wall-clock time that does not exist (a spring-forward gap)
runs at the same offset after the gap (02:30 becomes 03:30). A wall-clock time
that happens twice (the repeated hour in autumn) runs once, at its first
occurrence. Interval schedules count elapsed minutes and ignore both.

## How a run happens

The scheduler is a background job in the backend process (like the Boat
schedule poller), checking every `ROUTINE_POLL_SECONDS` (default 5).

1. **Due.** For each unpaused routine whose `next_run_at` has passed, the
   latest occurrence that is due is found. If it is no more than
   `catch_up_minutes` late it is queued; otherwise a `missed` run is recorded
   for it. Earlier occurrences that were also missed (the app was not running)
   are recorded once, as one `missed` run that says how many. `next_run_at`
   then moves to the first occurrence after now: missed work is never
   replayed one by one.
2. **Skip if still running.** If the routine already has a run that is
   queued, running or waiting for input, the new occurrence is recorded as
   `skipped` instead of starting a second, overlapping copy.
3. **Start.** A queued run is first marked `running` and committed, then a new
   conversation of the agent is created and the prompt is sent as its first
   turn. The conversation is linked from the run.
4. **Follow.** While the turn runs, a pending approval on it moves the run to
   `needs_input` and sends one notification; an answer moves it back to
   `running`. When the turn ends the run becomes `completed` (turn succeeded)
   or `failed` (failed or stopped), with the error and the start of the result
   kept on the run.

Run states: `queued`, `running`, `needs_input`, `completed`, `failed`,
`missed`, `skipped`, `interrupted`.

### Unattended approvals

A routine run never approves anything by itself:

- The turn runs with the stricter of the routine's and the agent's approval
  mode. Every request the engine would ask about creates the usual approval
  card in that conversation, and the run parks in `needs_input` with an in-app
  notification (and Telegram, if configured).
- If nobody answers within `approval_timeout_minutes`, the request expires and
  the engine is told **no** (the usual expiry path of the approval gate). The
  run then continues without that action, or fails if the engine gives up.
- Saved "always allow" rules are explicit decisions you made earlier and apply
  as in a chat; commands on the safety floor are never allowed by a rule, so a
  person always decides those.

### Restart recovery

When the backend starts, any run still marked `running` or `needs_input`
belonged to the process that stopped. It becomes `interrupted` and is **never
re-run** (it may already have had side effects); a notification says so. Its
turn is ended by the thread runtime's usual lease sweep. Queued runs that had
not started yet are kept and start normally (or are recorded as `missed` if
they are now later than the catch-up window). Because a run is marked
`running` before its turn is created, a crash in between also leaves an
`interrupted` run, not a second start.

## Webhooks

`POST /routines/{id}/webhook` enables the webhook, or rotates its secret, and
returns the secret **once**. `DELETE` turns it off.

```
curl -X POST http://127.0.0.1:8000/hooks/12 \
  -H "Authorization: Bearer rt_…" \
  -H "Content-Type: application/json" \
  -d '{"event": "deploy", "status": "failed"}'
```

- The bearer secret is checked (constant-time, against the stored hash)
  before the body is read. An unknown routine and a wrong secret get the same
  401.
- The body is limited to `ROUTINE_WEBHOOK_MAX_BYTES` (default 64 KiB; larger
  is 413) and must be UTF-8 text.
- A paused routine answers 409. At most 3 webhook runs per routine may be
  unfinished at once; more answer 429.
- Accepted calls answer 202 with the run id; the run starts within one poll.
- The body is appended to the routine's prompt inside a clearly delimited
  block that says it is untrusted data, with `<`, `>` and `&` escaped so the
  payload cannot close the block. This is formatting, not a guarantee: an
  engine can still be influenced by text it reads, which is why unattended
  runs ask before acting.

### Ingress

The main API is only for this machine (local access check). To receive
webhooks from elsewhere, run the separate ingress app, which serves only
`GET /health` and `POST /hooks/{id}` and nothing else:

```
uv run uvicorn app.ingress:app --host 127.0.0.1 --port 8788
```

Put a tunnel or reverse proxy in front of that port only. The ingress writes a
queued run to the database; the main backend process starts it. The main
backend also answers `POST /hooks/{id}` itself, for local callers.

## Notifications

Routine events create rows in an in-app notification feed
(`GET /notifications`, unread count, mark read): a run that needs input,
failed, was missed, skipped or interrupted, and a run that completed. The chat
header shows a bell with the unread count. When `TELEGRAM_BOT_TOKEN` and
`ROUTINE_TELEGRAM_CHAT_ID` are set, the same messages are also sent to that
Telegram chat.

## Import and export

`GET /routines/{id}/export` returns the definition without any secret.
`POST /routines/import` creates routines from such documents, always
**paused** and without a webhook, so an imported routine never runs until you
look at it and resume it.

## API

All under `/api/v1`, behind the local access check:

```
GET    /routines                    list (with next run, latest run, webhook on/off)
POST   /routines                    create
POST   /routines/preview            summary + next three dates for a draft schedule
POST   /routines/import             import documents (created paused)
GET    /routines/runs               recent runs of all routines
GET    /routines/{id}               one routine
PATCH  /routines/{id}               edit
DELETE /routines/{id}               delete (and its run history)
POST   /routines/{id}/pause         pause
POST   /routines/{id}/resume        resume (next run computed from now)
POST   /routines/{id}/run           run now (409 while a run is active)
GET    /routines/{id}/runs          its run history
GET    /routines/{id}/export        definition without secrets
POST   /routines/{id}/webhook       enable or rotate; returns the secret once
DELETE /routines/{id}/webhook       disable
GET    /notifications               feed (newest first)
GET    /notifications/unread-count
POST   /notifications/{id}/read
POST   /notifications/read-all
```

And outside `/api/v1`: `POST /hooks/{id}` (bearer secret).

## Limits

- The backend must be running for routines to run; a sleeping computer runs
  nothing, and occurrences missed while it is off follow the catch-up rule.
- One backend process schedules and runs routines (the single-process limit
  of the thread runtime). The ingress may be a second process; it only queues.
- A run that was interrupted is not retried automatically.
- Webhook deliveries are not de-duplicated: a sender that retries a delivery
  starts another run.
