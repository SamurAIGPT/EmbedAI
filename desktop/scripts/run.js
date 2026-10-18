// `npm run app`: one command for a first run and every run after it.
//
// It does only what is missing: set up when Electron or the backend's environment
// is not there, build when the page is missing or older than its source, then
// start. When everything is in place it just starts, so nothing is installed or
// built on an ordinary launch. `npm run start` and `npm run dev` remain the
// commands that never install or build.
//
//   npm run app            open Open Dots
//   npm run app -- dev     the same, in dev mode (its own data folder)

const { spawnSync } = require("node:child_process");
const path = require("node:path");
const { layout } = require("../src/paths");
const { checkLaunch } = require("../src/preflight");
const { nextStep } = require("../src/plan");

const mode = process.argv[2] === "dev" ? "dev" : "start";
const paths = layout();

function run(script, ...args) {
  const result = spawnSync(process.execPath, [path.join(__dirname, script), ...args], { stdio: "inherit" });
  return result.status ?? 1;
}

const done = new Set();
for (;;) {
  const step = nextStep(checkLaunch({ layout: paths }));
  if (step === "start") process.exit(run("launch.js", mode, ...process.argv.slice(3)));
  // A step that ran and left the same thing missing would repeat for ever.
  if (done.has(step)) {
    console.error(`"${step}" ran but did not fix what was missing. Run "npm run ${step}" and read its output.`);
    process.exit(1);
  }
  done.add(step);
  console.log(`\n==> ${step === "setup" ? "Setting up (first run)" : "Building the page"}`);
  const code = run(`${step}.js`);
  if (code !== 0) process.exit(code);
}
