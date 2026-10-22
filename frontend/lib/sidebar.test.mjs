import assert from "node:assert/strict";
import { test } from "node:test";
import { agentTarget, rowStatus, sidebarAgents } from "./sidebar.ts";

const thread = (id, agent_id, updated_at, archived_at = null) => ({ id, agent_id, updated_at, archived_at });

test("an agent leads to its newest conversation that is not archived", () => {
  const threads = [
    thread(1, 7, "2026-10-01T10:00:00Z"),
    thread(2, 7, "2026-10-03T10:00:00Z"),
    thread(3, 7, "2026-10-09T10:00:00Z", "2026-10-10T00:00:00Z"),
    thread(4, 8, "2026-10-20T10:00:00Z"),
  ];
  assert.deepEqual(agentTarget(threads, 7), { kind: "thread", id: 2 });
  assert.deepEqual(agentTarget(threads, 8), { kind: "thread", id: 4 });
});

test("an agent with no conversation, or only archived ones, starts a new chat", () => {
  assert.deepEqual(agentTarget([], 7), { kind: "new" });
  assert.deepEqual(agentTarget(null, 7), { kind: "new" });
  assert.deepEqual(agentTarget([thread(1, 7, "2026-10-01T10:00:00Z", "2026-10-02T00:00:00Z")], 7), { kind: "new" });
  assert.deepEqual(agentTarget([thread(1, null, "2026-10-01T10:00:00Z")], 7), { kind: "new" });
});

test("the list skips archived agents, puts pinned first, then sorts by name", () => {
  const agents = [
    { id: 1, name: "zed", archived_at: null },
    { id: 2, name: "Atlas", archived_at: null },
    { id: 3, name: "old", archived_at: "2026-10-01T00:00:00Z" },
    { id: 4, name: "mira", pinned: true, archived_at: null },
    { id: 5, name: "Bea", archived_at: null },
  ];
  assert.deepEqual(sidebarAgents(agents).map((a) => a.id), [4, 2, 5, 1]);
  assert.deepEqual(sidebarAgents(null), []);
});

test("sorting does not change the array it was given", () => {
  const agents = [{ id: 1, name: "b", archived_at: null }, { id: 2, name: "a", archived_at: null }];
  sidebarAgents(agents);
  assert.deepEqual(agents.map((a) => a.id), [1, 2]);
});

test("status precedence: waiting, then working, then needs a new session", () => {
  assert.equal(rowStatus({ waiting: true, working: true, brokenSession: true }), "waiting");
  assert.equal(rowStatus({ working: true, brokenSession: true }), "working");
  assert.equal(rowStatus({ brokenSession: true }), "reset");
  assert.equal(rowStatus({}), null);
});
