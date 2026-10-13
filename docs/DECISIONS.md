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
