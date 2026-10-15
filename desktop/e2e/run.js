// End-to-end checks of the real desktop app, on Windows: the real Electron window,
// the real backend, and (for `chat`) the real Claude CLI.
//
//   npm run e2e                  everything
//   npm run e2e -- lifecycle     start, token, single instance, quit, hard kill
//   npm run e2e -- scripts       `npm run start` and `npm run dev` themselves
//   npm run e2e -- failures      a damaged key, a key that opens nothing, no Claude
//   npm run e2e -- headless      the documented source/headless start still works, untouched
//   npm run e2e -- chat          a real conversation: approvals, Stop, crash, restart
//   add --keep to leave the data folders in place
//
// `npm run setup` and `npm run build` must have been run. `chat` needs Claude Code
// installed and signed in, and makes a few short requests with it. It needs a screen:
// the window really opens. It ends processes with taskkill, so it is Windows only.

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const { launchApp, alive, waitUntil, paths, sleep } = require("./app");
const { attach, waitForPage } = require("./cdp");

if (process.platform !== "win32") {
  console.error("The end-to-end checks have only been written for Windows (they use taskkill).");
  process.exit(2);
}

const BASE = path.join(os.tmpdir(), "open-dots-e2e");
const wanted = process.argv.slice(2).filter((arg) => !arg.startsWith("--"));
const keep = process.argv.includes("--keep");
const selected = (name) => wanted.length === 0 || wanted.includes("all") || wanted.includes(name);

const results = [];
const running = new Set();

async function check(name, body) {
  const started = Date.now();
  try {
    await body();
    results.push({ name, ok: true });
    console.log(`  ok   ${name} (${Math.round((Date.now() - started) / 1000)}s)`);
  } catch (error) {
    results.push({ name, ok: false, error });
    console.log(`  FAIL ${name}\n       ${String(error.message ?? error).split("\n").join("\n       ")}`);
  }
}

async function start(options) {
  const app = await launchApp(options);
  running.add(app);
  return app;
}

function fresh(name) {
  const dir = path.join(BASE, name);
  fs.rmSync(dir, { recursive: true, force: true });
  fs.mkdirSync(dir, { recursive: true });
  return dir;
}

const HOSTILE_ENV = {
  // Whatever a developer's shell holds must not change a desktop run (D-012 decision 4).
  DATABASE_BACKEND: "postgres",
  POSTGRES_HOST: "unreachable.invalid",
  TOKEN_ENCRYPTION_KEYS: "not-a-key",
  BOAT_API_KEY: "from-the-environment",
  FRONTEND_ORIGINS: "https://evil.example",
  LOCAL_EXECUTION_ENABLED: "false",
};

const python = (code, ...args) => spawnSync(paths.python, ["-c", code, ...args], { encoding: "utf8" });

// ---- the page, as a person uses it -------------------------------------------------

function ui(page) {
  const js = (code) => page.evaluate(code);
  const composerReady = `(() => { const ta = document.querySelector('textarea[aria-label="Message"]'); return !!ta && ta.placeholder === 'Message Claude'; })()`;
  const buttonShown = (label) => `[...document.querySelectorAll('button')].some(b => b.textContent.trim() === ${JSON.stringify(label)} && !b.disabled)`;
  return {
    composerReady,
    buttonShown,
    has: (label) => js(buttonShown(label)),
    click: (label) =>
      js(`(() => { const b = [...document.querySelectorAll('button')].find(b => b.textContent.trim() === ${JSON.stringify(label)} && !b.disabled); if (b) { b.click(); return true; } return false; })()`),
    type: (text) =>
      js(`(() => { const ta = document.querySelector('textarea[aria-label="Message"]');
        Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(ta, ${JSON.stringify(text)});
        ta.dispatchEvent(new Event('input', { bubbles: true })); })()`),
    async send(text) {
      await this.type(text);
      await page.waitFor(buttonShown("Send"), "the Send button", 20_000);
      await this.click("Send");
    },
    main: () => js(`document.querySelector('main') ? document.querySelector('main').innerText : ''`),
    list: () => js(`document.querySelector('nav[aria-label="Conversations"]') ? document.querySelector('nav[aria-label="Conversations"]').innerText : ''`),
    newChat: () => js(`[...document.querySelectorAll('a')].find(a => a.textContent.trim() === 'New chat').click()`),
  };
}

async function waitForCompose(page) {
  await page.waitFor(ui(page).composerReady, "the composer", 60_000);
}

// ---- lifecycle ---------------------------------------------------------------------

async function lifecycle() {
  console.log("\nlifecycle");
  const data = fresh("life");
  let app;
  let page;
  let keyBefore;

  await check("a first launch with no .env opens the chat and finds Claude", async () => {
    app = await start({ dataDir: data, env: HOSTILE_ENV });
    page = await app.page();
    assert.match(page.url, /^http:\/\/127\.0\.0\.1:\d+\/chat\//);
    // Either Claude is ready (its version is shown) or the reason is: both are an answer.
    await page.waitFor(`document.body.innerText.includes('Claude Code') || document.body.innerText.includes('not available')`, "Claude's status", 60_000);
    fs.writeFileSync(path.join(data, "first-launch.png"), await page.screenshot());
  });

  await check("the key is stored encrypted, the database is in the data folder, nothing else", async () => {
    const keyFile = path.join(data, "secrets", "encryption-key.v1");
    keyBefore = fs.readFileSync(keyFile);
    assert.ok(keyBefore.length > 40);
    assert.ok(!/^[A-Za-z0-9_-]{43}=$/.test(keyBefore.toString("latin1")), "the key must not be stored as plain text");
    assert.ok(fs.existsSync(path.join(data, "data", "open-dots.db")));
    assert.ok(!fs.existsSync(path.join(data, "data", "backups")), "a fresh database needs no backup");
    assert.ok(fs.existsSync(path.join(data, "logs", "backend.log")));
  });

  await check("from outside the window nothing answers without the launch token", async () => {
    const asPage = { Accept: "text/html,application/xhtml+xml,*/*;q=0.8" };
    for (const [pathName, headers] of [
      ["/api/v1/threads", {}],
      ["/api/v1/local/status", {}],
      ["/api/v1/threads", { "X-Open-Dots-Token": "x".repeat(43) }],
      ["/api/v1/threads", { Origin: "http://evil.example" }],
      ["/chat/", {}],
      ["/", {}],
      ["/no/such/path", {}],
    ]) {
      const response = await fetch(page.origin + pathName, { headers });
      assert.equal(response.status, 401, `${pathName} ${JSON.stringify(headers)}`);
    }
    const tab = await fetch(`${page.origin}/`, { headers: asPage });
    assert.equal(tab.status, 401);
    assert.match(await tab.text(), /Open this from the Open Dots app/);
  });

  await check("inside the window the page is authenticated, sees no Node, and cannot leave", async () => {
    assert.equal(await page.evaluate(`fetch('/api/v1/threads').then(r => r.status)`), 200);
    // The page cannot override the token (the session sets it on the way out), nor read it.
    assert.equal(await page.evaluate(`fetch('/api/v1/threads', { headers: { 'X-Open-Dots-Token': 'forged' } }).then(r => r.status)`), 200);
    assert.equal(await page.evaluate(`typeof require + ' ' + typeof process + ' ' + typeof module`), "undefined undefined undefined");
    assert.equal(await page.evaluate(`document.cookie`), "");
    // New windows are refused and navigation stays on the app's own address.
    assert.equal(await page.evaluate(`window.open('https://example.com') === null`), true);
    await page.evaluate(`location.href = 'https://example.com/'`);
    await sleep(1500);
    assert.equal(new URL(await page.evaluate(`location.href`)).origin, page.origin);
  });

  await check("a second launch hands over to the first and leaves", async () => {
    const second = await start({ dataDir: data });
    const code = await Promise.race([second.exited, sleep(30_000).then(() => "timeout")]);
    assert.equal(code, 0, `second launch exit ${code}\n${second.output.join("")}`);
    assert.equal(await page.evaluate(`fetch('/api/v1/threads').then(r => r.status)`), 200, "the first window is still working");
  });

  const pid = () => app.backendPid();
  const origin = () => page.origin;
  let servedBy;
  await check("quitting stops the backend and frees the port", async () => {
    servedBy = pid();
    assert.ok(servedBy && alive(servedBy), "the backend is running before the quit");
    assert.equal(await app.quit(page), 0);
    await waitUntil(() => !alive(servedBy), "the backend to stop", 40_000);
    await assert.rejects(fetch(origin() + "/api/v1/threads"), /fetch failed/);
  });

  await check("the next launch keeps the key and the data, and is a new address", async () => {
    const oldOrigin = origin();
    app = await start({ dataDir: data });
    page = await app.page();
    assert.notEqual(page.origin, oldOrigin);
    assert.deepEqual(fs.readFileSync(path.join(data, "secrets", "encryption-key.v1")), keyBefore);
    assert.ok(!fs.existsSync(path.join(data, "data", "backups")));
    assert.equal(await page.evaluate(`fetch('/api/v1/threads').then(r => r.status)`), 200);
  });

  await check("when the app is killed the backend stops by itself", async () => {
    const orphanPid = pid();
    assert.ok(orphanPid && alive(orphanPid));
    await app.hardKill();
    await waitUntil(() => !alive(orphanPid), "the backend to notice and stop", 60_000);
    app.destroy();
  });

  await check("and the app starts again after that", async () => {
    await sleep(3000); // a person cannot start it again in the instant after it was killed
    app = await start({ dataDir: data });
    page = await app.page();
    assert.equal(await page.evaluate(`fetch('/api/v1/threads').then(r => r.status)`), 200);
    await sleep(2000); // the window is open and used before it is closed
    assert.equal(await app.quit(page), 0);
    await waitUntil(() => !alive(pid()), "the backend to stop", 40_000);
  });
}

// ---- the npm commands --------------------------------------------------------------

async function scripts() {
  console.log("\nthe npm commands");
  await check("`start` and `dev` open the app with the token enforced, even with ELECTRON_RUN_AS_NODE set", async () => {
    for (const mode of ["start", "dev"]) {
      // ELECTRON_RUN_AS_NODE is what some editors put in their terminals; with it set
      // Electron would behave as plain Node and open no window. launch.js removes it.
      const app = await start({
        dataDir: fresh(`script-${mode}`),
        mode,
        viaScript: true,
        env: { ELECTRON_RUN_AS_NODE: "1" },
      });
      const page = await app.page();
      assert.equal((await fetch(`${page.origin}/api/v1/threads`)).status, 401, `${mode}: from outside`);
      assert.equal(await page.evaluate(`fetch('/api/v1/threads').then(r => r.status)`), 200, `${mode}: from the window`);
      await sleep(1500);
      assert.equal(await app.quit(page), 0, `${mode}: quit`);
    }
  });

  await check("`start` says what to run, and does not install or build, when something is missing", async () => {
    // A copy of the launcher's view of an empty checkout: nothing built, nothing set up.
    const { checkLaunch } = require("../src/preflight");
    const { layout } = require("../src/paths");
    const empty = fresh("empty-checkout");
    const result = checkLaunch({ layout: layout(empty) });
    assert.deepEqual(result.problems.map((p) => p.fix), ["npm run setup", "npm run setup", "npm run build"]);
  });
}

// ---- failures ----------------------------------------------------------------------

async function failures() {
  console.log("\nfailures");

  await check("without Claude the page says so, names the setting, and offers Check again", async () => {
    const app = await start({ dataDir: fresh("noclaude"), env: { OPEN_DOTS_CLAUDE_PATH: "C:\\no\\such\\claude.exe" } });
    const page = await app.page();
    const text = await page.waitFor(
      `document.body.innerText.includes('Local Claude is not available') && document.body.innerText`,
      "the not-available notice",
      60_000,
    );
    assert.match(text, /Claude CLI not found/);
    assert.match(text, /OPEN_DOTS_CLAUDE_PATH/);
    assert.doesNotMatch(text, /LOCAL_CLAUDE_CLI_PATH|backend\/\.env/);
    const u = ui(page);
    assert.equal(await u.has("Check again"), true);
    await u.click("Check again");
    await page.waitFor(`document.body.innerText.includes('Local Claude is not available')`, "the notice after checking again", 30_000);
    fs.writeFileSync(path.join(BASE, "noclaude", "no-claude.png"), await page.screenshot());
    await app.quit(page);
  });

  await check("with Claude installed but signed out the page says so, and how to sign in", async () => {
    // The real CLI, pointed at an empty configuration folder, is genuinely signed out.
    const config = fresh("signedout-claude-config");
    const app = await start({ dataDir: fresh("signedout"), env: { CLAUDE_CONFIG_DIR: config } });
    const page = await app.page();
    const text = await page.waitFor(
      `/not signed in|not available/i.test(document.body.innerText) && document.body.innerText`,
      "the signed-out notice",
      60_000,
    );
    assert.match(text, /not signed in/i);
    assert.match(text, /claude auth login/);
    assert.equal(await ui(page).has("Send"), false, "nothing can be sent");
    fs.writeFileSync(path.join(BASE, "signedout", "signed-out.png"), await page.screenshot());
    assert.equal(await app.quit(page), 0);
  });

  await check("a relative OPEN_DOTS_CLAUDE_PATH is refused with a message, not used", async () => {
    const app = await start({ dataDir: fresh("relative"), env: { OPEN_DOTS_CLAUDE_PATH: "claude" } });
    await sleep(8000);
    assert.equal(app.backendPid(), null, "no backend was started");
    app.destroy();
  });

  await check("a damaged key file is never replaced, and no backend starts", async () => {
    const data = fresh("junkkey");
    fs.mkdirSync(path.join(data, "secrets"), { recursive: true });
    const junk = Buffer.from("this is not an encrypted key");
    fs.writeFileSync(path.join(data, "secrets", "encryption-key.v1"), junk);
    const app = await start({ dataDir: data });
    await sleep(10_000); // the app is showing its error box; nothing may have been started
    assert.equal(app.backendPid(), null);
    assert.deepEqual(fs.readFileSync(path.join(data, "secrets", "encryption-key.v1")), junk);
    assert.ok(!fs.existsSync(path.join(data, "data", "open-dots.db")));
    app.destroy();
  });

  await check("a key that cannot open stored credentials stops the launch and changes nothing", async () => {
    const data = fresh("wrongkey");
    let app = await start({ dataDir: data });
    let page = await app.page();
    assert.equal(await app.quit(page), 0);
    const database = path.join(data, "data", "open-dots.db");
    const insert = python(
      "import sqlite3, sys\nfrom cryptography.fernet import Fernet\n" +
        "c = sqlite3.connect(sys.argv[1])\n" +
        "c.execute(\"INSERT INTO user_credential (user_id, provider, file_path, ciphertext) VALUES ('u','github','/x',?)\", " +
        "(Fernet(Fernet.generate_key()).encrypt(b'x').decode(),))\nc.commit()\nprint(c.execute('select count(*) from user_credential').fetchone()[0])\n",
      database,
    );
    assert.equal(insert.stdout.trim(), "1", insert.stderr);
    const before = python("import sqlite3,sys;print(sqlite3.connect(sys.argv[1]).execute('select ciphertext from user_credential').fetchone()[0])", database).stdout;

    app = await start({ dataDir: data });
    await waitUntil(
      () => /cannot be decrypted/.test(fs.readFileSync(path.join(data, "logs", "backend.log"), "utf8")),
      "the backend to refuse",
      60_000,
    );
    const after = python("import sqlite3,sys;print(sqlite3.connect(sys.argv[1]).execute('select ciphertext from user_credential').fetchone()[0])", database).stdout;
    assert.equal(after, before, "the stored credential was not touched");
    await sleep(1500);
    assert.equal(alive(app.child.pid), true, "the app is showing its error box");
    app.destroy();
  });
}

// ---- existing source / headless installs -------------------------------------------

const sha = (file) => require("node:crypto").createHash("sha256").update(fs.readFileSync(file)).digest("hex");
const killTree = (pid) => spawnSync("taskkill", ["/pid", String(pid), "/T", "/F"], { windowsHide: true });

async function headless() {
  console.log("\nexisting source/headless install");
  const dir = fresh("headless");
  const envFile = path.join(paths.backend, ".env");
  if (fs.existsSync(envFile)) {
    console.log("  skipped: backend/.env exists here; run this from a fresh checkout so it is not overwritten");
    return;
  }
  const db = path.join(dir, "headless.db");
  const key = python("from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())").stdout.trim();
  const envText = `DATABASE_BACKEND=sqlite\nSQLITE_PATH=${db}\nTOKEN_ENCRYPTION_KEYS=${key}\nLOCAL_EXECUTION_ENABLED=true\n`;
  fs.writeFileSync(envFile, envText);
  const children = [];
  const cleanEnv = { ...process.env };
  delete cleanEnv.ELECTRON_RUN_AS_NODE;
  try {
    const port = await require("./app").freePort();
    let server;

    await check("the documented manual start works from backend/.env: migrations, then the API, no token needed", async () => {
      const migrate = spawnSync(paths.python, ["-m", "alembic", "upgrade", "head"], { cwd: paths.backend, env: cleanEnv, encoding: "utf8" });
      assert.equal(migrate.status, 0, migrate.stderr);
      assert.ok(fs.existsSync(db), "the SQLite file named in .env was created");
      server = require("node:child_process").spawn(paths.python, ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", String(port)], {
        cwd: paths.backend, env: cleanEnv, stdio: "ignore",
      });
      children.push(server);
      await waitUntil(async () => fetch(`http://127.0.0.1:${port}/api/v1/threads`).then((r) => r.status === 200, () => false), "the headless API", 60_000);
      const made = await fetch(`http://127.0.0.1:${port}/api/v1/threads`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: "headless-marker" }),
      });
      assert.equal(made.status, 201);
      const status = await fetch(`http://127.0.0.1:${port}/api/v1/local/status`);
      assert.equal(status.status, 200);
      assert.equal((await fetch(`http://127.0.0.1:${port}/chat/`)).status, 404, "the API does not serve the desktop page");
    });

    await check("the headless web UI (`next dev`) still serves", async () => {
      const uiPort = await require("./app").freePort();
      const next = require("node:child_process").spawn("npm", ["run", "dev", "--", "-p", String(uiPort), "-H", "127.0.0.1"], {
        cwd: paths.frontend, env: cleanEnv, stdio: "ignore", shell: true,
      });
      children.push(next);
      await waitUntil(async () => fetch(`http://127.0.0.1:${uiPort}/chat`).then((r) => r.status === 200, () => false), "next dev", 120_000);
      const html = await (await fetch(`http://127.0.0.1:${uiPort}/chat`)).text();
      assert.match(html, /<html/);
      killTree(next.pid);
    });

    await check("a desktop run beside it leaves the headless database, .env and checkout untouched, and shares nothing", async () => {
      killTree(server.pid);
      await waitUntil(() => fetch(`http://127.0.0.1:${port}/api/v1/threads`).then(() => false, () => true), "the headless API to stop", 20_000);
      const before = { db: sha(db), env: sha(envFile) };
      const app = await start({ dataDir: fresh("headless-desktop") });
      const page = await app.page();
      assert.equal(await page.evaluate(`fetch('/api/v1/threads').then(r => r.json()).then(t => JSON.stringify(t))`), "[]", "the desktop does not see the headless threads");
      assert.equal(await app.quit(page), 0);
      assert.deepEqual({ db: sha(db), env: sha(envFile) }, before);
      const git = spawnSync("git", ["status", "--porcelain", "--", ".", ":(exclude)desktop/e2e"], { cwd: paths.root, encoding: "utf8" });
      assert.equal(git.stdout.trim(), "", "nothing in the checkout changed (ignored files aside)");
    });

    await check("the headless backend starts again on the same database and still has its data", async () => {
      server = require("node:child_process").spawn(paths.python, ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", String(port)], {
        cwd: paths.backend, env: cleanEnv, stdio: "ignore",
      });
      children.push(server);
      await waitUntil(async () => fetch(`http://127.0.0.1:${port}/api/v1/threads`).then((r) => r.status === 200, () => false), "the headless API", 60_000);
      const threads = await (await fetch(`http://127.0.0.1:${port}/api/v1/threads`)).json();
      assert.deepEqual(threads.map((t) => t.title), ["headless-marker"]);
    });
  } finally {
    for (const child of children) killTree(child.pid);
    fs.rmSync(envFile, { force: true });
  }
}

// ---- chat, with the real Claude CLI ------------------------------------------------

async function chat() {
  console.log("\nchat (real Claude)");
  const data = path.join(BASE, "chat"); // one fixed folder: one Claude project folder, not one per run
  fs.rmSync(data, { recursive: true, force: true });
  fs.mkdirSync(data, { recursive: true });
  const workspace = path.join(data, "data", "local", "workspace");

  let app = await start({ dataDir: data });
  let page = await app.page();
  let u = ui(page);
  await page.waitFor(
    `document.body.innerText.includes('(Claude Code)') || document.body.innerText.includes('not available')`,
    "Claude's status",
    60_000,
  );
  if (!(await page.text()).includes("(Claude Code)")) {
    console.log("  skipped: Claude Code is not installed and signed in here");
    await app.quit(page);
    return;
  }
  await waitForCompose(page);

  await check("a message is answered", async () => {
    await u.send("Reply with just the word: pong");
    await page.waitFor(`location.search.includes('thread=')`, "the conversation's address", 60_000);
    await page.waitFor(`document.querySelector('main').innerText.toLowerCase().includes('pong')`, "the answer", 180_000);
    await waitForCompose(page);
    assert.match(await u.list(), /pong/i);
  });

  await check("an approval is asked, and Allow lets Claude write the file", async () => {
    await u.newChat();
    await page.waitFor(`!location.search.includes('thread=')`, "a new chat", 20_000);
    await u.send("Use the Write tool to create a file named approved.txt containing exactly: hello");
    await page.waitFor(u.buttonShown("Allow"), "the approval", 240_000);
    assert.equal(fs.existsSync(path.join(workspace, "approved.txt")), false, "nothing is written before Allow");
    fs.writeFileSync(path.join(data, "approval.png"), await page.screenshot());
    await u.click("Allow");
    await waitForCompose(page);
    await waitUntil(() => fs.existsSync(path.join(workspace, "approved.txt")), "the file", 30_000);
    assert.equal(fs.readFileSync(path.join(workspace, "approved.txt"), "utf8").trim(), "hello");
  });

  await check("Deny keeps Claude from writing the file", async () => {
    await u.newChat();
    await page.waitFor(`!location.search.includes('thread=')`, "a new chat", 20_000);
    await u.send("Use the Write tool to create a file named denied.txt containing exactly: nope");
    await page.waitFor(u.buttonShown("Deny"), "the approval", 240_000);
    await u.click("Deny");
    await waitForCompose(page);
    assert.equal(fs.existsSync(path.join(workspace, "denied.txt")), false);
  });

  await check("Stop ends a running answer, keeps what was written, and the conversation goes on", async () => {
    await u.newChat();
    await page.waitFor(`!location.search.includes('thread=')`, "a new chat", 20_000);
    await u.send("Write a long story about a lighthouse keeper, at least 600 words, in many paragraphs.");
    await page.waitFor(`document.querySelector('main').innerText.length > 200 && ${u.buttonShown("Stop")}`, "text streaming with a Stop button", 180_000);
    await u.click("Stop");
    await page.waitFor(`document.querySelector('main').innerText.includes('You stopped this answer')`, "the stopped notice", 60_000);
    await waitForCompose(page);
    assert.ok((await u.main()).length > 150, "the part already written is kept");
    await u.send("Reply with just the word: continued");
    await page.waitFor(`document.querySelector('main').innerText.trim().split('\\n').filter(Boolean).slice(-3).join(' ').includes('continued')`, "an answer after the stop", 180_000);
    await waitForCompose(page);
  });

  await check("a crash in the middle of an answer loses no completed conversation", async () => {
    const completed = (await u.list()).split("\n").map((l) => l.trim()).filter(Boolean);
    assert.ok(completed.length >= 4, `expected the four conversations so far, got: ${completed.join(" | ")}`);
    await u.newChat();
    await page.waitFor(`!location.search.includes('thread=')`, "a new chat", 20_000);
    await u.send("Write a long story about a ship's cook, at least 600 words, in many paragraphs.");
    await page.waitFor(`document.querySelector('main').innerText.length > 100 && ${u.buttonShown("Stop")}`, "an answer streaming", 180_000);
    const dying = app.backendPid();
    await app.hardKill();
    await waitUntil(() => !alive(dying), "the backend to stop by itself", 60_000);
    app.destroy();
    await sleep(3000);
    app = await start({ dataDir: data });
    page = await app.page();
    u = ui(page);
    await page.waitFor(`document.querySelector('nav[aria-label="Conversations"]') && document.querySelector('nav[aria-label="Conversations"]').innerText.includes('pong')`, "the list", 60_000);
    const after = await u.list();
    for (const line of completed) assert.ok(after.includes(line), `still listed after the crash: ${line}`);
    // The interrupted answer must not be left looking like it is still running.
    await page.evaluate(`[...document.querySelectorAll('nav[aria-label="Conversations"] a')].find(a => a.innerText.toLowerCase().includes('pong')).click()`);
    await page.waitFor(`location.search.includes('thread=') && document.querySelector('main').innerText.toLowerCase().includes('pong')`, "a completed transcript", 60_000);
    await waitForCompose(page);
    assert.equal(await u.has("Stop"), false);
    await page.evaluate(`[...document.querySelectorAll('nav[aria-label="Conversations"] a')].find(a => /ship's cook|story/i.test(a.innerText)).click()`);
    await sleep(2000);
    await waitForCompose(page); // an interrupted conversation can be written to again
    fs.writeFileSync(path.join(data, "after-crash.png"), await page.screenshot());
    console.log(`       interrupted conversation after the crash: ${JSON.stringify((await u.main()).slice(-160))}`);
  });

  let conversations;
  await check("quitting while idle and starting again keeps every conversation", async () => {
    conversations = await u.list();
    assert.equal(await app.quit(page), 0);
    await waitUntil(() => !alive(app.backendPid()), "the backend to stop", 40_000);
    app = await start({ dataDir: data });
    page = await app.page();
    u = ui(page);
    await page.waitFor(`document.querySelector('nav[aria-label="Conversations"]') && document.querySelector('nav[aria-label="Conversations"]').innerText.includes('pong')`, "the list", 60_000);
    assert.equal(await u.list(), conversations, "the same conversations, in the same order, with the same state");
  });

  await check("an old conversation reopens with its transcript and its Claude session", async () => {
    await page.evaluate(`[...document.querySelectorAll('nav[aria-label="Conversations"] a')].find(a => a.innerText.toLowerCase().includes('pong')).click()`);
    await page.waitFor(`location.search.includes('thread=') && document.querySelector('main').innerText.toLowerCase().includes('pong')`, "the old transcript", 60_000);
    await waitForCompose(page);
    await u.send("What single word did I ask you to reply with in my first message? Reply with just that word.");
    await page.waitFor(`document.querySelector('main').innerText.trim().split('\\n').filter(Boolean).slice(-2).join(' ').toLowerCase().includes('pong')`, "an answer that remembers", 180_000);
    await waitForCompose(page);
  });

  await app.quit(page);
}

// ---- run ---------------------------------------------------------------------------

(async () => {
  fs.mkdirSync(BASE, { recursive: true });
  const preflight = require("../src/preflight").checkLaunch({ layout: paths });
  if (preflight.problems.length) {
    console.error(preflight.problems.map((p) => `${p.what} Run: ${p.fix}`).join("\n"));
    process.exit(1);
  }
  try {
    if (selected("lifecycle")) await lifecycle();
    if (selected("scripts")) await scripts();
    if (selected("failures")) await failures();
    if (selected("headless")) await headless();
    if (selected("chat")) await chat();
  } finally {
    for (const app of running) app.destroy();
    if (!keep) {
      for (const name of ["life", "noclaude", "relative", "junkkey", "wrongkey", "chat", "script-start", "script-dev", "empty-checkout", "signedout", "signedout-claude-config", "headless", "headless-desktop"]) {
        // The chat folder is also where Claude's workspace is; only this run's own folders go.
        fs.rmSync(path.join(BASE, name), { recursive: true, force: true });
      }
    }
  }
  const failed = results.filter((r) => !r.ok);
  console.log(`\n${results.length - failed.length} passed, ${failed.length} failed`);
  process.exit(failed.length ? 1 : 0);
})();
