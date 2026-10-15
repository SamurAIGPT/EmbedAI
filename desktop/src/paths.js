// Where things are in a source checkout. Pure: nothing here touches the disk.

const path = require("node:path");

function layout(root = path.resolve(__dirname, "..", "..")) {
  const backend = path.join(root, "backend");
  const frontend = path.join(root, "frontend");
  const isWindows = process.platform === "win32";
  return {
    root,
    backend,
    frontend,
    desktop: path.join(root, "desktop"),
    // The backend runs on the virtualenv's own Python, so `uv` is needed to set up
    // but not to start, and starting never syncs or installs anything.
    python: isWindows
      ? path.join(backend, ".venv", "Scripts", "python.exe")
      : path.join(backend, ".venv", "bin", "python"),
    backendScript: path.join(backend, "desktop_main.py"),
    // What `npm run build` makes and the backend serves.
    ui: path.join(frontend, "out"),
  };
}

module.exports = { layout };
