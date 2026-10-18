// Prepares a copy of an OLDER release of the app, so that an upgrade can be tested
// with the data that release really wrote.
//
// The copy is made from Git history (`git archive`), never by checking anything out
// in the working tree, and lives under the system temp folder. It is cached there
// (the first run takes a minute or two for `uv sync`); delete the folder to rebuild.
//
// It needs: the commit in this checkout's history (a shallow clone may lack it), `git`,
// `tar` and `uv` on PATH. The page is not rebuilt: the old release is only run as a
// window and a backend, so the current build of the page is copied in as its page.
//
// OPEN_DOTS_E2E_OLD_RELEASE_DIR points at a copy that was prepared some other way
// (for example offline); it is used as it is.

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const { layout } = require("../src/paths");

function run(command, args, options = {}) {
  const env = { ...process.env };
  delete env.VIRTUAL_ENV; // another project's environment must not leak into `uv`
  return spawnSync(command, args, { encoding: "utf8", env, ...options });
}

function lastLines(result) {
  return `${result.stderr ?? ""}${result.stdout ?? ""}`.trim().split("\n").slice(-6).join("\n");
}

// Returns { desktop } (the folder to start Electron with), or { skip } with the reason
// this cannot run here. Throws only when preparing a copy started and then failed.
function prepareOldRelease({ commit, repoRoot, uiFrom }) {
  const given = (process.env.OPEN_DOTS_E2E_OLD_RELEASE_DIR ?? "").trim();
  if (given) {
    const prepared = layout(given);
    if (!fs.existsSync(path.join(prepared.desktop, "main.js")) || !fs.existsSync(prepared.python) || !fs.existsSync(path.join(prepared.ui, "index.html"))) {
      return { skip: "OPEN_DOTS_E2E_OLD_RELEASE_DIR is not a prepared copy (it needs desktop/main.js, a backend environment and a built page)." };
    }
    return { desktop: prepared.desktop, source: "OPEN_DOTS_E2E_OLD_RELEASE_DIR" };
  }

  const known = run("git", ["-C", repoRoot, "cat-file", "-e", `${commit}^{commit}`]);
  if (known.error || known.status !== 0) {
    return { skip: `release ${commit} is not in this checkout's Git history (a shallow clone?). Fetch full history, or set OPEN_DOTS_E2E_OLD_RELEASE_DIR.` };
  }
  for (const tool of ["tar", "uv"]) {
    const found = run(tool, ["--version"]);
    if (found.error || found.status !== 0) return { skip: `\`${tool}\` was not found on PATH.` };
  }

  const dir = path.join(os.tmpdir(), "open-dots-e2e-old-release", commit);
  const marker = path.join(dir, ".ready");
  const prepared = layout(dir);
  if (!fs.existsSync(marker)) {
    fs.rmSync(dir, { recursive: true, force: true });
    fs.mkdirSync(dir, { recursive: true });
    // The archive is written inside the target folder and tar runs from there with a bare
    // file name: GNU tar (Git for Windows) reads "C:\..." as a remote host and mangles
    // backslashes in other paths, so no absolute path is given to it.
    const archive = path.join(dir, "release.tar");
    try {
      const made = run("git", ["-C", repoRoot, "archive", "--format=tar", "-o", archive, commit]);
      if (made.status !== 0) throw new Error(`git archive failed:\n${lastLines(made)}`);
      const extracted = run("tar", ["-xf", "release.tar"], { cwd: dir });
      if (extracted.status !== 0) throw new Error(`tar failed:\n${lastLines(extracted)}`);
    } finally {
      fs.rmSync(archive, { force: true });
    }
    const synced = run("uv", ["sync", "--locked"], { cwd: prepared.backend });
    if (synced.status !== 0) throw new Error(`uv sync failed for the old release:\n${lastLines(synced)}`);
    fs.cpSync(uiFrom, prepared.ui, { recursive: true });
    fs.writeFileSync(marker, new Date().toISOString());
  }
  return { desktop: prepared.desktop, source: `git ${commit}` };
}

module.exports = { prepareOldRelease };
