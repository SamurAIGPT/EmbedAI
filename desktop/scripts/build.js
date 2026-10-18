// `npm run build`: build the page the app shows, as plain files the backend serves.
// It installs nothing; it needs `npm run setup` to have been run.

const fs = require("node:fs");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const { layout } = require("../src/paths");

const paths = layout();
const next = path.join(paths.frontend, "node_modules", "next", "dist", "bin", "next");

function fail(message) {
  console.error(`\n${message}`);
  process.exit(1);
}

if (!fs.existsSync(next)) {
  fail("The page's dependencies are not installed. Run: npm run setup");
}

fs.rmSync(paths.ui, { recursive: true, force: true }); // only ever this build's own output

console.log("==> Building the page (static export)");
const result = spawnSync(process.execPath, [next, "build"], {
  cwd: paths.frontend,
  stdio: "inherit",
  env: {
    ...process.env,
    // Export plain files, and make the page talk to the server it was loaded from,
    // whatever port that turns out to be.
    OD_STATIC_EXPORT: "1",
    NEXT_PUBLIC_API_BASE_URL: "/api/v1",
  },
});
if (result.error || result.status !== 0) fail("The page did not build.");

for (const page of ["index.html", path.join("chat", "index.html"), path.join("settings", "index.html")]) {
  if (!fs.existsSync(path.join(paths.ui, page))) fail(`The build finished but ${page} is missing from ${paths.ui}.`);
}
console.log(`\nBuilt ${paths.ui}\nNext: npm run start`);
