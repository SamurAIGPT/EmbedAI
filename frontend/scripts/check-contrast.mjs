// Fails when a text colour on a surface falls under WCAG AA in either theme.
//
//   node scripts/check-contrast.mjs
//
// Reads the colour tokens straight from app/globals.css, so the check cannot drift from what
// the page uses. Node only, no packages. Exits 1 with one line per failing pair.

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const css = readFileSync(path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "app", "globals.css"), "utf8");

// The custom properties declared in the first block whose selector contains `selector`.
export function tokens(source, selector) {
  const start = source.indexOf(selector);
  if (start < 0) throw new Error(`no block for ${selector}`);
  const open = source.indexOf("{", start);
  const close = source.indexOf("}", open);
  const found = {};
  for (const match of source.slice(open + 1, close).matchAll(/--([\w-]+)\s*:\s*([^;]+);/g)) found[match[1]] = match[2].trim();
  return found;
}

function channel(value) {
  const c = value / 255;
  return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
}

export function luminance(hex) {
  const n = /^#([0-9a-f]{6})$/i.exec(hex);
  if (!n) throw new Error(`not a #rrggbb colour: ${hex}`);
  const v = parseInt(n[1], 16);
  return 0.2126 * channel((v >> 16) & 255) + 0.7152 * channel((v >> 8) & 255) + 0.0722 * channel(v & 255);
}

export function ratio(a, b) {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

// [foreground token, background token, minimum ratio]. Text is 4.5; the faint ink is used
// only for small secondary text, so it holds 4.5 too.
const SURFACES = ["bg-app", "bg-surface", "bg-raised", "bg-hover", "bg-inset"];
const PAIRS = [
  ...SURFACES.flatMap((bg) => [["ink", bg, 4.5], ["ink-muted", bg, 4.5], ["ink-faint", bg, 4.5]]),
  ["accent-fg", "accent", 4.5],
  ["accent", "bg-raised", 4.5],
  ["accent", "bg-surface", 4.5],
  ["success", "bg-raised", 4.5],
  ["warning", "bg-raised", 4.5],
  ["danger", "bg-raised", 4.5],
  ["accent-fg", "danger", 4.5],
  ["ink", "bubble-user", 4.5],
  ["ink", "code-bg", 4.5],
];

export function check() {
  const shared = tokens(css, ":root {\n  --identity-1");
  const themes = { light: tokens(css, ':root,\n:root[data-theme="light"]'), dark: tokens(css, ':root[data-theme="dark"]') };
  const failures = [];
  for (const [name, set] of Object.entries(themes)) {
    for (const [fg, bg, min] of PAIRS) {
      const got = ratio(set[fg], set[bg]);
      if (got < min) failures.push(`${name}: ${fg} ${set[fg]} on ${bg} ${set[bg]} is ${got.toFixed(2)}:1, needs ${min}:1`);
    }
  }
  for (let i = 1; i <= 10; i++) {
    const got = ratio("#ffffff", shared[`identity-${i}`]);
    if (got < 4.5) failures.push(`identity-${i} ${shared[`identity-${i}`]} with white text is ${got.toFixed(2)}:1, needs 4.5:1`);
  }
  return failures;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const failures = check();
  if (failures.length) {
    console.error(failures.join("\n"));
    process.exit(1);
  }
  console.log("contrast ok");
}
