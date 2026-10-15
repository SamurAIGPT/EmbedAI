const assert = require("node:assert/strict");
const { test } = require("node:test");

const { nextStep } = require("../src/plan");

const setup = { what: "x.", fix: "npm run setup" };
const build = { what: "y.", fix: "npm run build" };

test("a fresh checkout is set up first, then built, then started", () => {
  assert.equal(nextStep({ problems: [setup, setup, build], warnings: [] }), "setup");
  assert.equal(nextStep({ problems: [build], warnings: [] }), "build");
  assert.equal(nextStep({ problems: [], warnings: [] }), "start");
});

test("a page older than its source is rebuilt before starting", () => {
  assert.equal(nextStep({ problems: [], warnings: [build] }), "build");
});

test("setup comes before build even when a stale page is also reported", () => {
  assert.equal(nextStep({ problems: [setup], warnings: [build] }), "setup");
});

test("with nothing missing it only starts", () => {
  assert.equal(nextStep({ problems: [], warnings: [] }), "start");
});
