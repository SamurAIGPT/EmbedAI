# Architecture

## Current system

```text
Next.js web UI (frontend/)        Telegram bot (backend/app/telegram_bot.py)
          \                              /
           +------- FastAPI (backend/app) -------+
                     |            |
        routers: task, sandbox, schedule,    Postgres (Alembic)
        permission, link                     encrypted credentials
                     |
        services: task execution, Boat sandbox ops, Claude OAuth flow,
        permission rules/asks, git classifier, schedules, notifications
                     |
              Boat remote sandbox running Claude Code
              (calls back to the backend for permission approvals)
```

- **Backend:** FastAPI, SQLAlchemy models (`task`, `schedule`, `user_sandbox`, `user_credential`, `permission_rule`, `permission_ask`, `link_code`), Alembic migrations, background loop for schedules and task sync.
- **Frontend:** Next.js, components for connecting Claude/GitHub/Telegram and submitting tasks.
- **Threads (experimental):** `thread`, `turn`, `event` and `approval` tables, and a runtime that runs each turn in the user's `claude` CLI, stores every event before announcing it, and keeps a heartbeat so that a turn whose process died is marked interrupted. Routes under `/api/v1/threads` include a server-sent-events stream that resumes from a stored event number. The Chat page (`/chat`) is their interface; it keeps nothing of its own, so everything it shows is rebuilt from the stored events after a reload.
- **Identity:** guest or Telegram id stored in the browser; no owner authentication yet. The optional local Claude path (off by default) is instead restricted to requests from the machine itself by `local_access_policy`, and runs through the user's own `claude` CLI: chat-only by default, or with tools whose calls each wait for a person's approval when `LOCAL_TOOLS_ENABLED` is set.

See [README](../README.md), [backend/README.md](../backend/README.md) and [frontend/README.md](../frontend/README.md) for details.

## Target direction

```text
Web UI (desktop wrapper later)      Telegram (optional)
            \                         /
             +------ FastAPI API ----+
                        |
                Owner authorization
                        |
              Agents / Threads / Turns
                        |
                Runtime coordinator
              /         |          \
   ProviderAdapter  ApprovalBroker  EventStore + SSE
     /       \
 Claude     Codex
 adapter    adapter
     |         |
 claude CLI  codex app-server
        \     /
      Process manager
            |
     Agent workspace (restricted)
            |
     ComputerProvider (Docker today, see COMPUTERS.md; later: SSH server / desktop)
```

### Vocabulary

- **Agent:** named configuration (instructions, engine/model, workspace, permission mode).
- **Thread:** durable conversation with an app-owned id; may map to a provider session id.
- **Turn:** one request/execution episode.
- **Event:** ordered, user-visible state change with a per-thread sequence number.
- **Approval:** durable decision request tied to one tool call.
- **Process:** ephemeral OS child; not the durable record of a conversation.

### Design rules

1. Provider specifics stay inside adapters; the app sees normalized events (`turn.started`, `text.delta`, `tool.started`, `tool.output`, `tool.completed`, `approval.requested`, `approval.resolved`, `turn.completed`, `turn.failed`, `turn.cancelled`, `runtime.status`).
2. Events are stored before they are broadcast; SSE supports `Last-Event-ID` replay.
3. Text is appended once, from deltas or snapshots, never both.
4. A completed turn does not prove each tool succeeded; track tool outcomes separately.
5. Approvals live in an app-level broker above providers.
6. One active turn per thread; no automatic replay of an interrupted prompt.
7. Storage migrations are additive. Existing task, schedule and credential data is preserved.
8. Host CLI execution runs natively on loopback. Containers cannot see host CLI logins automatically, so Docker isolation is a separate, later feature.

This section describes intent, not shipped behavior. Durable threads (F04) already follow rules 2, 5 and 6 and never replay an interrupted turn: events are stored before they are announced and a stream resumes from `Last-Event-ID`, approvals are stored and checked against the running turn, and a thread has at most one running turn. The older Local Claude card still streams from memory only. Stop and resume (F05), named agents (F06), a second provider is not built yet. A Docker computer provider exists (experimental, [COMPUTERS.md](COMPUTERS.md)): an agent with a computer runs its tools in its own container through an approval-gated MCP server.
