// `npm run smoke:packaged`: start the packaged app (release/win-unpacked) the way a
// user's machine would have it, without Python on PATH, with a fresh data folder,
// and check that its frozen backend comes up, applies the migrations and serves
// the page. Windows only for now (K-009). Run `npm run package` first.

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawn, spawnSync } = require("node:child_process");

const desktop = path.resolve(__dirname, "..");
// Defaults to the unpacked build; pass the path of an installed "Open Dots.exe" to check that.
const exe = process.argv[2] ?? path.join(desktop, "release", "win-unpacked", "Open Dots.exe");
const TIMEOUT_MS = 90_000;

function fail(message) {
  console.error(`smoke:packaged FAILED: ${message}`);
  process.exit(1);
}

if (process.platform !== "win32") fail("only the Windows build is checked so far.");
if (!fs.existsSync(exe)) fail(`${exe} is missing. Run: npm run package`);

// PATH without any folder that holds a Python, and without the virtualenv variables.
const pathKey = Object.keys(process.env).find((k) => k.toLowerCase() === "path") ?? "PATH";
const cleanPath = (process.env[pathKey] ?? "")
  .split(path.delimiter)
  .filter((dir) => dir && !/python|\.venv|uv/i.test(dir) && !fs.existsSync(path.join(dir, "python.exe")))
  .join(path.delimiter);
const env = { ...process.env, [pathKey]: cleanPath };
for (const name of Object.keys(env)) if (/^(VIRTUAL_ENV|PYTHON.*|UV_.*|ELECTRON_RUN_AS_NODE)$/i.test(name)) delete env[name];

const dataRoot = fs.mkdtempSync(path.join(os.tmpdir(), "open-dots-smoke-"));
env.OPEN_DOTS_DATA_DIR = dataRoot;
const log = path.join(dataRoot, "logs", "backend.log");

const app = spawn(exe, [], { env, stdio: "ignore", windowsHide: false });
let exited = null;
app.once("exit", (code) => {
  exited = code;
});

function stop() {
  spawnSync("taskkill", ["/pid", String(app.pid), "/T", "/F"], { windowsHide: true });
}

async function main() {
  const deadline = Date.now() + TIMEOUT_MS;
  let port = null;
  while (Date.now() < deadline && port === null) {
    if (exited !== null) fail(`the app exited early with code ${exited}.\n${tail()}`);
    const text = fs.existsSync(log) ? fs.readFileSync(log, "utf8") : "";
    port = /Serving on http:\/\/127\.0\.0\.1:(\d+)/.exec(text)?.[1] ?? null;
    if (port === null) await new Promise((r) => setTimeout(r, 500));
  }
  if (port === null) fail(`the backend did not report that it was serving within ${TIMEOUT_MS / 1000}s.\n${tail()}`);

  // Without the launch token the backend must still answer, if only to refuse.
  const response = await fetch(`http://127.0.0.1:${port}/chat/`).catch((e) => fail(`no answer on port ${port}: ${e.message}`));
  if (response.status >= 500) fail(`the backend answered ${response.status}.\n${tail()}`);

  const database = path.join(dataRoot, "data");
  if (!fs.existsSync(database)) fail("the data folder was not created.");
  console.log(`smoke:packaged OK: frozen backend served on port ${port} (HTTP ${response.status}) with no Python on PATH.`);
}

function tail() {
  try {
    return fs.readFileSync(log, "utf8").split("\n").slice(-25).join("\n");
  } catch {
    return "(no backend log)";
  }
}

main()
  .catch((error) => fail(String(error?.stack ?? error)))
  .finally(() => {
    stop();
    // Leave the data folder for inspection only when the run failed (fail() exits first).
    setTimeout(() => fs.rmSync(dataRoot, { recursive: true, force: true }), 2000);
  });
