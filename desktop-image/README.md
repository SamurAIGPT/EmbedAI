# Local desktop image

Build from the repository root:

```sh
docker build -t open-dots-desktop:local desktop-image
```

This installs Debian's TigerVNC, Openbox, Firefox ESR, Chromium, xterm, xdotool,
maim, ImageMagick and socat, plus Playwright 1.58.0 as the in-container CDP
client. No host port is published. VNC and Chromium CDP listen only on container
loopback. The app keeps the existing non-root user, dropped capabilities,
no-new-privileges, resource limits and workspace-only bind mount.

The fixed display is 1280×800. The browser does not start automatically.
Its profile survives stop/start and is lost on Recreate. The workspace survives.
The image is built locally; no prebuilt image is published by this change.
See SOURCES.md for package provenance and licence information.

Assign a desktop computer to a Claude agent, then enable **Browser tools** in
its Computer panel to expose `browser_*` MCP tools. They are off by default.
Shell computers and native host desktops do not expose them. Rebuild
the image and **Recreate** older desktop computers before using these tools.
The broker starts Chromium on demand in the same visible desktop; the existing
viewer and take-control UI work with it. Firefox remains available manually.

`od-browser` accepts one bounded JSON request on stdin, and forwards it to a
private Unix socket inside the container. Its broker connects only to the
Chromium instance it launches on container loopback port 9222. It accepts no
attach endpoint, executable path or profile path. The profile is mode 0700 at
`/home/agent/browser-profile`, outside the mounted workspace. Thirty minutes of
browser-tool inactivity closes Chromium; the broker remains ready to reopen it.

Chromium runs without its own sandbox because the non-root container cannot
create Chromium's sandbox namespaces with the existing Docker restrictions.
The container remains hardened; this is a residual browser isolation risk.
The normal no-network default also applies to Chromium. Internet mode allows
general outbound access and is not a site allowlist.
