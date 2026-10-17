const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { afterEach, beforeEach, test } = require("node:test");
const { diagnose, findCli } = require("../src/doctor");
const { layout } = require("../src/paths");

let root;
let paths;
beforeEach(() => {
  root = fs.mkdtempSync(path.join(os.tmpdir(), "od-doctor-"));
  paths = layout(root);
});
afterEach(() => fs.rmSync(root, { recursive: true, force: true }));

function write(file, content = "x") {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, content);
}
function ready() {
  const home = path.join(paths.desktop, "node_modules", "electron");
  write(path.join(home, "path.txt"), "electron");
  write(path.join(home, "dist", "electron"));
  write(paths.python);
  const page = path.join(paths.ui, "index.html");
  write(page);
  const future = (Date.now() + 60_000) / 1000;
  fs.utimesSync(page, future, future);
}

test("a fresh checkout reports missing prerequisites with actionable fixes", () => {
  const result = diagnose({ layout: paths, nodeVersion: "v20.0.0", env: {} });
  assert.equal(result.ok, false);
  assert.equal(result.problems.length, 4);
  assert.ok(result.problems.some((item) => item.what.includes("Node.js 22")));
  assert.ok(result.problems.some((item) => item.fix === "npm run setup"));
  assert.ok(result.problems.some((item) => item.fix === "npm run build"));
});

test("absence of a CLI does not prevent configuring an API engine in a ready app", () => {
  ready();
  const result = diagnose({ layout: paths, nodeVersion: "v24.0.0", env: {} });
  assert.equal(result.ok, true);
  assert.ok(result.engines.every((engine) => !engine.installed && engine.signedIn === null));
  assert.ok(result.warnings.some((item) => item.fix.includes("API endpoint")));
  assert.equal(result.signInCheck, "not_run");
});

test("finding a CLI does not execute it or expose credential environment values", () => {
  ready();
  const bin = path.join(root, "bin");
  const executable = path.join(bin, "claude");
  const marker = path.join(root, "ran");
  write(executable, `#!/bin/sh\ntouch '${marker}'\n`);
  fs.chmodSync(executable, 0o755);
  const result = diagnose({ layout: paths, nodeVersion: "v24.0.0", platform: "darwin", env: { PATH: bin, TOKEN_ENCRYPTION_KEYS: "DOCTOR_TEST_SECRET", CLAUDE_CODE_OAUTH_TOKEN: "DOCTOR_TEST_SECRET" } });
  assert.equal(result.engines[0].executable, executable);
  assert.equal(result.engines[0].installed, true);
  assert.equal(fs.existsSync(marker), false);
  assert.ok(!JSON.stringify(result).includes("DOCTOR_TEST_SECRET"));
});

test("Unix lookup skips directories and files that cannot be executed", { skip: process.platform === "win32" }, () => {
  const first = path.join(root, "first");
  const second = path.join(root, "second");
  write(path.join(first, "claude"));
  fs.chmodSync(path.join(first, "claude"), 0o600);
  write(path.join(second, "claude"));
  fs.chmodSync(path.join(second, "claude"), 0o755);
  fs.mkdirSync(path.join(first, "codex"));
  assert.equal(findCli("claude", { platform: "linux", env: { PATH: `${first}:${second}` } }), path.join(second, "claude"));
  assert.equal(findCli("codex", { platform: "linux", env: { PATH: first } }), null);
});

test("Windows lookup accepts case-insensitive environment names and npm command shims", () => {
  const executable = "C:\\Tools\\claude.cmd";
  const fsImpl = {
    statSync(candidate) {
      if (candidate !== executable) throw new Error("absent");
      return { isFile: () => true };
    },
    accessSync(candidate, mode) { assert.equal(candidate, executable); assert.equal(mode, fs.constants.F_OK); },
  };
  assert.equal(findCli("claude", { platform: "win32", env: { Path: "C:\\Other;C:\\Tools", PathExt: ".EXE;.CMD" }, fsImpl }), executable);
});
