import assert from "node:assert/strict";
import { test } from "node:test";
import { engineConfig, engineDraft, engineGroups, firstReadyEngine } from "./engines.ts";

const engines = [
  { id: "claude", available: false },
  { id: "openrouter", available: true },
  { id: "cursor", available: true },
];

test("ready engines come first, preserving registry order within groups", () => {
  assert.deepEqual(engineGroups(engines).map(g => [g.label, g.engines.map(e => e.id)]), [
    ["Ready", ["openrouter", "cursor"]], ["Needs setup", ["claude"]],
  ]);
  assert.deepEqual(engineGroups([]), []);
});

test("first run detects an engine and preserves a person's working selection", () => {
  assert.equal(firstReadyEngine(engines), "cursor");
  assert.equal(firstReadyEngine(engines, "openrouter"), "openrouter");
  assert.equal(firstReadyEngine([{ id: "cursor", available: true }]), "cursor");
  assert.equal(firstReadyEngine([{ id: "claude", available: false }]), null);
  assert.equal(firstReadyEngine([{ id: "custom", available: true }]), "custom");
});

test("generated forms never prefill a secret or submit fields the engine does not own", () => {
  const engine = {
    config_fields: [{ name: "api_key", kind: "secret" }, { name: "model" }],
    config: { api_key: "must-not-be-read", model: "vendor/model", has_api_key: true },
  };
  assert.deepEqual(engineDraft(engine), { api_key: "", model: "vendor/model" });
  assert.deepEqual(engineConfig(engine, { api_key: "", model: " v/m ", base_url: "https://wrong.test" }), { model: "v/m" });
  assert.deepEqual(engineConfig(engine, { api_key: "new-key", model: "" }), { api_key: "new-key" });
});
