const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { afterEach, beforeEach, test } = require("node:test");

const { createKeyStore, KeyStoreError, looksLikeFernetKey, newFernetKey, FILE_NAME } = require("../src/keystore");

// Stands in for Electron's safeStorage: "encrypts" by scrambling, so a test can tell
// the key was passed through it and is not on disk as plain text.
function fakeSafeStorage({ available = true, backend = "gnome_libsecret", failDecrypt = false, withoutBackendApi = false } = {}) {
  return {
    isEncryptionAvailable: () => available,
    getSelectedStorageBackend: withoutBackendApi ? undefined : () => backend,
    encryptString: (text) => Buffer.from(Buffer.from(text, "utf8").reverse()),
    decryptString: (buffer) => {
      if (failDecrypt) throw new Error("cannot decrypt");
      return Buffer.from(buffer).reverse().toString("utf8");
    },
  };
}

let dir;
beforeEach(() => {
  dir = fs.mkdtempSync(path.join(os.tmpdir(), "od-keystore-"));
});
afterEach(() => {
  fs.rmSync(dir, { recursive: true, force: true });
});

const store = (options = {}) =>
  createKeyStore({ dir: path.join(dir, "secrets"), safeStorage: fakeSafeStorage(options.safe), platform: "win32", ...options.store });
const keyFile = () => path.join(dir, "secrets", FILE_NAME);

test("a new key is a 32-byte Fernet key in URL-safe base64 with padding", () => {
  for (let n = 0; n < 50; n += 1) {
    const key = newFernetKey();
    assert.equal(key.length, 44);
    assert.match(key, /^[A-Za-z0-9_-]{43}=$/);
    assert.equal(Buffer.from(key, "base64url").length, 32);
    assert.ok(looksLikeFernetKey(key));
  }
  assert.notEqual(newFernetKey(), newFernetKey());
});

test("the first call makes and stores the key, the second returns the same one", () => {
  const first = store().ensure();
  assert.equal(first.created, true);
  assert.ok(looksLikeFernetKey(first.key));
  const second = store().ensure();
  assert.deepEqual(second, { key: first.key, created: false });
});

test("the key is never on disk as plain text, and nothing else is left behind", () => {
  const { key } = store().ensure();
  const stored = fs.readFileSync(keyFile());
  assert.ok(!stored.includes(Buffer.from(key)));
  assert.ok(!stored.toString("latin1").includes(key));
  assert.deepEqual(fs.readdirSync(path.join(dir, "secrets")), [FILE_NAME]);
});

test("the file is private to the owner where the operating system has file modes", { skip: process.platform === "win32" }, () => {
  store().ensure();
  assert.equal(fs.statSync(keyFile()).mode & 0o777, 0o600);
});

test("without secret storage nothing is stored and the person is told", () => {
  assert.throws(() => store({ safe: { available: false } }).ensure(), (error) => error instanceof KeyStoreError && error.kind === "unavailable");
  assert.equal(fs.existsSync(path.join(dir, "secrets")), false);
});

test("on Linux only a named keyring is trusted: basic_text and unknown are refused", () => {
  for (const safe of [{ backend: "basic_text" }, { backend: "unknown" }, { withoutBackendApi: true }]) {
    const linux = store({ safe, store: { platform: "linux" } });
    assert.throws(() => linux.ensure(), (error) => error.kind === "weak", JSON.stringify(safe));
  }
  assert.equal(fs.existsSync(keyFile()), false);
  for (const backend of ["gnome_libsecret", "kwallet5", "kwallet6"]) {
    assert.equal(store({ safe: { backend }, store: { platform: "linux" } }).ensure().created, true, backend);
    fs.rmSync(path.join(dir, "secrets"), { recursive: true, force: true });
  }
});

test("macOS and Windows do not consult the Linux backend name", () => {
  for (const platform of ["win32", "darwin"]) {
    const result = store({ safe: { backend: "basic_text" }, store: { platform } }).ensure();
    assert.equal(result.created, true);
    fs.rmSync(path.join(dir, "secrets"), { recursive: true, force: true });
  }
});

test("a stored key that cannot be decrypted is an error and is never replaced", () => {
  const { key } = store().ensure();
  const before = fs.readFileSync(keyFile());
  assert.throws(() => store({ safe: { failDecrypt: true } }).ensure(), (error) => error.kind === "unreadable");
  assert.deepEqual(fs.readFileSync(keyFile()), before);
  // And with decryption working again, it is the same key.
  assert.equal(store().ensure().key, key);
});

test("a stored key that decrypts to something that is not a key is an error and is never replaced", () => {
  fs.mkdirSync(path.join(dir, "secrets"), { recursive: true });
  const junk = fakeSafeStorage().encryptString("this is not a key");
  fs.writeFileSync(keyFile(), junk);
  assert.throws(() => store().ensure(), (error) => error.kind === "corrupt");
  assert.deepEqual(fs.readFileSync(keyFile()), junk);
});

test("an empty key file is damaged, not missing", () => {
  fs.mkdirSync(path.join(dir, "secrets"), { recursive: true });
  fs.writeFileSync(keyFile(), "");
  assert.throws(() => store().ensure(), (error) => error.kind === "corrupt" || error.kind === "unreadable");
  assert.equal(fs.readFileSync(keyFile()).length, 0);
});

test("a key path that cannot be read is an error, not a reason to start over", () => {
  fs.mkdirSync(keyFile(), { recursive: true }); // a folder where the file should be
  assert.throws(() => store().ensure(), (error) => error.kind === "unreadable");
  assert.ok(fs.statSync(keyFile()).isDirectory());
});

test("a folder that cannot be made is reported as not stored", () => {
  const blocker = path.join(dir, "secrets");
  fs.writeFileSync(blocker, "a file where the folder should be");
  assert.throws(() => store().ensure(), (error) => error.kind === "unreadable" || error.kind === "unwritable");
});

test("two launches racing for the first key end up with one key", () => {
  // The other launch wins between our check and our write: its link appears first.
  const winnerKey = newFernetKey();
  const racing = {
    ...fs,
    linkSync(from, to) {
      fs.writeFileSync(to, fakeSafeStorage().encryptString(winnerKey));
      const error = new Error("exists");
      error.code = "EEXIST";
      throw error;
    },
  };
  const result = store({ store: { fsImpl: racing } }).ensure();
  assert.deepEqual(result, { key: winnerKey, created: false });
  assert.equal(store().ensure().key, winnerKey);
  assert.deepEqual(fs.readdirSync(path.join(dir, "secrets")), [FILE_NAME]);
});

test("a file system without hard links still gets exactly one key", () => {
  const noLinks = {
    ...fs,
    linkSync() {
      const error = new Error("not supported");
      error.code = "EPERM";
      throw error;
    },
  };
  const first = store({ store: { fsImpl: noLinks } }).ensure();
  assert.equal(first.created, true);
  assert.equal(store().ensure().key, first.key);
  assert.deepEqual(fs.readdirSync(path.join(dir, "secrets")), [FILE_NAME]);
});

test("a failing disk leaves no key file and no temporary file", () => {
  const failing = {
    ...fs,
    fsyncSync() {
      const error = new Error("disk full");
      error.code = "ENOSPC";
      throw error;
    },
  };
  assert.throws(() => store({ store: { fsImpl: failing } }).ensure(), (error) => error.kind === "unwritable");
  assert.deepEqual(fs.readdirSync(path.join(dir, "secrets")), []);
});
