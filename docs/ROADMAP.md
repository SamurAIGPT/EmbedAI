# Roadmap

This page tracks gaps in the current product. Planned work is not a promise or
a date. See [FEATURES.md](FEATURES.md) for the shipped surface and its guides.

## Snapshot

Reviewed 2026-10-24 against source `7eb56e8`; the latest desktop tag is
`v0.3.0` at `c1148bc`. The tag includes the assisted update checker. Source is
two commits beyond the tag, so changes after `v0.3.0` should be treated as
source-only until a later installer is built and checked. The two commits add
a browser-disabled computer message and a Windows release fallback workflow.
The `v0.3.0` prerelease currently has a Windows x64 installer; its macOS build
failed, and the newest macOS arm64 DMG is `v0.2.0`. The Windows fallback
workflow successfully published the `v0.3.0` installer.

Current source already has named agents, an offline role gallery, explicitly
invoked agent skills, memory and recall, search and attachments, routines,
delegation and rooms, approval-gated Docker computers with desktop takeover,
structured Chromium tools, a personal self-hosted mode, and desktop setup and
updates. The product gaps below build on those features.

## Highest priority

### P0 — Close the source-to-installer verification gap

Publish a per-engine and per-platform capability matrix and check the tagged
installer against it. Include CLI/login prerequisites, model and attachment
support, approval behavior, team and computer support, and whether each item is
source-tested, manually checked, or present in a release. Verify setup,
updates, teams/rooms and computer flows on the actual packaged builds. Restore a
current macOS arm64 release after fixing the failed v0.3.0 build. Keep the
unsigned/experimental label until the release path is dependable.

### P1 — Make memory writes truthful and durable on every supported engine

A current-source Codex run with no helper could recall a fact from earlier
conversation history but could not write it to private agent memory; the reply
still implied that it had saved the fact. Make explicit “remember this” requests
write a private, journaled note when supported, or clearly say when persistence
is unavailable. Show note provenance in a fresh conversation, support
correction and undo, and never move personal facts into team memory without
explicit choice. See [memory](MEMORY.md).

### P1 — Add guided agent setup by chatting

The existing lead can propose and apply team configuration, but there is no
discoverable user-facing “create an agent by chatting” flow. Add requirements
gathering, exact engine/model discovery, a reviewable setup result, and safe
undo. Reuse the current setup service and preserve approval settings and
request receipts.

### P1 — Connect calendar, email and a useful daily view

Add read-only Google Calendar context first, including meeting preparation and
event freshness. Add Gmail read-only triage, then a separate draft-only action
that can create a reply only in a thread already reviewed. Sending mail should
remain a separately granted and explicitly approved action. Extend the
existing **Needs you** card into a quiet, source-linked Today view for meetings,
important mail, routine outcomes, approvals and connection problems. Do not
report an unavailable provider as an empty inbox or calendar.

### P1 — Let people manage routines from chat

Routines already have a UI, schedule preview, run history, webhooks, approvals
and retry controls. Add bounded agent tools to inspect, propose, pause, resume,
run and delete them. A new routine must stay disabled until its target, prompt,
time zone, next run and approval floor are reviewed. Add webhook delivery
receipts and, later, connected-app event triggers with explicit grants,
provenance and bounded retries.

### P1 — Finish room management and routing

Rooms already support fixed-member, everyone and mention-based replies, shared
instructions, separate member conversations, approval display, hold/resume and
exports. Add safe roster editing while preserving history and queued work.
Then consider opt-in model-assisted selection for untagged messages, with
explicit-mention bypass, confidence thresholds, deterministic fallback and
metadata-only routing logs. Keep tool approval rules unchanged.

### P1 — Expand connected-app access after core trust work

There is no first-party OAuth catalog for common apps such as Gmail, Calendar,
Slack or Notion. Build on the first-party email/calendar work with scoped app
connections, per-agent grants, custom MCP authentication and app-event
triggers. Add idempotency, revocation, clear data provenance and bounded
responses before expanding the catalog.

### P1 — Verify memory, teams and computers across engines

Keep a live compatibility matrix for Claude, Codex, ACP and API engines. Team
tools currently require Claude; structured browser tools are Claude-only;
computer access varies by engine. Test approvals, failures, hostile tool
results and longer-running work for each supported path before broadening the
claims.

## Next product additions

### P2 — Learn and share reviewed skills

Open Duet already supports per-agent Markdown skills, import preview, secret
redaction, explicit invocation and disabled-by-default imports. Add a flow to
draft a skill from a successful run, review/edit it, and verify it on a second
fixture before saving. Later allow safe team sharing with text-only export,
secret scanning and imported skills disabled until review.

### P2 — Extend templates into safe import and team sharing

The bundled role gallery and single-agent import/export are shipped. Add local
team-package import, GitHub and external-bot import, bounded project-folder
suggestions, reusable presets and whole-team export. Preview fields, omit
secrets, credentials, permissions, history and local paths, and import
autonomous features disabled. A managed organization catalog is a separate
future decision.

### P2 — Improve file and computer reach

Add bounded PDF and Office document extraction with visible failures. Decide
whether remote VPS computers are needed; if so, design workspace placement,
SSH trust, viewer transport, lifecycle, egress and cost before building them.
For local Docker computers, evaluate a real egress policy and clearer handling
when another conversation owns the screen. Keep isolated Docker as the default.

### P2 — Add voice and richer media

Stage spoken replies before live calls. Add optional text-to-speech settings,
macOS dictation that inserts editable text without sending, and a half-duplex
call view with captions and explicit mute/hang-up controls. Keep screenshots
from intentional actions as bounded transcript attachments; do not retain
polled preview frames.

## Deliberate scope

- Keep personal self-hosting single-owner. Multi-tenant hosting, organization
  accounts and billing are not part of the current desktop/server path.
- Build the core workspace and trust controls before a very broad app catalog.
- Treat mascots and other cosmetic parity as lower priority than integrations,
  memory correctness and release confidence.
- Do not rebuild the existing search, role gallery, routines, team delegation,
  rooms, desktop preview, takeover or structured browser tools.
