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

// Where things are in an installed app. electron-builder copies the frozen backend
// to <resources>/backend and the built page to <resources>/ui (see package.json,
// build.extraResources). The frozen backend is its own program: no Python, no script.
function packagedLayout(resourcesPath, platform = process.platform) {
  const folder = path.join(resourcesPath, "backend");
  return {
    resources: resourcesPath,
    python: path.join(folder, platform === "win32" ? "open-dots-backend.exe" : "open-dots-backend"),
    backendScript: null,
    ui: path.join(resourcesPath, "ui"),
  };
}

module.exports = { layout, packagedLayout };
