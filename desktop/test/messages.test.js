const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { afterEach, beforeEach, test } = require("node:test");

const { EXIT, lastReason, describeFailure, describeUnexpectedExit } = require("../src/messages");

let dir;
let logFile;
beforeEach(() => {
  dir = fs.mkdtempSync(path.join(os.tmpdir(), "od-messages-"));
  logFile = path.join(dir, "backend.log");
});
afterEach(() => {
  fs.rmSync(dir, { recursive: true, force: true });
});

test("the reason is the backend's last explanatory line", () => {
  fs.writeFileSync(
    logFile,
    "noise\n[desktop] first thing\nmore noise\n[desktop] 1 stored credential(s) cannot be decrypted. Nothing was changed.\ntrailing noise\n",
  );
  assert.equal(lastReason(logFile), "1 stored credential(s) cannot be decrypted. Nothing was changed.");
});

test("with no explanatory line, or no log, there is no reason", () => {
  fs.writeFileSync(logFile, "just noise\n");
  assert.equal(lastReason(logFile), null);
  assert.equal(lastReason(path.join(dir, "missing.log")), null);
});

test("only the end of a large log is read", () => {
  const filler = "x".repeat(200) + "\n";
  fs.writeFileSync(logFile, `[desktop] old reason\n${filler.repeat(500)}[desktop] newest reason\n`);
  assert.equal(lastReason(logFile), "newest reason");
});

test("Windows line endings are understood", () => {
  fs.writeFileSync(logFile, "[desktop] a reason\r\nnext\r\n");
  assert.equal(lastReason(logFile), "a reason");
});

test("each exit code the backend uses has its own message that points at the log", () => {
  const titles = new Set();
  for (const code of [EXIT.HANDSHAKE, EXIT.KEY_MISMATCH, EXIT.DATABASE, EXIT.UI_MISSING, 1, null]) {
    const message = describeFailure({ code, logFile, reason: "the reason" });
    assert.ok(message.title && message.body);
    assert.ok(message.body.includes(logFile), `log location for ${code}`);
    assert.ok(message.body.includes("the reason"));
    titles.add(message.title);
  }
  assert.ok(titles.size >= 4, "the specific codes read differently from the generic one");
});

test("the missing-page message says which command builds it", () => {
  assert.match(describeFailure({ code: EXIT.UI_MISSING, logFile }).body, /npm run build/);
});

test("the key message says nothing was changed", () => {
  assert.match(describeFailure({ code: EXIT.KEY_MISMATCH, logFile }).body, /without changing anything/);
});

test("a backend that could not be run is not described as one that stopped", () => {
  const message = describeFailure({ kind: "spawn", code: null, logFile, reason: "spawn /app/backend/python EACCES" });
  assert.equal(message.title, "Open Duet could not start its backend");
  assert.doesNotMatch(message.body, /stopped/);
  assert.match(message.body, /could not be run/);
  assert.ok(message.body.includes("spawn /app/backend/python EACCES") && message.body.includes(logFile));
});

test("an unexpected exit says what happened to a running answer", () => {
  const message = describeUnexpectedExit({ code: 1, logFile, reason: "boom" });
  assert.match(message.body, /interrupted and will not be repeated/);
  assert.ok(message.body.includes(logFile) && message.body.includes("boom") && message.body.includes("exit 1"));
});

test("the exit codes are the ones the backend uses", () => {
  const source = fs.readFileSync(path.join(__dirname, "..", "..", "backend", "app", "desktop", "entry.py"), "utf8");
  const python = Object.fromEntries([...source.matchAll(/^EXIT_([A-Z_]+) = (\d+)$/gm)].map(([, name, value]) => [name, Number(value)]));
  assert.deepEqual(python, {
    HANDSHAKE: EXIT.HANDSHAKE,
    KEY_MISMATCH: EXIT.KEY_MISMATCH,
    DATABASE: EXIT.DATABASE,
    UI_MISSING: EXIT.UI_MISSING,
  });
});
