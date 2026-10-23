const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const crypto = require("node:crypto");
const { NativeControl, verifyBundle, checkedArgs, canonical, registerNativeControl } = require("../src/native-control");

function fixture(t) {
  const userData = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "native-control-test-")));
  const root = path.join(userData, "bundle");
  fs.mkdirSync(root);
  const files = {};
  for (const name of ["driver", "sdk.mjs", "NOTICES.txt", "sbom.json"]) {
    fs.writeFileSync(path.join(root, name), name);
    files[name] = crypto.createHash("sha256").update(name).digest("hex");
  }
  const manifest = JSON.stringify(files);
  fs.writeFileSync(path.join(root, "manifest.json"), manifest);
  const pin = { platform: process.platform, arch: process.arch, manifestSha256: crypto.createHash("sha256").update(manifest).digest("hex") };
  const grants = path.join(userData, "data/computers/grants/test");
  fs.mkdirSync(grants, { recursive: true });
  const names = ["get_desktop_state", "get_window_state", "list_apps", "list_windows", "click", "type_text", "press_key", "scroll"];
  const calls = [];
  let permitted = true, stops = 0;
  const service = new NativeControl({ root, pin, userData, platform: "darwin", permissions: () => permitted,
    loadSdk: async () => ({ requestPermissions() {}, createHost: () => ({ start: async () => ({}), stop: async () => { stops++; } }) }),
    rpc: () => ({ closed: false, close() { this.closed = true; }, async call(method, args) {
      if (method === "tools/list") return { tools: names.concat("exec", "read_file").map(name => ({ name, inputSchema: { type: "object", properties: { text: { type: "string" }, x: { type: "number" }, screenshot_out_file: { type: "string" } } } })) };
      calls.push({ method, args });
      return { content: [{ type: "text", text: "private screen text" }] };
    } }),
  });
  t.after(async () => { await service.close(); fs.rmSync(userData, { recursive: true, force: true }); });
  async function acquire(agent = 1, turn = 1) {
    await service.configure(agent, true);
    return (await service.adminCall({ method: "acquire", agent, turn, grants })).capability;
  }
  function approve(name, args = {}, generation = service.generation) {
    // Native permits bind parsed JSON values, including integral floats, and
    // the approval's original desktop epoch. The filename is not authority.
    const file = path.join(grants, `${crypto.randomBytes(32).toString("hex")}-${crypto.randomBytes(8).toString("hex")}`);
    fs.writeFileSync(file, JSON.stringify({ tool: name, arguments: args, generation }));
    return file;
  }
  return { service, root, pin, calls, grants, userData, acquire, approve, permissions: value => { permitted = value; }, stops: () => stops };
}

test("integrity rejects changed, added and symlinked resources and missing pins", t => {
  const f = fixture(t);
  verifyBundle(f.root, f.pin);
  assert.throws(() => verifyBundle(f.root, { ...f.pin, arch: "other" }));
  fs.writeFileSync(path.join(f.root, "driver"), "modified");
  assert.throws(() => verifyBundle(f.root, f.pin));
  fs.writeFileSync(path.join(f.root, "driver"), "driver");
  fs.writeFileSync(path.join(f.root, "extra"), "unexpected");
  assert.throws(() => verifyBundle(f.root, f.pin));
  fs.unlinkSync(path.join(f.root, "extra"));
  fs.renameSync(path.join(f.root, "driver"), path.join(f.userData, "moved"));
  fs.symlinkSync(path.join(f.userData, "moved"), path.join(f.root, "driver"));
  assert.throws(() => verifyBundle(f.root, f.pin));
});

test("native provider is opt-in, macOS-only and exposes no shell/file/path tools", async t => {
  const f = fixture(t);
  assert.equal(f.service.status(1).enabled, false);
  await assert.rejects(f.service.adminCall({ method: "acquire", agent: 1, turn: 1, grants: f.grants }));
  const cap = await f.acquire();
  const tools = (await f.service.call(cap, { method: "tools/list" })).tools;
  assert.equal(tools.length, 8);
  assert.ok(tools.every(tool => !tool.name.includes("exec") && !tool.name.includes("file") && !tool.inputSchema.properties.screenshot_out_file));
  f.service.platform = "linux";
  await assert.rejects(f.service.configure(2, true));
});

test("fresh observation, exact arguments and one-use approval are required for every input", async t => {
  const f = fixture(t), cap = await f.acquire();
  const call = (name, args = {}) => f.service.call(cap, { method: "tools/call", name, arguments: args });
  await assert.rejects(call("native_get_desktop_state"));
  f.approve("native_click", { x: 1 });
  await assert.rejects(call("native_click", { x: 1 }), /snapshot/);
  f.approve("native_get_desktop_state");
  await call("native_get_desktop_state");
  f.approve("native_click", { x: 1.0 });
  await assert.rejects(call("native_click", { x: 2 }));
  await call("native_click", { x: 1 });
  await assert.rejects(call("native_click", { x: 1 }));
  assert.equal(f.calls.filter(c => c.args?.name === "click").length, 1);
});

test("takeover stops the daemon and invalidates approvals answered before or after hand-back", async t => {
  const f = fixture(t), cap = await f.acquire(), oldEpoch = f.service.generation;
  f.approve("native_get_desktop_state");
  await f.service.call(cap, { method: "tools/call", name: "native_get_desktop_state", arguments: {} });
  f.approve("native_click", { x: 1 });
  await f.service.takeControl(true);
  assert.ok(f.stops() > 0);
  await assert.rejects(f.service.call(cap, { method: "tools/list" }));
  await f.service.takeControl(false);
  f.approve("native_get_desktop_state");
  await f.service.call(cap, { method: "tools/call", name: "native_get_desktop_state", arguments: {} });
  f.approve("native_click", { x: 2 }, oldEpoch); // pending card answered late
  await assert.rejects(f.service.call(cap, { method: "tools/call", name: "native_click", arguments: { x: 1 } }));
  await assert.rejects(f.service.call(cap, { method: "tools/call", name: "native_click", arguments: { x: 2 } }));
});

test("two agents cannot share a lease, foreign releases and old capabilities cannot act", async t => {
  const f = fixture(t), cap = await f.acquire();
  await f.service.configure(2, true);
  await assert.rejects(f.service.adminCall({ method: "acquire", agent: 2, turn: 2, grants: f.grants }));
  await f.service.adminCall({ method: "release", agent: 2, turn: 2 });
  assert.equal(f.service.owner.agent, 1);
  await f.service.adminCall({ method: "release", agent: 1, turn: 1 });
  await assert.rejects(f.service.call(cap, { method: "tools/list" }));
  const newer = await f.service.adminCall({ method: "acquire", agent: 2, turn: 2, grants: f.grants });
  assert.notEqual(newer.capability, cap);
});

test("permissions revoked, disabled agents and disconnected drivers fail closed", async t => {
  const f = fixture(t), cap = await f.acquire();
  f.permissions(false);
  assert.equal(f.service.status(1).ready, false);
  assert.match(f.service.status(1).reason, /Screen Recording/);
  await assert.rejects(f.service.call(cap, { method: "tools/list" }));
  f.permissions(true);
  f.service.driver.close();
  await assert.rejects(f.service.call(cap, { method: "tools/list" }));
  await f.service.configure(1, false);
  await assert.rejects(f.service.call(cap, { method: "tools/list" }));
});

test("application shutdown during SDK loading cannot start an orphan driver", async t => {
  const f = fixture(t);
  let finish;
  f.service.loadSdk = () => new Promise(resolve => { finish = resolve; });
  const starting = f.service.start();
  await f.service.close();
  finish({ createHost: () => assert.fail("driver started after shutdown") });
  await assert.rejects(starting);
});

test("unknown arguments, output paths, excessive typing, coordinates and action counts are rejected", () => {
  const schema = { properties: { text: { type: "string" }, x: { type: "number" }, count: { type: "integer" }, screenshot_out_file: { type: "string" } } };
  for (const args of [{ text: "a".repeat(4001) }, { x: -1 }, { x: Infinity }, { count: 4 }, { count: 1.5 }, { screenshot_out_file: "/tmp/leak" }, { session: "other" }, []]) assert.throws(() => checkedArgs(schema, args));
  checkedArgs(schema, { text: "hello", x: 123.5, count: 1 });
  assert.equal(canonical({ z: 1, a: "é" }), '{"a":"é","z":1}');
});

test("owner IPC rejects foreign windows, subframes and remote origins, and enable needs native confirmation", async t => {
  const f = fixture(t);
  const mainFrame = { url: "http://127.0.0.1:1234/chat/" };
  const window = { webContents: { mainFrame } };
  const event = { sender: window.webContents, senderFrame: mainFrame };
  let handler;
  registerNativeControl({ ipcMain: { handle: (_, h) => { handler = h; } }, dialog: { showMessageBox: async () => ({ response: 0 }) }, service: f.service, getWindow: () => window, getOrigin: () => "http://127.0.0.1:1234" });
  for (const foreign of [{ ...event, sender: {} }, { ...event, senderFrame: { ...mainFrame } }]) await assert.rejects(handler(foreign, { method: "enable", agent: 1 }));
  await handler(event, { method: "enable", agent: 1 });
  assert.equal(f.service.status(1).enabled, false);
  mainFrame.url = "https://remote.example/chat/";
  await assert.rejects(handler(event, { method: "status", agent: 1 }));
});

test("real loopback bridge rejects unauthenticated, browser-origin and replayed turn requests", async t => {
  const f = fixture(t), connection = await f.service.listen(), cap = await f.acquire();
  const url = `http://127.0.0.1:${connection.port}/`;
  const send = (key, body, headers = {}) => fetch(url, { method: "POST", headers: { Authorization: "Bearer " + key, ...headers }, body: JSON.stringify(body) });
  assert.equal((await send("wrong", { method: "selected", agent: 1 })).status, 409);
  assert.equal((await send(connection.token, { method: "selected", agent: 1 }, { Origin: "http://127.0.0.1" })).status, 409);
  assert.deepEqual(await (await send(connection.token, { method: "selected", agent: 1 })).json(), { selected: true });
  assert.equal((await send(cap, { method: "acquire", agent: 2 })).status, 409);
  await f.service.adminCall({ method: "release", agent: 1, turn: 1 });
  assert.equal((await send(cap, { method: "tools/list" })).status, 409);
});
