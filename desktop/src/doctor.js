// Source-checkout diagnostics. Read files only: never load Electron, start an
// engine, read its credentials, install dependencies or modify application data.
const fs = require("node:fs");
const path = require("node:path");
const { checkLaunch, nodeIsNewEnough, NODE_MAJOR_NEEDED } = require("./preflight");

function envValue(env, name) {
  const key = Object.keys(env).find((item) => item.toUpperCase() === name);
  return key ? env[key] : "";
}

function findCli(name, { env = process.env, platform = process.platform, fsImpl = fs } = {}) {
  const windows = platform === "win32";
  const paths = String((windows ? envValue(env, "PATH") : env.PATH) || "").split(windows ? ";" : ":").filter(Boolean);
  const extensions = windows
    ? String(envValue(env, "PATHEXT") || ".COM;.EXE;.BAT;.CMD").split(";").filter(Boolean)
    : [""];
  const join = windows ? path.win32.join : path.join;
  for (const directory of paths) {
    for (const extension of extensions) {
      const candidate = join(directory, name + extension.toLowerCase());
      try {
        if (!fsImpl.statSync(candidate).isFile()) continue;
        fsImpl.accessSync(candidate, windows ? fs.constants.F_OK : fs.constants.X_OK);
        return candidate;
      } catch { /* absent or not executable: try the next candidate */ }
    }
  }
  return null;
}

function diagnose({ layout, nodeVersion = process.version, env = process.env, platform = process.platform, fsImpl = fs }) {
  const { problems: launchProblems, warnings: launchWarnings, electronBinary } = checkLaunch({ layout, fsImpl });
  const problems = [...launchProblems];
  const warnings = [...launchWarnings];
  if (!nodeIsNewEnough(nodeVersion)) {
    problems.unshift({ what: `Node.js ${NODE_MAJOR_NEEDED} or newer is required.`, fix: `Install Node.js ${NODE_MAJOR_NEEDED} or newer, then rerun npm run doctor.` });
  }
  const engines = ["claude", "codex"].map((id) => {
    const executable = findCli(id, { env, platform, fsImpl });
    return { id, installed: executable !== null, executable, signedIn: null };
  });
  if (!engines.some((engine) => engine.installed)) {
    warnings.push({ what: "No Claude or Codex executable was found on PATH.", fix: "Install a CLI and sign in, or configure an ACP command or API endpoint in Settings → Engines." });
  }
  return {
    ok: problems.length === 0,
    scope: "source-checkout",
    nodeVersion,
    electronBinary,
    python: layout.python,
    ui: layout.ui,
    problems,
    warnings,
    engines,
    signInCheck: "not_run",
    next: problems.length ? "Fix the problems above, then rerun npm run doctor." : "Run npm run app. Check engine readiness and sign-in in Settings → Engines.",
  };
}

module.exports = { diagnose, findCli };
