// `npm run package`: make an installer for this operating system, from the
// source checkout. Steps: the page (npm run build), the frozen backend
// (backend/build.py), then electron-builder. It installs nothing: it needs
// `npm run setup` (which also installs electron-builder) and `uv`.
//
// PyInstaller does not cross-compile, so the installer is for the OS it runs on.
// Output: desktop/release/. Unsigned (signing is a later step).

const fs = require("node:fs");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const { layout } = require("../src/paths");

const paths = layout();
const builder = path.join(paths.desktop, "node_modules", "electron-builder", "cli.js");
const frozen = path.join(paths.backend, "dist", "open-dots-backend");

function fail(message) {
  console.error(`\n${message}`);
  process.exit(1);
}

function run(title, command, args, cwd) {
  console.log(`\n==> ${title}`);
  const result = spawnSync(command, args, { cwd, stdio: "inherit", shell: false });
  if (result.error || result.status !== 0) fail(`${title} failed.`);
}

if (!fs.existsSync(builder)) fail("electron-builder is not installed. Run: npm run setup");

const target = { win32: "--win", darwin: "--mac", linux: "--linux" }[process.platform];
if (!target) fail(`No installer target for ${process.platform}.`);

run("Building the page", process.execPath, [path.join(__dirname, "build.js")], paths.desktop);
run(
  "Freezing the backend",
  "uv",
  ["run", "--with", "pyinstaller", "python", "build.py"],
  paths.backend,
);
if (!fs.existsSync(frozen)) fail(`The frozen backend is missing from ${frozen}.`);

run("Making the installer", process.execPath, [builder, target, "--publish", "never"], paths.desktop);
console.log(`\nInstaller in ${path.join(paths.desktop, "release")}`);
