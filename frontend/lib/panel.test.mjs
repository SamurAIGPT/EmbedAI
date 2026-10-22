import assert from "node:assert/strict";
import { test } from "node:test";
import { chatHref, parsePanel } from "./panel.ts";

test("only known panels are accepted", () => {
  assert.equal(parsePanel("usage"), "usage");
  assert.equal(parsePanel("computer"), "computer");
  for (const raw of [null, "", "files", "Usage", "usage ", "<script>"]) assert.equal(parsePanel(raw), null);
});

test("opening a panel keeps the conversation and other parameters", () => {
  assert.equal(chatHref("thread=3", "usage"), "/chat?thread=3&panel=usage");
  assert.equal(chatHref("thread=3&engine=codex", "computer"), "/chat?thread=3&engine=codex&panel=computer");
  assert.equal(chatHref("", "usage"), "/chat?panel=usage");
});

test("switching replaces the panel and closing removes it", () => {
  assert.equal(chatHref("thread=3&panel=usage", "computer"), "/chat?thread=3&panel=computer");
  assert.equal(chatHref("thread=3&panel=usage", null), "/chat?thread=3");
  assert.equal(chatHref("panel=usage", null), "/chat");
});
