// `npm run build`: build the page the app shows, as plain files the backend serves.
// It installs nothing; it needs `npm run setup` to have been run.
// The recipe itself (static export, a page that talks to the server it was loaded
// from) is shared with the server install: scripts/build-ui.js at the repository root.

const fs = require("node:fs");
const path = require("node:path");
const { layout } = require("../src/paths");
const { buildUi, UiBuildError } = require("../../scripts/build-ui");

const paths = layout();
const next = path.join(paths.frontend, "node_modules", "next", "dist", "bin", "next");

function fail(message) {
  console.error(`\n${message}`);
  process.exit(1);
}

if (!fs.existsSync(next)) {
  fail("The page's dependencies are not installed. Run: npm run setup");
}

try {
  buildUi({
    frontend: paths.frontend,
    out: paths.ui,
    // The desktop app has its own sign-in (a launch token), so it does not need the
    // server's sign-in and pairing pages to be there, only the ones it shows.
    pages: ["index.html", path.join("chat", "index.html"), path.join("settings", "index.html")],
  });
} catch (error) {
  if (!(error instanceof UiBuildError)) throw error;
  fail(error.message);
}
console.log(`\nBuilt ${paths.ui}\nNext: npm run start`);
