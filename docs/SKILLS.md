# Skills

A skill is a saved set of instructions for one agent, such as "how I want the weekly report written". You choose a skill when you send a message; the agent never uses one on its own.

## What a skill is

- One Markdown file per skill, `skills/<name>/SKILL.md`, in the agent's own folder (next to its notes).
- A short header (`name`, `description`, `enabled`, `source`) and then the instructions.
- Names use lowercase letters, digits and dashes, up to 48 characters. Instructions are at most 32 KB, a description at most 300 characters, and an agent can have 30 skills.
- Text only. Nothing in a skill is run, and files next to `SKILL.md` are never read. A link (symlink) in the skills folder is refused.
- Anything that looks like a secret is replaced when a skill is saved and again when it is read.

## Using a skill

Send a message with up to three skill names (`skills` on `POST /threads/{id}/turns`). The chosen skills' instructions are added before that one message, marked as guidance. They grant no permission and do not change the approval mode: every approval still works as it does without a skill. The next message carries no skill text unless you name one again. The transcript shows which skills a message used (`skill.used`, names only).

A skill that is unknown, turned off, repeated, or chosen in a conversation with no agent refuses the message with `422 skill_unavailable`. Nothing is started.

## API

All routes are local-access only, like the agents routes. Errors are `{"detail": {"error_code", "message"}}`.

| Route | What it does |
|---|---|
| `GET /agents/{id}/skills` | `name, description, enabled, source, bytes, updated_at` for each skill |
| `GET /agents/{id}/skills/{name}` | the skill with `body`, `redacted` (count) and `warnings` |
| `PUT /agents/{id}/skills/{name}` | create or replace: `{description, body, enabled, source}` |
| `DELETE /agents/{id}/skills/{name}` | remove it |
| `POST /agents/{id}/skills/import` | `{markdown}` becomes a preview (`name, description, body, warnings`). Nothing is saved; save the reviewed result with `PUT`, turned off until you have read it |

Warnings flag text that tells the agent to ignore its instructions or skip approvals. They are advice for the reviewer, not a block.

## Agent files

An agent export can include skills (`include_skills=true`; off by default). On import (`include_skills`, on by default) every skill arrives **turned off** with source `imported`, whatever the file says. Skills are bounded (30, 300 KB together) and redacted in both directions. Removing an agent removes its skills.

## What was not run

Covered by tests on SQLite with a scripted engine: the files, limits, redaction, links, preview, the prompt text, refusal, events and the agent-file round trip. Not run: PostgreSQL (skills use no database tables), a real model following a skill, Windows file permissions, and phone or desktop screens for the skills UI.
