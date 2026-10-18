// Starts the real desktop app for a test: the same main.js, the same backend, with
// a data folder of the test's choosing and a DevTools port to look at the window.

const { spawn, spawnSync } = require("node:child_process");
const fs = require("node:fs");
const net = require("node:net");
const path = require("node:path");

const { layout } = require("../src/paths");
const { findElectronBinary } = require("../src/preflight");
const { waitForPage, attach, sleep } = require("./cdp");

const paths = layout();

function freePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      server.close(() => resolve(port));
    });
  });
}

function alive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return error.code === "EPERM";
  }
}

async function waitUntil(predicate, label, timeoutMs = 60_000) {
  const end = Date.now() + timeoutMs;
  while (Date.now() < end) {
    if (await predicate()) return;
    await sleep(250);
  }
  throw new Error(`timed out waiting for ${label}`);
}

// The backend's process id, from the line it logs when it starts serving.
function backendPid(dataDir) {
  try {
    const log = fs.readFileSync(path.join(dataDir, "logs", "backend.log"), "utf8");
    const found = [...log.matchAll(/Serving on http:\/\/127\.0\.0\.1:\d+ \(process (\d+)\)/g)];
    return found.length ? Number(found[found.length - 1][1]) : null;
  } catch {
    return null;
  }
}

// `viaScript` starts it the way `npm run start` / `npm run dev` do (through
// scripts/launch.js, which checks what is needed and starts Electron itself);
// otherwise Electron is started directly.
async function launchApp({ dataDir, env = {}, mode = "start", viaScript = false, entry = paths.desktop } = {}) {
  const binary = findElectronBinary(paths.desktop);
  if (!binary) throw new Error("Electron is not installed. Run: npm run setup");
  const debugPort = await freePort();
  const environment = { ...process.env, OPEN_DOTS_DATA_DIR: dataDir, ...env };
  if (!viaScript) delete environment.ELECTRON_RUN_AS_NODE;
  const flag = `--remote-debugging-port=${debugPort}`;
  const child = viaScript
    ? spawn(process.execPath, [path.join(paths.desktop, "scripts", "launch.js"), mode, flag], {
        env: environment,
        stdio: ["ignore", "pipe", "pipe"],
      })
    : spawn(binary, [entry, `--mode=${mode}`, flag], {
        env: environment,
        stdio: ["ignore", "pipe", "pipe"],
      });
  const output = [];
  child.stdout.on("data", (chunk) => output.push(chunk.toString()));
  child.stderr.on("data", (chunk) => output.push(chunk.toString()));
  let exitCode;
  const exited = new Promise((resolve) => child.once("exit", (code) => ((exitCode = code), resolve(code))));

  const app = {
    child,
    debugPort,
    dataDir,
    output,
    exited,
    get exitCode() {
      return exitCode;
    },
    backendPid: () => backendPid(dataDir),

    // The window's page, once the backend has proved itself and the app is loaded.
    async page({ timeoutMs = 180_000 } = {}) {
      let target;
      try {
        target = await waitForPage(debugPort, "http://127.0.0.1:", timeoutMs);
      } catch (error) {
        throw new Error(`${error.message}\n${output.join("").slice(-3000)}`);
      }
      const page = await attach(target);
      page.origin = new URL(target.url).origin;
      page.url = target.url;
      page.title = target.title;
      return page;
    },

    // Ends the app the way a person does: its window is closed.
    async quit(page) {
      (page ?? (await attach((await waitForPage(debugPort, "http://127.0.0.1:", 10_000))))).evaluate("window.close()").catch(() => {});
      return Promise.race([exited, sleep(30_000).then(() => "timeout")]);
    },

    // Ends only Electron's main process, as a crash or a kill would. Not its children.
    hardKill() {
      spawnSync("taskkill", ["/pid", String(child.pid), "/F"], { windowsHide: true });
      return exited;
    },

    // Ends everything this launch started (for cleaning up after a test).
    destroy() {
      spawnSync("taskkill", ["/pid", String(child.pid), "/T", "/F"], { windowsHide: true });
      const pid = backendPid(dataDir);
      if (pid && alive(pid)) spawnSync("taskkill", ["/pid", String(pid), "/T", "/F"], { windowsHide: true });
    },
  };
  return app;
}

module.exports = { freePort, launchApp, alive, waitUntil, backendPid, paths, sleep };
