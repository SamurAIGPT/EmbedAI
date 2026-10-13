# Features

Shipped capabilities of the current prototype. Planned work is in [ROADMAP.md](ROADMAP.md).

- Run Claude Code tasks in disposable Boat sandboxes from Telegram or the web UI.
- Connect Claude through its OAuth login flow; optionally connect GitHub.
- Approval prompts for risky actions (file writes, shell commands, Git pushes) with remembered permission rules and a Git command classifier.
- Continue completed tasks in the same Claude conversation from the web UI.
- Schedule recurring prompts with failure tracking; results reported to Telegram-linked users.
- Encrypted credential storage; sandbox, task, permission and schedule records in PostgreSQL.
- Guest web mode without Telegram, optionally linked to a Telegram identity via link codes.

Limitations: see "Known limitations" in the [README](../README.md).
