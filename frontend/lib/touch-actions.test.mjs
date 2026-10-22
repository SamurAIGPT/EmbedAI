import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";

const dir = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "components", "ui");

// Phones have no hover. A control hidden with opacity-0 and revealed by hover must also be
// shown on touch screens, or it is unreachable there.
test("hover-revealed controls in the shared components stay visible on touch", () => {
  const offenders = [];
  for (const name of readdirSync(dir).filter((n) => n.endsWith(".tsx"))) {
    readFileSync(path.join(dir, name), "utf8").split("\n").forEach((line, i) => {
      if (/\bopacity-0\b/.test(line) && !/touch:opacity-100/.test(line)) offenders.push(`${name}:${i + 1}`);
    });
  }
  assert.deepEqual(offenders, []);
});
