# Features

Shipped capabilities of the current prototype. Planned work is in [ROADMAP.md](ROADMAP.md).

- Run Claude Code tasks in disposable Boat sandboxes from Telegram or the web UI.
- Connect Claude through its OAuth login flow; optionally connect GitHub.
- Approval prompts for risky actions (file writes, shell commands, Git pushes) with remembered permission rules and a Git command classifier.
- Continue completed tasks in the same Claude conversation from the web UI.
- Schedule recurring prompts with failure tracking; results reported to Telegram-linked users.
- Encrypted credential storage; sandbox, task, permission and schedule records in PostgreSQL (default), or in a local SQLite file when `DATABASE_BACKEND=sqlite` (experimental; see [SETUP.md](SETUP.md#database)).
- Guest web mode without Telegram, optionally linked to a Telegram identity via link codes.

- **Local Claude (experimental, off by default):** run a prompt through your own signed-in `claude` CLI on the same machine, from the web UI, with the answer appearing as Claude writes it. Chat-only by default. With `LOCAL_TOOLS_ENABLED=true` Claude can read and write files in its workspace folder and run commands, shown as a timeline, and each call it would ask about waits for your Allow or Deny in the page. Only usable from the machine running the backend. See [SETUP.md](SETUP.md#local-claude-experimental).

- **Durable threads (experimental, API only, off by default):** with the same switch as local Claude (`LOCAL_EXECUTION_ENABLED`), conversations are stored in the database. Messages, answers, tool calls and approvals survive a page refresh and a backend restart, and each message continues the same Claude session. A server-sent-events stream shows answers as they are written and resumes exactly after a disconnect. An interrupted turn is marked and never run again; a lost Claude session must be reset explicitly. There is no UI for it yet. See [SETUP.md](SETUP.md#durable-threads-experimental).

Limitations: see "Known limitations" in the [README](../README.md).
