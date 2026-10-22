import assert from "node:assert/strict";
import { test } from "node:test";
import { announcement, groupToolRuns, spokenText, toolRunSummary } from "./transcript-view.ts";

const tool = (n, state = "done") => ({ kind: "tool", key: `t${n}`, state });
const other = (kind, key, extra = {}) => ({ kind, key, ...extra });

test("three or more tool calls in a row become one group; fewer stay single", () => {
  const out = groupToolRuns([other("user", "u1"), tool(1), tool(2), tool(3), tool(4), other("assistant", "a1"), tool(5), tool(6)]);
  assert.deepEqual(out.map((x) => x.kind), ["user", "tool_run", "assistant", "tool", "tool"]);
  assert.equal(out[1].tools.length, 4);
  assert.equal(out[1].key, "run-t1");
});

test("an approval between calls ends a run", () => {
  const out = groupToolRuns([tool(1), tool(2), other("approval", "p1"), tool(3), tool(4), tool(5)]);
  assert.deepEqual(out.map((x) => x.kind), ["tool", "tool", "approval", "tool_run"]);
});

test("grouping keeps every item and its order, and handles empty input", () => {
  assert.deepEqual(groupToolRuns([]), []);
  const items = [tool(1), tool(2), tool(3), other("turn_end", "e")];
  const flat = groupToolRuns(items).flatMap((x) => (x.kind === "tool_run" ? x.tools : [x]));
  assert.deepEqual(flat, items);
});

test("the run summary counts running and failed calls", () => {
  assert.deepEqual(toolRunSummary([{ state: "done" }, { state: "running" }, { state: "error" }, { state: "stopped" }]), { total: 4, running: 1, failed: 1 });
});

test("a pending approval is announced before older news", () => {
  const a = announcement([other("assistant", "a1", { text: "Done." }), other("approval", "p1", { status: "pending" })]);
  assert.equal(a.key, "p1");
  assert.match(a.text, /approval/);
});

test("a settled answer is announced with its start; a stopped or streaming one is not", () => {
  const a = announcement([other("assistant", "a1", { text: "Here is\n  the result." })]);
  assert.deepEqual(a, { key: "a1", text: "Agent replied: Here is the result." });
  assert.equal(announcement([other("assistant", "a1", { text: "partial", incomplete: true })]), null);
  const long = announcement([other("assistant", "a2", { text: "x".repeat(500) })]);
  assert.ok(long.text.endsWith("…") && long.text.length < 270);
});

test("a resolved approval and a completed run say nothing; a failed run does", () => {
  assert.equal(announcement([other("approval", "p1", { status: "allowed" }), other("turn_end", "e1", { status: "completed" })]), null);
  assert.equal(announcement([other("turn_end", "e2", { status: "failed" })]).text, "The run failed.");
  assert.equal(announcement([]), null);
});

test("spoken text drops Markdown marks and code but keeps link words", () => {
  const nl = String.fromCharCode(10);
  assert.equal(spokenText(["## Plan", "", "A **bold** step with `code` and [a link](https://x.test).", "", "- one", "- two"].join(nl)), "Plan A bold step with code and a link. one two");
  assert.equal(spokenText(["Run:", "```bash", "rm -rf /", "```", "done"].join(nl)), "Run: code block done");
});
