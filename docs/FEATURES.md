# Features

Shipped capabilities of the current prototype. Planned work is in [ROADMAP.md](ROADMAP.md).

- Run Claude Code tasks in disposable Boat sandboxes from Telegram or the web UI.
- Connect Claude through its OAuth login flow; optionally connect GitHub.
- Approval prompts for risky actions (file writes, shell commands, Git pushes) with remembered permission rules and a Git command classifier.
- Continue completed tasks in the same Claude conversation from the web UI.
- Schedule recurring prompts with failure tracking; results reported to Telegram-linked users.
- Encrypted credential storage; sandbox, task, permission and schedule records in PostgreSQL.
- Guest web mode without Telegram, optionally linked to a Telegram identity via link codes.

- **Local Claude (experimental, off by default):** run a chat-only prompt through your own signed-in `claude` CLI on the same machine, from the web UI. Claude has no tools in this mode (no files, no commands). Only usable from the machine running the backend. See [SETUP.md](SETUP.md#local-claude-experimental).

Limitations: see "Known limitations" in the [README](../README.md).
