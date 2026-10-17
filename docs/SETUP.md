# Setup

The authoritative quick start is the [README](../README.md#quick-start). Summary:

**Prerequisites:** `uv`, Node.js 20.9+ with npm, Docker (for Postgres; not needed with the experimental SQLite option), Bash (Git Bash on Windows), a Boat API key, and optionally a Telegram bot token.

**Run:** configure `backend/.env` from `backend/.env.example` and `frontend/.env` from `frontend/.env.example`, then start the stack with `scripts/dev.sh`.

Component-specific details: [backend/README.md](../backend/README.md), [frontend/README.md](../frontend/README.md).

## Database

**PostgreSQL is the default** and is what the Docker setup above starts. Nothing changes for an existing installation.

**SQLite (experimental, opt-in):** one local file, no Docker. Set `DATABASE_BACKEND=sqlite` in `backend/.env` (optionally `SQLITE_PATH`; the default is `~/.open-dots/open-dots.db`), then run `uv run alembic upgrade head` (or `scripts/dev.sh`, which skips Docker for SQLite). A new SQLite file is created from a frozen baseline of the current schema and stamped at the current revision; an existing file is upgraded like any other. A SQLite file that is not one this app created (tables but no version table, an empty version table, missing app tables, or not a SQLite file at all) is refused and left byte-for-byte unchanged.

What to know before using SQLite:

- The database holds encrypted credentials. The file is created readable only by you on Linux and macOS; on Windows it lives in your profile folder. Keep it out of synced or shared folders. Back it up while the app is stopped, or with SQLite's backup command; do not copy the file while the app is running.
- It runs in WAL mode with foreign keys on and a 5 second busy timeout. Writers take turns. The places that relied on PostgreSQL row locks or on a transaction staying open were reworked so they behave the same on both databases: the permission-approval wait, conflicting answers to one approval, reply admission, schedule claiming, and the code that talks to a sandbox or the Claude CLI (it no longer holds a database snapshot while it waits). The test suite checks these with several simultaneous connections on both databases.
- Still unproven on SQLite: the API, the Telegram bot and the scheduler running as separate operating-system processes on one file, and real Boat sandboxes end to end. Keep PostgreSQL for those until they are exercised; SQLite is suitable for trying the local Claude path and the web UI.
- To bring existing PostgreSQL data along, see [Moving existing data from PostgreSQL to SQLite](#moving-existing-data-from-postgresql-to-sqlite).
- Tested on Windows only. The code avoids OS-specific behaviour (paths use forward slashes in the database URL, POSIX file modes are applied only where they exist) but Linux and macOS runs are still to do.

### Moving existing data from PostgreSQL to SQLite

Copying and switching are two separate steps. The tool copies; you switch yourself afterwards.

1. **Stop the backend, the Telegram bot and the scheduler** (anything that writes to the database). The tool refuses if it sees active tasks, pending approvals or other connections on the database; `--allow-active` overrides that, but a copy taken while something is still writing can miss what it writes next.
2. From `backend/`, with `backend/.env` still pointing at PostgreSQL and the same `TOKEN_ENCRYPTION_KEYS` you used so far:
   ```
   uv run python -m app.db.copy_to_sqlite --dry-run     # checks and counts, creates nothing
   uv run python -m app.db.copy_to_sqlite               # copies to SQLITE_PATH (default ~/.open-dots/open-dots.db)
   ```
   `--target PATH` writes elsewhere. The target must not exist: the tool never overwrites a file (move or delete an old one yourself).
3. The tool reads PostgreSQL in one read-only snapshot, builds the SQLite file under a temporary name beside the target, copies every table keeping ids, then checks it: row counts, every row compared, every stored credential decrypted with your key, SQLite `integrity_check` and `foreign_key_check`. Only if everything passes is the file published. If anything fails, nothing is left behind. It prints table names and counts only, never row values.
4. To use SQLite, set `DATABASE_BACKEND=sqlite` in `backend/.env` (plus `SQLITE_PATH` if you used `--target`) and start the app. The tool does not change any setting.

**If something goes wrong**

- *"... already exists" (the target).* The app creates its SQLite file as soon as it starts with `DATABASE_BACKEND=sqlite`, and the tool's default target is that same file. Either run the copy before the app has ever started on SQLite, or use `--target` with a new name. The message says what is in the existing file (empty, an Open Dots database with N tasks, or something else). The tool never overwrites it; move or rename it yourself.
- *A `-wal` or `-shm` file is named.* SQLite keeps these next to a database while it is open. Move or rename them together with the `.db` file (same base name), and stop the backend first: they normally disappear when it stops cleanly. The tool refuses if only a side file remains, because it could be attached to a new database by mistake.
- *The page is empty after switching.* The app opened a different database file than the one you copied to. If you used `--target`, set `SQLITE_PATH` to it in `backend/.env`. The backend logs `Database in use: ...` at startup, so you can see which file it opened. Also use the same browser and address as before: the page remembers your user id in that browser.
- *Refused for active tasks or open connections.* Stop the backend, the bot and any database tool that is connected, then run it again.
- Inspecting an existing file to describe it never changes the database or its write-ahead log (SQLite's `-shm` index file may be touched, as by any reader).

**Rolling back:** set `DATABASE_BACKEND` back to `postgres` (or remove it). PostgreSQL still holds its data as it was when you copied; anything written in SQLite after you switched is not copied back, so rolling back loses it. Keep the PostgreSQL container and its volume until you are sure.

Tested on Windows with PostgreSQL 17 in Docker (`postgres:17-alpine`); the code avoids OS-specific behaviour (the no-overwrite publish uses a hard link, which fails atomically when the target exists, with a rename fallback for filesystems that have no hard links), but Linux and macOS have not been run yet.

## Local Claude (experimental)

Runs Claude through the `claude` CLI already installed and signed in on the machine that runs the backend, instead of a Boat sandbox. By default it is **chat-only**: tools, MCP servers and skills are all disabled, so Claude can answer but cannot read files or run commands. With `LOCAL_TOOLS_ENABLED=true` it can also use tools, each one waiting for your approval (see [Tools and approvals](#tools-and-approvals-local-claude)). The answer streams into the page as Claude writes it; the page keeps only a list of past answers (for conversations that continue and survive a restart, see [Durable threads](#durable-threads-experimental)).

1. Install Claude Code and sign in (`claude auth login`).
2. In `backend/.env` set `LOCAL_EXECUTION_ENABLED=true` and restart the backend. Optionally set `LOCAL_CLAUDE_CLI_PATH` to the full path of the real `claude` executable (on Windows the npm `claude.cmd` shim is resolved to the real `claude.exe` automatically) and `LOCAL_WORKSPACE_ROOT` (default `~/.open-dots/local`).
3. Open the web UI from this machine. A "Local Claude" card appears when the CLI is ready, or explains why it is not.

Who can use it: only requests that come from this machine, with a `localhost`, `127.0.0.1` or `[::1]` Host header, no proxy or tunnel headers, and a browser Origin listed in `FRONTEND_ORIGINS` (for example, opening the UI at `http://127.0.0.1:3000` requires adding that origin). Anything else, including schedules, Telegram and the older task endpoints, is refused. There is no password or token: anyone who can already run programs as you on this machine can use it. Do not enable it on a shared or publicly reachable server.

Settings: `LOCAL_EXECUTION_ENABLED` (default `false`), `LOCAL_TOOLS_ENABLED` (default `false`), `LOCAL_APPROVAL_TIMEOUT_SECONDS` (default 300), `LOCAL_ALLOWED_HOSTS`, `LOCAL_CLAUDE_CLI_PATH`, `LOCAL_WORKSPACE_ROOT`, `LOCAL_TASK_TIMEOUT_SECONDS` (default 300), `LOCAL_MAX_OUTPUT_BYTES` (default 1000000; the limit on the answer text, and the raw CLI stream may be up to 8 times larger).

Streaming: the page opens a server-sent-events stream (`GET /api/v1/local/tasks/{id}/events`) for each run and shows the text as it arrives; the final result and any error always come from the saved task. Closing or refreshing the tab does not stop a run (it ends or times out on its own), and reopening the page shows the answer so far. Streams are held in memory, so live streaming needs a single backend process; with several workers, or after a restart, the page falls back to polling the saved task. Nothing is replayed after a disconnect.

### Tools and approvals (local Claude)

Set `LOCAL_TOOLS_ENABLED=true` in `backend/.env` and restart the backend. Claude can then use six tools in its workspace folder (`LOCAL_WORKSPACE_ROOT`, default `~/.open-dots/local/workspace`): `Read`, `Write`, `Edit`, `Glob`, `Grep` and `Bash`. There is no web access, no sub-agents and no MCP servers.

How it works: the page shows each tool call as a step in a timeline. When the CLI would ask a person before running something, the run stops and the page shows a card with the tool and its exact input (the full command, the file path and the new contents) and **Allow** and **Deny** buttons. Nothing runs until you press Allow, and what runs is exactly what the card showed. Deny, an unanswered question (`LOCAL_APPROVAL_TIMEOUT_SECONDS`, default 300) or a run that ends all count as a refusal; Claude is told and carries on. There is no "always allow". A request too large to be shown in full (over 50000 characters) is refused rather than approved unseen. Time spent waiting for you does not count towards `LOCAL_TASK_TIMEOUT_SECONDS`.

What you must know before turning it on:

- Once you press Allow, a command runs **as you, on this computer, without a sandbox**. Read the command before approving. This is the same trust you give the Claude CLI in a terminal.
- The CLI, not this app, decides which calls ask. In its default mode it asks for writes, for anything outside the workspace folder, and for commands it does not recognise as read-only. A few read-only commands (for example `echo` and `ls`) and reads inside the workspace run without asking, and permission rules in your own Claude settings are not turned off by this app, so a rule there that allows something will let it run unasked. Tested with Claude Code 2.1.232: a write inside the workspace, a read of `C:\Windows\win.ini` and `python -c ...` each asked; denying each stopped it.
- Approvals and the timeline live in memory, like the answer stream (single backend process). The timeline is shown while you keep the page open and is not saved; the saved task keeps only the answer. If the backend restarts during a run, the run ends as interrupted.
- Each local task started from the page is a new conversation. For conversations that continue, see [Durable threads](#durable-threads-experimental).
- Anyone who can already reach the local routes (see "Who can use it") can approve, because there is no password. Keep it off shared machines.
- The Claude CLI has its own memory feature that can save notes under your `~/.claude/projects/` folder, outside the workspace, without an approval card. Runs started by this app turn it off (`CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` for that process only), so the app's conversations cannot write there; your own use of the CLI is unchanged. Checked with Claude Code 2.1.232: with the variable set, a "remember this" request wrote no notes.

Tested on Windows with Claude Code 2.1.232. The code is written for Windows, macOS and Linux (OS-specific parts are guarded in `local_claude_runner.py`: the npm `claude.cmd` shim, `taskkill` versus `killpg`, console-window flags), but macOS and Linux have not been run yet.

## Approval modes, saved rules and the decision log

These apply to local Claude turns with tools on (`LOCAL_TOOLS_ENABLED`). Everything here is local access only.

**Modes.** `ask` (the default) leaves every question to the Claude CLI's default mode. `accept_edits` lets file edits in the workspace through without a card; commands and anything else the CLI would ask about still ask. `plan` makes the turn read-only. The mode applies to the next turn, including one that resumes an existing session. There is no mode that skips permissions. Chat-only runs (tools off) stay locked down whatever the mode.

**Always allow.** Answer an approval with `{"decision": "allow", "remember": true}` and that exact Bash command is saved for that conversation's workspace folder. Manage rules with `GET /api/v1/approval-rules`, `POST /api/v1/approval-rules` (`{"match": "exact" | "prefix", "command": "...", "workspace": "/abs/folder" (optional)}`) and `DELETE /api/v1/approval-rules/{id}`.

- `exact` matches the whole command; `prefix` matches the leading words (`git status` allows `git status -s`) and never a command containing `;`, `&`, `|`, `<`, `>`, a backtick, `$(` or a newline.
- A rule with a workspace applies only there, matched on the folder exactly as stored (no symlink or `..` folding). A rule without one applies in every folder.
- Only the Bash tool has rules. Commands containing a credential, control characters, or anything on the safety floor cannot be saved. At most 200 rules.
- A matching rule answers the question immediately. The approval is still recorded and shown in the conversation, and its `approval.resolved` event carries `"by": "saved_rule"` and the `rule_id`, so the Chat page says a rule allowed it rather than you.
- The answer to `remember` says what happened: `rule_id` is the saved rule, or it is null and `remember_error` says why nothing was saved (a credential, the safety floor, a conversation without a folder...); the call was then allowed this once only.
- `GET /api/v1/approval-rules?workspace=/abs/folder` lists the rules that apply in that folder: the ones tied to it and the ones with no folder.
- **Undo.** Deleting a rule (`DELETE /api/v1/approval-rules/{id}`, optionally `?thread_id=` for the conversation it was undone from) is how you take an "always allow" back. It is logged as an `undone` decision, and the next matching command asks again.

**Safety floor.** These are never allowed by a rule; a card appears and only you can allow them: recursive delete of `/`, your home folder or a system directory; recursive `chmod`/`chown` of those; formatting or overwriting a disk device (`mkfs`, `wipefs`, `diskutil eraseDisk`/`eraseVolume`/`reformat`/`zeroDisk`, `dd of=/dev/...`, `> /dev/sda`, including `/dev/disk/by-id/...` and `/dev/md/...`); `git push` with `--force`, `-f`, `--force-with-lease` or `+ref`; fork bombs (`:(){ :|:& };:` and the `function f { f|f& }; f` spelling). The words given to `eval` are read as a command too. A command the reader cannot follow to the end (an unbalanced quote, `$(...)`, backticks, `eval` of a variable, or a program named by a variable such as `$CMD` or `bash -c "$CMD"`) is held the same way. The floor lives only in front of automatic allows: it does not change what the CLI decides for itself, so `accept_edits` still lets edits through and a command the CLI never asks about is not seen by the app.

**Decision log.** One line per event (`shown`, `allowed`, `denied`, `auto_allowed`, `expired`, `cancelled`, and `undone` when you remove a saved rule) with the time, thread, turn, tool, a one-line summary, the conversation's approval mode (`mode`), who or what decided (`source`: `user`, `saved_rule`, `timeout`, `provider`), and which rule (`rule`: the saved command, or `safety_floor:<rule>` when a rule matched but the call was asked about). The summary is cut to 300 characters and secrets (API keys, tokens, bearer headers, `NAME=value` for secret-looking names, long random strings) are removed before writing; the full input is never logged. Files are `decisions-YYYY-MM.ndjson` in `~/.open-dots/decisions` (override with `DECISION_LOG_DIR`; the desktop app uses its data folder), mode 0600 in a 0700 folder. Months that ended more than `DECISION_LOG_RETENTION_DAYS` (default 180) ago are deleted, at most once an hour. A write that fails is logged and ignored: an approval never fails because of it. Read it with `GET /api/v1/decisions?limit=50&before=<next_before>&event=denied&thread_id=3`. The log is not tamper-proof: anyone who can write to that folder can change it.

**CSV export.** `GET /api/v1/decisions/export.csv` takes the same `event` and `thread_id` filters (and `limit`, at most 10000) and returns the log newest first as CSV. Every cell is redacted again, and a cell starting with `=`, `+`, `-`, `@` or a tab is prefixed with `'`, so a spreadsheet shows it as text instead of running it.

**Health scan (sentinel).** A read-only scan of recent conversations: `GET /api/v1/sentinel/scan?hours=24` (1 to 2160), or from `backend/`, `uv run python -m app.services.sentinel [--hours 24] [--json]`. It reads stored thread events and the decision log, changes nothing and calls no model, and reports, worst first: **retry loops** (the same tool failing the same way 3 or more times in one turn), **repeated denials** (the same call denied 2 or more times in one conversation, so Claude kept asking for what you refused), **tool errors** grouped by tool and error, and **failed answers** grouped by error code (stops and shutdowns are left out). An issue never holds tool output: its title is the error with quoted text, URLs, email addresses, paths, ids and numbers replaced, cut at 160 characters, and a failed shell command is grouped only by exit code and an error class (`command not found`, `no such file`, `timed out`, ...). Each issue points at its evidence (an event id or a decision id). The scan keeps no state and writes no file.

## The Chat page

Open `/chat` (the "Open chat" link on the home page). It needs the same setup as [local Claude](#local-claude-experimental): `LOCAL_EXECUTION_ENABLED=true` on the backend and the `claude` CLI installed and signed in. If not, the page says which, and what to change. With `LOCAL_TOOLS_ENABLED` off it is chat only and the header says so.

- **Conversations.** The list on the left shows every conversation, newest activity first. "New chat" starts an empty one; the conversation is created when you send the first message, and its address (`/chat?thread=<number>`) can be bookmarked. On a narrow window the list is behind the menu button.
- **Answers and tool calls.** Claude's answer appears as it is written. Each tool call is one step you can open to see its exact input and its output (long output is cut for display, with a button to show all).
- **Approvals.** A call that needs your permission shows a card with exactly what will run, and does not run until you press Allow. Deny, or no answer, runs nothing. Until you answer, the message box is closed. For a shell command the card also offers **Always allow this command**: it is allowed now, and exactly that command runs without asking in this conversation's folder from now on; the card then offers **Undo**. If it cannot be saved (a credential in it, the safety floor) the card says it was allowed once only. A call a saved rule allowed shows "Allowed by a saved rule, without asking" with **Stop always allowing**. The header shows the conversation's approval mode (its agent's, or Ask first for a plain chat) when tools are on.
- **Approvals panel.** The **Approvals** button in the header (also a section of the desktop Settings page) has three tabs: **Always allowed** lists the saved rules, by default the ones that apply in this conversation's folder, and adds (exactly, or starts with; one folder or every folder) and removes them; **Decisions** pages through the decision log, newest first, filtered by event and conversation, with **Export CSV**; **Health** runs the read-only scan described above.
- **Reload or restart.** Reloading the page, or restarting the backend, shows the same conversation, and your next message continues the same Claude session. If the backend stopped while Claude was working, the page says the message was not resumed or sent again; send it again if you still want it.
- **A lost session.** If Claude can no longer find a conversation's session, the page explains it and offers "Start a new session". The messages stay visible, but Claude no longer has them as context.
- **Connection.** If the page loses the backend it shows "Reconnecting…" and catches up by itself; nothing is lost or shown twice.
- **Stop.** While Claude is answering, the send button is Stop. Claude is stopped, what it had written is kept and marked as unfinished, a question waiting for your answer is cancelled (nothing runs), and any tool call that had not finished is marked. The conversation continues with the same Claude session. If the answer had already finished, nothing is lost.
- **Rename, archive, delete.** Each conversation has a menu (the dots next to it). Renaming does not move it in the list. Archiving hides it from the list; "Show archived" shows archived ones again, where Unarchive brings one back. Deleting asks first and removes the conversation's messages, tool calls and approvals from this app's database, and that cannot be undone. Archiving and deleting are refused while an answer is running: stop it first. Delete removes this app's own records of the conversation and nothing else; it is not a secure erase. It leaves the Claude CLI's own copy of the session in your `.claude` folder (this app never deletes anything there), any files Claude wrote or changed in its workspace, copies or backups of the database file, and the older Local Claude card's task history. The database may also keep the deleted text in unused space inside its file until that space is reused or the file is compacted.
- **Formatted answers.** Answers are shown as Markdown: headings, lists, tables, quotes, bold and italic, inline code, and code blocks with a Copy button. Raw HTML in an answer is shown as text, never run, and only `http`, `https` and `mailto` links are clickable (they open in a new tab). A half-written answer renders as it stands and settles as more arrives.
- **Agents.** When the backend supports named agents, the header has an **Agents** button and, for a new chat, an agent picker. The Agents panel lists, creates, edits and archives agents: a name, instructions, a model (type any name the Claude CLI accepts, or pick default, sonnet, opus or haiku; `default` means Claude Code's own choice), a workspace folder, and an approval mode: **Ask first** (writes and commands ask), **Accept edits** (file edits go ahead, commands ask) or **Plan only** (nothing is changed). Each agent also has a title and description (for you; not sent to Claude), **Duplicate** (same settings, no notes, named "<name> copy"), **Pin** (pinned agents list first), **Notes** and **Export**, and the panel has **Import…**; see [Agent notes and agent files](#agent-notes-and-agent-files). A change applies to an agent's next message. A conversation shows its agent's name in the list and the header. With an older backend that has no agents the buttons and picker are simply absent.
- **Not yet:** search.
- **Where it is stored.** Conversations, including what Claude read, wrote and ran, are in the database, as described under [Durable threads](#durable-threads-experimental); the page itself keeps nothing in the browser.

## Agent notes and agent files

**Notes.** Every named agent keeps notes between conversations in one Markdown file, `MEMORY.md`, in its own folder: `agents/<id>` under `LOCAL_WORKSPACE_ROOT` (default `~/.open-dots/local/workspace/agents/<id>/MEMORY.md`). The file stays there even when the agent works in a project folder, so a project never gets an agent's notes in it. At the start of every turn the first 200 lines, then at most 24 KB of those, are added to the agent's system prompt after its instructions; anything past that does not load, and the agent is told the file was cut. The agent is told where the file is and asked to keep it short and current; when the file is inside its workspace it can edit it with its file tools, which ask for approval like any other write. The notes are given to it as background that never grants permission for anything.

- **Secrets.** Anything that looks like an API key, token, `password: ...` value, bearer token or private key block is replaced with `[redacted]` when the notes are saved (through the API, the panel, starter notes or an import) and again when they are loaded into a turn, so a key the agent once copied into the file does not keep travelling. Saves report how many things were replaced.
- **The file.** It is written atomically (a crash leaves the old file) with owner-only permissions (0600). A link put in place of the file is never followed. At most 256 KB can be saved.
- **Editing.** In the Agents panel, **Notes** opens the file with a gauge of lines and size against what loads (amber from 80%, red once anything is cut). Saving sends the version you opened; if the agent changed the file meanwhile, the save is refused and you choose between loading its version and overwriting it with yours. **Use notes in this agent's conversations** (also in the edit form) switches loading off without deleting the file. A new agent can be given **Starter notes**.
- **Lifecycle.** A duplicate starts with no notes. Deleting an agent deletes its `MEMORY.md` (its folder stays, because its conversations may use it), and a new agent never inherits a file left behind at its id. Archiving keeps the notes.
- **API.** `GET /api/v1/agents/{id}/memory` returns `text`, `hash`, `lines`, `bytes`, `loaded_lines`, `loaded_bytes`, `truncated`, `max_lines`, `max_bytes`, `enabled` and `path`. `PUT /api/v1/agents/{id}/memory` with `{"text": ..., "expected_hash": ...}` saves; a stale hash is 409 `memory_changed` with `current` and `current_hash`; leave `expected_hash` out to overwrite on purpose; more than 256 KB is 413 `memory_too_large`. `memory_enabled` is a field of the agent (`PATCH /api/v1/agents/{id}`), and `starter_notes` is accepted by `POST /api/v1/agents`.

**Agent files.** **Export** in the Agents panel (or `GET /api/v1/agents/{id}/export`) saves an agent as one `<name>.agent.json` file to keep or to give to someone. It holds only the name, title, description and instructions, plus the notes as starter notes when you tick **Include its notes** (`?include_notes=true`; off by default, because notes can hold private details). It never holds the model, provider, workspace folder, approval mode, pin, conversations or anything about this computer. Text that looks like a secret is replaced before the file is written, and the panel lists the parts where that happened (`redacted` in the response). **Import…** (or `POST /api/v1/agents/import` with `{"document": <file>, "include_notes": true}`) adds a new agent from such a file. Only those fields are read; anything else a file says is ignored, so an imported agent always starts on **Ask first** with the default model and its own folder. A name already in use gets a number (`Reviewer 2`). A file that is not an agent file is refused with 422 `invalid_agent_file`.

After updating, run `uv run alembic upgrade head` (`scripts/dev.sh` does). It adds the `agent.memory_enabled` column (on for existing agents) and changes nothing else.

## Durable threads (experimental)

Conversations that survive a page refresh and a backend restart, where each message continues the same Claude session. The [Chat page](#the-chat-page) is its interface; the older Local Claude card on the home page still runs one prompt at a time. It is local access only and needs the same setup as [local Claude](#local-claude-experimental) (`LOCAL_EXECUTION_ENABLED=true`, the `claude` CLI signed in). Tools and approvals follow `LOCAL_TOOLS_ENABLED` exactly as described under [Tools and approvals](#tools-and-approvals-local-claude).

After updating, run `uv run alembic upgrade head` (`scripts/dev.sh` does). It adds four tables (`thread`, `turn`, `event`, `approval`) and changes nothing that exists.

| Endpoint | What it does |
|---|---|
| `POST /api/v1/threads` | Start a conversation. Optional `provider` (default `claude`), `model` and `title`. |
| `GET /api/v1/threads`, `GET /api/v1/threads/{id}` | List conversations, newest activity first; read one with its turns. |
| `POST /api/v1/threads/{id}/turns` | Send a message, `{"prompt_text": "..."}`. Answers 202 once the turn has started; the answer arrives as events. 409 `turn_running` while a turn is running, 409 `session_broken` (see below), 503 `provider_unavailable` if the CLI is not ready. |
| `GET /api/v1/threads/{id}/events?after=N` | Stored events after number N, in order. |
| `GET /api/v1/threads/{id}/events/stream` | Server-sent events. Each stored event has `id: <number>`; a browser that reconnects sends `Last-Event-ID` itself and receives exactly what it missed (or pass `?after=N`). `text.delta` frames are a typing preview with no id that is never stored: clear it when the `assistant.message` event arrives. |
| `GET /api/v1/threads/{id}/approvals?status=pending`, `POST /api/v1/threads/{id}/approvals/{approval_id}` | Tool calls waiting for you, with the exact input; answer `{"decision": "allow"}` or `{"decision": "deny"}`. An approval that was already answered, expired, or whose turn is no longer running answers 409 `approval_not_waiting` and authorizes nothing. |
| `POST /api/v1/threads/{id}/stop` | Stop the running answer. 202 once Claude was told to stop; the end arrives as a `turn.cancelled` event. 409 `no_running_turn` if nothing is running, 409 `turn_not_stoppable` if the turn is running in another backend process. |
| `PATCH /api/v1/threads/{id}` | Rename and/or archive: `{"title": "..."}` (1 to 200 characters), `{"archived": true}` or `false`. 409 `turn_running` when archiving while an answer runs. |
| `DELETE /api/v1/threads/{id}` | Delete the conversation's turns, events and approvals from this app's database (204). 409 `turn_running` while an answer runs. Not a secure erase: it does not touch the Claude CLI's own session files or anything outside the database (see the Chat page section). |
| `GET /api/v1/threads?include_archived=true` | The list, with archived conversations included (they are left out by default). |
| `POST /api/v1/threads/{id}/session/reset` | Start a new Claude session for the thread (see below). Not allowed while a turn is running. |

Events, in the order they happen: `turn.started` (it carries the prompt), `assistant.message`, `tool.started`, `approval.requested`, `approval.resolved`, `tool.completed`, then one of `turn.completed`, `turn.failed` or `turn.interrupted` (`turn.cancelled` is what Stop produces); and `session.reset` when a session is reset.

What to know:

- **Restarts and crashes.** Everything stored survives. A turn that was running when the backend stopped is marked `interrupted` and is never run again. Quitting the backend cleanly does this at once; after a crash it happens about a minute later, when the turn's heartbeat expires, and until then the thread reports a turn as running. Claude does not remember the call it was about to make, so say so in your next message if it matters. The text of an answer that was still being typed at that moment is not saved; only completed answers are. A turn that fails while the backend keeps running keeps its partial answer, marked `incomplete`.
- **A lost session.** If Claude can no longer find a thread's session (for example its `~/.claude` data was removed), the turn fails with `session_resume_failed` and the thread becomes `broken`. The app never starts a fresh session on its own: Claude would answer without the earlier context while looking like a continuation. Reset the session explicitly; the stored transcript stays visible, but Claude no longer has it as context.
- **Approvals** are stored with the exact input that was shown and are valid only while their turn is running. An Allow reaches Claude only if the turn still belongs to the backend process that is running it.
- **Stored transcripts.** Events contain what Claude read, wrote and ran, including file contents and commands. They are stored in the database (the SQLite file, or PostgreSQL) and there is no retention period yet. A conversation can be deleted from the Chat page (or `DELETE /api/v1/threads/{id}`), which removes this app's records of it, but not the Claude CLI's own copy under `~/.claude`, and it is not a secure erase (see [the Chat page](#the-chat-page)). Keep the database out of shared or synced folders.
- **One backend process for the live parts.** The typing preview and a live Allow or Deny answer are handled in the memory of the process running the turn. With several backend processes, stored events still reach every stream within about a second, and an answer recorded by another process is noticed within about a second.
- **Databases.** Tested on SQLite. On PostgreSQL the migration applies, but the behaviour has not been verified yet, so use SQLite for this feature for now.
- The heartbeat and expiry times (`THREAD_HEARTBEAT_SECONDS`, default 10, and `THREAD_LEASE_SECONDS`, default 60) are internal tuning values; you should not need to change them.
- Tested with Claude Code 2.1.232 on Windows. The code is written for Windows, macOS and Linux, but macOS and Linux have not been run yet.

## Desktop app (from a source checkout, experimental)

An Electron window around the local backend: you open one app instead of starting Python, the API and the web UI yourself. There is no installer yet; you run it from a clone of this repository. It uses local Claude only: Boat, Telegram, schedules and PostgreSQL are not part of it (the settings for those are not read), and it needs no `.env` file.

**You need:** Node.js 22 or newer, [`uv`](https://docs.astral.sh/uv/getting-started/installation/) (it downloads the Python the backend needs, so you do not install Python yourself), and the [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) installed and signed in. Run the commands below in the `desktop/` folder.

**The short way:** `npm run app` does whatever is missing, then opens Open Dots: it sets up the first time, rebuilds the page when it is missing or older than its source, and otherwise just starts. (`npm run app -- dev` is the same in dev mode.) The four commands below are what it runs, and what to use when you want one step on its own.

| Command | What it does |
|---|---|
| `npm run setup` | Installs everything: the backend's Python environment (`uv sync --locked`), the page's dependencies and Electron, including its program file. Run it once, and again after the dependencies change. |
| `npm run build` | Builds the page the app shows, as plain files. Run it once, and again after the page's code changes. |
| `npm run start` | Opens Open Dots. |
| `npm run dev` | The same, with its own data folder and window title, so developing never touches the data you use. |

`start` and `dev` never install or build anything. If something is missing they say which command to run and stop. (If the page's code is newer than the last build, they tell you, and open the older build.)

**Windows installer (experimental, unsigned).** `npm run package` (in `desktop/`, after `npm run setup`) builds the page, freezes the backend with PyInstaller (`backend/build.py`, needs `uv`) and makes `desktop/release/Open-Dots-Setup-<version>.exe` with electron-builder. The installed app carries its own backend, so it needs no Python; it still needs the Claude Code CLI. `npm run smoke:packaged` starts `release/win-unpacked` with no Python on `PATH` and a fresh data folder and checks that the backend serves. The installer is unsigned, so Windows SmartScreen will warn; PyInstaller does not cross-compile, so each OS needs its own build.

**First start.** Nothing to configure. The first start takes longer: it creates the database and the encryption key.

**What the app does for you**

- **Encryption key.** Made on first start and kept with the operating system's secret storage (Windows DPAPI, the macOS Keychain, or the Linux keyring) in `secrets/encryption-key.v1` in the data folder. It is never written as plain text and never replaced: if the stored key cannot be read, or the stored credentials cannot be opened with it, the app stops and tells you instead of making a new one. This protects against other user accounts and a copied disk; it does not protect against software running as you. Where there is no secret storage (a Linux session without a running keyring) the app does not start rather than keep the key in a plain file.
- **Database.** SQLite, in the `data` folder, created and updated by the app. Before an update that changes the database, the app copies it to `data/backups/` (the newest five copies are kept). A file that is not one this app made is refused and left alone. When the app stops, the write-ahead log is folded into the file, so the file on its own holds everything.
- **Access.** Only the app's own window can use the backend. Each start makes a fresh random token, which the backend receives before it listens and the window adds to its own requests; a browser tab, another program or another website that finds the address gets nothing (a browser tab is told to use the app window). The token is never written to a file, a log, the environment or the page. There is no way to switch this off, in `dev` either. The address and port are chosen by the operating system on every start.
- **Settings.** Not read from backend environment variables or any `.env` file. SQLite and local execution stay on, and the workspace stays under the data folder (`local/workspace`). The Settings page lets you choose the Claude executable and default workspace root, switch local tools on or off, and change answer/approval timeouts and the output limit. Tools are on by default; Claude's existing permission rules still apply, as described above.

**Where things are.** The data folder is `Open Dots` (or `Open Dots (dev)` for `dev`) in your operating system's application-data folder (on Windows, `%APPDATA%\Open Dots`). In it: `data/` (the database, its backups, Claude's workspace in `local/`), `secrets/`, and `logs/backend.log` (the backend's log, also where it says why it did not start). To remove the app's data, close it and delete that folder. Claude's own copy of each session stays in your `.claude` folder, as for the Chat page.

**Settings.** Follow **Settings** in the Chat header (also available when Claude is missing). The Claude section has six preferences: executable path, workspace root, local tools, answer timeout, approval timeout and answer output limit. **Browse** opens a native file/folder picker; you can also enter a full path. Leave the executable empty for detection through `PATH` (Windows npm `claude.cmd` shims are supported), or the workspace empty for the desktop default. Timeouts are 30–3600 seconds; output is 1024–16000000 bytes.

Each field has its own **Save** and **Discard**, source, effective value, default and apply rule. Settings are stored in the app database, with versions that reject a stale save rather than silently overwrite another change. Changes to the executable, tools, workspace and approval timeout also record their old and new non-secret values in a settings audit table. **Reload saved settings** discards unsaved edits and reads the latest versions. No restart is needed: executable, tools, answer timeout and output limit apply to the next turn; workspace changes affect new conversations only; approval timeout changes affect new approvals only. A running answer keeps its configuration, and an approval already waiting keeps its deadline.

**Workspace identity.** New conversations use the `workspace` subfolder of the chosen root. Each conversation stores its full resolved workspace path, so changing the default never moves existing sessions. If that folder disappears or becomes inaccessible, sending another message is refused; the conversation stays readable and the app does not recreate or switch its folder. Restore the original folder/access to continue. When upgrading an existing database, conversations are pinned to the workspace in use at upgrade time and the launcher makes its usual backup before migrating.

**Validation and recovery.** Invalid stored settings do not prevent startup. An invalid tools value disables tools; an invalid executable makes Claude unavailable without silently choosing another; an invalid workspace blocks new conversations. Invalid numeric limits use bounded defaults with an error shown beside the setting. Correct and save the value in Settings. Storage shows the data folder, database, backups and OS-backed encryption-key status, all read-only. Settings editing requires the desktop launch token plus local access checks. Headless installs keep their environment/`.env` configuration.

**Claude not found or not signed in.** The Chat page links to Settings. Install Claude or sign in with `claude auth login` in your terminal, then choose **Check again**. **Check Claude status** in Settings checks the saved executable and login, not an unsaved draft. `OPEN_DOTS_CLAUDE_PATH` remains a launcher fallback when no executable preference is stored; a stored value takes precedence. An empty stored path explicitly selects automatic detection.

**One more launcher variable**, optional: `OPEN_DOTS_DATA_DIR` (a full path) uses another data folder, for tests or a second copy. Like `OPEN_DOTS_CLAUDE_PATH` it is read by the launcher, not by the backend, and is not a `.env` setting.

**Quitting.** Closing the window stops the backend. If the app is killed, the backend notices and stops within seconds. A message Claude was answering at that moment is interrupted and is not sent again (see [Durable threads](#durable-threads-experimental)).

**Limits of this version**

- Verified on Windows only (Windows 11, Electron 43, Node 22, Claude Code 2.1.232): starting with no `.env`, the token, a restart that keeps the data, a real conversation with an approval, Deny and Stop. The code is written for Windows, macOS and Linux, but macOS and Linux have not been run; on Linux the app also needs a running keyring. It has not been tried on a machine that never ran it before, or by anyone but its author.
- No installer, no automatic updates and no first-run onboarding wizard. Settings and Browse are available; storage locations cannot be changed here.
- One app per data folder (a second start brings the first window forward), but this is the app's own lock, not a lock on the data folder: do not point two copies at one folder with `OPEN_DOTS_DATA_DIR`.
- If the backend stops while the app is open, the app tells you and closes; it does not restart it.
- The page's address (and so its browser storage) changes on every start. The page keeps nothing there that matters.
- `npm run e2e` (in `desktop/`) runs the app for real: it opens a window, and its `chat` part makes a few short requests with your Claude. It is written for Windows. `npm run e2e -- upgrade` is optional and only runs when named: it starts the previous release (taken from Git history, prepared once under your temp folder), then checks that upgrading keeps an existing Claude conversation in its original workspace after you change the default one. It needs Claude signed in (two short requests), `git`, `tar` and `uv`, and skips with a reason if any is missing. The conversations it creates stay in Claude's own session store; the test never deletes anything there.

This file will change when the installable launcher (roadmap F10) ships.
