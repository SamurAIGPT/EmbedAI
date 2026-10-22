// `npm run setup`: install everything the desktop app needs from a source checkout.
// Run it once, and again after the dependencies change. Starting the app never
// does this for you.

const { spawnSync } = require("node:child_process");
const { layout } = require("../src/paths");
const { nodeIsNewEnough, NODE_MAJOR_NEEDED } = require("../src/preflight");

const paths = layout();
const isWindows = process.platform === "win32";

function fail(message) {
  console.error(`\n${message}`);
  process.exit(1);
}

function step(title, command, args, cwd) {
  console.log(`\n==> ${title}`);
  // npm is a .cmd file on Windows, which only a shell can start.
  const result = spawnSync(command, args, { cwd, stdio: "inherit", shell: isWindows && command === "npm" });
  if (result.error || result.status !== 0) {
    fail(`That step failed: ${command} ${args.join(" ")} (in ${cwd}).`);
  }
}

if (!nodeIsNewEnough()) {
  fail(`Node ${NODE_MAJOR_NEEDED} or newer is needed (this is ${process.version}). Get it from https://nodejs.org/.`);
}

const uv = spawnSync("uv", ["--version"], { encoding: "utf8", shell: false });
if (uv.error || uv.status !== 0) {
  fail(
    "uv is needed to set up the backend and was not found on PATH.\n" +
      "Install it from https://docs.astral.sh/uv/getting-started/installation/ and run this again.\n" +
      "(uv downloads the Python the backend needs; you do not have to install Python yourself.)",
  );
}

step("Backend: Python environment (uv sync)", "uv", ["sync", "--locked"], paths.backend);
step("Page: dependencies (npm ci)", "npm", ["ci"], paths.frontend);
step("Desktop shell: dependencies (npm ci)", "npm", ["ci"], paths.desktop);
// Electron downloads its own binary the first time it is used. Do it here, where
// the download is expected, so that starting the app never does.
step(
  "Desktop shell: Electron binary",
  process.execPath,
  [require("node:path").join(paths.desktop, "node_modules", "electron", "install.js")],
  paths.desktop,
);

console.log(
  "\nSetup is done. Next:\n" +
    "  npm run build    build the page the app shows (again after the page's code changes)\n" +
    "  npm run start    open Open Duet\n" +
    "Claude Code must be installed and signed in for chat to work: https://docs.anthropic.com/en/docs/claude-code",
);
