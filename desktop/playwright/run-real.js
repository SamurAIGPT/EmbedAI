// `npm run e2e:ui:real`: the chat checks against the real Claude CLI, failing (not skipping)
// when it is unavailable or signed out.
const { spawnSync } = require("node:child_process");

const result = spawnSync(process.execPath, [require.resolve("@playwright/test/cli"), "test", "chat", ...process.argv.slice(2)], {
  stdio: "inherit",
  env: { ...process.env, OPEN_DOTS_E2E_REQUIRE_CLAUDE: "1" },
});
process.exit(result.status ?? 1);
