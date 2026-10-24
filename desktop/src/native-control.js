// Only Electron main owns this service. The renderer gets status and owner
// controls; the backend gets a separate capability over its launch handshake.
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const http = require("node:http");
const { pathToFileURL } = require("node:url");
const { spawn } = require("node:child_process");
const { createInterface } = require("node:readline");
const { sameOrigin } = require("./origin");

const READS = new Set(["get_desktop_state", "get_window_state", "list_apps", "list_windows"]);
const INPUTS = new Set(["click", "type_text", "press_key", "scroll"]);
const ALLOWED = new Set([...READS, ...INPUTS]);
const fail = () => { throw new Error("Native desktop unavailable. Check permissions, driver preparation and ownership in the desktop app."); };
const token = () => crypto.randomBytes(32).toString("hex");
function equal(a, b) {
  return typeof a === "string" && typeof b === "string" && a.length === b.length && crypto.timingSafeEqual(Buffer.from(a), Buffer.from(b));
}
function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value && typeof value === "object") return `{${Object.keys(value).sort().map(k => `${JSON.stringify(k)}:${canonical(value[k])}`).join(",")}}`;
  return JSON.stringify(value);
}
function consume(folder, name, args, generation) {
  const prefix = crypto.createHash("sha256").update(`${name}\n${canonical(args)}`).digest("hex") + "-";
  for (const file of fs.readdirSync(folder).sort()) {
    if (generation === undefined ? !file.startsWith(prefix) : !/^[a-f0-9]{64}-[a-f0-9]{16}$/.test(file)) continue;
    const full = path.join(folder, file);
    try {
      const stat = fs.lstatSync(full);
      if (!stat.isFile()) continue;
      const record = generation === undefined ? null : JSON.parse(fs.readFileSync(full, "utf8"));
      if (record && (record.tool !== name || canonical(record.arguments) !== canonical(args))) continue;
      fs.unlinkSync(full);
      if (Date.now() - stat.mtimeMs <= 15 * 60_000 && record?.generation === generation) return true;
    } catch { /* another call spent it */ }
  }
  return false;
}
function verifyBundle(root, pin) {
  root = fs.realpathSync(root);
  if (pin.platform !== process.platform || pin.arch !== process.arch || !pin.manifestSha256) fail();
  const manifest = fs.readFileSync(path.join(root, "manifest.json"));
  if (crypto.createHash("sha256").update(manifest).digest("hex") !== pin.manifestSha256) fail();
  const files = JSON.parse(manifest);
  const actual = [];
  function walk(folder) {
    for (const entry of fs.readdirSync(folder, { withFileTypes: true })) {
      const full = path.join(folder, entry.name);
      if (entry.isDirectory()) walk(full);
      else if (entry.isFile()) actual.push(path.relative(root, full));
      else fail();
    }
  }
  walk(root);
  if (actual.filter(n => n !== "manifest.json").sort().join("\n") !== Object.keys(files).sort().join("\n")) fail();
  for (const [name, hash] of Object.entries(files)) {
    const full = path.resolve(root, name);
    if (!full.startsWith(path.resolve(root) + path.sep) || fs.realpathSync(full) !== full || !fs.lstatSync(full).isFile()) fail();
    if (crypto.createHash("sha256").update(fs.readFileSync(full)).digest("hex") !== hash) fail();
  }
  for (const required of ["driver", "sdk.mjs", "NOTICES.txt", "sbom.json"]) if (!files[required]) fail();
}

// Reject unknown/path-bearing fields even if a later driver adds them. Schemas
// come from the pinned executable, but never grant its full tool surface.
const FIELDS = new Set(["pid", "window_id", "element_token", "element_index", "snapshot_id", "x", "y", "text", "key", "direction", "amount", "by", "button", "count", "modifier", "modifiers", "delivery_mode", "action", "scope", "include_screenshot", "include_accessibility_tree", "max_depth", "max_dimension", "max_elements", "on_screen_only"]);
function checkedArgs(schema, args) {
  if (!args || Array.isArray(args) || typeof args !== "object" || JSON.stringify(args).length > 20_000) fail();
  for (const name of schema.required || []) if (!(name in args)) fail();
  for (const [name, value] of Object.entries(args)) {
    const spec = schema.properties?.[name];
    if (!FIELDS.has(name) || !spec) fail();
    if (spec.type === "string" && (typeof value !== "string" || value.length > (name === "text" ? 4000 : 256))) fail();
    if (spec.type === "boolean" && typeof value !== "boolean") fail();
    if (["integer", "number"].includes(spec.type) && (typeof value !== "number" || !Number.isFinite(value) || Math.abs(value) > 10_000_000 || (spec.type === "integer" && !Number.isSafeInteger(value)))) fail();
    if (spec.type === "array" && (!Array.isArray(value) || value.length > 8 || value.some(v => typeof v !== "string" || v.length > 64))) fail();
    if (!["string", "boolean", "integer", "number", "array"].includes(spec.type)) fail();
    if (spec.enum && !spec.enum.includes(value)) fail();
    if (spec.minimum !== undefined && value < spec.minimum) fail();
    if (spec.maximum !== undefined && value > spec.maximum) fail();
    if (spec.pattern && !new RegExp(spec.pattern).test(value)) fail();
    if (["x", "y"].includes(name) && (value < 0 || value > 32768)) fail();
    if (name === "count" && (value < 1 || value > 3)) fail();
    if (name === "amount" && (value < 1 || value > 100)) fail();
    if (name === "max_depth" && (value < 1 || value > 20)) fail();
    if (name === "max_elements" && (value < 1 || value > 2000)) fail();
    if (name === "max_dimension" && (value < 1 || value > 2048)) fail();
  }
}

class DriverRpc {
  constructor(connection) {
    this.next = 0;
    this.pending = new Map();
    this.child = spawn(connection.mcp.command, connection.mcp.args, { env: { PATH: process.env.PATH, ...Object.fromEntries((connection.mcp.environment || []).map(v => [v.name, v.value])) }, stdio: ["pipe", "pipe", "ignore"] });
    this.child.stdin.on("error", () => this.close());
    this.lines = createInterface({ input: this.child.stdout });
    this.lines.on("line", line => {
      if (line.length > 16 * 1024 * 1024) return this.close();
      try {
        const msg = JSON.parse(line), pending = this.pending.get(msg.id);
        if (!pending) return;
        this.pending.delete(msg.id);
        clearTimeout(pending.timer);
        msg.error ? pending.reject(new Error("Native driver rejected the request.")) : pending.resolve(msg.result);
      } catch { this.close(); }
    });
    this.child.once("error", () => this.close());
    this.child.once("exit", () => this.close());
  }
  call(method, params = {}) {
    if (this.closed) return Promise.reject(new Error("Native driver disconnected."));
    const id = ++this.next;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { this.close(); reject(new Error("Native driver timed out; action was not retried.")); }, 20_000);
      this.pending.set(id, { resolve, reject, timer });
      this.child.stdin.write(JSON.stringify({ jsonrpc: "2.0", id, method, params }) + "\n");
    });
  }
  close() {
    if (this.closed) return;
    this.closed = true;
    this.child.kill();
    this.lines.close();
    for (const p of this.pending.values()) { clearTimeout(p.timer); p.reject(new Error("Native driver disconnected; action was not retried.")); }
    this.pending.clear();
  }
}

class NativeControl {
  constructor({ root, pin, userData, permissions, loadSdk = url => import(url), rpc = c => new DriverRpc(c), platform = process.platform, available = true }) {
    fs.mkdirSync(userData, { recursive: true, mode: 0o700 });
    userData = fs.realpathSync(userData);
    Object.assign(this, { root, pin, userData, permissions, loadSdk, rpc, platform, available });
    this.enabled = new Set();
    if (available) {
      try { for (const id of JSON.parse(fs.readFileSync(path.join(userData, "native-agents.json")))) if (Number.isSafeInteger(id) && id > 0) this.enabled.add(id); } catch { /* disabled on first launch */ }
    } else {
      fs.writeFileSync(path.join(userData, "native-agents.json"), "[]", { mode: 0o600 });
    }
    this.held = false;
    this.owner = null;
    this.generation = 0;
    this.busy = false;
    this.admin = token();
  }
  status(agent) {
    const permitted = this.platform === "darwin" && this.permissions(false);
    return { supported: this.platform === "darwin" && this.available, enabled: this.enabled.has(agent), held: this.held, busy: !!this.owner, ready: permitted && !!this.driver && !this.driver.closed, reason: this.reason || (this.enabled.has(agent) && !permitted ? "Grant Accessibility and Screen Recording to Open Duet in System Settings, then retry." : null) };
  }
  async configure(agent, enabled) {
    if (!Number.isSafeInteger(agent) || agent < 1 || typeof enabled !== "boolean" || this.platform !== "darwin" || !this.available) fail();
    if (enabled) {
      await this.start(true);
      this.enabled.add(agent);
    } else {
      this.enabled.delete(agent);
      if (this.owner?.agent === agent) { this.owner = null; await this.stop(); }
      else if (!this.enabled.size) await this.stop();
    }
    fs.mkdirSync(this.userData, { recursive: true, mode: 0o700 });
    const file = path.join(this.userData, "native-agents.json");
    fs.writeFileSync(file + ".tmp", JSON.stringify([...this.enabled]), { mode: 0o600 });
    fs.renameSync(file + ".tmp", file);
    return this.status(agent);
  }
  async start(prompt = false) {
    if (this.closing || this.held || this.platform !== "darwin" || !this.available) fail();
    if (this.starting) return this.starting;
    this.starting = (async () => {
      const generation = this.generation;
      verifyBundle(this.root, this.pin);
      const sdk = await this.loadSdk(pathToFileURL(path.join(this.root, "sdk.mjs")).href);
      if (this.closing || this.held || generation !== this.generation) fail();
      if (prompt) sdk.requestPermissions();
      if (!this.permissions(false)) { await this.stop(); throw new Error("Grant Accessibility and Screen Recording to Open Duet in System Settings, then retry."); }
      if (this.driver && !this.driver.closed) return;
      if (this.host) await this.stop();
      const hostGeneration = this.generation;
      this.host = sdk.createHost(path.join(this.root, "driver"), "app.opendots.desktop");
      const host = this.host;
      try {
        const connection = await host.start();
        if (hostGeneration !== this.generation || this.held) fail();
        const driver = this.rpc(connection);
        this.driver = driver;
        await driver.call("initialize", { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "open-duet-native", version: "1" } });
        driver.child?.stdin.write(JSON.stringify({ jsonrpc: "2.0", method: "notifications/initialized" }) + "\n");
        const listing = await driver.call("tools/list");
        this.tools = (listing.tools || []).filter(t => ALLOWED.has(t.name)).map(t => ({ ...t, name: "native_" + t.name, inputSchema: { ...t.inputSchema, additionalProperties: false, properties: Object.fromEntries(Object.entries(t.inputSchema.properties || {}).filter(([key]) => FIELDS.has(key))) } }));
        if (![...ALLOWED].every(name => this.tools.some(t => t.name === "native_" + name))) fail();
        this.reason = null;
      } catch {
        this.driver?.close(); this.driver = null;
        await host.stop().catch(() => {});
        this.host = null;
        fail();
      }
    })();
    try { return await this.starting; } catch (e) { this.reason = e.message; throw e; } finally { this.starting = null; }
  }
  async stop() {
    this.generation++;
    this.driver?.close(); this.driver = null;
    const host = this.host; this.host = null;
    if (host) await host.stop();
    if (this.owner) this.owner.observed = false;
  }
  async takeControl(held) {
    if (typeof held !== "boolean") fail();
    this.held = held;
    // Stop the daemon, not just its proxy: pending input must not outlive takeover.
    if (held) await this.stop();
    else if (this.enabled.size) await this.start();
    return { held: this.held };
  }
  async adminCall(body) {
    const { agent, turn, grants } = body;
    if (body.method === "selected") return { selected: this.enabled.has(agent) };
    if (body.method === "release") {
      if (this.owner?.agent === agent && this.owner?.turn === turn) { this.owner = null; await this.stop(); }
      return {};
    }
    if (body.method !== "acquire" || !this.enabled.has(agent) || !Number.isSafeInteger(turn) || turn < 1 || this.owner) fail();
    const expected = path.join(this.userData, "data", "computers", "grants") + path.sep;
    if (typeof grants !== "string" || !path.resolve(grants).startsWith(expected) || fs.realpathSync(grants) !== grants) fail();
    this.owner = { agent, turn, grants, capability: token(), observed: false };
    try { await this.start(); } catch (e) { this.owner = null; throw e; }
    return { capability: this.owner.capability };
  }
  async call(capability, body) {
    const owner = this.owner;
    if (!owner || !equal(capability, owner.capability) || this.held || this.busy || !this.permissions(false) || !this.driver || this.driver.closed) fail();
    if (body.method === "epoch") return { generation: this.generation };
    if (body.method === "tools/list") return { tools: this.tools };
    if (body.method !== "tools/call") fail();
    const tool = this.tools.find(t => t.name === body.name);
    if (!tool) fail();
    const args = body.arguments;
    checkedArgs(tool.inputSchema, args);
    const name = body.name.slice(7);
    if (!consume(owner.grants, body.name, args, this.generation)) throw new Error("This native action has no unused approval for the current desktop state.");
    if (INPUTS.has(name) && !owner.observed) throw new Error("Take a fresh native desktop or window snapshot before input.");
    this.busy = true;
    owner.observed = false; // one input per fresh snapshot, including after failures
    const generation = this.generation;
    try {
      const result = await this.driver.call("tools/call", { name, arguments: args });
      if (generation !== this.generation || this.held || this.owner !== owner) fail();
      if (!result.isError && ["get_desktop_state", "get_window_state"].includes(name)) owner.observed = true;
      if (result.isError) return { isError: true, content: [{ type: "text", text: "Native desktop action failed. Observe the current screen before trying again." }] };
      return result;
    } finally { this.busy = false; }
  }
  async listen() {
    this.server = http.createServer(async (req, res) => {
      res.setHeader("Content-Type", "application/json");
      try {
        if (req.method !== "POST" || req.url !== "/" || req.headers.origin) fail();
        const capability = req.headers.authorization?.replace(/^Bearer /, "");
        if (!equal(capability, this.admin) && !equal(capability, this.owner?.capability)) fail();
        let data = "";
        for await (const chunk of req) { data += chunk; if (data.length > 64_000) fail(); }
        const body = JSON.parse(data);
        const result = equal(capability, this.admin) ? await this.adminCall(body) : await this.call(capability, body);
        res.end(JSON.stringify(result));
      } catch { res.statusCode = 409; res.end(JSON.stringify({ error: "Native desktop unavailable or action refused. Check its permissions, approval and control state in the desktop app." })); }
    });
    this.server.requestTimeout = 25_000;
    await new Promise((resolve, reject) => { this.server.once("error", reject); this.server.listen(0, "127.0.0.1", resolve); });
    return { port: this.server.address().port, token: this.admin };
  }
  async close() {
    this.closing = true;
    this.admin = token(); this.owner = null;
    this.server?.closeAllConnections();
    this.server?.close();
    await this.stop();
  }
}

function registerNativeControl({ ipcMain, dialog, service, getWindow, getOrigin }) {
  let changing = false;
  ipcMain.handle("open-dots:native-control", async (event, request) => {
    const window = getWindow();
    if (!window || event.sender !== window.webContents || event.senderFrame !== window.webContents.mainFrame || !sameOrigin(event.senderFrame?.url, getOrigin())) fail();
    if (!["/chat", "/chat/"].includes(new URL(event.senderFrame.url).pathname)) fail();
    const { method, agent } = request || {};
    if (!Number.isSafeInteger(agent) || agent < 1) fail();
    if (method === "status") return service.status(agent);
    if (changing) fail();
    changing = true;
    try {
      if (method === "enable") {
        const answer = await dialog.showMessageBox(window, { type: "warning", title: "Allow native desktop control?", message: `Allow agent ${agent} to see and control this Mac?`, detail: "This includes apps outside its workspace. Screen input uses your approval mode. You can take control or disable access here at any time.", buttons: ["Cancel", "Enable for this agent"], defaultId: 0, cancelId: 0 });
        if (answer.response !== 1) return service.status(agent);
        return await service.configure(agent, true);
      }
      if (method === "disable") return await service.configure(agent, false);
      if (method === "take") return await service.takeControl(true);
      if (method === "release") return await service.takeControl(false);
      fail();
    } finally { changing = false; }
  });
}
module.exports = { NativeControl, DriverRpc, verifyBundle, checkedArgs, consume, canonical, registerNativeControl };
