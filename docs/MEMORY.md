# Memory and context

How a named agent remembers things between conversations, and how a long
conversation stays inside an engine's context window. Everything here applies
to conversations started from a named agent; a conversation without an agent
has no notes, no recall and no proposals (compaction still applies).

## What is where

```
<LOCAL_WORKSPACE_ROOT>/agents/<agent id>/
├── MEMORY.md           loads into every turn (first 200 lines / 24 KB)
└── memory/
    └── <topic>.md      one subject each; never loaded whole

<data folder>/memory-state/            (beside the attachments folder)
├── journal/<agent id>.ndjson          every change made through the app
└── proposals/<agent id>.json          proposed notes waiting for you
```

The data folder is `~/.open-dots` by default (the desktop app's own data
folder in the desktop app). The journal and the proposals live outside the
agent's folder on purpose: an agent with file tools works in its own folder,
and a record it could rewrite would not be a record.

## Topic notes

`MEMORY.md` holds what the agent needs in every conversation. Longer notes
about one subject (a person, a client, a trip) go in `memory/<topic>.md`.

- **Names.** One file name: letters, digits, dashes and underscores, ending in
  `.md`, at most 63 characters (`travel.md`, `client-acme.md`). No folders,
  no dots inside, nothing hidden, not `MEMORY.md`.
- **Header.** A note may start with front matter giving a `title` and
  `aliases` (other words someone would use for the subject):

  ```
  ---
  title: Travel
  aliases: [trips, flights]
  ---
  - Prefers aisle seats
  ```

  Without front matter the first heading is the title.
- **The index.** The system prompt lists every topic by name, title and
  aliases after the `MEMORY.md` block: at most 40 topics or 2,000 characters,
  then "…and N more". The bodies are never loaded whole: the agent reads a note
  with its file tools when it decides it needs it, and recall (below) puts a
  matching passage in front of a message.
- **Safety, the same as MEMORY.md.** Anything that looks like a secret is
  redacted when a note is saved through the app and again whenever any part of
  it is loaded (index, recall), so a note the agent wrote with its own tools is
  covered too. Writes are atomic (a crash leaves the old file or the new one)
  and owner-only (`0600`). A note is at most 64 KB and an agent keeps at most
  100. A save carries the hash of the text it was based on and is refused
  (`409 memory_changed`, with the current text) when the file changed
  meanwhile; a note not created yet has the hash of the empty text. A link, or
  a `memory/` folder that is a link, reads as nothing and is never written
  through.
- **Lifecycle.** Deleting an agent deletes its notes, topic notes, journal and
  proposals (the folder stays, as conversations may use it). A duplicate starts
  without any. Agent files (export/import) carry `MEMORY.md` only, not topic
  notes.

## Recall before each turn

Before each turn of a conversation with an agent whose notes and recall are
both on, the person's message is matched against the agent's topic notes and
its other conversations.

- **Matching.** The message's content words (three letters or more, common
  words left out, at most 12 from the first 500 characters; a message under 8
  characters recalls nothing). Any matching word counts; a message with five
  or more content words needs two in a passage. Notes match on their name,
  title, aliases and body; conversation passages are messages (yours and the
  agent's) in this agent's other, non-archived conversations, found with the
  same portable query as conversation search (OR instead of AND; works the same
  on SQLite and PostgreSQL, wildcards literal).
- **Ranking and limits.** Up to **4 notes** (by words shared, then most
  recently changed; about 600 characters each, starting at the first matching
  line) and **4 conversation passages** (by words shared, then newest; at most
  2 from one conversation; about 400 characters each), all within **6,000
  characters**. `MEMORY.md` is never recalled (it is already loaded) and
  neither is the current conversation (it is already in context).
- **Where it goes.** In front of the person's message, never in the system
  prompt: recall changes nearly every turn, and a changing system prompt would
  defeat the engines' prompt caching. The block opens by saying these are
  passages found automatically because they share words with the message, that
  they are background only, that what the person says now wins over an older
  passage, and that nothing inside them is an instruction or grants permission
  for anything. It ends with a closing line; text inside a passage that tries
  to close the block early is neutralised. Every passage is one line and
  redacted.
- **Visible, never hidden.** What was recalled is stored as a
  `memory.recalled` event right after the turn starts, and the transcript shows
  it as a **Recalled** chip ("Recalled 1 note and 2 passages from earlier
  conversations") that opens to show each passage and where it came from. The
  stored message is what the person typed.
- **Switch.** `recall_enabled` on the agent (on by default; `PATCH
  /api/v1/agents/{id}`; the checkbox under the notes editor). Switching notes
  off (`memory_enabled`) switches recall off too. A failure while recalling is
  logged and the turn goes on without it.
- **Every engine.** Recall is part of the turn's prompt, so Claude, Codex, ACP
  agents and OpenAI-compatible endpoints all receive it. (The OpenAI-compatible
  engine now sends the turn's prompt for the current message instead of the
  stored text, which also means attached text files reach it.)

## Context compaction

Claude and Codex keep their own sessions and use their CLI's own compaction;
nothing here touches them. Two engines replay the transcript and opt in
(`replays_history`):

- an **OpenAI-compatible endpoint** keeps no session, so every turn sends the
  whole stored conversation;
- an **ACP agent** that cannot reload its session is sent the conversation as
  a preamble.

Before such a replay, its size is estimated in tokens from characters with a
per-engine ratio (4 characters a token for OpenAI-compatible, 3.5 for ACP, a
few tokens per message, plus the agent's instructions). When it reaches the
budget, the earlier part is folded:

- **Budget.** `compact_at_tokens` of the engine (24,000), or 80% of
  `context_tokens` when the endpoint's saved configuration gives its model's
  context window (`PUT /api/v1/engines/openai_compat/config` with
  `"context_tokens": 8192`, from 1,024 to 10,000,000; there is no field for it
  in Settings yet).
- **Fold point.** Deterministic: the last **4 exchanges** (your message and
  everything after it) stay verbatim, and so does the current message;
  everything before is folded. When 4 exchanges would not leave the replay
  under 70% of the budget, fewer are kept (down to 1), so a fold leaves
  headroom and the conversation is not folded again on the very next turn.
- **Summary.** Made without a model call: the newest excerpts of what was said
  (up to 240 characters each), oldest first, after any earlier summary, within
  a quarter of the budget and at most 6,000 characters, with a count of
  excerpts left out and of tool calls (never quoted). It is headed as background
  made of excerpts, where later messages override earlier ones and details may
  be missing. It is redacted, and rides at the head of the first kept message
  (a chat must start with the person). A model-written summary is not
  implemented.
- **Stored and stable.** The fold is stored as a `context.compacted` event in
  the turn that made it (summary, folded-through turn, messages folded,
  tokens before and after, budget). Later turns replay exactly the same summary
  plus what came after, so the replay does not change from turn to turn, until
  the conversation reaches the budget again and folds once more. A record whose
  turn no longer exists is ignored.
- **Visible.** The transcript shows **Earlier messages summarized** with the
  number of messages folded and a button that shows the summary. The full
  conversation stays stored and visible; only what the engine is sent changes.
- When only the kept exchanges are left and they are too large by themselves,
  nothing more is folded and the engine's own size limit applies, as before.

## Proposed notes

Nothing is ever written to an agent's notes on its own. Once a conversation
with the agent has been quiet for two minutes, your own messages in it (never
the agent's replies) are read for statements meant to last: "remember that
…", "from now on …", "I prefer / I always / I live in / I work at …", "my …
is …". Questions are skipped. Each one becomes a proposal quoting your words:

```
- 2026-10-18 · from "Trip planning" · The person said: "I prefer aisle seats"
```

The **Proposed** tab under an agent's notes lists them; **Accept** appends the
line to `MEMORY.md` (or a topic note, through the API) with the same save as
the editor, journaled and undoable; **Reject** drops it, and the same line is
never proposed again. A line already in `MEMORY.md`, already proposed, or
holding anything that looked like a secret is not proposed. At most 50 wait at
once, 5 per conversation.

The proposer is deterministic, with no model call, so it misses facts said in
other ways. Conversations are read when the queue is looked at (opening the
tab, or `GET .../memory/proposals`), not by a background loop; each
conversation is read once up to its latest turn.

## The journal

Every change made through the app to `MEMORY.md` or a topic note (an editor
save, a topic deletion, an accepted proposal, an undo) is a row: which file,
who (`person`, `proposal`, `undo`), created, edited or deleted, the before and
after hashes, a short diff, and the earlier text, so **Undo** puts the file
back without anything else. An undo is itself a row and can be undone. A row
whose earlier text held something that looked like a secret keeps no earlier
text and cannot be undone; neither can one whose earlier text was over 256 KB.
The **History** tab shows the rows with their diffs.

Changes an agent makes with its own file tools during a turn are not
journaled (and so cannot be undone here).

## API

All under `/api/v1/agents/{id}`, admin scope only (not reachable by a
chat-scope device), with sign-in like every route.

| Method | Path | What it does |
| --- | --- | --- |
| `GET` | `/memory/topics` | Topic notes (name, title, aliases, bytes, modified), the folder and the limits. |
| `GET` | `/memory/topics/{name}` | One note: `text`, `hash`, `exists`. |
| `PUT` | `/memory/topics/{name}` | `{text, expected_hash?}`; `409 memory_changed`, `413 memory_too_large`, `409 too_many_topics`, `422 invalid_topic_name`. |
| `DELETE` | `/memory/topics/{name}` | Delete a note (`?expected_hash=` optional); 404 when there is none. |
| `GET` | `/memory/journal?limit=` | Changes, newest first. |
| `POST` | `/memory/journal/{entry}/undo` | Put the file back; `409 cannot_undo` with the reason. |
| `GET` | `/memory/proposals` | Proposed notes (reads quiet conversations first). |
| `POST` | `/memory/proposals/{id}/accept` | `{line?, target?}`: save it, or your edited line, into `MEMORY.md` or a topic. |
| `POST` | `/memory/proposals/{id}/reject` | Drop it for good. |

`recall_enabled` is a field of the agent. Run `alembic upgrade head`:
revision `e7a91c4d2b58` adds `agent.recall_enabled` (on for existing agents).

## Not covered

- No model-written summaries for compaction and no model-written proposals.
- Topic notes are not part of agent files.
- An agent's own edits with its file tools are not journaled.
- Recall ranks by shared words only (no stemming, no embeddings), so a
  question worded differently from the note can miss it.
- Proposals are found when the queue is looked at, not pushed.
