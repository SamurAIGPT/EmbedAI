// Runs the backend as a child process and speaks the launcher's side of the
// protocol (D-012 decision 3; the backend's side is backend/app/desktop/entry.py):
//
//  - one JSON line on its stdin carries the token, the key and the folders;
//  - stdin then stays open: closing it is how the backend is asked to stop, and
//    what stops it if this process dies;
//  - the backend says where it listens with a ready line on stdout, and the line
//    carries an HMAC of the port keyed by the token, so text that only looks like
//    it (the backend's log lines share the stream) is ignored.

const childProcess = require("node:child_process");
const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const readline = require("node:readline");

const READY_PREFIX = "OPEN_DOTS_READY ";
const PROTOCOL_VERSION = 1;
const READY_TIMEOUT_MS = 120_000; // the first start imports everything and migrates
const STOP_GRACE_MS = 25_000; // the backend's own backstop is 20 s
const LOG_LIMIT_BYTES = 5 * 1024 * 1024;

// Variables that would change which Python or which modules the backend runs, and
// the launcher's own. Everything else is passed on: the Claude CLI needs the user's
// PATH and its own settings (including ANTHROPIC_*).
const NOT_PASSED_ON = /^(ELECTRON_RUN_AS_NODE|PYTHONHOME|PYTHONPATH|PYTHONSTARTUP|OPEN_DOTS_.*)$/i;

// Where the engines' CLIs are usually installed (per-user installers, npm prefixes,
// Homebrew). An app opened from Finder, the Dock or a desktop launcher gets only the
// system PATH, not the one the person's shell sets up, so these are added to the
// END of the PATH when they exist and are missing. Nothing already there is
// overridden. The same idea as the server's app/server/engine_path.py.
const USER_ENGINE_DIRS = [".local/bin", ".npm-global/bin", ".claude/local"];
const SYSTEM_ENGINE_DIRS = ["/opt/homebrew/bin", "/usr/local/bin"];

function isDirectory(folder) {
  try {
    return fs.statSync(folder).isDirectory();
  } catch {
    return false;
  }
}

function withEngineFolders(current, { home, isDirectoryImpl }) {
  const parts = (current ?? "").split(":").filter(Boolean);
  const candidates = [...(home ? USER_ENGINE_DIRS.map((name) => path.posix.join(home, name)) : []), ...SYSTEM_ENGINE_DIRS];
  for (const folder of candidates) {
    if (!parts.includes(folder) && isDirectoryImpl(folder)) parts.push(folder);
  }
  return parts.join(":");
}

function backendEnvironment(
  base,
  { platform = process.platform, home = os.homedir(), isDirectory: isDirectoryImpl = isDirectory } = {},
) {
  const env = {};
  for (const [name, value] of Object.entries(base)) {
    if (!NOT_PASSED_ON.test(name) && value !== undefined) env[name] = value;
  }
  // Windows gives every app the user's PATH, and its folders are written differently.
  if (platform !== "win32") {
    const extended = withEngineFolders(env.PATH, { home, isDirectoryImpl });
    if (extended) env.PATH = extended;
  }
  env.PYTHONUTF8 = "1";
  env.PYTHONIOENCODING = "utf-8";
  return env;
}

function expectedProof(token, port) {
  return crypto.createHmac("sha256", token).update(`ready:${port}`).digest("hex");
}

// The ready line's content if the line is the backend's, else null.
function parseReadyLine(line, token) {
  if (!line.startsWith(READY_PREFIX)) return null;
  let message;
  try {
    message = JSON.parse(line.slice(READY_PREFIX.length));
  } catch {
    return null;
  }
  const { port, proof } = message ?? {};
  if (!Number.isInteger(port) || port < 1 || port > 65535 || typeof proof !== "string") return null;
  const expected = Buffer.from(expectedProof(token, port), "utf8");
  const given = Buffer.from(proof, "utf8");
  if (expected.length !== given.length || !crypto.timingSafeEqual(expected, given)) return null;
  return { port, pid: Number.isInteger(message.pid) ? message.pid : null };
}

function newToken(randomBytes = crypto.randomBytes) {
  return randomBytes(32).toString("base64url");
}

function rotateLog(file, maxBytes = LOG_LIMIT_BYTES, fsImpl = fs) {
  try {
    if (fsImpl.statSync(file).size > maxBytes) fsImpl.renameSync(file, `${file}.1`);
  } catch {
    /* no log yet */
  }
}

function killTree(pid, { spawnSync = childProcess.spawnSync, platform = process.platform, kill = process.kill } = {}) {
  if (platform === "win32") {
    spawnSync("taskkill", ["/pid", String(pid), "/T", "/F"], { windowsHide: true });
    return;
  }
  try {
    kill(pid, "SIGKILL");
  } catch {
    /* already gone */
  }
}

// Starts the backend. Resolves `ready` with { port, origin } once the backend has
// proved itself; rejects with { kind, code } if it exits first or takes too long.
// `exited` resolves with { code } whenever the process ends.
function startBackend({
  python,
  script,
  cwd,
  env,
  message,
  token,
  logFile,
  spawn = childProcess.spawn,
  fsImpl = fs,
  killTreeImpl = killTree,
  readyTimeoutMs = READY_TIMEOUT_MS,
  stopGraceMs = STOP_GRACE_MS,
}) {
  fsImpl.mkdirSync(path.dirname(logFile), { recursive: true });
  rotateLog(logFile, LOG_LIMIT_BYTES, fsImpl);
  const logFd = fsImpl.openSync(logFile, "a");
  const log = (text) => {
    try {
      fsImpl.writeSync(logFd, `${text}\n`);
    } catch {
      /* a full disk must not take the app down */
    }
  };

  // `script` is null for the packaged backend, which is a program of its own.
  const child = spawn(python, script ? [script] : [], {
    cwd,
    env,
    stdio: ["pipe", "pipe", logFd],
    windowsHide: true,
  });

  let finished = false;
  let exitInfo = null;
  let resolveExited;
  const exited = new Promise((resolve) => {
    resolveExited = resolve;
  });

  const ready = new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      if (finished) return;
      finished = true;
      reject({ kind: "timeout", code: null });
      requestStop();
    }, readyTimeoutMs);

    const lines = readline.createInterface({ input: child.stdout });
    lines.on("line", (line) => {
      const found = !finished ? parseReadyLine(line, token) : null;
      if (found) {
        finished = true;
        clearTimeout(timer);
        resolve({ port: found.port, pid: found.pid ?? child.pid, origin: `http://127.0.0.1:${found.port}` });
        return;
      }
      // A line that only claims to be the ready line is just text; keep it in the log.
      log(line);
    });

    child.once("error", (error) => {
      log(`[launcher] could not start the backend: ${error.message}`);
      if (!finished) {
        finished = true;
        clearTimeout(timer);
        reject({ kind: "spawn", code: null, message: error.message });
      }
    });

    child.once("exit", (code, signal) => {
      exitInfo = { code, signal };
      clearTimeout(timer);
      if (!finished) {
        finished = true;
        reject({ kind: "exit", code });
      }
      resolveExited(exitInfo);
      try {
        fsImpl.closeSync(logFd);
      } catch {
        /* closed */
      }
    });
  });
  // The caller decides what to do with a failure; an unhandled rejection must not.
  ready.catch(() => {});

  // The launcher's message. A broken pipe here means the backend is already gone,
  // which `exit` reports.
  child.stdin.on("error", () => {});
  child.stdin.write(`${JSON.stringify({ version: PROTOCOL_VERSION, ...message })}\n`);

  let stopping = null;
  function requestStop() {
    if (stopping) return stopping;
    stopping = new Promise((resolve) => {
      if (exitInfo) return resolve(exitInfo);
      const timer = setTimeout(() => {
        log("[launcher] the backend did not stop in time; ending it");
        killTreeImpl(child.pid);
      }, stopGraceMs);
      exited.then((info) => {
        clearTimeout(timer);
        resolve(info);
      });
      // Closing stdin is the backend's cue for a clean stop, the same on every OS.
      try {
        child.stdin.end();
      } catch {
        /* already closed */
      }
    });
    return stopping;
  }

  return { child, ready, exited, stop: requestStop, log };
}

module.exports = {
  startBackend,
  backendEnvironment,
  parseReadyLine,
  expectedProof,
  newToken,
  rotateLog,
  killTree,
  READY_PREFIX,
  PROTOCOL_VERSION,
};
