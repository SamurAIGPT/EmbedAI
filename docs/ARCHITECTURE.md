# Architecture

Open Duet is a local-first agent workspace. A web or Electron client talks to
one FastAPI backend, which owns conversations, approval decisions, agent data,
and routine scheduling.

```text
Browser / Electron
        │
        ▼
Next.js UI ─────────────── FastAPI backend ─────── PostgreSQL or SQLite
                                  │                 conversations, agents,
                ┌─────────────────┼──────────────┐  approvals, routines
                │                 │              │
          Engine adapters    Team runtime   Routine scheduler
          Claude, Codex,           │
          ACP, API chat      jobs and rooms
                │                 │
        CLI / model API      Agent conversations
                │
       Approval broker ───── Docker computer MCP
                                  │
                           isolated per-agent
                           shell or desktop

Optional legacy path: Telegram and Boat sandbox tasks
```

## Main components

- **Agents and workspaces.** An agent stores its name, instructions, engine,
  model, workspace, approval mode, notes and skills. Each conversation is a
  durable thread with ordered turns and events. An engine adapter translates
  provider-specific events and capabilities into the thread runtime's common
  interface. A conversation keeps its provider even if the agent later changes
  engines.
- **Thread runtime and approvals.** The runtime stores events before sending
  them to the client over server-sent events, supports reconnect from the last
  event id, and refuses to replay an interrupted prompt. Tool requests pass
  through the approval broker, which applies the active mode, saved rules and
  safety floor. The provider still enforces its own permission behavior where
  applicable.
- **Teams and rooms.** Claude team tools can delegate jobs, create or configure
  specialists, and create shared rooms. Job and room-member turns use their own
  conversations; the room transcript stores visible messages and dispatch
  state. Teammate text is treated as data and cannot approve an action. Team
  tools are gated and require Claude or Codex as the lead engine.
- **Agent computers.** An assigned Docker computer runs the agent's bounded
  tools through a computer MCP server. Only the selected workspace is mounted;
  permits bind approval to one tool call and its exact input. Docker desktops
  support screen viewing and takeover. The opt-in native macOS preview is a
  separate desktop-app path. Structured Chromium tools are opt-in and
  Claude-only. SSH computers and an enforceable outbound allowlist are not
  implemented.
- **Routines.** Routines create ordinary agent conversations from a schedule,
  webhook or manual run. The backend records occurrences and results, applies
  the stricter approval mode, and reports pending approval or failures. A
  PostgreSQL advisory lock elects one scheduler; interrupted work is not
  replayed automatically.
- **Memory and context.** Notes, topics, journals and proposals live with the
  local workspace/data folders rather than inside model-provider state. Recall
  and upkeep use engine capabilities; model-powered background writing is not
  available on every engine without a helper.
- **Storage and access.** Alembic manages PostgreSQL and SQLite schemas. API
  credentials are encrypted at rest, and the desktop app uses operating-system
  secret storage. Web users authenticate as one owner; paired devices receive
  scoped, revocable tokens. The self-hosted server remains a single-owner
  product.

## Capability boundaries

Engine support is feature-specific. Claude, Codex, ACP and API-backed engines
do not expose identical tools, image handling, memory calls, team tools or
computer access. The engine registry reports capabilities to the UI; consult
[ENGINES.md](ENGINES.md) and the feature guides before assuming a tool is
available. Source behavior and the latest tagged installer can differ; the
[roadmap snapshot](ROADMAP.md) records the current boundary.

The original Boat sandbox task flow and Telegram bot remain optional and
separate from local agents, threads and routines.
