# Features

Open Duet is a workspace for named AI agents, durable conversations, teams,
and recurring work. This page describes the repository's current source;
features added after a desktop release tag may not be in that installer. See
the [README](../README.md) for install options and the [roadmap](ROADMAP.md)
for remaining work.

## Agent workspace

- **Agents and starting roles.** Create named agents with their own
  instructions, engine/model, workspace, approval mode, notes and skills. Use
  the bundled, searchable role gallery to start an agent; the person reviews
  and saves the configuration. Agent files can be exported and imported with
  safer defaults. See [templates](TEMPLATES.md), [skills](SKILLS.md), and
  [setup](SETUP.md#the-chat-page).
- **Chat and files.** Conversations persist across reloads, stream answers and
  tool activity, and support stop, search, text/image attachments, workspace
  file browsing and downloads, and reported token/cost visibility. See
  [everyday features](EVERYDAY.md).
- **Memory.** Agents can use private notes, topic notes, cross-chat recall,
  past-conversation search, memory proposals and upkeep, compaction, and a
  journal with undo. Availability of model-powered upkeep depends on the
  selected engine or a configured helper. See [memory](MEMORY.md).
- **Teams and rooms.** A Claude or Codex lead can delegate to specialists and manage
  shared rooms with member mentions, shared instructions, responder settings,
  queued replies, approvals and run status. Team tools are gated and require
  Claude or Codex. Room members are selected at creation and cannot yet be
  changed afterward. See [teams](TEAMS.md).
- **Skills.** Save or import reviewed Markdown instructions for an agent and
  choose them explicitly for a message. Skills do not execute scripts or grant
  permissions. See [skills](SKILLS.md).

## Engines, approvals and computers

- **Engines.** The source includes Claude, Codex, generic ACP and
  OpenAI-compatible/OpenRouter paths, plus opt-in Gemini and Cursor ACP presets
  and experimental OpenCode. Engine capabilities differ: for example,
  attachments, teams, memory tools and computers are not available on every
  engine. See [engine setup and capabilities](ENGINES.md).
- **Approvals.** Ask, Accept edits and Plan modes, saved command rules, a safety
  floor, a redacted decision log and a read-only health scan. Computer actions
  use the same approval boundary. See [setup and approvals](SETUP.md#approval-modes-saved-rules-and-the-decision-log).
- **Agent computers.** An agent can run in its own Docker shell or Linux
  desktop with bounded tools, resource limits, a workspace mount, live viewing
  and take-control. Structured Chromium tools are opt-in and currently Claude
  only. The desktop app also has a separate opt-in native macOS preview. These
  features remain experimental; remote SSH computers and enforceable egress
  allowlists are not implemented. See [computers](COMPUTERS.md),
  [browser tools](BROWSER.md) and [native desktop](NATIVE-DESKTOP.md).

## Routines, access and distribution

- **Routines.** Run an agent on a schedule, by webhook or on demand. Runs have
  durable history, missed/interrupted states, approval handling, notifications
  and configurable retry of failed runs. A routine never approves actions by
  itself. See [routines](ROUTINES.md).
- **Owner access and self-hosting.** Use a local desktop, or run a personal
  server with owner sign-in, paired-device scopes, HTTPS proxy guidance,
  backups and updates. This is a single-owner product; multi-user organization
  hosting is not implemented. See [authentication](AUTH.md) and
  [self-hosting](SELF-HOSTING.md).
- **Desktop app.** The app has first-run setup, engine settings, a doctor
  command, SQLite storage and unsigned Windows/macOS installers. The `v0.3.0`
  prerelease has a Windows x64 installer; the newest macOS arm64 DMG is
  `v0.2.0`. The `v0.3.0` Windows app includes an assisted, checksum-verified
  updater; it does not install an update automatically. The release-to-release
  update path has not been verified end to end.
- **Optional legacy path.** The original Boat sandbox tasks and Telegram bot
  remain separate from the local agent workspace. Neither is needed for local
  chat or routines.
