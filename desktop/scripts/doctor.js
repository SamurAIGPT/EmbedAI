const { layout } = require("../src/paths");
const { diagnose } = require("../src/doctor");

const args = process.argv.slice(2);
if (args.includes("--help")) {
  console.log("Usage: npm run doctor [-- --json]\nChecks this source checkout and finds installed Claude/Codex CLIs. Reads files only; sign-in and configured endpoints are checked in Settings → Engines.");
} else if (args.some((arg) => arg !== "--json")) {
  console.error("Unknown option. Usage: npm run doctor [-- --json]");
  process.exitCode = 2;
} else {
  const result = diagnose({ layout: layout() });
  if (args.includes("--json")) {
    console.log(JSON.stringify(result, null, 2));
  } else {
    console.log(`Open Duet source checkout: ${result.ok ? "prerequisites ready" : "needs setup"}`);
    console.log(`Node: ${result.nodeVersion}`);
    for (const item of result.problems) console.log(`Problem: ${item.what}\n  Fix: ${item.fix}`);
    for (const item of result.warnings) console.log(`Warning: ${item.what}\n  Next: ${item.fix}`);
    for (const engine of result.engines) console.log(`${engine.id}: ${engine.installed ? `installed at ${JSON.stringify(engine.executable)}` : "not found on PATH"}`);
    console.log("Sign-in and configured ACP/API endpoints: check in Settings → Engines.");
    console.log(result.next);
  }
  process.exitCode = result.ok ? 0 : 1;
}
