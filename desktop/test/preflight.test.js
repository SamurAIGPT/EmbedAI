const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { afterEach, beforeEach, test } = require("node:test");

const { checkLaunch, findElectronBinary, newestSourceTime, nodeIsNewEnough } = require("../src/preflight");
const { layout } = require("../src/paths");

let root;
let paths;
beforeEach(() => {
  root = fs.mkdtempSync(path.join(os.tmpdir(), "od-preflight-"));
  paths = layout(root);
});
afterEach(() => {
  fs.rmSync(root, { recursive: true, force: true });
});

function write(file, content = "x", mtime) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, content);
  if (mtime) fs.utimesSync(file, mtime / 1000, mtime / 1000);
}

// What Electron's own installer leaves: a path.txt next to a dist folder.
function installElectron() {
  const home = path.join(paths.desktop, "node_modules", "electron");
  write(path.join(home, "path.txt"), "electron.exe");
  write(path.join(home, "dist", "electron.exe"));
  return path.join(home, "dist", "electron.exe");
}
function everythingBuilt() {
  installElectron();
  write(paths.python);
  write(path.join(paths.ui, "index.html"), "x", Date.now() + 60_000);
}

test("with everything set up and built there is nothing to say", () => {
  everythingBuilt();
  const result = checkLaunch({ layout: paths });
  assert.deepEqual(result.problems, []);
  assert.deepEqual(result.warnings, []);
  assert.equal(result.electronBinary, path.join(paths.desktop, "node_modules", "electron", "dist", "electron.exe"));
});

test("a fresh checkout is told to run setup and then build, by name", () => {
  const result = checkLaunch({ layout: paths });
  const fixes = result.problems.map((problem) => problem.fix);
  assert.deepEqual(fixes, ["npm run setup", "npm run setup", "npm run build"]);
  assert.ok(result.problems.every((problem) => problem.what.endsWith(".")));
});

test("Electron whose binary was never downloaded counts as not installed, and nothing is downloaded", () => {
  everythingBuilt();
  const home = path.join(paths.desktop, "node_modules", "electron");
  fs.rmSync(path.join(home, "dist"), { recursive: true }); // package installed, binary missing
  assert.deepEqual(checkLaunch({ layout: paths }).problems.map((p) => p.what), ["Electron is not installed."]);
  fs.rmSync(path.join(home, "path.txt")); // the installer never ran at all
  assert.equal(findElectronBinary(paths.desktop), null);
});

test("starting never loads the electron package, which downloads its binary on first use", () => {
  for (const file of [["src", "preflight.js"], ["scripts", "launch.js"]]) {
    const source = fs.readFileSync(path.join(__dirname, "..", ...file), "utf8");
    assert.ok(!/require\(["']electron["']\)/.test(source), file.join("/"));
  }
});

test("a missing Python environment and a missing build are separate problems", () => {
  everythingBuilt();
  fs.rmSync(paths.python);
  assert.deepEqual(checkLaunch({ layout: paths }).problems.map((p) => p.fix), ["npm run setup"]);
  write(paths.python);
  fs.rmSync(paths.ui, { recursive: true });
  assert.deepEqual(checkLaunch({ layout: paths }).problems.map((p) => p.fix), ["npm run build"]);
});

test("a page older than its source is a warning, not a stop", () => {
  everythingBuilt();
  write(path.join(paths.frontend, "components", "chat", "ChatApp.tsx"), "x", Date.now() + 3_600_000);
  const result = checkLaunch({ layout: paths });
  assert.deepEqual(result.problems, []);
  assert.equal(result.warnings.length, 1);
  assert.equal(result.warnings[0].fix, "npm run build");
});

test("only the files the page is built from count towards staleness", () => {
  const future = Date.now() + 3_600_000;
  write(path.join(paths.frontend, "components", "a.test.mjs"), "x", future);
  write(path.join(paths.frontend, "node_modules", "next", "x.js"), "x", future);
  write(path.join(paths.frontend, ".next", "cache.js"), "x", future);
  write(path.join(paths.frontend, "README.md"), "x", future);
  assert.equal(newestSourceTime(paths.frontend), 0);
  write(path.join(paths.frontend, "next.config.ts"), "x", future);
  assert.ok(newestSourceTime(paths.frontend) > 0);
});

test("the Python in the virtualenv is where each platform keeps it", () => {
  assert.match(paths.python, process.platform === "win32" ? /\.venv[\\/]Scripts[\\/]python\.exe$/ : /\.venv\/bin\/python$/);
  assert.equal(paths.backendScript, path.join(root, "backend", "desktop_main.py"));
  assert.equal(paths.ui, path.join(root, "frontend", "out"));
});

test("Node 22 or newer is needed", () => {
  assert.equal(nodeIsNewEnough("v22.0.0"), true);
  assert.equal(nodeIsNewEnough("v24.1.0"), true);
  assert.equal(nodeIsNewEnough("v20.19.0"), false);
  assert.equal(nodeIsNewEnough("nonsense"), false);
});
