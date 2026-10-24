# Open Duet

**Your own team of AI coworkers, running on your computer or a server you control.**

Open Duet is an open-source workspace for creating named AI agents, giving each
one a job and a place to work, and chatting with them over time. Let a lead
agent hand work to specialists, keep useful context between conversations, or
set a routine to run later. Choose how much freedom an agent has, and review
the actions it takes.

You choose where the app runs and which engine gets your prompts. Open Duet
does not include a model or a model subscription.

[Watch the demo](https://youtu.be/MzgrNQXgVX4) · [Quick start](#quick-start) · [Run it on your server](docs/SELF-HOSTING.md) · [Guides](#guides)

<p align="center"><a href="https://youtu.be/MzgrNQXgVX4" title="Watch the Open Duet demo on YouTube"><img src="https://i.ytimg.com/vi/MzgrNQXgVX4/maxresdefault.jpg" alt="Open Duet demo video thumbnail — click to watch on YouTube" width="720"></a></p>

> **Status:** Active development. The desktop app, self-hosted server, agent computers, and newer team features are still evolving. See [known limitations](#known-limitations) before relying on them for important work.

## What you can do

- **Create a roster of agents.** Give each one a name, instructions, model,
  workspace, memory, and approval settings. Use different agents for research,
  planning, writing, support, or development.
- **Work as a team.** Set a lead and specialists, let the lead delegate tasks,
  and follow the results in chat. You can also bring agents together in shared
  rooms. Team leads currently need to use Claude. See
  [teams and delegated work](docs/TEAMS.md).
- **Keep useful context.** Agents can keep notes, recall relevant notes and
  earlier conversations, and search their own history. You can review and edit
  what they remember. See [memory](docs/MEMORY.md).
- **Set work to run later.** Create routines that run on a schedule, when
  another service triggers them, or when you press Run now. Follow each run
  and respond if it needs your approval. Routines do not need Boat. See
  [routines](docs/ROUTINES.md).
- **Stay in control.** Choose Ask first, Accept edits, or Plan only. Review
  tool activity and approval decisions in the app. Agents can also be given
  an isolated Docker computer; computer and browser access are experimental
  and off until you enable them.
- **Keep track of work.** Search conversations, attach text files and images,
  browse or download workspace files, and review reported token use and cost.
- **Plan tasks and deadlines.** Keep work on a board, assign an agent or team,
  and review deadlines alongside routine occurrences in a week or month
  calendar. See [tasks and calendar](docs/TASKS.md).

## Example: prepare a weekly project briefing

Create a lead agent and a couple of specialists, such as a researcher and an
editor. Give them the same project workspace, and set the lead's engine to
Claude. Then ask the lead to prepare a briefing from the notes and reports in
that folder.

The lead can split the request into smaller tasks and hand them to the
specialists. Their work runs in its own conversations; when the results are
ready, they return to the lead, which can put the briefing together. You can
open the chats to follow along, and approve actions when the approval settings
require it. Once the task is useful, make it a weekly routine and review each
run from the app.

## What stays on your machine or server

Open Duet stores conversations, agent notes, routines, and settings in the
local data folder or server database for the instance you run. Workspace files
stay in the folders you assign to agents. API keys saved through Open Duet are
encrypted there; command-line sign-ins remain with the engine installed on
that machine.

When an agent answers, the selected engine receives the prompt and the context
needed for that turn. That can include text you attach, relevant notes, and
workspace content the engine reads. If the engine is a hosted service, that
information is handled under that provider's terms. Open Duet does not include
a hosted model or a model subscription.

## Choose an engine

Connect an engine you already use: Claude Code or Codex CLI, Gemini CLI or
Cursor, another command-line agent using the Agent Client Protocol (ACP),
OpenRouter, or an OpenAI-compatible API such as Ollama or LM Studio. Available
tools vary by engine; for example, image input and agent computers are not
supported by every engine.

See [engine setup and capabilities](docs/ENGINES.md). You bring the provider
account, CLI sign-in, or API key; model usage may cost money.

## Ways to run Open Duet

| | Desktop on your computer | Server you control | Development checkout |
|---|---|---|---|
| Good for | Trying a personal, local workspace | Keeping agents and routines available while your computer is off | Contributing or changing Open Duet |
| Where data lives | The desktop app's local data folder | Your server | Your development machine or configured database |
| You'll need | Node.js 22+, uv, and an engine on your computer | A Linux server, a domain with HTTPS, Docker or Node.js and uv, and an engine on the server | Node.js, uv, and Docker if you use PostgreSQL |
| Start here | [Quick start](#quick-start) | [Self-hosting guide](docs/SELF-HOSTING.md) | [Development setup](docs/SETUP.md) |
| Status | Experimental | Experimental; single owner | |

## Quick start

The desktop app runs from a source checkout. It needs Node.js 22 or newer,
[uv](https://docs.astral.sh/uv/), and at least one engine available on the
same computer. For a CLI engine, install it and sign in before opening Open
Duet. You can also configure an API-based engine in **Settings → Engines**.
You do not need Docker for ordinary chat; Docker is only needed for the
optional isolated agent computers.

```bash
git clone https://github.com/Anil-matcha/open-duet.git
cd open-duet/desktop
npm run app
```

On first launch, the setup screen checks your engines, helps you choose a
workspace and approval mode, and can create a starter agent. Then:

1. Open **Settings → Engines** and confirm that an engine is ready.
2. Create an agent with a name, a clear job, and the workspace it should use.
3. Start a chat. Choose **Ask first** if you want the agent to pause for
   approval before actions that need it.
4. Add teammates or a routine when you want to split up work or repeat it later.

As of 2026-10-24, the [Releases page](https://github.com/Anil-matcha/open-duet/releases) has a Windows x64 installer for `v0.3.0`; the latest macOS arm64 DMG is from `v0.2.0`. Both are experimental and unsigned. The `v0.3.0` Windows app includes an assisted update flow that offers a SHA-256-verified installer for you to open; it does not install updates automatically. The release-to-release update path has not been verified end to end.

## Scheduling tasks

**Routines** run prompts on local agents on a schedule, by webhook, or on demand. They run in the backend, so the backend must stay running. A routine never approves an action by itself. See [the routines guide](docs/ROUTINES.md).

The original Telegram bot and [Boat](https://docs.boat.dev/api-keys) sandbox
task flow are also available as an optional, separate path. Those scheduled
tasks use the API process too; see [backend details](backend/README.md#scheduling)
and [environment settings](backend/.env.example). Neither Telegram nor Boat
is needed for the agent workspace.

## Known limitations

- Open Duet is early and changing. The server setup is experimental and
  intended for one owner with paired devices; it is not a multi-user
  organization workspace.
- Team delegation is new, and the lead agent currently needs to use Claude.
- Docker computers and their browser tools are experimental and opt-in. The
  macOS native desktop preview is also experimental and available only in the
  desktop app.
- Desktop installers are unsigned and have limited platform verification. See
  [desktop setup details](docs/SETUP.md#desktop-app-from-a-source-checkout-experimental).
- Open Duet does not provide model access. You are responsible for your model
  provider's account, terms, and usage costs.
- A built-in catalog of integrations such as Gmail, Slack, and Notion is not
  available yet. The optional Telegram and Boat flow remains separate from
  the agent workspace.

## Guides

- [First-run setup and approvals](docs/SETUP.md)
- [Engines and models](docs/ENGINES.md)
- [Teams and delegated work](docs/TEAMS.md)
- [Agent settings and chat management](docs/CHAT_MANAGEMENT.md)
- [Memory and context](docs/MEMORY.md)
- [Routines](docs/ROUTINES.md)
- [Tasks and calendar](docs/TASKS.md)
- [Agent computers](docs/COMPUTERS.md) and [browser tools](docs/BROWSER.md)
- [Self-hosting](docs/SELF-HOSTING.md)
- [Contributing](docs/CONTRIBUTING.md)

## Contributing

Issues and pull requests are welcome. Start with the
[contributing guide](docs/CONTRIBUTING.md).

## License

Open Duet is licensed under the [Apache License 2.0](LICENSE).
