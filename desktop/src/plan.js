// What the one-command launcher does next, from what the preflight found. Pure:
// it never runs anything, so it can be tested without installing or building.

// "setup" while Electron or the backend's Python is missing, then "build" while the
// page is missing or older than its source, then "start".
function nextStep({ problems, warnings }) {
  if (problems.some((problem) => problem.fix === "npm run setup")) return "setup";
  if (problems.some((problem) => problem.fix === "npm run build")) return "build";
  if (warnings.some((warning) => warning.fix === "npm run build")) return "build";
  return "start";
}

module.exports = { nextStep };
