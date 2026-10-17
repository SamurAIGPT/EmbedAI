# Engines

Open Dots runs a conversation's turns on an **engine**: the program or service
that writes the answer and, for some engines, runs tools. Every engine sits
behind one adapter contract, so the thread runtime, approvals, events and the
UI do not know which engine they are talking to.

| Engine id | Label | Kind | What it drives |
|---|---|---|---|
| `claude` | Claude | cli | your own `claude` CLI (stream-json both ways) |
| `codex` | Codex | cli | your own `codex` CLI over `codex app-server` (JSON-RPC on stdio) |
| `acp` | ACP agent | acp | any agent CLI that speaks the Agent Client Protocol, from a saved command |
| `openai_compat` | OpenAI-compatible | api | `POST {base_url}/chat/completions`, streamed (Ollama, LM Studio, vLLM, hosted APIs) |

## Choose and configure an engine

Open **Settings → Engines** in the web UI or desktop app. **Refresh status**
checks installation, login and endpoint readiness. An unavailable engine shows
its reason without blocking the others. Sign in to Claude or Codex in a terminal;
the app does not install engines or complete their login for you.

For ACP, save the command and optional display name. For an OpenAI-compatible
endpoint, save its base URL (including `/v1` when required), optional default
model and optional API key. Configuration is encrypted at rest. A blank key
keeps the saved key for the same URL; changing the URL drops it unless you
enter a key again. **Forget settings** deletes the configuration and credential.

Choose an engine and model in the Agents panel or before sending a new chat.
The model field offers catalog suggestions and accepts a custom ID only when
the engine supports it. The generic ACP engine uses its CLI's configured model;
its picker exposes only the agent default. A computer attached to an agent
requires a computer-capable engine (currently Claude).

An existing conversation keeps its engine. Its header lets you change the
model while idle. An explicit thread model overrides the agent; an unset one
follows the agent's model only while they share a provider, otherwise it uses
the engine default. Changing an agent's engine affects new conversations.

## The contract

`backend/app/services/provider_adapter.py`. An adapter has:

- `key`, `label`, `kind` (`cli`, `acp`, `api`).
- `capabilities`: six flags the runtime and the API read instead of engine names.

  | Flag | Meaning |
  |---|---|
  | `tools` | can run tools (commands, file edits) in the workspace |
  | `approvals` | its tool calls go through the approval gate |
  | `mcp` | can mount an MCP server this app gives it |
  | `images` | accepts images in a prompt (nothing sends images yet) |
  | `resume` | a conversation continues with the engine's earlier context |
  | `computers` | its turns can run their tools in an agent computer |

- `models` (static catalog), `custom_models` (any id accepted), and `probe()`,
  which may also return a live catalog.
- `new_session_id()`, `status()`, `run_turn(ctx, sink, gate)`: the turn API the
  runtime already used. A turn reports normalized events (`assistant.message`,
  `tool.started`, `tool.completed`, typing deltas) through the sink, asks a
  person through the gate, and returns a `TurnOutcome`, which may now carry
  `provider_state` (the engine's own conversation id, never a secret).
- For an engine with a saved configuration: `needs_config`,
  `validate_config(payload, current)` and `public_config(config)` (never a
  secret).

Every one of these except the turn API is optional; the registry fills in safe
defaults (no capabilities, any model, a probe derived from `status()`).

|  | Claude | Codex | ACP agent | OpenAI-compatible |
|---|---|---|---|---|
| tools | yes | yes | yes | no |
| approvals | yes | yes | yes | no |
| mcp | yes (the computer's server) | no | no | no |
| images | no | no | no | no |
| resume | `--resume` | `thread/resume` | `session/load`, or the transcript as text | stored history each turn |
| computers | yes | no | no | no |

## The registry

`backend/app/services/engine_registry.py`, `GET /api/v1/engines`.

- Built from the runtime's adapters. An adapter that cannot be constructed is
  kept as an unavailable engine with the reason; the rest still load.
- Nothing is probed at startup. A probe runs when someone asks for the
  engines, is bounded (12 s overall, 5 to 8 s per CLI call) and kills the
  process group of a CLI that does not answer. A probe that raises or hangs
  reports `available: false` with a reason; the request never fails.
- Results are kept for 15 s; `?refresh=true` checks again.
- Each entry: `id`, `label`, `kind`, `installed`, `version`, `signed_in`,
  `available`, `reason`, `models` (with `default`), `default_model`,
  `custom_models`, `capabilities`, `configurable`, `config` (no secrets).
- Probes read one fact from CLI output (`codex login status` prints the
  account; only "Logged in" / "Not logged in" is read) and never log it.

## Validation

- Agents (`POST`/`PATCH /api/v1/agents`) and threads (`POST /api/v1/threads`,
  `PATCH` with `model`) must name a registered engine (`unknown_provider`) and
  a model it accepts (`unknown_model`). A model id is one token (letters,
  digits, `. _ : / @ + - [ ]`, not starting with `-`); engines with
  `custom_models: false` (the ACP agent) take only their catalog.
- An agent can change engine. Its conversations keep the engine they started
  on; an agent's model applies only to conversations on the agent's engine.
- Computers stay Claude-only: `POST /agents/{id}/computer` answers 409
  `engine_without_computers` for an engine without the flag, moving an agent
  with a computer to such an engine is refused the same way, and a turn that
  still meets this combination fails with that code.

## Codex

One `codex app-server` per turn: `initialize`, `thread/start` or
`thread/resume` (Codex's thread id is kept in `provider_state`), `turn/start`,
then notifications until `turn/completed`. Approval modes map onto Codex's own
policy and sandbox, sent on every thread and turn call and checked against what
Codex reports back (a mismatch fails the turn before it starts):

| Mode | approvalPolicy | sandbox | What this app adds |
|---|---|---|---|
| ask | `untrusted` | `workspace-write` | every command/file-change request becomes a card |
| accept_edits | `untrusted` | `workspace-write` | a file change wholly inside the workspace is accepted without a card |
| plan | `never` | `read-only` | any request that still arrives is declined |
| tools off | `never` | `read-only` | as plan |

Commands are put to the gate as `Bash` with the exact command Codex will run,
so saved "always allow" rules and the safety floor apply. MCP tool approvals
for servers in your own Codex config go through the gate; other requests
(`item/tool/call`, user-input questions, extra permissions, other
elicitations) are refused. Stop sends `turn/interrupt`, then the process group
is killed. A missing Codex thread on resume marks the session `broken`.

Known differences from Claude: Codex loads your own `~/.codex/config.toml`
(including its MCP servers) and its read-only sandbox lets it read files
anywhere on disk without asking; Claude's ask mode asks about reads outside
the workspace.

## ACP agent

One process per turn from the saved command (split like a shell, never run
through one): `initialize` (file system and terminal advertised as off),
`session/new` with no MCP servers, or `session/load` when the agent can load
sessions, then `session/prompt`. An agent that cannot load sessions gets the
conversation so far as text. `session/request_permission` goes through the
gate; only an `allow_once` option approves (never `allow_always`), Deny picks
`reject_once`, and plan or tools-off refuse without a card. Stop sends
`session/cancel`, then kills. Model choice stays inside the agent.

## OpenAI-compatible endpoint

Chat only. `base_url`, optional default `model` and optional key are saved
encrypted (one `user_credential` row, `engine:openai_compat`). The key is sent
only as `Authorization: Bearer` to that address, is kept for the same address
only, and is never returned, logged or stored in events or the decision log;
endpoint errors have it removed. Each turn sends the stored history (newest
kept, 120,000 characters) with the agent's instructions as the system message.

## Not built yet

- Several instances of one engine (two ACP agents, two endpoints), per-engine
  icons, installing or signing in to an engine from the app.
- Quota- or limit-aware fallback to another engine.
- Model prices, context windows and usage-limit meters.
- Images, MCP servers or computers for engines other than Claude.
- A long-lived engine process across turns (each turn starts one).
