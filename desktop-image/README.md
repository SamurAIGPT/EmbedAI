# Local desktop image

Build from the repository root:

```sh
docker build -t open-dots-desktop:local desktop-image
```

This installs Debian's TigerVNC, Openbox, Firefox ESR, xterm, xdotool, maim,
ImageMagick and socat. No host port is published. VNC listens only on container
loopback. The app keeps the existing non-root user, dropped capabilities,
no-new-privileges, resource limits and workspace-only bind mount.

The fixed display is 1280×800. The browser does not start automatically.
Its profile survives stop/start and is lost on Recreate. The workspace survives.
The image is built locally; no prebuilt image is published by this change.
See SOURCES.md for package provenance and licence information.
