import assert from "node:assert/strict";
import vm from "node:vm";
import { test } from "node:test";
import { applyTheme, parseChoice, readChoice, resolveTheme, THEME_INIT_SCRIPT, THEME_KEY, writeChoice } from "./theme.ts";

function memoryStorage(initial = {}) {
  const map = new Map(Object.entries(initial));
  return { map, getItem: (k) => (map.has(k) ? map.get(k) : null), setItem: (k, v) => { map.set(k, v); }, removeItem: (k) => { map.delete(k); } };
}
const blocked = () => { throw new Error("storage blocked"); };
const fakeDoc = () => ({ documentElement: { dataset: {}, style: {} } });

test("anything but light or dark means follow the system", () => {
  assert.equal(parseChoice("light"), "light");
  assert.equal(parseChoice("dark"), "dark");
  for (const raw of [null, undefined, "", "system", "blue", 3]) assert.equal(parseChoice(raw), "system");
});

test("system resolves by the operating system, a fixed choice never does", () => {
  assert.equal(resolveTheme("system", true), "dark");
  assert.equal(resolveTheme("system", false), "light");
  assert.equal(resolveTheme("light", true), "light");
  assert.equal(resolveTheme("dark", false), "dark");
});

test("the choice is remembered, and system clears it", () => {
  const store = memoryStorage();
  assert.equal(writeChoice(() => store, "dark"), true);
  assert.equal(store.map.get(THEME_KEY), "dark");
  assert.equal(readChoice(() => store), "dark");
  writeChoice(() => store, "system");
  assert.equal(store.map.has(THEME_KEY), false);
});

test("blocked storage still works: reads say system, writes report failure", () => {
  assert.equal(readChoice(blocked), "system");
  assert.equal(writeChoice(blocked, "dark"), false);
  const store = { getItem: blocked, setItem: blocked, removeItem: blocked };
  assert.equal(readChoice(() => store), "system");
  assert.equal(writeChoice(() => store, "light"), false);
});

test("applyTheme sets the page attributes", () => {
  const doc = fakeDoc();
  assert.equal(applyTheme(doc, "system", true), "dark");
  assert.deepEqual(doc.documentElement.dataset, { theme: "dark", themeChoice: "system" });
  assert.equal(doc.documentElement.style.colorScheme, "dark");
});

// The inline script and the functions above must agree for every stored value and system setting.
test("the pre-paint script gives the same result as applyTheme", () => {
  for (const value of [undefined, "light", "dark", "system", "junk"]) {
    for (const systemDark of [false, true]) {
      for (const storageBroken of [false, true]) {
        const doc = fakeDoc();
        const sandbox = {
          document: doc,
          matchMedia: () => ({ matches: systemDark }),
          localStorage: storageBroken ? { getItem: blocked } : { getItem: (k) => (k === THEME_KEY && value !== undefined ? value : null) },
        };
        vm.runInNewContext(THEME_INIT_SCRIPT, sandbox);
        const expected = fakeDoc();
        applyTheme(expected, storageBroken ? "system" : parseChoice(value), systemDark);
        const label = `${value} dark=${systemDark} broken=${storageBroken}`;
        assert.deepEqual({ ...doc.documentElement.dataset }, { ...expected.documentElement.dataset }, label);
        assert.equal(doc.documentElement.style.colorScheme, expected.documentElement.style.colorScheme, label);
      }
    }
  }
});

test("the pre-paint script survives a missing matchMedia", () => {
  const doc = fakeDoc();
  vm.runInNewContext(THEME_INIT_SCRIPT, { document: doc, localStorage: { getItem: () => null } });
  assert.equal(doc.documentElement.dataset.theme, "light");
});
