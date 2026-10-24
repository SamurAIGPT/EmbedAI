# Managing agents through chat

Open Agents and choose **Create by chatting**. It opens your existing lead's conversation (or a new conversation with that lead) with an editable, unsent starter. Navigation does not send a request or create an agent. Without a lead, choose an existing ready agent or create one in the form. The manual form and templates remain available.

In both the new-agent form and an existing agent's settings, **Team lead (Chief of Staff)** grants coordination of the selected team members. Selecting a new lead replaces the previous lead; removing the role preserves membership and existing work. The sidebar also offers agent settings and Make/Remove lead. Role and membership changes are person-controlled. New specialists retain Ask-first permissions; the lead cannot change approval settings or grant app access through setup.

Claude and Codex can mount the team tools. ACP and OpenAI-compatible adapters do not offer these management tools. A capable engine must also be installed and signed in. Turning local shell tools off does not remove team configuration tools. Codex uses a read-only sandbox with MCP approval requests enabled in that case; the app still checks every call.

## Chat controls

| Request | Tool and behavior |
|---|---|
| Discover available controls and missing dependencies | `list_management_capabilities` reports supported operations and explicit limitations. |
| Create specialists; correct an existing agent's brief | `list_team_setup`, `create_agent`, `propose_team_setup`. Quick and combined creation use the same validated, source-bound receipt service. Edits require the current revision. |
| Change name, title, description, standing instructions or working folder | `propose_profile`, for self or a teammate reachable by the current lead. Requires approval and revision; folder must already exist. |
| Change default engine/model | `propose_model`, using discovered IDs/revision, with approval. The lead must retain team tools; agents with computers need a compatible engine. Existing conversations retain their engine/model and pinned workspace. |
| Assign work, consult, request corrections, inspect prior jobs | `delegate`, `ask_agent`, `job_status`. A delegation receipt means assigned; results arrive through the existing resume flow. |
| Retry failed work or flag a person-only gate | `retry_thread`; optional `raise_escalation` when enabled. Existing budgets and retry limits remain in force. |
| Create rooms, list rooms, post to a room | `create_room`, `list_rooms`, `say_in_room`. Member management and room settings use the room UI. |
| Find and close finished delegated conversations | `list_threads`, `close_thread`. Person-started conversations cannot be closed through this housekeeping path. |
| Preview a schedule | `preview_schedule` validates timezone and schedule, returning upcoming dates without writing anything. |
| Create recurring work | `create_routine` reuses the scheduler, requires approval and saves paused with Ask-first permissions. Profile creation does not activate a routine. |
| Change or execute a routine | `list_routines`, `manage_routine`: revision-bound update, pause, resume, run now or delete, with approval. Resume/run now require explicit activation intent. Queued is not completed. Delete removes run history and has no Undo. |
| Read or author reusable instructions | `skills_list` lists names/revisions; optional `name` reads a skill. `skill_manage` creates or updates the requested skill after approval, saving disabled for review in Agent settings. Text cannot grant permissions. |
| Delete a teammate | Existing `propose_bot_deletion` remains a separate owner-approved operation. The current lead and busy agents are protected. |
| Search previous conversations | Existing read-only memory/session search tools. |

The lead should ask only material unanswered questions about purpose, output, constraints and required capabilities. It discovers IDs before writing and reports applied/pending/failed states truthfully. A correction changes the same agent instead of creating a duplicate. The absence of a connector, computer or routine must be stated separately from successful profile creation. Never request credentials in chat.

## Persistence and recovery

Creation/update and management receipts bind request ID, caller, source conversation and the canonical request digest. Identical retries replay the saved result; changing the request or source while reusing the ID conflicts. Plans reject permission fields, lock the current team and targets, revalidate current turn ownership/origin at commit, and roll back database changes together on failure. Secrets are redacted in saved setup plans and field details.

Applied profile/setup cards show changed fields, Open/Edit actions and server-checked Undo availability. Undo refuses changed, used or busy agents, including a created specialist subsequently promoted to lead. Merely navigating to an unsent new chat creates no conversation; sending its first message creates a conversation and can make creation Undo unavailable. Duplicate receipt events fold into one card.

Routine creation and its receipt commit together. Skill files use the existing atomic filesystem writer; filesystem changes and database receipts are not one atomic transaction. If receipt persistence fails after a skill write, list skills and compare its revision before attempting another change. There is no combined Undo across profiles, skills, routines, connections and computers.

Plan mode, unattended origins and chat-only paired-device sessions cannot use configuration tools to bypass their restrictions. Restricted origin is carried into delegated work and room messages. Membership and current lead authority are checked on the server.

## Remaining gaps

This is source implementation, not a claim of full parity or installer availability. These controls still require separate work:

- Connected-account setup, secure credential/sign-in requests, app-event subscriptions, and limit-reset triggers.
- Chat-based computer allocation/selection, shared-computer administration and arbitrary MCP-server installation. Existing approved computer execution remains separate.
- Room reconfiguration through chat, general start-thread/attachment management, voice notes and voice preferences.
- Shared team-memory editing and a reusable team/role library.
- Dedicated applied-result cards and Undo for routine/skill changes; use the existing Routines and Skills editors to inspect saved values.
- Real-Claude verification while signed out, PostgreSQL concurrency verification and packaged desktop release verification.

Deterministic verification covers setup receipts, origin/approval restrictions, current-turn validation, profile corrections, routines, Codex MCP transport and compatible computer turns. `scripts/smoke-agent-setup.py` launches a fresh server/database and Chrome to check role creation/settings, coordination selection, demotion/promotion, the unsent starter and phone layout. Build `frontend/out` first; the test never uses the person's live agents.

Source verification on 2026-10-24: 347 backend checks passed across team, agent/routine routes, Codex/computer integration and authentication; focused follow-up tests cover skill authoring, privileged-field refusal, stale routines, redaction and promoted-agent Undo. All 219 frontend tests and TypeScript checks passed. The isolated Chrome smoke test passed. A real Codex CLI 0.164.0 completed clarification → create → correction of the same persisted agent → Undo, with local host tools disabled. Real-Claude testing was unavailable because the CLI was signed out. The static production build passed using webpack; Turbopack could not open its worker port in this environment. No packaged installer or PostgreSQL multi-backend run was verified.
