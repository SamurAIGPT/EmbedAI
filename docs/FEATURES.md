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

- **Durable threads and the Chat page (experimental, off by default):** with the same switch as local Claude (`LOCAL_EXECUTION_ENABLED`), conversations are stored in the database. Messages, answers, tool calls and approvals survive a page refresh and a backend restart, and each message continues the same Claude session. A server-sent-events stream shows answers as they are written and resumes exactly after a disconnect. An interrupted turn is marked and never run again; a lost Claude session must be reset explicitly. The Chat page (`/chat`, linked from the home page) shows them: a list of conversations, answers as they are written, tool calls and approvals in order, and a clear message with a way forward when a session is lost. You can stop an answer, and rename, archive or delete a conversation. Not yet: Markdown. See [SETUP.md](SETUP.md#the-chat-page).

- **Desktop app, from a source checkout (experimental):** an Electron window around the local backend, started with `npm run start` in `desktop/`. It needs no `.env`: it keeps its own encryption key in the operating system's secret storage, creates and backs up its SQLite database, and only its own window can use the backend (a fresh launch token on every start). Local Claude only; no installer yet. Verified on Windows only. See [SETUP.md](SETUP.md#desktop-app-from-a-source-checkout-experimental).

- **Desktop settings:** configure the Claude executable and default workspace root with native Browse dialogs, local tools, answer/approval timeouts and output limit. Per-field saves are versioned in the database, sensitive changes are audited, and the page shows sources, effective values and validation errors. New turns use saved configuration; running turns and existing approval deadlines stay unchanged. Conversations keep their original workspace across changes and restarts. Storage information is read-only; no onboarding wizard yet. See [SETUP.md](SETUP.md#desktop-app-from-a-source-checkout-experimental).

Limitations: see "Known limitations" in the [README](../README.md).
