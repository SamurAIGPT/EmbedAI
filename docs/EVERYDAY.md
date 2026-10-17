# Everyday features: search, attachments, workspace files, usage

Four small features that make daily use easier, plus a pass over the chat's
error and empty states. Each one works on SQLite and PostgreSQL alike and needs
no new tables: they read what the app already stores (threads, turns, events)
or keep files next to the database.

## Conversation search

`GET /api/v1/search?q=...` (chat scope).

- **What is searched.** Conversation titles, what the person wrote
  (`turn.started` events) and what the agent answered (`assistant.message`
  events). Tool output, approvals and typing previews are not searched.
- **How.** Every word of the query (up to 6) must appear, in any order and
  case, inside one title or one message: a portable `lower(...) LIKE` with
  escaped wildcards. Message text is read from the event's JSON with
  SQLAlchemy's JSON element access, which compiles to `json_extract` on SQLite
  and `->>` on PostgreSQL. No FTS index: a personal history is small enough
  for a scan, and one query path on both databases is easier to trust. A
  query under 2 characters returns nothing.
- **Filters.** `agent_id`, `since` / `until` (ISO dates, inclusive),
  `include_archived` (off by default), `limit` (1 to 100, default 40).
- **Result.** Newest first. Each hit names its conversation (id, title, agent,
  archived), where it matched (`title`, `user` or `assistant`, with the event
  `seq` and turn), when, and a snippet of about 160 characters around the
  first match with `highlights`: `[start, length]` pairs into the snippet, one
  per matched word occurrence. The UI marks them; it never renders the snippet
  as HTML.
- **UI.** A search box at the top of the conversation list. Typing waits a
  moment, then shows hits with the matching words marked; choosing one opens
  that conversation. Filters: archived, agent.

## Attachments and images

`POST /api/v1/threads/{id}/attachments` (multipart, field `file`) uploads one
file and returns `{id, name, mime, bytes, kind}`. `POST .../turns` takes
`attachment_ids` (up to 8). `GET .../attachments/{attachment_id}` returns the
bytes. All chat scope.

- **Storage.** A private folder outside every workspace:
  `<data>/attachments/<thread id>/<uuid><ext>` with a small `<uuid>.json`
  beside it (display name, type, size, sha256). `<data>` is
  `ATTACHMENTS_DIR`, else `~/.open-dots/attachments`, else the desktop data
  folder. Folders `0700`, files `0600`. The stored file name is always a fresh
  UUID plus an extension chosen from the type; the person's file name is only
  ever a display name (control characters and path separators removed,
  shortened).
- **What is accepted.**
  - Images: PNG, JPEG, GIF, WebP, up to 10 MB, and the bytes must start with
    that format's signature (a renamed file is refused).
  - Text: `.txt .md .csv .tsv .json .log .yaml .yml .xml .html .css .js .ts
    .tsx .py .sh .sql .toml .ini`, up to 1 MB, valid UTF-8 with no NUL bytes.
  - Anything else: 415 with the list above. Over a limit: 413. An empty file:
    400. A thread holds at most 200 MB of attachments (507 after).
- **To the engine.** Text files are added to the message as fenced blocks
  under their display names (every engine reads them). Images go only to an
  engine whose capability flags say `images`. Today that is Claude: the CLI
  gets one stream-json user message with text and base64 image content blocks.
  Sending an image to any other engine is refused before the turn starts:
  422 `images_unsupported`, "<Engine> can't read images. Remove the image or
  use an engine that can."
- **In the transcript.** The `turn.started` event carries the attachment list
  (never the bytes), so the user's message shows image thumbnails and file
  chips, after a reload too. The prompt stored on the turn stays what the
  person typed.
- **Composer.** Paste an image, drag files onto the chat, or pick with the
  paperclip. Chips show name and size with a remove button; an upload that
  fails says why and is not sent.

## Workspace files

`GET /api/v1/threads/{id}/files?path=` lists a folder of the conversation's
pinned workspace (for an agent's conversation, the agent's workspace).
`GET .../files/content?path=` previews a text file (first 256 KB) or an image
(up to 10 MB), whose bytes come from `GET .../files/raw?path=`.
`GET .../files/download?path=` downloads a file (up to 50 MB).
Admin scope only: a paired chat-only device cannot browse files.

Confinement, checked on every request:

- `path` is relative. An absolute path, a `..` part, a backslash, NUL or a
  drive prefix is 400 before the disk is touched.
- The workspace root and the target are both canonicalised (`realpath`); the
  target must be inside the root. A symlink that points outside (file or
  folder, anywhere on the path) is 403 `outside_workspace`; a listing skips
  such entries rather than show them.
- The file is opened with `O_NOFOLLOW` and its inode compared with the
  canonical path after opening, so a swap between check and open is refused.
- Secrets are never served, even inside the workspace: `.env` and `.env.*`,
  `.git/`, `.ssh/`, `.aws/`, `.gnupg/`, `.netrc`, `.npmrc`, `.pypirc`,
  `*.pem`, `*.key`, `*.p12`, `*.pfx`, `id_rsa*`, `id_ed25519*`, `id_ecdsa*`.
  Listings show them as `hidden` with no size; reading one is 403
  `secret_file`.
- Downloads are `Content-Disposition: attachment` with a safe file name,
  `X-Content-Type-Options: nosniff` and `Content-Security-Policy: sandbox`.
  A listing returns at most 500 entries (`truncated` says so).

UI: a Files panel for the open conversation: folders, sizes, a text or image
preview, and a download link.

## Usage and cost

Engines report tokens and cost at the end of a turn when they know them
(Claude: input, output, cache read and cache write tokens, and cost in USD).
The turn stores exactly what was reported. Nothing is estimated.

- Each finished turn in the transcript shows its tokens and cost, or
  "usage not reported".
- A finished turn's `turn.completed` / `turn.failed` event carries the
  `usage` its engine reported (absent when it reported nothing).
- `GET /api/v1/usage?month=YYYY-MM` (admin; months and days in UTC): totals for the month, by
  conversation, by engine and model, and by day. A total counts `turns`,
  `reported_turns` and `unreported_turns`; `cost_usd` is the sum of reported
  costs and is null when no turn in that group reported one.
- `GET /api/v1/usage.csv?month=YYYY-MM` (admin): one row per turn of that
  month (time, conversation, engine, model, status, tokens, cost), with cells
  that start like a spreadsheet formula neutralised.
- UI: a Usage panel (month picker, totals, by conversation, CSV link), and a
  conversation total in the header.

## Errors and empty states

Every refusal a person can act on has a sentence that says what to do:
images to a text-only engine, a file too big or of the wrong type, search
with no results, an empty workspace, a month with no usage, and a turn that
failed with its reason.

## Not in this version

- Attached text files reach the engine only in the turn they were sent with;
  an engine that rebuilds context from stored history (OpenAI-compatible)
  sees the typed message, not the file, on later turns. Text inside attached
  files is not searched.
- SQLite's `lower()` folds ASCII only, so search is case-insensitive for
  ASCII letters; other scripts match case-sensitively there (PostgreSQL folds
  them).

- Ranking by relevance, stemming, or an FTS index.
- PDFs and office documents as attachments; images for Codex, ACP and
  OpenAI-compatible engines.
- Editing or deleting workspace files from the browser; zip download of a
  folder.
- Budgets, alerts or price tables for engines that do not report cost.
