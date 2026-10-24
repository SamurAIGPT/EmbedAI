# Native desktop (macOS preview)

The desktop app can give a named Claude agent access to this Mac's real screen
and accessible app controls. It is off by default. In the agent's Computer
panel, choose **Enable for this agent…**, confirm the native dialog, and grant
Accessibility and Screen Recording to Open Duet in System Settings. Retry after
changing permissions. Docker computers remain the isolated default.

An enabled agent uses native tools for new turns, even if it also has a Docker
computer configured. An unavailable native provider fails the turn. Disable it
explicitly to use the configured Docker computer again. Other engines fail
before starting; there is no host shell or file tool in this provider.

Native tools list apps and windows, capture desktop/window state (including an
accessibility tree), and click, type, press keys or scroll. Prefer the element
tokens from a fresh window snapshot. Every input requires its own approval,
including in Accept edits; Plan refuses input. Read observations are recorded
as automatic approvals. A fresh desktop or window observation is required
before every input, and stale semantic tokens are rejected by the driver.

Only one turn owns the shared native desktop at a time. **Take control** stops
the native daemon and refuses pending/future model calls. **Hand control back**
starts a new driver generation; approvals from before takeover cannot run,
including cards answered late. The agent must obtain a fresh observation and
new approval. No action is automatically retried after an error or timeout.
Disabling the owner agent or ending its turn revokes the connection.

Owner controls exist only in the Electron main frame. The loopback bridge has
separate launch and turn capabilities; the launch capability is passed to the
backend through stdin, never to the renderer or engine. Web/hosted backends
have no native connection. Desktop HTTP requests still require the desktop
launch token, so paired-device tokens cannot use this path.

Durable tool/approval records redact typed text, keys and element tokens; native
tool results (screenshots and accessibility contents) are omitted from app
history. Private per-turn permit files contain exact arguments until consumed
or turn cleanup. The selected engine receives live observations and inputs;
its own session storage and text the assistant repeats are outside this tool
record redaction policy. Use a disposable OS account for acceptance testing.

## Build resources

The maintained build stages a reviewed driver/SDK pair in
`desktop/native-control/`, outside ASAR. Its `manifest.json` covers every file;
the matching manifest digest, version, architecture and platform are pinned in
`desktop/src/native-control-pin.json`. The bundle includes `driver`, `sdk.mjs`,
licenses/notices and a CycloneDX SBOM. Startup rejects missing, altered, extra
or symlinked files and unsupported architectures. Nothing is downloaded at
runtime, and a workspace cannot select a different driver or MCP descriptor.

The desktop app validates the staged resources before starting the native
driver. Native resources must be signed before generating their manifest; the
outer packager must leave them intact. A release pipeline that includes native
preview must use its Developer ID for the driver and native libraries,
regenerate the pin, then sign/notarize the enclosing app.
The current staged development bundle is ad-hoc signed and arm64 only. Intel
needs its own reviewed bundle and matching pin; Linux/Windows are unavailable.

The `v0.4.0` macOS installer omits this bundle, so native desktop preview is
unavailable in that packaged app. It remains available from a source checkout
when the reviewed bundle is staged; a future installer can include it after
Developer ID signing and notarization are configured.

Automated checks use a fake driver for approval/lease/permission contracts and
a real loopback bridge. Real OS permission grant/revoke, semantic clicks in a
disposable fixture app, packaged offline startup, Developer ID signing,
notarization, Intel and a real model GUI turn have not been run.
