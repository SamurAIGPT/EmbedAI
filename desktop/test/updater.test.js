const test = require("node:test");
const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { compareVersions, newestRelease, pickAsset, githubHost, describeError, createUpdater } = require("../src/updater");
const { trustedUpdateRequest, registerUpdates } = require("../src/updates-ipc");

const BYTES = Buffer.from("installer bytes");
const DIGEST = `sha256:${crypto.createHash("sha256").update(BYTES).digest("hex")}`;

function release(tag, overrides = {}) {
  return {
    tag_name: tag,
    html_url: `https://github.com/Anil-matcha/open-duet/releases/tag/${tag}`,
    assets: [
      { name: `Open-Duet-${tag.slice(1)}-arm64.dmg`, browser_download_url: `https://github.com/x/${tag}.dmg`, size: BYTES.length, digest: DIGEST },
      { name: `Open-Duet-Setup-${tag.slice(1)}.exe`, browser_download_url: `https://github.com/x/${tag}.exe`, size: BYTES.length, digest: DIGEST },
    ],
    ...overrides,
  };
}

function jsonResponse(body) {
  return { ok: true, status: 200, json: async () => body };
}

function fileResponse(bytes, url = "https://objects.githubusercontent.com/f") {
  return { ok: true, status: 200, url, headers: new Headers(), body: new Response(bytes).body };
}

function harness({ releases = [release("v0.3.0")], file = fileResponse(BYTES), open = async () => "" } = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "updater-"));
  const states = [];
  const updater = createUpdater({
    currentVersion: "0.2.0",
    platform: "darwin",
    arch: "arm64",
    cacheDir: path.join(root, "cache"),
    downloadsDir: path.join(root, "Downloads"),
    openFile: open,
    onState: (state) => states.push(state.status),
    fetchImpl: async (url) => (url.includes("api.github.com") ? jsonResponse(releases) : file),
  });
  return { root, states, updater };
}

test("versions compare by semver, with pre-releases below releases", () => {
  assert.equal(compareVersions("0.10.0", "0.9.9"), 1);
  assert.equal(compareVersions("v1.0.0", "1.0.0"), 0);
  assert.equal(compareVersions("1.0.0-beta.2", "1.0.0"), -1);
  assert.equal(compareVersions("1.0.0-beta.2", "1.0.0-beta.10"), -1);
  assert.throws(() => compareVersions("latest", "1.0.0"));
});

test("newestRelease skips drafts and non-version tags", () => {
  const picked = newestRelease([release("v0.2.0"), release("v0.4.0", { draft: true }), release("nightly"), release("v0.3.1")]);
  assert.equal(picked.tag_name, "v0.3.1");
  assert.equal(newestRelease([]), null);
  assert.equal(newestRelease(null), null);
});

test("pickAsset matches the platform installer only", () => {
  const r = release("v0.3.0");
  assert.equal(pickAsset(r, "darwin", "arm64").name, "Open-Duet-0.3.0-arm64.dmg");
  assert.equal(pickAsset(r, "win32", "x64").name, "Open-Duet-Setup-0.3.0.exe");
  assert.equal(pickAsset(r, "darwin", "x64"), null);
  assert.equal(pickAsset(r, "linux", "x64"), null);
  const odd = release("v0.3.0", { assets: [{ name: "../Open-Duet-0.3.0-arm64.dmg", browser_download_url: "https://github.com/x" }] });
  assert.equal(pickAsset(odd, "darwin", "arm64"), null);
});

test("only https GitHub hosts are trusted", () => {
  assert.equal(githubHost("https://github.com/a/b"), true);
  assert.equal(githubHost("https://objects.githubusercontent.com/x"), true);
  assert.equal(githubHost("http://github.com/a"), false);
  assert.equal(githubHost("https://github.com.evil.example/a"), false);
  assert.equal(githubHost("not a url"), false);
});

test("a newer release is offered; the same version is not", async () => {
  const newer = harness();
  const state = await newer.updater.check({ manual: true });
  assert.equal(state.status, "available");
  assert.equal(state.version, "0.3.0");
  assert.equal(state.canDownload, true);
  const same = harness({ releases: [release("v0.2.0")] });
  const idle = await same.updater.check({ manual: true });
  assert.equal(idle.status, "idle");
  assert.match(idle.message, /latest/);
});

test("a failed background check stays quiet; a manual one explains", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "updater-"));
  const make = () => createUpdater({ currentVersion: "0.2.0", platform: "darwin", arch: "arm64", cacheDir: root, downloadsDir: root, openFile: async () => "", fetchImpl: async () => { throw Object.assign(new Error("fetch failed"), { cause: { code: "ENOTFOUND" } }); } });
  const quiet = make();
  assert.equal((await quiet.check()).status, "idle");
  const loud = make();
  const state = await loud.check({ manual: true });
  assert.equal(state.status, "error");
  assert.match(state.message, /Could not reach GitHub/);
});

test("download verifies the digest, moves the file to Downloads and install opens it", async () => {
  const opened = [];
  const { root, updater } = harness({ open: async (file) => { opened.push(file); return ""; } });
  await updater.check({ manual: true });
  const downloaded = await updater.download();
  assert.equal(downloaded.status, "downloaded");
  assert.equal(downloaded.verified, true);
  const target = path.join(root, "Downloads", "Open-Duet-0.3.0-arm64.dmg");
  assert.deepEqual(fs.readFileSync(target), BYTES);
  assert.equal(fs.existsSync(path.join(root, "cache", "Open-Duet-0.3.0-arm64.dmg.part")), false);
  const installing = await updater.install();
  assert.equal(installing.status, "installing");
  assert.deepEqual(opened, [target]);
});

test("a digest mismatch deletes the download and reports an integrity failure", async () => {
  const { root, updater } = harness({ file: fileResponse(Buffer.from("tampered bytes!")) });
  await updater.check({ manual: true });
  const state = await updater.download();
  assert.equal(state.status, "error");
  assert.match(state.message, /failed verification/);
  assert.equal(fs.existsSync(path.join(root, "Downloads", "Open-Duet-0.3.0-arm64.dmg")), false);
  assert.equal(fs.readdirSync(path.join(root, "cache")).length, 0);
});

test("a redirect away from GitHub is refused", async () => {
  const { updater } = harness({ file: fileResponse(BYTES, "https://evil.example/f") });
  await updater.check({ manual: true });
  const state = await updater.download();
  assert.equal(state.status, "error");
  assert.match(state.message, /redirected/);
});

test("install cannot run before a download, and a failed open is reported", async () => {
  const early = harness();
  await early.updater.check({ manual: true });
  assert.equal((await early.updater.install()).status, "available");
  const failing = harness({ open: async () => "no application knows how to open it" });
  await failing.updater.check({ manual: true });
  await failing.updater.download();
  const state = await failing.updater.install();
  assert.equal(state.status, "error");
  assert.match(state.message, /no application/);
});

test("a release with no installer for this computer cannot be downloaded", async () => {
  const { updater } = harness({ releases: [release("v0.3.0", { assets: [] })] });
  const state = await updater.check({ manual: true });
  assert.equal(state.status, "available");
  assert.equal(state.canDownload, false);
  assert.equal((await updater.download()).status, "available");
});

test("describeError maps common failures to plain advice", () => {
  assert.match(describeError(Object.assign(new Error("x"), { code: "ENOSPC" })), /disk space/);
  assert.match(describeError(Object.assign(new Error("x"), { code: "EACCES" })), /Downloads folder/);
  assert.match(describeError(new Error("GitHub answered 403")), /limiting/);
});

test("update IPC accepts only the app's own main frame", async () => {
  const webContents = { mainFrame: {} };
  const window = { webContents };
  const good = { sender: webContents, senderFrame: Object.assign(webContents.mainFrame, { url: "http://127.0.0.1:5000/settings/" }) };
  assert.equal(trustedUpdateRequest(good, window, "http://127.0.0.1:5000"), true);
  assert.equal(trustedUpdateRequest({ sender: {}, senderFrame: webContents.mainFrame }, window, "http://127.0.0.1:5000"), false);
  assert.equal(trustedUpdateRequest({ sender: webContents, senderFrame: { url: "http://127.0.0.1:5000/" } }, window, "http://127.0.0.1:5000"), false);
  assert.equal(trustedUpdateRequest(good, null, "http://127.0.0.1:5000"), false);

  let handler;
  const calls = [];
  const updater = { reset() {}, check: async (o) => (calls.push(["check", o]), { status: "idle" }), download: async () => ({}), install: async () => ({}), getState: () => ({ notesUrl: "https://github.com/r" }) };
  registerUpdates({ ipcMain: { handle: (_c, h) => { handler = h; } }, openExternal: async (u) => calls.push(["open", u]), updater, getWindow: () => window, getOrigin: () => "http://127.0.0.1:5000" });
  await handler(good, "check");
  await handler(good, "release-page");
  await assert.rejects(handler(good, "rm -rf"), /Unknown/);
  await assert.rejects(handler({ sender: {}, senderFrame: {} }, "state"), /only available/);
  assert.deepEqual(calls, [["check", { manual: true }], ["open", "https://github.com/r"]]);
});
