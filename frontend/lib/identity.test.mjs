import assert from "node:assert/strict";
import { test } from "node:test";
import { IDENTITY_COUNT, identityIndex, identityVar, initialOf } from "./identity.ts";

test("an agent always gets the same colour, inside the palette", () => {
  for (const id of [1, 2, 3, 99, 123456, "7"]) {
    const index = identityIndex(id);
    assert.equal(index, identityIndex(id));
    assert.ok(index >= 0 && index < IDENTITY_COUNT);
  }
  assert.equal(identityIndex(7), identityIndex("7"));
});

test("neighbouring ids do not all share a colour", () => {
  const colours = new Set(Array.from({ length: 30 }, (_, i) => identityIndex(i + 1)));
  assert.ok(colours.size >= 6);
});

test("the colour is a CSS variable that globals.css defines", () => {
  assert.match(identityVar(1), /^var\(--identity-([1-9]|10)\)$/);
});

test("the initial is the first letter or digit, upper-cased", () => {
  assert.equal(initialOf("scout"), "S");
  assert.equal(initialOf("  ✨ mira"), "M");
  assert.equal(initialOf("émile"), "É");
  assert.equal(initialOf("42 things"), "4");
  assert.equal(initialOf(""), "?");
  assert.equal(initialOf(null), "?");
  assert.equal(initialOf("---"), "?");
});
