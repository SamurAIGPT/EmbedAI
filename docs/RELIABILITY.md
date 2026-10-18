# Reliability

What the backend promises when processes stop, hang or collide, how each
promise is tested, and what is not covered yet. Written after a pass that
verified the features from October 2026 together instead of one at a time:
named agents and notes, approvals (modes, saved rules, safety floor, decision
log, sentinel), Docker computers, the engines (Claude, Codex, ACP,
OpenAI-compatible), owner sign-in and paired devices, routines and webhooks,
and the desktop first-run setup.

## The promises

### One backend per database

A backend on SQLite holds an exclusive lock on `<database>.backend-lease` next
to the database file (`app/core/data_lease.py`) for as long as it runs. A
second backend on the same file refuses to start and names the process that
holds it:

    Another Open Dots backend (process 4242 on mac.local) is already using the
    database /Users/me/.open-dots/open-dots.db. Stop it first, or point this one
    at a different database (SQLITE_PATH).

- The lock belongs to the operating system (`flock`, or `LockFileEx` on
  Windows), so it ends when the process ends, however it ends. A crash or
  `kill -9` leaves no stale lock and nothing to delete by hand. The file's
  contents (pid, host, start time) are only used for the message.
- The server takes it at startup, before routines are recovered or turns are
  swept. The desktop launch takes it before the database is judged or
  migrated, and a refused launch exits with code 4 ("could not open its
  database") and the message above in its log.
- The webhook ingress (`app/ingress.py`) and the Telegram bot share the
  database on purpose and take no lease.
- PostgreSQL takes no file lease: several backends on one server are allowed,
  and turn ownership is enforced in the database (owner tokens and leases).

### One routine scheduler per database

On PostgreSQL only the backend that holds a session-level advisory lock runs
the routine scheduler (`app/core/scheduler_lock.py`). The lock is taken on a
connection kept open for the life of the process, so PostgreSQL drops it when
that session ends: a clean stop unlocks it, a crash or `kill -9` closes the
connection. Any other backend logs "another backend on this database runs the
routine scheduler; this one stands by", serves the API and runs turns, queues
a Run now or webhook run for the holder, and tries again on every scheduler
pass, so it takes over within one pass after the holder stops. A holder whose
lock connection breaks stops scheduling until it gets the lock back. The
startup recovery that marks runs `interrupted` runs only when the lock is
taken, and on PostgreSQL it leaves a run whose turn is still heartbeating
alone (following settles it). SQLite keeps its file lease.

### A stop interrupts, never strands

On SIGTERM or Ctrl+C (and when the desktop launcher goes away) the backend:

1. ends its event streams within a second (`app/core/shutdown.py` chains onto
   the server's signal handlers), so the server's wait for open connections
   cannot hold the stop up;
2. stops its background jobs, so no routine starts a turn while it stops;
3. refuses new turns (503, "The app is shutting down");
4. kills each running turn's engine process group (which holds the engine's
   MCP servers and the commands it started) and marks the turn `interrupted`,
   never to be resumed or replayed;
5. settles routine runs that followed those turns as `interrupted`, with their
   notification, instead of leaving them `running` until the next start.

Before this pass, a SIGTERM with the web page open left the backend waiting
forever ("Waiting for connections to close"), its turns still running. The
desktop app was not affected (it sets a five-second graceful timeout).

A hard kill (`kill -9`, power loss) cannot clean up. The next start marks the
turn `interrupted` once its lease runs out (`THREAD_LEASE_SECONDS`, 60) and
marks routine runs left running or waiting `interrupted`.

### Engines a hard kill left running are ended at the next start

Engine processes run in sessions of their own. The backend records each
engine process group it starts (Claude, `codex app-server`, ACP agents, the
status probes) in `ENGINE_PROCESS_DIR` (default `engine-processes` next to the
SQLite file): pid, start time, program and the backend's own pid and start
time; the record is removed when the process ends. Each engine also carries an
environment marker naming its backend, which its children inherit, including
a computer's `docker exec` calls, which run in sessions of their own
(`app/core/engine_processes.py`). At startup the sweep:

- leaves records of a backend that is still running alone (another backend on
  the same machine);
- kills a recorded group only if the process at that pid has the recorded
  start time and runs the recorded program (or carries the marker), so a pid
  the system has since given to another program is never signalled;
- when the group's leader is gone, kills only remaining members that carry
  the dead backend's marker;
- kills other processes carrying the marker of a backend that left records
  behind (crashed). A backend that stopped cleanly removed its records, so what
  its engines deliberately left running in the background is not touched.

**Lifeline.** No extra lifeline pipe was added. Each engine's stdin and stdout
are pipes to the backend; the real Claude CLI 2.1.302 in stream-json mode was
seen to exit within ten seconds of a `kill -9` of the backend (its stdin
closed), so for Claude the pipe already is the lifeline. An engine that
ignores a closed stdin, its MCP servers and `docker exec` calls are what the
startup sweep is for.

### A cut turn says whether the engine had the message

A turn records `engine_phase` (`preparing`, then `sent` just before the
prompt is handed to the engine). When a crash, a stop or an expired lease
interrupts it, `turn.interrupted` carries `replay`:

- `before_accept`: the engine never received the message; nothing ran;
- `unknown`: the engine had it but had reported nothing yet;
- `after_accept`: the engine had produced output, a tool call or a question;
  it may have changed things.

The failed-turn message says which ("The app stopped before the engine
received this message, so nothing ran..."). No interrupted turn is ever
re-run automatically, whichever class it is.

### A limited engine is never retried silently

See [ENGINES.md](ENGINES.md#limits-and-the-fallback-engine): a usage or rate
limit is classified for every engine, marked on the engine until its reset,
and moved to the agent's fallback engine only before the engine accepted the
turn and only to an engine that can do what the turn needs, with a visible
`engine.switched` event.

### A silent engine is stopped

The turn watchdog fails a turn whose engine has done nothing (no stored event,
no typing) for `TURN_STALL_SECONDS` (default 600, `0` turns it off), with
`error_code` `engine_stalled` and a message that says what happened. It stops
the engine the way Stop does, and if the adapter still does not return within
five seconds, stops waiting for it. A turn waiting on an approval is never
stalled (the approval has its own timeout), and the clock starts after the
agent's computer is attached. The default is longer than
`LOCAL_TASK_TIMEOUT_SECONDS`, so the watchdog only catches an engine whose own
time limit did not end it. An engine process that dies ends its turn at once,
without the watchdog.

### A broken engine blocks nothing

No engine is probed at startup. The engine list answers within the probe
timeout (12 seconds) with each broken engine as unavailable and a reason. A
turn's start now bounds the engine's status check too (45 seconds) and answers
503 with the reason, where an engine whose status hung used to hang the
request and one whose status raised used to answer 500. A hanging Codex and a
crashing ACP agent do not delay a Claude conversation, and the probes kill the
processes they started.

## How it is tested

| What | Where |
|---|---|
| Lease: refusal names the holder, `kill -9` and SIGTERM leave no stale lock, shared within one process, the app's startup refuses | `backend/tests/core/test_data_lease.py` |
| Two desktop launches on one data folder | `backend/tests/desktop/test_desktop_entry.py` |
| SIGTERM and SIGINT mid-turn with an event stream open; the engine and its child are gone; restart; a routine run in flight; `kill -9` then restart | `backend/tests/e2e/test_shutdown.py` |
| Watchdog: quiet engine, typing then silence, an adapter that ignores Stop, approvals pause it, `0` disables it, a dying engine | `backend/tests/services/test_turn_watchdog.py` |
| Engines: hanging and raising status, one engine never blocks another; real hanging and crashing programs on the real backend | `backend/tests/services/test_engine_resilience.py`, `backend/tests/e2e/test_engine_resilience.py` |
| Features together on the real backend with the real auth middleware: a routine on an agent with a computer waiting for an approval; the webhook ingress against the main app's auth; a chat-scope device; approvals and the decision log on Claude, Codex and ACP | `backend/tests/e2e/test_cross_feature.py` |
| Scheduler lock: one holder, release, a killed session, an unreachable database | `backend/tests/core/test_scheduler_lock.py` (PostgreSQL) |
| Two real backend processes on one PostgreSQL database: one scheduler, Run now on the standby runs once on the holder, takeover after `kill -9` and after SIGTERM | `backend/tests/e2e/test_scheduler_lock.py` (PostgreSQL) |
| Orphan sweep with real processes: a crashed backend's engine group and stray `docker exec` stand-in ended; a recycled pid, a live backend's engines, a bystander with the same program name and a clean backend's background process left alone | `backend/tests/core/test_engine_processes.py`, `backend/tests/e2e/test_shutdown.py` (`kill -9` of the real backend, restart) |
| Limits and fallback: classification, reset times, Claude limit frames, the switch before acceptance, no switch after, missing capability, continuing conversation, not ready, limited too, expiry; replay classes | `backend/tests/services/test_engine_fallback.py` |
| First day end to end: passphrase, sign out and in, agent turn, approval, routine, second backend refused, SIGTERM, restart | `scripts/smoke-backend.py` (CI job "Backend smoke (real process)", about 10 seconds) |

The `tests/e2e` tests start the real backend with uvicorn in its own process
(`backend/tests/e2e/backend.py`): a scratch folder, a fresh SQLite database
migrated with Alembic, its own HOME, and a fake `claude` executable
(`backend/tests/e2e/fake_claude.py`) that answers status calls and hands turns
to the scripted fake CLI the unit tests use. They always run on SQLite, also
in the PostgreSQL CI job, so they never touch shared rows. They need POSIX
signals and skip on Windows.

    cd backend
    DATABASE_BACKEND=sqlite uv run pytest tests/e2e -q
    uv run python ../scripts/smoke-backend.py          # add --keep to look at the scratch folder

## Live verification (October 18, 2026)

On macOS with a scratch SQLite database, the CI throwaway encryption key,
`LOCAL_EXECUTION_ENABLED` and `LOCAL_TOOLS_ENABLED`:

- real Claude CLI 2.1.302: haiku turns through the API and the web UI, a
  Write approval answered in the browser, a scheduled `once` routine and a
  webhook routine through the main app, both under sign-in (a webhook secret
  alone does not open the API);
- real Codex CLI 0.163.0: one turn through the engine layer;
- SIGTERM during a streaming Claude turn: the backend exited in under a
  second, the turn was `interrupted`, no `claude` process was left, and the
  page shows "The app was closed while the agent was working";
- the desktop app from source over CDP on a fresh data folder: the first-run
  setup appeared and completed, Chat loaded, one Claude turn answered, the
  Approvals panel and Settings rendered, and quitting ended the backend;
- Docker did not answer (`docker info` timed out), so the real-Docker computer
  tests and a real computer turn were not run; computers were verified with
  the fake docker executable only.

Second pass (October 18, 2026), same setup:

- two real backends on a scratch PostgreSQL 14: one ran the scheduler, the
  other stood by and took over after `kill -9` (tests above);
- an agent on an OpenAI-compatible endpoint that always answers 429, with
  Claude haiku as its fallback: the first message switched to Claude (the
  notice in the transcript, a real haiku answer), the second message in the
  same conversation was refused ("it lacks this conversation's earlier
  messages"), and `GET /engines` and the picker showed "Rate limited · resets
  in 43m";
- `kill -9` of the backend while a real Claude turn waited on a Write
  approval: Claude exited by itself within ten seconds; the restart removed its
  record, left an unrelated process with `claude` in its arguments alone, and
  the turn was interrupted with `replay` `after_accept`.

## Findings that are not bugs in this app

### Recovery verification (October 18, 2026)

After resuming the interrupted implementation, the complete SQLite suite passed
(1,791 tests, 22 skipped), as did all ten real-process backend smoke checks,
137 frontend helper tests, 70 desktop tests, frontend lint (one existing
SubmitTask effect-cleanup warning), the production web build and the desktop
static export. The static export required a fresh Next build cache after a
local worker-port permission error.

These checks use scripted engines. They do not repeat the earlier real-engine,
browser or Electron verification, establish live third-party compatibility,
or verify signed installers, updates, Windows, Linux or real Docker.

- The Claude CLI runs commands it considers read only (`ls -la`) without
  asking, even in `ask` mode, so they produce no approval card and no
  decision-log line. The decision log records what an engine asked about, not
  everything it ran. The tool call still appears in the transcript.
- A turn's typing that was never completed as a message is not kept when the
  backend stops; the transcript shows only the interruption note.

## Gaps

Compared with what a larger agent host does for the same problems, still open:

- **Other background jobs on PostgreSQL.** Only the routine scheduler is
  single-instance; the older Boat schedule poller and task sync still run in
  every backend.
- **Orphans between starts.** The sweep runs at startup only: an orphan lives
  until the next start, and a backend that is never restarted leaves it. No
  explicit lifeline pipe (see above); the Windows sweep does nothing.
- **Fallback limits.** A continuing conversation can only move to an engine
  that replays the transcript; limits live in one process's memory; one
  fallback, no chain; no automatic retry of an interrupted turn, even
  `before_accept`.
- **Routine header mode.** A routine's conversation header shows the agent's
  approval mode, not the stricter one the run used (the decision log records
  the mode each approval ran under).
- **Hand-off notes.** A turn that ends at a time or step limit leaves no
  summary of the work done for the next turn to continue from.
- **Watchdog scope.** The watchdog watches events, not the process: an engine
  that keeps printing without making progress is not caught, and a long
  silent tool run longer than `TURN_STALL_SECONDS` is stopped. Raise the
  setting for such work.
- **Scripted behaviour scenarios.** The fake engines replay fixed steps in
  tests; there is no catalogue of scenario files with expected traces, no
  golden-trace replay of real conversations, and no opt-in live-model tier.
- **Per-feature QA specs.** Verification is described in this file and in
  CHANGELOG entries, not in one repeatable checklist per feature.
- **Windows.** The new real-process tests and the smoke script need POSIX
  signals; the Windows lease path (`msvcrt.locking`) is untested.
- **Packaged app.** The smoke script tests the source backend, not a frozen
  build; the desktop end-to-end suite is still Windows only.
