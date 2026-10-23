# Engines

Open Duet runs a conversation's turns on an **engine**: the program or service
that writes the answer and, for some engines, runs tools. Every engine sits
behind one adapter contract, so the thread runtime, approvals, events and the
UI do not know which engine they are talking to.

| Engine id | Label | Kind | What it drives |
|---|---|---|---|
| `claude` | Claude | cli | your own `claude` CLI (stream-json both ways) |
| `codex` | Codex | cli | your own `codex` CLI over `codex app-server` (JSON-RPC on stdio) |
| `gemini` | Gemini CLI | acp | Gemini CLI with engine-scoped credentials and model selection |
| `cursor` | Cursor | acp | Cursor CLI, with a live model catalog |
| `opencode` | OpenCode (experimental, opt-in) | acp | OpenCode CLI; real permission override proof pending |
| `acp` | ACP agent | acp | any agent CLI that speaks the Agent Client Protocol, from a saved command |
| `openai_compat` | OpenAI-compatible | api | `POST {base_url}/chat/completions`, streamed (Ollama, LM Studio, vLLM, hosted APIs) |
| `openrouter` | OpenRouter | api | the OpenAI-compatible engine preset for `https://openrouter.ai/api/v1` |

## Choose and configure an engine

Open **Settings → Engines** in the web UI or desktop app. **Check again**
checks installation, login and endpoint readiness. An unavailable engine shows
its reason without blocking the others. Sign in to Claude or Codex in a terminal;
the app does not install engines or complete their login for you.

For ACP, save the command and optional display name. For an OpenAI-compatible
endpoint, save its base URL (including `/v1` when required), optional default
model and optional API key. Configuration is encrypted at rest. A blank key
keeps the saved key for the same URL; changing the URL drops it unless you
enter a key again. **Forget settings** deletes the configuration and credential.
For OpenRouter, save an API key. The address is fixed to OpenRouter over HTTPS; configure a proxy with the generic OpenAI-compatible engine.


Settings forms come from each adapter's `config_fields` and `setup` descriptors.
Ready engines appear before engines that need setup, including in engine pickers.
Connection commands are displayed for the person to run; the app never installs
a CLI. Under the generic endpoint card, **Fill address from template** supplies
an address for Ollama, LM Studio, Groq, Cerebras, Mistral or OpenAI. It supplies
no key or model. These services have not been checked with real accounts.

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
  | `images` | accepts images attached to a message ([EVERYDAY.md](EVERYDAY.md)); an image for any other engine is refused with `images_unsupported` |
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

|  | Claude | Codex | ACP agent | OpenAI-compatible (and OpenRouter) |
|---|---|---|---|---|
| tools | yes | yes | yes | no |
| approvals | yes | yes | yes | no |
| mcp | yes (the computer's server) | no | no | no |
| images | yes (stream-json content blocks) | no | no | no |
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

## OpenRouter

A preset of the OpenAI-compatible engine (`OpenRouterAdapter` in
`backend/app/services/openai_chat_adapter.py`), registered as its own engine
`openrouter` with its own saved configuration (`engine:openrouter`), so it can
be set up beside a local endpoint. Everything above applies, with these
differences:

- The address is fixed to `https://openrouter.ai/api/v1`. Configuration requests
  containing `base_url` are rejected. Use the OpenAI-compatible engine for a
  proxy. Older saved proxy configurations must be saved there again; they are
  refused without sending the key to that proxy.
- A key is required. Readiness authenticates through `GET /key`, independently
  of the public model catalog. A rejected key reports signed out. A failed or
  malformed key check reports unavailable without displaying its body.
- Model ids use `vendor/model` with an optional variant such as `:free`.
  Refresh lists up to 600 models from `GET /models`, labels free models, and
  puts free models supporting tools first (this engine remains chat only).
  A free-tier key with no chosen model uses the first available free model.
  Other keys require a chosen model; the app does not select a paid model.
- Requests to openrouter.ai also carry OpenRouter's optional app-attribution
  headers, `HTTP-Referer: https://github.com/Anil-matcha/open-duet` and
  `X-Title: Open Duet`. They tell OpenRouter that this account's traffic comes
  from Open Duet (OpenRouter uses them for its public app rankings), and they
  are sent only to openrouter.ai.
- Chat only, like any OpenAI-compatible endpoint: no tools, images or
  computers. HTTP 402 is a credit limit. A `free-models-per-day` refusal is a
  usage limit; a general 429 is a rate limit. No reset time is guessed.

Tested against a mock endpoint only; no turn has been run against the real
OpenRouter service yet.

## Limits and the fallback engine

When an engine refuses a turn for a usage limit, a rate limit or a lack of
credits, the runtime recognises it for every engine
(`backend/app/services/engine_errors.py`): Claude's usage-limit words and its
refusing `rate_limit_event` (with the reset time it reports), Codex's rate and
usage limits, and an OpenAI-compatible endpoint's 429 or
`insufficient_quota`. Only failures the engine itself reported are read, never
a timeout or a crash. The turn fails with `error_code` `engine_limited` and a
plain message that says when the limit resets when the engine said so.
Claude's API-error frames are no longer stored as if they were the answer.

The engine is then marked limited in this backend's memory: `GET
/api/v1/engines` returns `limit` (`kind` usage_limit, rate_limit or credits,
`resets_at`, `since`, `message`) until the reset time, a minute for a rate
limit that named none, or until a turn on that engine succeeds. The engine
picker shows a chip ("Usage limit · resets in 2h 5m") and the message.

An agent may name a **fallback engine** and model (`fallback_provider`,
`fallback_model` on `POST/PATCH /api/v1/agents`, or **Fallback engine when
limited** in the Agents panel; it must be another engine than the agent's own,
422 `invalid_fallback` otherwise). A turn moves to it only when all of these
hold:

- the engine refused the turn **before accepting it**: no output, no tool
  call, no approval question and no typing had come from it. A limit hit after
  the engine started is never moved (it may have run tools); the message says
  so;
- the fallback covers what the turn needs: tools and approvals when the turn
  has tools (an engine with tools but no approval gate would run them
  unasked), images when the message has any, an agent computer when the agent
  has one, and, for a conversation that already has messages, the earlier
  messages: only an engine that replays the stored transcript (OpenAI-compatible)
  can take a continuing conversation, since no engine can resume another
  engine's session;
- the fallback is ready, accepts the model, and is not limited itself.

Then the transcript gets an `engine.switched` event (from, to, model, reason,
reset time, and the message "Switched this turn to X because Y refused it
before starting: ...") shown as its own notice, and the same turn runs on the
fallback. The conversation stays on its own engine: the fallback's session and
state are not kept, so the next message tries the conversation's engine again.
For an engine with a native session, that next message includes the missing
fallback exchanges as explicitly framed historical records, including partial
output and tool records from failed fallback turns. This handoff is derived
from the stored transcript and survives a backend restart. It grants no new
permission and does not rerun a turn. A successful turn on the original engine
consumes the handoff; an explicit session reset discards it. Records over
24,000 characters use a bounded excerpt summary; the full transcript stays
stored. A replaying fallback uses its own context budget and token estimate.
When a switching condition fails, the turn fails with `engine_limited`, the reason the fallback could
not take it, and the next step. There is never a silent switch, and a limit is
never retried on the same engine.

Limits are per backend process (a restart forgets them until the next refusal).

## Not built yet

- Presets for other services (Gemini, OpenCode, Cursor); OpenRouter is the
  only preset so far.
- Several instances of one engine (two ACP agents, two endpoints), per-engine
  icons, installing or signing in to an engine from the app.
- Moving a continuing conversation to a session engine (Claude, Codex) when its
  own engine is limited; a fallback chain of more than one engine; usage-limit
  meters read ahead of a refusal.
- Model prices, context windows and usage-limit meters.
- Images (attached text files work everywhere), MCP servers or computers for engines other than Claude.
- A long-lived engine process across turns (each turn starts one).


## ACP preset engines

Gemini CLI (`gemini`) and Cursor (`cursor`) use the same ACP transport, approval gate, workspace, transcript replay, resume and Stop handling as the custom ACP engine. Install and sign-in advice appears in Settings → Engines. Program overrides contain the program only (for example `npx -y @google/gemini-cli`); the preset adds the ACP arguments itself. Optional saved keys are encrypted, write-only, and injected only into that engine's process. The engine's own shell commands can read those keys.

- **Gemini CLI:** install `npm install -g @google/gemini-cli`, run `gemini` to sign in or save an API key. Starts with `--acp --approval-mode default`, plus `-m` for a selected model. Existing refreshable Google login files and API keys are detected locally. Vertex configuration must be opted in through `OPEN_DOTS_ENGINE_ENV`; consumer login availability depends on Google's current policy. Static model suggestions accept custom IDs.
- **Cursor:** install from [Cursor's CLI documentation](https://cursor.com/docs/cli), then `cursor-agent login` or save a key. Starts with `cursor-agent acp`, without automatic approval flags. Credentials suppress ACP browser authentication. Status and model discovery use bounded CLI commands. A selected model must be offered by the session and accepted by `session/set_model`; refusal ends the turn before the prompt. Cursor's `cursor/ask_question` extension is answered as unsupported, so it cannot hang waiting for a question card.
- **OpenCode (experimental):** install `npm install -g opencode-ai` and run `opencode auth login`. Discovery uses `opencode models` without creating a session. A model must be offered by the session's configuration and confirmed by `session/set_config_option`. Additional saved provider-key maps are not implemented. OpenCode strips ambient foreign API keys and configuration overrides, pins project configuration off, and supplies `OPENCODE_PERMISSION` asking for every tool (edit/bash denied in plan mode, all tools denied when tools are off). This override has **not** been proven against hostile real CLI configuration; OpenCode is disabled by default.

`OPEN_DOTS_PRESET_ENGINES` selects enabled presets by comma-separated ID. Unset enables `gemini,cursor,openrouter`. An empty value disables all presets; `gemini,cursor,openrouter,opencode` also enables experimental OpenCode. Custom ACP and OpenAI-compatible engines are always registered. Preset CLIs are not supported on Windows yet.

For ask and accept-edits modes, Gemini always uses `--approval-mode default`, Cursor receives no automatic approval flag, and OpenCode receives ask rules. Every permission request uses the existing approval gate; accept-edits only auto-allows edits whose listed locations all lie inside the workspace. Plan mode and tools-off refuse permission requests. A vendor's own configuration can allow tools without sending a request, especially Cursor allow rules; these presets do not provide a filesystem sandbox. Review CLI configuration before enabling tools.

`engine_signed_out` means connect the engine in Settings or run the sign-in command shown there. This is a credential failure, not a usage limit, and does not trigger limit fallback. `engine_model_unavailable` means the selected model could not be confirmed; no prompt was sent. Session resume is used only when advertised; otherwise stored history is included in the new prompt.

### Preset verification record (2026-10-23)

| Engine | Checked | Not checked |
|---|---|---|
| Gemini CLI | Synthetic ACP authentication, argv, model reporting and local credential fixtures; installed 0.34.0 distribution source inspected for auth methods and flags | Real signed-in turn, real permission requests, quota messages and resume |
| Cursor | Synthetic status/models discovery, credential isolation, model selection/refusal, generic ACP lifecycle regression tests | Real CLI/account, exact live model IDs, question behavior, permission bypass from user configuration |
| OpenCode | Synthetic model discovery without sessions, model confirmation, pinned environment controls | Real CLI/account, permission overrides against hostile configuration, real resume; remains opt-in |
| OpenRouter | Mock HTTP authentication, catalog, streaming and fallback tests | Real account and credits/limit behavior |

No real-account compatibility, Windows compatibility, optional Docker package build or approval bypass resistance is claimed by these checks. Real CLI runs must record version, redacted responses and permission behavior before changing this table or default-enabling OpenCode.
