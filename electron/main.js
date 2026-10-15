// Desktop feasibility spike: starts the packaged backend, waits for it, opens the
// exported web page in a window, and stops the backend when the app quits.
// Nothing here is product code: no installer, updater, tray or settings.

const { app, BrowserWindow, dialog } = require("electron");
const { spawn } = require("node:child_process");
const crypto = require("node:crypto");
const fs = require("node:fs");
const net = require("node:net");
const path = require("node:path");

const IS_WIN = process.platform === "win32";

// Spike runs point the data folder somewhere disposable.
if (process.env.OD_SPIKE_DATA) {
  app.setPath("userData", process.env.OD_SPIKE_DATA);
}
const DATA = app.getPath("userData");

const BACKEND_EXE =
  process.env.OD_BACKEND_EXE ||
  path.join(__dirname, "build", "dist", "od-backend", IS_WIN ? "od-backend.exe" : "od-backend");
const UI_DIR = process.env.OD_UI_DIR || path.join(__dirname, "..", "frontend", "out");

let backend = null;
let backendExited = false;
let quitting = false;
let port = 0;

function freePort(preferred) {
  return new Promise((resolve) => {
    const tryPort = (candidate, onFail) => {
      const server = net.createServer();
      server.once("error", onFail);
      server.listen(candidate, "127.0.0.1", () => {
        const { port: got } = server.address();
        server.close(() => resolve(got));
      });
    };
    const any = () => tryPort(0, () => resolve(0));
    if (preferred) tryPort(preferred, any);
    else any();
  });
}

// The page keeps its state in localStorage, which belongs to one origin
// (address and port). A new port on every start would lose it, so the last
// port is reused whenever it is free.
function loadSavedPort() {
  try {
    return Number(fs.readFileSync(path.join(DATA, "port.txt"), "utf8")) || 0;
  } catch {
    return 0;
  }
}

// The backend requires an encryption key. Spike only: kept in a plain file in
// the data folder; a real build would use the operating system's secret store.
function encryptionKey() {
  const file = path.join(DATA, "token.key");
  try {
    return fs.readFileSync(file, "utf8").trim();
  } catch {
    const key = crypto.randomBytes(32).toString("base64").replace(/\+/g, "-").replace(/\//g, "_");
    fs.writeFileSync(file, key, { mode: 0o600 });
    return key;
  }
}

function startBackend() {
  fs.mkdirSync(DATA, { recursive: true });
  const log = fs.openSync(path.join(DATA, "backend.log"), "a");
  const env = {
    ...process.env,
    OD_PORT: String(port),
    OD_UI_DIR: UI_DIR,
    OD_WATCH_STDIN: "1",
    DATABASE_BACKEND: "sqlite",
    SQLITE_PATH: path.join(DATA, "open-dots.db"),
    LOCAL_WORKSPACE_ROOT: path.join(DATA, "workspace"),
    LOCAL_EXECUTION_ENABLED: "true",
    LOCAL_TOOLS_ENABLED: "true",
    FRONTEND_ORIGINS: `http://127.0.0.1:${port}`,
    TOKEN_ENCRYPTION_KEYS: encryptionKey(),
    PYTHONUTF8: "1",
  };
  backend = spawn(BACKEND_EXE, [], {
    cwd: DATA, // not the repo: the backend must not pick up a developer's .env
    env,
    stdio: ["pipe", log, log],
    windowsHide: true,
  });
  backend.on("exit", (code) => {
    backendExited = true;
    if (!quitting) {
      dialog.showErrorBox("Open Dots", `The backend stopped unexpectedly (exit ${code}). See ${path.join(DATA, "backend.log")}`);
      app.quit();
    }
  });
  fs.writeFileSync(
    path.join(DATA, "spike-state.json"),
    JSON.stringify({ port, backendPid: backend.pid, electronPid: process.pid })
  );
}

async function waitForBackend(timeoutMs = 60000) {
  const end = Date.now() + timeoutMs;
  while (Date.now() < end && !backendExited) {
    try {
      const res = await fetch(`http://127.0.0.1:${port}/api/v1/providers`);
      if (res.ok) return true;
    } catch {
      /* not up yet */
    }
    await new Promise((r) => setTimeout(r, 200));
  }
  return false;
}

function stopBackend() {
  return new Promise((resolve) => {
    if (!backend || backendExited) return resolve();
    const timer = setTimeout(() => {
      try {
        backend.kill();
      } catch {
        /* already gone */
      }
      resolve();
    }, 10000);
    backend.once("exit", () => {
      clearTimeout(timer);
      resolve();
    });
    // Closing stdin is the backend's cue for a clean shutdown (works the same on every OS).
    backend.stdin.end();
  });
}

async function report(win, readyMs) {
  // Verification hook for the spike: what the window actually shows.
  const file = process.env.OD_SPIKE_REPORT;
  if (!file) return;
  await new Promise((r) => setTimeout(r, 3000));
  const wc = win.webContents;
  const probe = await wc.executeJavaScript(`(() => {
    if (${process.env.OD_SPIKE_SETMARK === "1"}) localStorage.setItem("od_spike_mark", "kept");
    return {
      title: document.title,
      url: location.href,
      text: document.body.innerText.slice(0, 1500),
      mark: localStorage.getItem("od_spike_mark"),
      nodeLeak: typeof require !== "undefined" || typeof process !== "undefined",
    };
  })()`);
  const image = await wc.capturePage();
  fs.writeFileSync(file.replace(/\.json$/, ".png"), image.toPNG());
  fs.writeFileSync(file, JSON.stringify({ ...probe, readyMs, port }, null, 2));
}

async function main() {
  if (!app.requestSingleInstanceLock()) return app.quit();
  await app.whenReady();
  const started = Date.now();
  port = await freePort(loadSavedPort());
  fs.mkdirSync(DATA, { recursive: true });
  fs.writeFileSync(path.join(DATA, "port.txt"), String(port));
  startBackend();
  if (!(await waitForBackend())) {
    dialog.showErrorBox("Open Dots", `The backend did not start. See ${path.join(DATA, "backend.log")}`);
    quitting = true;
    await stopBackend();
    return app.quit();
  }
  const readyMs = Date.now() - started;

  const origin = `http://127.0.0.1:${port}`;
  const win = new BrowserWindow({
    width: 1100,
    height: 800,
    show: !process.env.OD_SPIKE_HIDDEN,
    webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true },
  });
  win.webContents.on("will-navigate", (event, url) => {
    if (!url.startsWith(origin)) event.preventDefault();
  });
  win.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  await win.loadURL(`${origin}/`);
  await report(win, readyMs);
}

app.on("window-all-closed", () => app.quit());
app.on("before-quit", (event) => {
  if (quitting) return;
  quitting = true;
  event.preventDefault();
  stopBackend().finally(() => app.quit());
});

main();
