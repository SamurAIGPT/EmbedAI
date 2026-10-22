import assert from "node:assert/strict";
import { test } from "node:test";
import { check, luminance, ratio } from "../scripts/check-contrast.mjs";

test("the contrast formula matches known values", () => {
  assert.equal(luminance("#000000"), 0);
  assert.equal(luminance("#ffffff"), 1);
  assert.equal(ratio("#000000", "#ffffff"), 21);
  assert.ok(Math.abs(ratio("#777777", "#ffffff") - 4.48) < 0.02);
});

test("both themes keep text readable on every surface", () => {
  assert.deepEqual(check(), []);
});
