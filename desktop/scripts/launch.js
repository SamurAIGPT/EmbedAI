// `npm run start` and `npm run dev`: open the app from this source checkout.
//
// Neither installs or builds anything. If something is missing they say which
// command makes it and stop. The two differ only in what they keep apart: `dev`
// uses its own data folder and its own window title, so developing never touches
// the data of the app you use.
//
// Electron is started here, with ELECTRON_RUN_AS_NODE removed: some editors set
// it in their terminals, and with it set Electron behaves as plain Node and
// opens no window.

const { spawn } = require("node:child_process");
const { layout } = require("../src/paths");
const { checkLaunch, nodeIsNewEnough, NODE_MAJOR_NEEDED } = require("../src/preflight");

const mode = process.argv[2] === "dev" ? "dev" : "start";
const extraArguments = process.argv.slice(3);

if (!nodeIsNewEnough()) {
  console.error(`Node ${NODE_MAJOR_NEEDED} or newer is needed (this is ${process.version}).`);
  process.exit(1);
}

const { problems, warnings, electronBinary } = checkLaunch({ layout: layout() });
for (const warning of warnings) console.warn(`Note: ${warning.what} Run: ${warning.fix}`);
if (problems.length) {
  console.error("Open Duet cannot start yet:");
  for (const problem of problems) console.error(`  - ${problem.what} Run: ${problem.fix}`);
  process.exit(1);
}

const environment = { ...process.env };
delete environment.ELECTRON_RUN_AS_NODE;

const child = spawn(electronBinary, [layout().desktop, `--mode=${mode}`, ...extraArguments], {
  stdio: "inherit",
  env: environment,
});
child.on("error", (error) => {
  console.error(`Could not start Electron: ${error.message}`);
  process.exit(1);
});
child.on("exit", (code, signal) => process.exit(code ?? (signal ? 1 : 0)));
