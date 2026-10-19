// node --test scripts/build-ui.test.js
// Uses a stand-in for Next.js that records how it was called, so no real build runs.

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { buildUi, SERVER_PAGES, UiBuildError } = require("./build-ui");

// A frontend folder whose "next" writes the pages named in FAKE_PAGES to out/ and
// writes the environment it was given to env.json.
function fakeFrontend({ pages = SERVER_PAGES, exitCode = 0 } = {}) {
  const frontend = fs.mkdtempSync(path.join(os.tmpdir(), "od-ui-"));
  const bin = path.join(frontend, "node_modules", "next", "dist", "bin");
  fs.mkdirSync(bin, { recursive: true });
  fs.writeFileSync(
    path.join(bin, "next"),
    `const fs = require("fs"), path = require("path");
fs.writeFileSync("env.json", JSON.stringify({ args: process.argv.slice(2), export: process.env.OD_STATIC_EXPORT, base: process.env.NEXT_PUBLIC_API_BASE_URL }));
for (const page of ${JSON.stringify(pages)}) {
  fs.mkdirSync(path.dirname(path.join("out", page)), { recursive: true });
  fs.writeFileSync(path.join("out", page), "<html></html>");
}
process.exit(${exitCode});`,
  );
  return frontend;
}

const quiet = () => {};

test("builds a static export that talks to the server it was loaded from", () => {
  const frontend = fakeFrontend();
  const out = buildUi({ frontend, log: quiet });
  assert.equal(out, path.join(frontend, "out"));
  const seen = JSON.parse(fs.readFileSync(path.join(frontend, "env.json"), "utf8"));
  assert.deepEqual(seen, { args: ["build"], export: "1", base: "/api/v1" });
});

test("the server needs the sign-in and pairing pages too", () => {
  assert.ok(SERVER_PAGES.includes(path.join("login", "index.html")));
  assert.ok(SERVER_PAGES.includes(path.join("pair", "index.html")));
});

test("a build without a required page is an error that names the page", () => {
  const frontend = fakeFrontend({ pages: ["index.html"] });
  assert.throws(() => buildUi({ frontend, log: quiet }), (error) => {
    assert.ok(error instanceof UiBuildError);
    assert.match(error.message, /chat/);
    return true;
  });
});

test("the desktop app can ask for fewer pages", () => {
  const frontend = fakeFrontend({ pages: ["index.html"] });
  assert.doesNotThrow(() => buildUi({ frontend, pages: ["index.html"], log: quiet }));
});

test("a failed build is an error", () => {
  const frontend = fakeFrontend({ exitCode: 1 });
  assert.throws(() => buildUi({ frontend, log: quiet }), /did not build/);
});

test("missing dependencies are reported before anything is deleted", () => {
  const frontend = fs.mkdtempSync(path.join(os.tmpdir(), "od-ui-"));
  fs.mkdirSync(path.join(frontend, "out"));
  fs.writeFileSync(path.join(frontend, "out", "keep.txt"), "x");
  assert.throws(() => buildUi({ frontend, log: quiet }), /npm ci/);
  assert.ok(fs.existsSync(path.join(frontend, "out", "keep.txt")));
});

test("an old build is cleared first, and only the output folder is touched", () => {
  const frontend = fakeFrontend();
  fs.mkdirSync(path.join(frontend, "out"));
  fs.writeFileSync(path.join(frontend, "out", "stale.html"), "old");
  fs.writeFileSync(path.join(frontend, "package.json"), "{}");
  buildUi({ frontend, log: quiet });
  assert.equal(fs.existsSync(path.join(frontend, "out", "stale.html")), false);
  assert.ok(fs.existsSync(path.join(frontend, "package.json")));
});
