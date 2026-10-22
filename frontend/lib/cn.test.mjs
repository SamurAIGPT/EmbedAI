import assert from "node:assert/strict";
import { test } from "node:test";
import { cn } from "./cn.ts";

test("cn drops falsy values and lets the last utility win", () => {
  assert.equal(cn("p-2", false && "x", undefined, "p-4"), "p-4");
  assert.equal(cn("text-ink", ["font-medium", { hidden: true }]), "text-ink font-medium hidden");
});
