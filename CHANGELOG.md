# Changelog

## Unreleased

### Added
- Local Claude answers now stream into the web UI as Claude writes them (server-sent events, `GET /api/v1/local/tasks/{id}/events`). The saved task still decides the final outcome, a partial answer is kept if a run fails, and an empty answer is shown as such. The existing polling endpoints still work. Events live in memory for now (single backend process); see [D-004](docs/DECISIONS.md).
- Local Claude (experimental, off by default): run chat-only prompts through your own signed-in `claude` CLI from the web UI. Only usable from the machine running the backend. See [docs/SETUP.md](docs/SETUP.md#local-claude-experimental).
