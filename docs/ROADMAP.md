# Roadmap

Open Dots is a self-hosted workspace for personal and business agents. The current code is a prototype that runs Claude Code tasks in Boat sandboxes from Telegram or a web UI. The roadmap moves it toward a local-first agent chat that uses the Claude/Codex CLIs you already have signed in.

This page separates **shipped** from **planned**. Planned items are not promises or dates. For shipped behavior see [FEATURES.md](FEATURES.md).

## Principles

- Ship small, tested vertical slices (backend, UI, persistence, tests, docs).
- Keep working features (Boat sandboxes, Telegram, GitHub, schedules, permission rules) while adding the local path.
- Security first: loopback by default, authenticated owner access, workspace-confined files, no silent privilege escalation.

## Sprint 1 — core beta (`v0.1.0-beta`)

| ID | Feature | Status |
|---|---|---|
| F00 | Verified repo baseline and docs | In progress |
| F01 | Run Claude CLI locally from the web UI (owner auth) | Planned |
| F02 | Stream assistant text and status (SSE) | Planned |
| F03 | Tool timeline and inline approvals | Planned |
| F04 | Durable agents/threads/events and thread list | Planned |
| F05 | Stop, restart and resume | Planned |
| F06 | Multiple named agents with dedicated workspaces | Planned |
| F07 | Codex provider (experimental until Windows sandbox issue is resolved) | Planned |
| F08 | Browse/download agent workspace files | Planned |
| F09 | First-run onboarding and local storage | Planned |
| F10 | Installable beta, CI and documentation | Planned |

Beta gate: authenticated owner, streaming local Claude, working allow/deny/cancel, history surviving restart, safe file download, passing tests/build, honest docs.

## Sprint 2 — everyday usefulness (`v0.2.0`)

Permission modes, thread search, agent memory, scheduled agent routines, run history and notifications, per-agent MCP servers, an additional connector, SKILL.md import, agent templates, API-key (OpenAI-compatible) provider.

## Sprint 3 and beyond

Usage and audit log, attachments and diffs, Docker computer provider and lifecycle, computer preview/takeover, desktop wrapper, installers, multi-agent rooms, delegation, voice notes, further parity items based on user demand.

Out of scope until explicitly approved: multi-tenant paid hosting, hosting third-party subscription credentials, enterprise roles/billing.

## Known limitations today

See the README's "Known limitations", notably: the web UI has no real authentication yet (F01 addresses this before any local execution is exposed).
