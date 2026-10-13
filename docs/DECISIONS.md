# Architecture Decisions

Format: decision, evidence, rejected alternative, consequence, date.

## D-001 — Separate public `docs/` from local `.internal/`

- **Decision:** `docs/` is tracked and holds contributor/user documentation; `.internal/` is Git-ignored and holds agent handoff notes and raw investigations.
- **Evidence:** `.gitignore` previously ignored `docs/`; it now ignores `.internal/`.
- **Rejected:** keeping all planning in one ignored `docs/` (nothing shareable), or tracking handoff logs (risk of leaking machine-specific data).
- **Consequence:** A fresh clone has no `.internal/`; status must be derived from code, tests, commits and public docs.
- **Date:** 2026-10-13

## D-002 — Native launcher for host CLI execution

- **Decision:** Early beta runs natively on loopback; Docker isolation comes later as a computer provider.
- **Evidence:** A containerized server cannot see host CLI logins or processes.
- **Rejected:** copying consumer login tokens into images.
- **Date:** 2026-10-13

## D-003 — Local execution is gated by a loopback access policy, not a token

- **Decision:** Local Claude runs are allowed only for requests from the same machine (loopback client, trusted Host, trusted Origin, no proxy headers), behind `LOCAL_EXECUTION_ENABLED` (default off). The check lives in one module, `local_access_policy`, which issues a grant that the execution service requires. Local tasks use their own provider value and are hidden from the guest-id endpoints. Runs are chat-only (all tools disabled) until approvals exist.
- **Evidence:** Peer projects rely on loopback-only for local execution; a user-entered token would add friction to the first run and would not protect the older unauthenticated endpoints anyway.
- **Rejected:** a mandatory owner token for every route (breaks existing guest and Telegram users); no check at all (any website or tunnel could start a process on the machine).
- **Consequence:** Another program or user on the same machine can use the feature. The policy sits behind an interface so a launcher-issued token can replace it for a desktop build.
- **Date:** 2026-10-13
