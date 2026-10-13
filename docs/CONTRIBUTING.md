# Contributing

Issues and pull requests are welcome.

## Workflow

1. Check [ROADMAP.md](ROADMAP.md) and pick one feature or fix; keep PRs small and focused.
2. Branch from `main`; use Conventional Commit prefixes (`feat:`, `fix:`, `test:`, `docs:`, `chore:`).
3. Add focused tests for service or API changes; run backend tests and the frontend lint/build.
4. Keep the README and `docs/` aligned with behavior; note limitations honestly.
5. Do not merge with failing checks.

## Security expectations

- Authenticate and validate every route that mutates state or runs code.
- Never commit secrets, tokens, raw provider transcripts or unsanitized captures.
- Do not silently weaken sandboxing or bypass approvals.
- Features exposing host execution, subprocesses or files need a security review in the PR.

## Frontend note

The frontend uses a recent Next.js with breaking changes; read the guides in `frontend/node_modules/next/dist/docs/` before editing it.
