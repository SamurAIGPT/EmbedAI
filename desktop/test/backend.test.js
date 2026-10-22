const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { PassThrough } = require("node:stream");
const { afterEach, beforeEach, test } = require("node:test");

const {
  startBackend,
  backendEnvironment,
  parseReadyLine,
  expectedProof,
  newToken,
  rotateLog,
  killTree,
  READY_PREFIX,
} = require("../src/backend");

const TOKEN = "t".repeat(43);

let dir;
const children = [];
beforeEach(() => {
  dir = fs.mkdtempSync(path.join(os.tmpdir(), "od-backend-"));
});
afterEach(() => {
  // A fake backend that is still "running" keeps its timers, and so the test process, alive.
  for (const child of children.splice(0)) child.exit(0);
  fs.rmSync(dir, { recursive: true, force: true });
});

// A child process that only exists in the test: what it is given and what it says
// are the test's to control.
function fakeChild() {
  const child = new EventEmitter();
  child.pid = 4242;
  child.stdout = new PassThrough();
  child.stdin = new PassThrough();
  child.written = [];
  child.stdin.on("data", (chunk) => child.written.push(chunk.toString("utf8")));
  child.ended = false;
  child.stdin.end = () => {
    child.ended = true;
  };
  child.say = (text) => child.stdout.write(`${text}\n`);
  child.exit = (code, signal = null) => child.emit("exit", code, signal);
  return child;
}

function start(options = {}) {
  const child = fakeChild();
  children.push(child);
  const spawned = [];
  const killed = [];
  const logFile = path.join(dir, "logs", "backend.log");
  const backend = startBackend({
    python: "py",
    script: "desktop_main.py",
    cwd: dir,
    env: { A: "1" },
    message: { token: TOKEN, encryption_key: "KEY", data_dir: dir, ui_dir: path.join(dir, "ui") },
    token: TOKEN,
    logFile,
    spawn: (...args) => {
      spawned.push(args);
      return child;
    },
    killTreeImpl: (pid) => killed.push(pid),
    readyTimeoutMs: 60_000,
    stopGraceMs: 60_000,
    ...options,
  });
  return { child, backend, spawned, killed, logFile };
}

const readyLine = (port, token = TOKEN) =>
  `${READY_PREFIX}${JSON.stringify({ port, pid: 99, proof: expectedProof(token, port) })}`;
const settle = () => new Promise((resolve) => setImmediate(resolve));
const log = (logFile) => (fs.existsSync(logFile) ? fs.readFileSync(logFile, "utf8") : "");

test("the proof is the same HMAC the Python backend makes (a shared test vector)", () => {
  // Computed with Python: hmac.new(b"t" * 43, b"ready:5000", "sha256").hexdigest()
  assert.equal(expectedProof(TOKEN, 5000), "8b61da84dde096f8c0ba1345c759dbdb5d241222f4d439efd5527dcbc1b96022");
});

test("it starts the backend with the right arguments, folders and pipes", () => {
  const { spawned, logFile } = start();
  const [python, args, options] = spawned[0];
  assert.equal(python, "py");
  assert.deepEqual(args, ["desktop_main.py"]);
  assert.equal(options.cwd, dir);
  assert.deepEqual(options.env, { A: "1" });
  assert.equal(options.windowsHide, true);
  assert.equal(options.stdio[0], "pipe");
  assert.equal(options.stdio[1], "pipe");
  assert.equal(typeof options.stdio[2], "number"); // stderr goes straight to the log file
  assert.ok(fs.existsSync(logFile));
});

test("the launcher's message is one JSON line, and stdin stays open as the lifeline", async () => {
  const { child } = start();
  await settle();
  assert.equal(child.written.length, 1);
  assert.ok(child.written[0].endsWith("\n"));
  assert.equal(child.written[0].trimEnd().includes("\n"), false);
  assert.deepEqual(JSON.parse(child.written[0]), {
    version: 1,
    token: TOKEN,
    encryption_key: "KEY",
    data_dir: dir,
    ui_dir: path.join(dir, "ui"),
  });
  assert.equal(child.ended, false);
});

test("a ready line with the right proof resolves with the port and the origin", async () => {
  const { child, backend } = start();
  child.say(readyLine(51234));
  assert.deepEqual(await backend.ready, { port: 51234, pid: 99, origin: "http://127.0.0.1:51234" });
});

test("text that only looks like the ready line is ignored and kept as log text", async () => {
  const { child, backend, logFile } = start();
  let resolved = false;
  backend.ready.then(() => {
    resolved = true;
  });
  const spoofs = [
    readyLine(51234, "u".repeat(43)), // proof made with another token
    `${READY_PREFIX}${JSON.stringify({ port: 51234, proof: "00" })}`, // wrong length
    `${READY_PREFIX}${JSON.stringify({ port: 51234 })}`, // no proof
    `${READY_PREFIX}${JSON.stringify({ port: 70000, proof: expectedProof(TOKEN, 70000) })}`, // not a port
    `${READY_PREFIX}${JSON.stringify({ port: "5000", proof: expectedProof(TOKEN, 5000) })}`, // not a number
    `${READY_PREFIX}not json`,
    `  ${readyLine(51234)}`, // not at the start of the line
    "listening on 51234",
  ];
  for (const line of spoofs) child.say(line);
  await settle();
  await settle();
  assert.equal(resolved, false);
  for (const line of spoofs) assert.ok(log(logFile).includes(line), line);

  child.say(readyLine(40000));
  assert.equal((await backend.ready).port, 40000);
});

test("the ready line itself is not copied into the log", async () => {
  const { child, backend, logFile } = start();
  child.say("starting up");
  child.say(readyLine(51234));
  await backend.ready;
  assert.ok(log(logFile).includes("starting up"));
  assert.ok(!log(logFile).includes(READY_PREFIX));
});

test("a backend that exits before it is ready rejects with its exit code", async () => {
  const { child, backend } = start();
  child.exit(3);
  await assert.rejects(backend.ready, { kind: "exit", code: 3 });
  assert.deepEqual(await backend.exited, { code: 3, signal: null });
});

test("a backend that cannot be started at all rejects as a spawn failure", async () => {
  const { child, backend, logFile } = start();
  child.emit("error", Object.assign(new Error("spawn py ENOENT"), { code: "ENOENT" }));
  await assert.rejects(backend.ready, { kind: "spawn" });
  assert.match(log(logFile), /could not start the backend: spawn py ENOENT/);
});

test("a backend that never says it is ready is stopped and rejects as a timeout", async () => {
  const { child, backend } = start({ readyTimeoutMs: 30 });
  await assert.rejects(backend.ready, { kind: "timeout" });
  assert.equal(child.ended, true, "stdin was closed to ask it to stop");
});

test("an exit after ready is reported by `exited`, not as a failure to start", async () => {
  const { child, backend } = start();
  child.say(readyLine(51234));
  await backend.ready;
  child.exit(1);
  assert.deepEqual(await backend.exited, { code: 1, signal: null });
});

test("stop closes stdin and waits for the backend to leave on its own", async () => {
  const { child, backend, killed } = start();
  child.say(readyLine(51234));
  await backend.ready;
  const stopped = backend.stop();
  assert.equal(child.ended, true);
  child.exit(0);
  assert.deepEqual(await stopped, { code: 0, signal: null });
  assert.deepEqual(killed, []);
});

test("a backend that does not leave within the grace period is ended", async () => {
  const { child, backend, killed } = start({ stopGraceMs: 20 });
  child.say(readyLine(51234));
  await backend.ready;
  const stopped = backend.stop();
  await new Promise((resolve) => setTimeout(resolve, 80));
  assert.deepEqual(killed, [4242]);
  child.exit(null, "SIGKILL");
  await stopped;
});

test("stopping twice is one stop, and stopping after exit does nothing", async () => {
  const { child, backend, killed } = start();
  child.say(readyLine(51234));
  await backend.ready;
  assert.equal(backend.stop(), backend.stop());
  child.exit(0);
  await backend.exited;
  assert.deepEqual(await backend.stop(), { code: 0, signal: null });
  assert.deepEqual(killed, []);
});

test("what the backend says on stdout is kept in the log", async () => {
  const { child, backend, logFile } = start();
  child.say("first line");
  child.say("second line");
  child.say(readyLine(51234));
  await backend.ready;
  assert.match(log(logFile), /first line\nsecond line\n/);
});

test("parseReadyLine accepts exactly what the backend prints", () => {
  assert.deepEqual(parseReadyLine(readyLine(8080), TOKEN), { port: 8080, pid: 99 });
  assert.equal(parseReadyLine(readyLine(8080), "x".repeat(43)), null);
  assert.equal(parseReadyLine("OPEN_DOTS_READY", TOKEN), null);
});

test("the backend's environment drops what could change its Python and the launcher's own settings", () => {
  const base = {
    PATH: "/usr/bin",
    ANTHROPIC_API_KEY: "kept for the Claude CLI",
    HOME: "/home/x",
    ELECTRON_RUN_AS_NODE: "1",
    PYTHONHOME: "/elsewhere",
    PYTHONPATH: "/elsewhere",
    PYTHONSTARTUP: "/elsewhere/x.py",
    OPEN_DOTS_CLAUDE_PATH: "/x/claude",
    open_dots_data_dir: "/x",
    UNSET: undefined,
  };
  const before = { ...base };
  const env = backendEnvironment(base);
  assert.deepEqual(Object.keys(env).sort(), ["ANTHROPIC_API_KEY", "HOME", "PATH", "PYTHONIOENCODING", "PYTHONUTF8"]);
  assert.equal(env.PYTHONUTF8, "1");
  assert.deepEqual(base, before, "the input is not changed");
});

test("an app opened from Finder or the Dock still lets the backend find user-installed engines", () => {
  // macOS gives an app started from Finder, the Dock or Launchpad only the system
  // PATH, so a Claude or Codex CLI in Homebrew or a per-user folder is not found.
  const existing = new Set(["/Users/x/.local/bin", "/opt/homebrew/bin", "/usr/local/bin"]);
  const options = { platform: "darwin", home: "/Users/x", isDirectory: (folder) => existing.has(folder) };
  const env = backendEnvironment({ PATH: "/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin" }, options);
  assert.equal(env.PATH, "/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:/Users/x/.local/bin:/opt/homebrew/bin");

  // A PATH that already has them (started from a terminal) is left as it was.
  const full = "/opt/homebrew/bin:/Users/x/.local/bin:/usr/local/bin:/usr/bin:/bin";
  assert.equal(backendEnvironment({ PATH: full }, options).PATH, full);

  // Windows gives every app the user's PATH: nothing is added there.
  const windows = { platform: "win32", home: "C:\\Users\\x", isDirectory: () => true };
  assert.equal(backendEnvironment({ Path: "C:\\Windows" }, windows).Path, "C:\\Windows");
});

test("a token is 32 random bytes in URL-safe text and differs every time", () => {
  const first = newToken();
  assert.match(first, /^[A-Za-z0-9_-]{43}$/);
  assert.notEqual(first, newToken());
});

test("the log is set aside when it grows too large, and not before", () => {
  const file = path.join(dir, "backend.log");
  fs.writeFileSync(file, "x".repeat(100));
  rotateLog(file, 1000);
  assert.ok(fs.existsSync(file));
  rotateLog(file, 50);
  assert.ok(!fs.existsSync(file));
  assert.equal(fs.readFileSync(`${file}.1`, "utf8").length, 100);
  rotateLog(file, 50); // no log: nothing to do
});

test("ending a process tree uses taskkill on Windows and a kill signal elsewhere", () => {
  const calls = [];
  killTree(7, { platform: "win32", spawnSync: (...args) => calls.push(args) });
  assert.deepEqual(calls[0].slice(0, 2), ["taskkill", ["/pid", "7", "/T", "/F"]]);
  const signals = [];
  killTree(7, { platform: "linux", kill: (...args) => signals.push(args) });
  assert.deepEqual(signals, [[7, "SIGKILL"]]);
  killTree(7, { platform: "linux", kill: () => { throw new Error("gone"); } }); // never throws
});
