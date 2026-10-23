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
  const enabledPresets = new Set(env.OPEN_DOTS_PRESET_ENGINES === undefined
    ? ["cursor", "gemini", "openrouter"]
    : String(env.OPEN_DOTS_PRESET_ENGINES).split(",").map((name) => name.trim()));
  const commands = [["claude", "claude"], ["codex", "codex"],
    ...[["cursor", "cursor-agent"], ["gemini", "gemini"], ["opencode", "opencode"]].filter(([id]) => enabledPresets.has(id))];
  const engines = commands.map(([id, command]) => {
    const executable = findCli(command, { env, platform, fsImpl });
    return { id, installed: executable !== null, executable, signedIn: null };
  });
  if (!engines.some((engine) => engine.installed)) {
    warnings.push({ what: "No engine executable was found on PATH.", fix: "Install Claude, Codex, Gemini or Cursor and sign in, or connect OpenRouter, an ACP command or an API endpoint in Settings → Engines." });
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
