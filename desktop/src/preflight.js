// What `npm run start` and `npm run dev` check before they open a window. They
// never install or build anything: when something is missing they say which
// command makes it, and stop.

const fs = require("node:fs");
const path = require("node:path");

const NODE_MAJOR_NEEDED = 22;

function nodeIsNewEnough(version = process.version) {
  return Number(/^v?(\d+)/.exec(version)?.[1] ?? 0) >= NODE_MAJOR_NEEDED;
}

// Newest modification time among the files the page is built from.
function newestSourceTime(frontend, fsImpl = fs) {
  let newest = 0;
  const visit = (target) => {
    let stat;
    try {
      stat = fsImpl.statSync(target);
    } catch {
      return;
    }
    if (stat.isDirectory()) {
      for (const entry of fsImpl.readdirSync(target)) {
        if (entry !== "node_modules" && !entry.startsWith(".")) visit(path.join(target, entry));
      }
    } else if (!/\.test\.[cm]?[jt]sx?$/.test(target)) {
      newest = Math.max(newest, stat.mtimeMs);
    }
  };
  for (const name of ["app", "components", "lib", "public"]) visit(path.join(frontend, name));
  for (const name of ["package.json", "package-lock.json", "next.config.ts"]) visit(path.join(frontend, name));
  return newest;
}

// Where the Electron binary is, read from the files its installer leaves. The
// electron package itself is not loaded: on first use it downloads its binary,
// and starting the app must never download or install anything.
function findElectronBinary(desktop, fsImpl = fs) {
  const home = path.join(desktop, "node_modules", "electron");
  try {
    const relative = fsImpl.readFileSync(path.join(home, "path.txt"), "utf8").trim();
    const binary = path.join(home, "dist", relative);
    return relative && fsImpl.existsSync(binary) ? binary : null;
  } catch {
    return null;
  }
}

function checkLaunch({ layout, fsImpl = fs } = {}) {
  const problems = [];
  const warnings = [];

  const electronBinary = findElectronBinary(layout.desktop, fsImpl);
  if (electronBinary === null) {
    problems.push({ what: "Electron is not installed.", fix: "npm run setup" });
  }

  if (!fsImpl.existsSync(layout.python)) {
    problems.push({ what: "The backend's Python environment is not set up.", fix: "npm run setup" });
  }

  const page = path.join(layout.ui, "index.html");
  if (!fsImpl.existsSync(page)) {
    problems.push({ what: "The page has not been built.", fix: "npm run build" });
  } else {
    let builtAt = 0;
    try {
      builtAt = fsImpl.statSync(page).mtimeMs;
    } catch {
      /* treated as unbuilt below */
    }
    if (newestSourceTime(layout.frontend, fsImpl) > builtAt) {
      warnings.push({
        what: "The page's source is newer than the last build, so the window may show an older page.",
        fix: "npm run build",
      });
    }
  }
  return { problems, warnings, electronBinary };
}

module.exports = { checkLaunch, findElectronBinary, newestSourceTime, nodeIsNewEnough, NODE_MAJOR_NEEDED };
