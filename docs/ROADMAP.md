# Roadmap

Open Dots is a self-hosted workspace for personal and business agents. It runs Claude Code tasks in Boat sandboxes from Telegram or a web UI, and durable local conversations through Claude, Codex, a generic ACP command or an OpenAI-compatible endpoint.

This page separates **shipped** from **planned**. Planned items are not promises or dates. For shipped behavior see [FEATURES.md](FEATURES.md).

## Principles

- Ship small, tested vertical slices (backend, UI, persistence, tests, docs).
- Keep working features (Boat sandboxes, Telegram, GitHub, schedules, permission rules) while adding the local path.
- Security first: local-machine-only access for host execution, workspace-confined files, no silent privilege escalation.

## Sprint 1 — core beta (`v0.1.0-beta`)

| ID | Feature | Status |
|---|---|---|
| F00 | Verified repo baseline and docs | Done |
| F01 | Run Claude CLI locally from the web UI (loopback access policy, chat-only) | Done |
| F02 | Stream assistant text and status (SSE) | Implemented, awaiting review |
| F03 | Tool timeline and inline approvals | Implemented (opt-in, in memory), awaiting review |
| F04 | Durable threads, turns, events and approvals, and a thread list (named agents are F06) | Implemented, with a Chat page; SQLite supported, PostgreSQL behaviour not yet verified |
| F05 | Stop, restart and resume | Stop implemented (Chat page and API); a stopped or interrupted message is never resumed or sent again, the conversation continues from the same session |
| F06 | Multiple named agents with dedicated workspaces | Implemented: `/api/v1/agents`, `agent_id` on threads, per-agent folder, per-agent notes (`MEMORY.md`), starter notes, export/import as one file, and the Agents panel in the Chat page; SQLite and PostgreSQL tested |
| F07 | Multiple engines and model selection | Implemented (experimental): Claude, Codex app-server, generic ACP and OpenAI-compatible chat; agent/new-chat engine and model controls, idle conversation model changes, web/desktop engine Settings. See [ENGINES.md](ENGINES.md) for capabilities and limits |
| F08 | Browse/download agent workspace files | Planned |
| F09 | First-run onboarding and local storage | Implemented (experimental): desktop setup for engine/model, workspace, tools and approval modes; optional starter agent, durable completion, skip/replay; SQLite and database-backed desktop settings; source `doctor` command. Source flow verified on macOS; packaged first-run and Linux verification remain |
| F10 | Installable beta, CI and documentation | Planned |
| F11 | Agent computers: an isolated Docker container per agent, tools only through an approval-gated MCP server (see [COMPUTERS.md](COMPUTERS.md)) | Implemented (experimental): lifecycle, limits, network none/default, idle stop, orphan cleanup, Computer panel; egress allowlist, SSH server and desktop computers planned |

Beta gate: access restricted to the local owner, streaming local Claude, working allow/deny/cancel, history surviving restart, safe file download, passing tests/build, honest docs.

## Sprint 2 — everyday usefulness (`v0.2.0`)

Thread search, agent topic notes and automatic note upkeep (single-file agent notes exist, see F06), scheduled agent routines, run history and notifications, per-agent MCP servers, an additional connector, SKILL.md import, agent templates (an agent can already be exported and imported as one file). Quota-aware engine fallback, model prices/context windows, multiple endpoint instances and richer non-Claude capabilities remain planned.

## Sprint 3 and beyond

Usage and audit log, attachments and diffs, computer egress allowlist, bring-your-own server over SSH and desktop computers with preview/takeover (the Docker computer provider exists, see F11), a packaged desktop app and installers (a source-checkout desktop app exists, see F09), multi-agent rooms, delegation, voice notes, further parity items based on user demand.

Out of scope until explicitly approved: multi-tenant paid hosting, hosting third-party subscription credentials, enterprise roles/billing.

## Known limitations today

See the README's "Known limitations", notably: the web UI has no real authentication yet (F01 addresses this before any local execution is exposed).
